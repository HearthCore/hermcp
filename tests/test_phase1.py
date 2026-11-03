import json
import os
import stat

import pytest
from starlette.testclient import TestClient

from hermcp.audit import REDACTED, redact
from hermcp.keys import split_token
from hermcp.scopes import ScopeError, Scopes


def _synthetic(name, toolset, access, secrets=False):
    from hermcp.registry import Tool
    from hermcp.tools import reg
    if name not in reg.tools:
        reg.add(Tool(name=name, toolset=toolset, access=access, description="test", secrets=secrets,
                     handler=lambda ctx, **kw: {"ok": True}, properties={"name": {"type": "string"}, "value": {"type": "string"}}))
    return reg.tools[name]


def rpc(client, token, method, params=None, id_=1, headers=None):
    h = {"Authorization": f"Bearer {token}"} if token else {}
    h.update(headers or {})
    return client.post("/mcp", json={"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}}, headers=h)


def mk(happ, name, **sc):
    rec, token = happ.keystore.create(name, Scopes(**sc))
    return token


def tool_names(client, token):
    r = rpc(client, token, "tools/list")
    return {t["name"] for t in r.json()["result"]["tools"]}


# ---- key store ------------------------------------------------------------------------
def test_keys_hashed_and_0600(happ):
    token = mk(happ, "a", access="read")
    raw = happ.keystore.path.read_text()
    assert token not in raw and token.split("_", 2)[2] not in raw
    assert stat.S_IMODE(os.stat(happ.keystore.path).st_mode) == 0o600
    assert split_token(token)[0] in raw


def test_authenticate_roundtrip_and_reject(happ):
    token = mk(happ, "a")
    ks = happ.keystore
    assert ks.authenticate(token).name == "a"
    assert ks.authenticate(token[:-1] + "x") is None
    assert ks.authenticate("hmcp_nope_" + "x" * 30) is None
    assert ks.authenticate("garbage") is None


def test_revoke_rotate_expiry(happ):
    ks = happ.keystore
    token = mk(happ, "a")
    new = ks.rotate("a")[1]
    assert ks.authenticate(token) is None and ks.authenticate(new)
    ks.revoke("a")
    assert ks.authenticate(new) is None
    rec, tok = ks.create("exp", Scopes(), expires=1.0)
    assert ks.authenticate(tok) is None


def test_duplicate_name_rejected(happ):
    mk(happ, "a")
    with pytest.raises(Exception):
        mk(happ, "a")


# ---- scopes ---------------------------------------------------------------------------
def test_scope_validation():
    with pytest.raises(ScopeError):
        Scopes(access="root")
    with pytest.raises(ScopeError):
        Scopes.from_dict({"bogus": 1})
    with pytest.raises(ScopeError):
        Scopes(toolsets=[])
    assert Scopes(approvals="yes").approvals is False   # only literal True counts


def test_access_hierarchy(happ):
    reg = happ.registry
    cfg_set, cfg_get = reg.tools["config_set"], reg.tools["config_get"]
    r, w, a = Scopes(access="read"), Scopes(access="write"), Scopes(access="admin")
    assert r.tool_allowed(cfg_get)[0] and not r.tool_allowed(cfg_set)[0]
    assert w.tool_allowed(cfg_set)[0] and a.tool_allowed(cfg_set)[0]


def test_toolset_allowlist_and_deny(happ):
    reg = happ.registry
    s = Scopes(access="admin", toolsets=["status"], deny_tools=["status_doctor"])
    assert s.tool_allowed(reg.tools["status_gateway"])[0]
    assert not s.tool_allowed(reg.tools["status_doctor"])[0]
    assert not s.tool_allowed(reg.tools["config_get"])[0]
    assert s.tool_allowed(reg.tools["hermes_call"])[0]   # meta always available


def test_approvals_never_implied_by_star(happ):
    t = _synthetic("zz_appr", "approvals", "read")
    assert not Scopes(access="admin", toolsets=["*"]).tool_allowed(t)[0]
    assert Scopes(access="admin", toolsets=["*"], approvals=True).tool_allowed(t)[0]


def test_secrets_write_flag(happ):
    t = _synthetic("zz_sec", "providers", "admin", secrets=True)
    assert not Scopes(access="admin").tool_allowed(t)[0]
    assert Scopes(access="admin", secrets_write=True).tool_allowed(t)[0]


def test_profile_pinning(happ):
    s = Scopes(access="write", profiles=["researcher"])
    assert s.default_profile() == "researcher" and s.profile_allowed("researcher") and not s.profile_allowed("default")
    assert not s.tool_allowed(happ.registry.tools["config_set"])[0] or happ.registry.tools["config_set"].profile_aware


# ---- redaction ------------------------------------------------------------------------
def test_redaction():
    out = redact({"api_key": "sk-abcdefgh12345", "name": "x", "note": "use Bearer abc.def.ghi now",
                  "nested": {"password": "p", "ok": 1}, "hm": "hmcp_aabbccdd_" + "z" * 40})
    assert out["api_key"] == REDACTED and out["nested"]["password"] == REDACTED
    assert "abc.def" not in out["note"] and "hmcp_" not in out["hm"] and out["name"] == "x"
    pair = redact({"key": "providers.x.api_key", "value": "supersecret"})
    assert pair["value"] == REDACTED
    assert redact({"key": "display.skin", "value": "mono"})["value"] == "mono"


def test_audit_never_contains_secret(happ):
    _synthetic("zz_sec", "providers", "admin", secrets=True)
    token = mk(happ, "w", access="admin", secrets_write=True)
    c = TestClient(happ.app)
    rpc(c, token, "tools/call", {"name": "zz_sec", "arguments": {"name": "FOO_API_KEY", "value": "TOPSECRET123", "dry_run": True}})
    rpc(c, token, "tools/call", {"name": "config_get", "arguments": {"key": "model.default"}})
    log = happ.audit.path.read_text()
    assert "TOPSECRET123" not in log and token not in log
    assert "config_get" in log


# ---- HTTP / MCP -----------------------------------------------------------------------
def test_health_open(happ):
    r = TestClient(happ.app).get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_mcp_requires_auth(happ):
    c = TestClient(happ.app)
    assert rpc(c, None, "tools/list").status_code == 401
    assert rpc(c, "hmcp_bad_" + "x" * 30, "tools/list").status_code == 401
    r = rpc(c, "x", "tools/list")
    assert r.status_code == 401 and "WWW-Authenticate" in r.headers


def test_failed_auth_rate_limit(happ):
    c = TestClient(happ.app)
    codes = [rpc(c, "hmcp_bad_" + "x" * 30, "ping").status_code for _ in range(12)]
    assert codes[0] == 401 and codes[-1] == 429
    good = mk(happ, "g")
    assert rpc(c, good, "ping").status_code == 429   # blocked IP stays blocked for the window


def test_host_header_allowlist(happ):
    c = TestClient(happ.app, base_url="http://evil.example")
    token = mk(happ, "g")
    assert rpc(c, token, "ping").status_code == 403


def test_initialize_and_tools_list_stateless(happ):
    c = TestClient(happ.app)
    token = mk(happ, "r", access="read")
    r = rpc(c, token, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
    res = r.json()["result"]
    assert res["protocolVersion"] == "2025-06-18" and "tools" in res["capabilities"]
    assert "mcp-session-id" not in {k.lower() for k in r.headers}
    # no session needed for the next call
    assert "hermes_call" in tool_names(c, token)


def test_notification_gets_202(happ):
    c = TestClient(happ.app)
    token = mk(happ, "r")
    r = c.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"},
               headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 202


def test_read_key_hides_and_rejects_write(happ):
    c = TestClient(happ.app)
    rt, at = mk(happ, "r", access="read"), mk(happ, "adm", access="admin")
    rn, an = tool_names(c, rt), tool_names(c, at)
    assert "config_set" not in rn and "config_get" in rn
    assert "config_set" in an
    r = rpc(c, rt, "tools/call", {"name": "config_set", "arguments": {"key": "display.skin", "value": "mono"}})
    body = r.json()["result"]
    assert body["isError"] is True and "forbidden" in body["content"][0]["text"]
    r = rpc(c, rt, "tools/call", {"name": "hermes_call", "arguments": {"name": "config_set", "args": {"key": "a.b", "value": "1"}}})
    assert r.json()["result"]["isError"] is True and "forbidden" in r.json()["result"]["content"][0]["text"]
    assert (happ.keystore.path.parent.parent.parent / "config.yaml").read_text().count("mono") == 0


def test_toolset_scope_hides_from_list(happ):
    c = TestClient(happ.app)
    t = mk(happ, "s", access="admin", toolsets=["status"])
    names = tool_names(c, t)
    assert "hermes_health" in names and "config_get" not in names and "kanban_list" not in names


def test_core_tool_count_reasonable(happ):
    c = TestClient(happ.app)
    t = mk(happ, "a", access="admin", approvals=True, secrets_write=True)
    n = tool_names(c, t)
    assert 15 <= len(n) <= 45, len(n)
    assert len(happ.registry.tools) > len(n)    # long tail lives behind hermes_call


def test_search_describe_call(happ):
    c = TestClient(happ.app)
    t = mk(happ, "a", access="read")
    r = rpc(c, t, "tools/call", {"name": "hermes_search_tools", "arguments": {"query": "kanban"}})
    data = json.loads(r.json()["result"]["content"][0]["text"])["result"]
    assert any(x["name"].startswith("kanban_") for x in data["tools"])
    r = rpc(c, t, "tools/call", {"name": "hermes_describe_tool", "arguments": {"name": "config_get"}})
    assert "inputSchema" in json.loads(r.json()["result"]["content"][0]["text"])["result"]


def test_invalid_args_and_unknown_tool(happ):
    c = TestClient(happ.app)
    t = mk(happ, "a", access="read")
    r = rpc(c, t, "tools/call", {"name": "config_get", "arguments": {"bogus": 1}})
    assert r.json()["result"]["isError"] and "invalid_arguments" in r.json()["result"]["content"][0]["text"]
    r = rpc(c, t, "tools/call", {"name": "nope"})
    assert "unknown_tool" in r.json()["result"]["content"][0]["text"]


def test_profile_pinned_key(happ):
    c = TestClient(happ.app)
    t = mk(happ, "alice", access="write", profiles=["researcher"], toolsets=["config", "status"])
    r = rpc(c, t, "tools/call", {"name": "config_get", "arguments": {"key": "model.default"}})
    assert "researcher-model" in r.json()["result"]["content"][0]["text"]            # default profile = researcher
    r = rpc(c, t, "tools/call", {"name": "config_get", "arguments": {"key": "model.default", "profile": "default"}})
    assert "bound to profile 'researcher'; requested profile 'default'" in r.json()["result"]["content"][0]["text"]
