"""Phase 3: OAuth 2.1, approvals bridge, memory, dangerous scope, admin tools."""
import base64
import hashlib
import json
import os
import shutil
import sys
import tempfile
import types
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from starlette.testclient import TestClient

from hermcp.scopes import Scopes

ORIGIN = "https://hermcp.test"
CALLBACK = "https://claude.ai/api/mcp/auth_callback"


def pkce():
    verifier = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


# ---- OAuth ---------------------------------------------------------------------------------
@pytest.fixture()
def oapp(home, monkeypatch):
    from hermcp import oauth
    from hermcp.app import create_app
    from hermcp.settings import load_settings
    s = load_settings({"HERMES_HOME": str(home), "HERMCP_PUBLIC_URL": f"{ORIGIN}/mcp",
                       "HERMCP_ALLOWED_HOSTS": "hermcp.test"})
    cid, secret = oauth.init_client(s.data)
    app = create_app(s)
    app.cid, app.secret = cid, secret
    return app


def authorize(c, app, challenge, **over):
    q = {"response_type": "code", "client_id": app.cid, "redirect_uri": CALLBACK, "state": "st4te",
         "code_challenge": challenge, "code_challenge_method": "S256", "resource": f"{ORIGIN}/mcp", **over}
    return c.get("/authorize", params=q, follow_redirects=False)


def get_code(c, app, token_key):
    verifier, challenge = pkce()
    r = authorize(c, app, challenge)
    assert r.status_code == 302 and "/authorize/consent" in r.headers["location"], r.text
    rid = parse_qs(urlparse(r.headers["location"]).query)["rid"][0]
    assert c.get(f"/authorize/consent?rid={rid}").status_code == 200
    r = c.post("/authorize/consent", data={"rid": rid, "key": token_key}, follow_redirects=False)
    assert r.status_code == 302, r.text
    loc = urlparse(r.headers["location"])
    assert f"{loc.scheme}://{loc.netloc}{loc.path}" == CALLBACK
    q = parse_qs(loc.query)
    assert q["state"] == ["st4te"] and q["iss"] == [ORIGIN]
    return q["code"][0], verifier


def exchange(c, app, code, verifier, secret=None):
    return c.post("/token", data={"grant_type": "authorization_code", "code": code, "redirect_uri": CALLBACK,
                                  "client_id": app.cid, "client_secret": secret or app.secret,
                                  "code_verifier": verifier, "resource": f"{ORIGIN}/mcp"})


def mcp_list(c, access):
    return c.post("/mcp", headers={"Authorization": f"Bearer {access}"},
                  json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})


def test_metadata_documents(oapp):
    c = TestClient(oapp.app, base_url=ORIGIN)
    am = c.get("/.well-known/oauth-authorization-server").json()
    assert am["issuer"] == ORIGIN and am["token_endpoint"] == f"{ORIGIN}/token"
    assert am["token_endpoint_auth_methods_supported"] == ["client_secret_post"]   # never client_secret_basic
    assert am["code_challenge_methods_supported"] == ["S256"]
    assert "registration_endpoint" not in am and "revocation_endpoint" not in am
    for path in ("/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp"):
        pr = c.get(path).json()
        assert pr["resource"] == f"{ORIGIN}/mcp" and pr["authorization_servers"] == [ORIGIN]
    r = c.post("/mcp", json={})
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == (
        f'Bearer resource_metadata="{ORIGIN}/.well-known/oauth-protected-resource/mcp"')
    assert c.post("/register", json={}).status_code == 400


def test_no_oauth_without_client(happ):
    c = TestClient(happ.app)
    assert c.get("/.well-known/oauth-authorization-server").status_code == 404


def test_full_flow_scopes_refresh_and_hashing(oapp):
    c = TestClient(oapp.app, base_url=ORIGIN)
    _, key = oapp.keystore.create("alice", Scopes(access="read", toolsets=["status", "sessions"]))
    code, verifier = get_code(c, oapp, key)
    r = exchange(c, oapp, code, verifier)
    assert r.status_code == 200, r.text
    tok = r.json()
    assert tok["token_type"] == "Bearer" and tok["expires_in"] == 3600 and tok["scope"] == "hermes"
    # token works and inherits the key's scopes (read key -> no write tools)
    names = {t["name"] for t in mcp_list(c, tok["access_token"]).json()["result"]["tools"]}
    assert "hermes_whoami" in names and "config_set" not in names
    # code is single use
    assert exchange(c, oapp, code, verifier).status_code == 400
    # hashed at rest, 0600
    store = Path(oapp.settings.data / "oauth-tokens.json")
    raw = store.read_text()
    assert tok["access_token"] not in raw and tok["refresh_token"] not in raw
    assert store.stat().st_mode & 0o777 == 0o600
    assert oapp.secret not in (oapp.settings.data / "oauth-client.json").read_text()
    # refresh rotates; the old refresh token is dead
    refresh = {"grant_type": "refresh_token", "client_id": oapp.cid, "client_secret": oapp.secret}
    r2 = c.post("/token", data={**refresh, "refresh_token": tok["refresh_token"]})
    assert r2.status_code == 200
    new = r2.json()
    assert new["refresh_token"] != tok["refresh_token"]
    assert c.post("/token", data={**refresh, "refresh_token": tok["refresh_token"]}).status_code == 400
    assert mcp_list(c, tok["access_token"]).status_code == 401      # old access died with rotation
    assert mcp_list(c, new["access_token"]).status_code == 200
    # a restart (fresh app on the same data dir) keeps the connector alive
    from hermcp.app import create_app
    app2 = create_app(oapp.settings)
    assert mcp_list(TestClient(app2.app, base_url=ORIGIN), new["access_token"]).status_code == 200
    # rotating the underlying key kills the tokens
    oapp.keystore.rotate("alice")
    assert mcp_list(c, new["access_token"]).status_code == 401


def test_revoked_key_kills_token(oapp):
    c = TestClient(oapp.app, base_url=ORIGIN)
    _, key = oapp.keystore.create("tmp", Scopes(access="read"))
    code, verifier = get_code(c, oapp, key)
    access = exchange(c, oapp, code, verifier).json()["access_token"]
    assert mcp_list(c, access).status_code == 200
    oapp.keystore.revoke("tmp")
    assert mcp_list(c, access).status_code == 401


def test_token_endpoint_rejections(oapp):
    c = TestClient(oapp.app, base_url=ORIGIN)
    _, key = oapp.keystore.create("k", Scopes(access="read"))
    code, verifier = get_code(c, oapp, key)
    assert exchange(c, oapp, code, verifier, secret="wrong-secret").status_code == 401
    assert exchange(c, oapp, code, "x" * 50).status_code == 400            # wrong PKCE verifier
    basic = base64.b64encode(f"{oapp.cid}:{oapp.secret}".encode()).decode()
    r = c.post("/token", headers={"Authorization": f"Basic {basic}"},
               data={"grant_type": "authorization_code", "code": code, "redirect_uri": CALLBACK,
                     "client_id": oapp.cid, "code_verifier": verifier})
    assert r.status_code == 401                                             # client_secret_basic refused
    assert exchange(c, oapp, code, verifier).status_code == 200            # failed tries did not burn the code


def test_authorize_rejections(oapp):
    c = TestClient(oapp.app, base_url=ORIGIN)
    _, challenge = pkce()
    r = authorize(c, oapp, challenge, redirect_uri="https://evil.example/cb")
    assert r.status_code == 400 and "location" not in r.headers            # no open redirect
    assert authorize(c, oapp, challenge, client_id="nope").status_code == 400
    r = authorize(c, oapp, challenge, code_challenge_method="plain")
    assert "/authorize/consent" not in r.headers.get("location", "")
    r = authorize(c, oapp, challenge, resource="https://other.example/mcp")
    assert "invalid_target" in r.headers.get("location", "")


def test_consent_bad_key_and_expiry(oapp):
    c = TestClient(oapp.app, base_url=ORIGIN)
    _, challenge = pkce()
    rid = parse_qs(urlparse(authorize(c, oapp, challenge).headers["location"]).query)["rid"][0]
    bad = {"rid": rid, "key": "hmcp_nope_nopenopenopenopenope"}
    r = c.post("/authorize/consent", data=bad)
    assert r.status_code == 403 and "not valid" in r.text
    assert c.post("/authorize/consent", data={"rid": "unknown", "key": "x"}).status_code == 400
    assert c.get("/authorize/consent?rid=unknown").status_code == 400
    for _ in range(5):                      # pending request dies after too many wrong keys
        c.post("/authorize/consent", data=bad)
    assert c.get(f"/authorize/consent?rid={rid}").status_code == 400


# ---- approvals bridge ------------------------------------------------------------------------
class FakeApproval(types.ModuleType):
    def __init__(self):
        super().__init__("tools.approval")
        import threading
        self._lock = threading.Lock()
        self._gateway_queues = {
            "tg:123": [types.SimpleNamespace(data={
                "request_id": "r1", "command": "rm -rf /tmp/x TOKEN=hmcp_abcdef_0123456789abcdef",
                "description": "d", "pattern_key": "rm", "allowed_choices": ["once", "deny"],
                "timeout_seconds": 300})]}
        self.resolved = []

    def list_gateway_approvals(self, key):
        return [dict(e.data) for e in self._gateway_queues.get(key, [])]

    def resolve_gateway_approval(self, key, choice, resolve_all=False, reason=None, request_id=None):
        q = self._gateway_queues.get(key, [])
        hit = [e for e in q if e.data["request_id"] == request_id]
        for e in hit:
            q.remove(e)
        self.resolved.append((key, choice, request_id, reason))
        return len(hit)


@pytest.fixture()
def short_home(monkeypatch):
    h = Path(tempfile.mkdtemp(prefix="hc", dir="/tmp"))       # AF_UNIX paths are limited to ~107 bytes
    (h / "profiles" / "researcher").mkdir(parents=True)
    (h / "config.yaml").write_text("model:\n  default: m\n")
    monkeypatch.setenv("HERMES_HOME", str(h))
    monkeypatch.setenv("HERMCP_HERMES_BIN", str(Path(__file__).with_name("fake_hermes.py")))
    monkeypatch.setenv("FAKE_HERMES_LOG", str(h / "fake.log"))
    monkeypatch.delenv("HERMCP_PUBLIC_URL", raising=False)
    yield h
    shutil.rmtree(h, ignore_errors=True)


def make_app(home):
    from hermcp.app import create_app
    from hermcp.settings import load_settings
    return create_app(load_settings({"HERMES_HOME": str(home), "HERMCP_ALLOWED_HOSTS": "testserver"}))


@pytest.fixture()
def bridge_app(short_home, monkeypatch):
    from hermcp import bridge
    fake = FakeApproval()
    pkg = types.ModuleType("tools")
    pkg.approval = fake
    monkeypatch.setitem(sys.modules, "tools", pkg)
    monkeypatch.setitem(sys.modules, "tools.approval", fake)
    path = bridge.start(short_home)
    assert path is not None
    app = make_app(short_home)
    app.fake, app.sock = fake, path
    yield app
    bridge.stop()


def call_tool(app, token, name, /, **args):
    r = TestClient(app.app).post("/mcp", headers={"Authorization": f"Bearer {token}"}, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
    res = r.json()["result"]
    body = json.loads(res["content"][0]["text"])
    return (not res["isError"]), body.get("result", body.get("error"))


def test_bridge_socket_is_private(bridge_app):
    assert bridge_app.sock.stat().st_mode & 0o777 == 0o600
    assert bridge_app.sock.parent.stat().st_mode & 0o777 == 0o700


def test_approvals_need_explicit_flag(bridge_app):
    _, star = bridge_app.keystore.create("star", Scopes(access="admin", toolsets=["*"]))
    ok, err = call_tool(bridge_app, star, "approvals_pending")
    assert not ok and err["code"] == "forbidden"
    names = {t.name for t in bridge_app.registry.visible(bridge_app.keystore.get("star").scopes)}
    assert "approvals_pending" not in names and "chat_run_approval" not in names


def test_approvals_list_answer_audited(bridge_app):
    _, tok = bridge_app.keystore.create("ap", Scopes(access="write", approvals=True))
    ok, r = call_tool(bridge_app, tok, "approvals_bridge_status")
    assert ok and r["reachable"] is True
    ok, r = call_tool(bridge_app, tok, "approvals_pending")
    assert ok and r["count"] == 1
    item = r["pending"][0]
    assert item["session_key"] == "tg:123" and item["request_id"] == "r1"
    assert "hmcp_abcdef" not in item["command"]                              # secrets redacted on egress
    ok, e = call_tool(bridge_app, tok, "approvals_answer", session_key="tg:123", request_id="r1", choice="always")
    assert not ok and e["code"] == "choice_not_allowed" and "admin key" in e["message"]   # always: admin keys only
    ok, e = call_tool(bridge_app, tok, "approvals_answer", session_key="tg:123", request_id="r1", choice="session")
    assert not ok and e["code"] == "forbidden"                               # session needs access=admin
    ok, e = call_tool(bridge_app, tok, "approvals_answer", session_key="tg:123", request_id="r1", choice="deny",
                      dry_run=True)
    assert ok and e["applied"] is False and not bridge_app.fake.resolved
    ok, r = call_tool(bridge_app, tok, "approvals_answer", session_key="tg:123", request_id="r1", choice="deny",
                      reason="not now")
    assert ok and r["resolved"] == 1
    assert bridge_app.fake.resolved == [("tg:123", "deny", "r1", "not now")]
    ok, e = call_tool(bridge_app, tok, "approvals_answer", session_key="tg:123", request_id="r1", choice="once")
    assert not ok and e["code"] == "approval_not_pending"
    tools = [x["tool"] for x in bridge_app.audit.tail(100)]
    assert "approvals_answer" in tools and "bridge.answer" in tools and "bridge.list" in tools


def test_admin_key_with_the_flag_may_answer_always_when_the_request_offers_it(bridge_app):
    """`always` is released per key (admin + approvals_always), still gated by what the gateway offers, and audited."""
    _, adm = bridge_app.keystore.create("admin", Scopes(access="admin", approvals=True, approvals_always=True))
    _, adm_plain = bridge_app.keystore.create("adm2", Scopes(access="admin", approvals=True))
    _, wr = bridge_app.keystore.create("wr", Scopes(access="write", approvals=True, approvals_always=True))
    bridge_app.fake._gateway_queues["tg:456"] = [types.SimpleNamespace(data={
        "request_id": "r2", "command": "systemctl restart demo", "description": "d", "pattern_key": "systemctl",
        "allowed_choices": ["once", "session", "deny", "always"], "timeout_seconds": 300})]
    for tok in (wr, adm_plain):          # a write key and an admin key without the opt-in flag: both refused
        ok, e = call_tool(bridge_app, tok, "approvals_answer", session_key="tg:456", request_id="r2", choice="always")
        assert not ok and e["code"] == "choice_not_allowed" and e["allowed"] == ["once", "session", "deny"]
    assert not bridge_app.fake.resolved                                     # nothing released by those keys
    ok, r = call_tool(bridge_app, adm, "approvals_answer", session_key="tg:456", request_id="r2", choice="always")
    assert ok and r["resolved"] == 1 and r["choice"] == "always"
    assert bridge_app.fake.resolved == [("tg:456", "always", "r2", None)]
    granted = [x for x in bridge_app.audit.tail(100) if x["tool"] == "approvals_answer"]
    assert granted and granted[-1]["args"]["choice"] == "always"            # the permanent grant is on record
    assert granted[-1]["key_name"] == "admin"


def test_bridge_still_refuses_always_when_the_request_does_not_offer_it(bridge_app):
    _, adm = bridge_app.keystore.create("admin2", Scopes(access="admin", approvals=True, approvals_always=True))
    ok, e = call_tool(bridge_app, adm, "approvals_answer", session_key="tg:123", request_id="r1", choice="always")
    assert not ok and e["code"] == "bridge_error" and "always" in e["message"]      # tg:123 offers once/deny only
    assert not bridge_app.fake.resolved


def test_bridge_rejects_disallowed_choice_and_bad_input(bridge_app):
    from hermcp import bridge
    r = bridge.call(bridge_app.sock, {"op": "answer", "session_key": "tg:123", "request_id": "r1",
                                      "choice": "always"})
    assert r["error"].startswith("choice 'always' not allowed")
    assert bridge.call(bridge_app.sock, {"op": "answer", "choice": "once"})["ok"] is False
    assert bridge.call(bridge_app.sock, {"op": "nope"})["ok"] is False
    assert not bridge_app.fake.resolved


def test_profile_pinned_key_cannot_reach_other_profile(bridge_app):
    _, tok = bridge_app.keystore.create("researcher-ap", Scopes(access="write", approvals=True, profiles=["researcher"]))
    ok, e = call_tool(bridge_app, tok, "approvals_pending", profile="default")
    assert not ok and e["code"] == "forbidden"
    ok, e = call_tool(bridge_app, tok, "approvals_pending")      # defaults to researcher: its gateway has no bridge
    assert not ok and e["code"] == "adapter_error" and "plugins.enabled" in e["message"]


def test_bridge_down_gives_actionable_error(short_home):
    app = make_app(short_home)
    _, tok = app.keystore.create("ap", Scopes(access="write", approvals=True))
    ok, e = call_tool(app, tok, "approvals_pending")
    assert not ok and e["code"] == "adapter_error" and "restart the gateway" in e["message"]
    ok, r = call_tool(app, tok, "approvals_bridge_status")
    assert ok and r["reachable"] is False


# ---- dangerous scope, memory, admin ------------------------------------------------------------
def test_dangerous_tools_denied_by_default(happ, cli_log):
    _, admin = happ.keystore.create("adm", Scopes(access="admin"))
    ok, e = call_tool(happ, admin, "memory_reset", dry_run=True)
    assert not ok and e["code"] == "forbidden" and "dangerous" in e["message"]
    ok, e = call_tool(happ, admin, "profiles_delete", name="researcher", dry_run=True)
    assert not ok and e["code"] == "forbidden"
    assert "memory_reset" not in {t.name for t in happ.registry.visible(happ.keystore.get("adm").scopes)}
    _, bold = happ.keystore.create("bold", Scopes(access="admin", dangerous=True))
    ok, r = call_tool(happ, bold, "memory_reset", dry_run=True)
    assert ok and r["dry_run"] and r["would_run"][-2:] == ["reset", "--yes"]
    ok, r = call_tool(happ, bold, "memory_reset")                       # no confirm -> still only a plan
    assert ok and r["applied"] is False and r["needed"] == "confirm=true"
    assert not [x for x in cli_log() if x["argv"][:2] == ["memory", "reset"]]
    ok, r = call_tool(happ, bold, "memory_reset", confirm=True)
    assert ok and r["applied"]
    assert [x for x in cli_log() if x["argv"][:2] == ["memory", "reset"]]


def test_scope_roundtrip_with_dangerous():
    s = Scopes.from_dict({"access": "admin", "dangerous": True})
    assert Scopes.from_dict(s.to_dict()).dangerous is True
    assert Scopes.from_dict({"access": "read"}).dangerous is False


def test_memory_tools(happ, monkeypatch):
    monkeypatch.setenv("FAKE_HERMES_REPLY", "provider: builtin")
    _, k = happ.keystore.create("w", Scopes(access="write"))
    ok, r = call_tool(happ, k, "memory_status")
    assert ok and "builtin" in r["output"]
    ok, r = call_tool(happ, k, "memory_off", dry_run=True)
    assert ok and r["would_run"] == ["hermes", "memory", "off"]
    ok, e = call_tool(happ, k, "memory_reset")
    assert not ok and e["code"] == "forbidden"


def test_admin_tools(happ):
    _, adm = happ.keystore.create("adm", Scopes(access="admin"))
    _, rd = happ.keystore.create("rd", Scopes(access="read"))
    ok, r = call_tool(happ, adm, "keys_list")
    assert ok and {k["name"] for k in r["keys"]} == {"adm", "rd"}
    assert "hash" not in json.dumps(r) and "salt" not in json.dumps(r)
    ok, r = call_tool(happ, adm, "audit_tail", lines=10)
    assert ok and r["count"] >= 1 and all("ts" in x for x in r["records"])
    ok, r = call_tool(happ, adm, "audit_tail", key="adm", tool="keys_list")
    assert ok and r["records"] and all(x["tool"] == "keys_list" for x in r["records"])
    ok, e = call_tool(happ, rd, "audit_tail")
    assert not ok and e["code"] == "forbidden"
    names = {t["name"] for t in mcp_list(TestClient(happ.app), rd).json()["result"]["tools"]}
    assert "audit_tail" not in names


def test_run_approval_choices(happ):
    _, adm = happ.keystore.create("adm", Scopes(access="admin", approvals=True, approvals_always=True))
    _, adm_plain = happ.keystore.create("adm0", Scopes(access="admin", approvals=True))
    _, w = happ.keystore.create("w", Scopes(access="write", approvals=True, approvals_always=True))
    for tok in (w, adm_plain):                                                 # flag without admin, admin without flag
        ok, e = call_tool(happ, tok, "chat_run_approval", run_id="run_1", choice="always", request_id="r")
        assert not ok and e["code"] == "choice_not_allowed" and "approvals_always" in e["message"]
        assert e["allowed"] == ["once", "session", "deny"]
    ok, e = call_tool(happ, w, "chat_run_approval", run_id="run_1", choice="session", request_id="r")
    assert not ok and e["code"] == "forbidden" and "admin" in e["message"]      # session: admin keys only
    # admin + approvals_always passes the choice gate; this profile has no reachable run, so it stops at the adapter
    ok, e = call_tool(happ, adm, "chat_run_approval", run_id="run_1", choice="always", request_id="r")
    assert not ok and e["code"] not in ("choice_not_allowed", "invalid_arguments") and "approvals_always" not in json.dumps(e)
    ok, e = call_tool(happ, adm, "chat_run_approval", run_id="run_1", choice="once", request_id="r", resolve_all=True)
    assert not ok and e["code"] == "invalid_arguments"                         # resolve_all is gone
    ok, e = call_tool(happ, adm, "chat_run_approval", run_id="run_1", choice="once")
    assert not ok and e["code"] == "invalid_arguments"                         # request_id is required
    ok, e = call_tool(happ, adm, "chat_run_approval", run_id="run_1", choice="sometimes", request_id="r")
    assert not ok and e["code"] == "invalid_arguments" and "always" in e["message"]


# ---- doctor, service unit ------------------------------------------------------------------------
def test_doctor_runs_read_only(home, capsys):
    from hermcp import doctor
    before = {p.name for p in home.rglob("*")}
    doctor.run()
    out = capsys.readouterr().out
    assert "[ok] key store" in out and "approvals bridge [default]: not running" in out
    assert "doctor:" in out
    after = {p.name for p in home.rglob("*")}
    assert not [n for n in after if n.endswith(".sock")] and "oauth-client.json" not in after
    assert before <= after


def test_service_unit_invariants():
    unit = (Path(__file__).resolve().parents[1] / "deploy" / "hermcp.service").read_text()
    for needle in ("RestartPreventExitStatus=75", "Restart=on-failure", "NoNewPrivileges=yes",
                   "ExecStart=/usr/local/lib/hermes-agent/venv/bin/python -m hermcp serve",
                   "EnvironmentFile=-%h/.hermes/plugins/hermcp/.env"):
        assert needle in unit
    assert "9241" not in unit
