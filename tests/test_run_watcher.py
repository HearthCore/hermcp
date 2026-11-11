"""Run watcher + chat_send watch registration + approval hardening (fake Hermes api_server)."""
import json
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from starlette.testclient import TestClient

from hermcp.adapters.inbox_store import Inbox, get_inbox
from hermcp.adapters.run_watcher import RunWatcher
from hermcp.app import create_app
from hermcp.keys import Scopes
from hermcp.settings import load_settings

ME = __import__("os").getpid()


class Status:
    """Scripted status source: set .resp[(profile, run_id)] = (code, body)."""
    def __init__(self):
        self.resp, self.calls = {}, []

    def __call__(self, profile, run_id):
        self.calls.append((profile, run_id))
        return self.resp.get((profile, run_id), (404, None))


class Rig:
    def __init__(self, home, **env):
        self.settings = load_settings({"HERMES_HOME": str(home), "HERMCP_ALLOWED_HOSTS": "testserver", **env})
        self.box = get_inbox(self.settings)
        self.src = Status()
        self.clock = [1_000_000.0]
        self.w = RunWatcher(self.settings, fetch=self.src, clock=lambda: self.clock[0])

    def watch(self, run_id="run_a", key="k1", profile="default"):
        self.box.add_watch(run_id=run_id, profile=profile, session_id=None, key_id=key, key_name="claude")

    def tick(self, dt=3.0):
        self.clock[0] += dt
        return self.w.tick()

    def events(self):
        return self.box.list(limit=200)["events"][::-1]


@pytest.fixture()
def rig(home):
    return Rig(home)


def st(status, **kw):
    return (200, {"object": "hermes.run", "run_id": "run_a", "status": status, **kw})


def test_lifecycle_approval_then_finish(rig):
    rig.watch()
    rig.src.resp[("default", "run_a")] = st("queued")
    assert rig.tick() == 0
    rig.src.resp[("default", "run_a")] = st("running", session_id="sess1")
    assert rig.tick() == 0
    ap = {"request_id": "rq1", "command": "rm -rf /tmp/x  token=hmcp_abcdef12_" + "z" * 30, "description": "delete temp"}
    rig.src.resp[("default", "run_a")] = st("waiting_for_approval", approval=ap, session_id="sess1")
    assert rig.tick() == 0                                          # an approval row, not an event
    assert rig.tick() == 0                                          # same request: one row
    open_ = rig.box.open_approvals()
    assert len(open_) == 1 and open_[0]["approval_state"] == "waiting"
    rig.src.resp[("default", "run_a")] = st("running")
    rig.tick()
    rig.src.resp[("default", "run_a")] = st("waiting_for_approval", approval={**ap, "request_id": "rq2"})
    assert rig.tick() == 0                                          # a NEW request is a new row
    assert len(rig.box.open_approvals()) == 2
    rig.src.resp[("default", "run_a")] = st("completed", output="SECRET OUTPUT " * 100)
    assert rig.tick() == 1                                          # the terminal event
    assert rig.tick() == 0 and rig.box.get_watch("run_a")["done"] == 1     # finished watches are not polled again
    evs = rig.events()
    assert [e["kind"] for e in evs] == ["run_finished"]
    a = rig.box.get_approval("run:run_a:rq1")
    assert a is not None and a["origin"] == "watcher" and a["approval_id"] == "rq1"
    assert a["run_id"] == "run_a" and a["requester"] == "claude"
    assert a["session_id"] == "sess1" and a["profile"] == "default" and a["pushed_by"] == "hermcp:watcher"
    assert "rm -rf /tmp/x" in a["action"] and "hmcp_abcdef12" not in a["action"] and a["rationale"] == "delete temp"
    assert set(a["untrusted_fields"]) == {"summary", "action", "rationale"}
    # Teil 1: the run finished, so neither approval may still count as waiting
    assert rig.box.open_approvals() == []
    assert a["approval_state"] == "expired" and a["approval_closed_at"]
    assert "SECRET OUTPUT" not in json.dumps(evs) and "characters of output" in evs[-1]["summary"]


def test_approval_closes_when_the_run_ends(rig):
    """Teil 1: a `needs_approval` whose run ends is closed, however the run ends."""
    for status in ("completed", "failed", "cancelled", "interrupted"):
        run = f"run_{status}"
        rig.watch(run)
        rig.src.resp[("default", run)] = (200, {"object": "hermes.run", "run_id": run, "status": "waiting_for_approval",
                                                "approval": {"request_id": "rq", "command": "c"}})
        rig.tick()
        assert len(rig.box.open_approvals()) == 1
        rig.src.resp[("default", run)] = (200, {"object": "hermes.run", "run_id": run, "status": status})
        rig.tick()
        assert rig.box.open_approvals() == [], status


def test_approved_approval_is_not_closed_as_expired(rig):
    """Teil 1: an approval that was decided stays decided when the run later finishes."""
    rig.watch()
    rig.src.resp[("default", "run_a")] = st("waiting_for_approval", approval={"request_id": "rq1", "command": "c"})
    rig.tick()
    settled, applied = rig.box.settle_approval(approval_key="run:run_a:rq1", decision="once", surface="telegram")
    assert applied and settled["approval_state"] == "approved"
    rig.src.resp[("default", "run_a")] = st("completed", output="")
    rig.tick()
    assert rig.box.get_approval("run:run_a:rq1")["approval_state"] == "approved"


@pytest.mark.parametrize("status,kind,sev", [("failed", "run_failed", "error"), ("interrupted", "run_failed", "error"),
                                              ("cancelled", "run_cancelled", "info")])
def test_terminal_states(rig, status, kind, sev):
    rig.watch()
    rig.src.resp[("default", "run_a")] = st(status, error="boom " + "x" * 500 + " Bearer abc.def.ghi")
    assert rig.tick() == 1
    e = rig.events()[0]
    assert e["kind"] == kind and e["severity"] == sev
    if status != "cancelled":
        assert len(e["summary"]) <= 200 + 30 and "Bearer abc" not in e["summary"] and "boom" in e["summary"]
    assert rig.box.get_watch("run_a")["done"] == 1


def test_404_needs_clear_evidence(rig):
    rig.watch()
    assert rig.tick(1.0) == 0 and rig.tick(1.0) == 0 and rig.tick(1.0) == 0      # 3x 404 but < 5 s span: no verdict yet
    assert rig.events() == []
    for _ in range(4):
        rig.tick(3.0)
    evs = rig.events()
    assert [e["kind"] for e in evs] == ["run_lost"] and rig.box.get_watch("run_a")["done"] == 1
    rig.watch("run_b")                                              # a run that shows up again resets the counter
    rig.src.resp[("default", "run_b")] = st("running")
    rig.tick(); rig.tick()
    assert len(rig.events()) == 1


def test_gateway_trouble_backs_off_and_never_declares_lost(rig):
    rig.watch()
    rig.src.resp[("default", "run_a")] = (None, None)               # unreachable
    n0 = len(rig.src.calls)
    for _ in range(3):
        rig.tick(1.0)
    assert len(rig.src.calls) - n0 <= 2                              # backoff, not a hammering loop
    rig.src.resp[("default", "run_a")] = (503, None)
    for _ in range(20):
        rig.tick(5.0)
    assert rig.events() == [] and rig.box.get_watch("run_a")["done"] == 0
    rig.src.resp[("default", "run_a")] = st("completed", output="")
    for _ in range(30):
        rig.tick(30.0)
        if rig.events():
            break
    assert [e["kind"] for e in rig.events()] == ["run_finished"]     # recovers by itself


def test_auth_and_odd_answers_do_not_crash_or_emit(rig):
    rig.watch()
    for code, body in ((401, None), (403, {}), (200, "not a dict"), (418, {}), (200, {"status": "weird"})):
        rig.src.resp[("default", "run_a")] = (code, body)
        rig.tick(61.0)
    assert rig.events() == [] and rig.box.get_watch("run_a")["done"] == 0


def test_per_run_cap_keeps_a_slot_for_the_terminal_event(home):
    r = Rig(home, HERMCP_INBOX_RUN_MAX_EVENTS="4")
    r.watch()
    for i in range(10):
        r.src.resp[("default", "run_a")] = st("waiting_for_approval", approval={"request_id": f"rq{i}", "command": "c"})
        r.tick()
    assert r.events() == []                                          # approvals are rows, not events
    assert len(r.box.open_approvals()) == 10                          # ... and each one is its own approval
    r.src.resp[("default", "run_a")] = st("completed", output="")
    r.tick()
    assert [e["kind"] for e in r.events()] == ["run_finished"] and len(r.events()) == 1
    assert r.box.open_approvals() == []                                # the run ended: none still waiting


def test_watch_limit_per_key_and_expiry(home):
    r = Rig(home, HERMCP_INBOX_WATCH_MAX_PER_KEY="3", HERMCP_INBOX_WATCH_TTL_HOURS="1")
    for i in range(3):
        r.watch(f"run_{i}", key="k1")
    with pytest.raises(Exception) as exc:
        r.watch("run_x", key="k1")
    assert "watch_limit" in repr(getattr(exc.value, "code", exc.value)) or "already being watched" in str(exc.value)
    r.watch("run_y", key="k2")                                       # other key unaffected
    r.clock[0] = time.time() + 2 * 3600                              # all watches past their TTL
    assert r.box.active_watches(r.clock[0]) == []                    # expire silently
    assert r.events() == []


def test_migration_on_old_database(tmp_path):
    db = tmp_path / "inbox.db"
    c = sqlite3.connect(db)
    c.execute("""CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, source TEXT NOT NULL,
        kind TEXT NOT NULL, summary TEXT NOT NULL, ref TEXT, severity TEXT NOT NULL DEFAULT 'info',
        acked INTEGER NOT NULL DEFAULT 0, acked_at TEXT, pushed_by TEXT)""")
    c.execute("INSERT INTO events(ts,source,kind,summary,pushed_by) VALUES('2026-10-21T08:00:00Z','cron','info','old row','k')")
    c.commit(); c.close()
    box = Inbox(db)
    Inbox(db)                                                        # idempotent
    first = box.poll()                                               # old backlog is summarised for a new consumer ...
    assert first["events"] == [] and first["older_unread"] == 1
    ev = box.poll(include_older=True)["events"][0]                   # ... and still fully reachable
    assert ev["summary"] == "old row" and ev["origin"] == "push" and ev["untrusted_fields"][0] == "source"
    assert "run_id" not in ev                                        # watcher fields only on watcher events
    sys_ev = box.push_system(kind="run_finished", summary="x", run_id="r", profile="default", requester="k", dedupe_key="r:f")
    assert sys_ev["id"] == 2 and box.push_system(kind="run_finished", summary="x", run_id="r", profile="default",
                                                 requester="k", dedupe_key="r:f") is None


# ---- tools: inbox_push cannot forge watcher events -------------------------------------------
def make_app(home, **env):
    app = create_app(load_settings({"HERMES_HOME": str(home), "HERMCP_ALLOWED_HOSTS": "testserver,127.0.0.1", **env}))
    c = TestClient(app.app)

    def tok(name, scopes):
        return app.keystore.create(name, scopes)[1]

    def call(token, tool, /, **args):
        r = c.post("/mcp", headers={"Authorization": f"Bearer {token}"},
                   json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}})
        res = r.json()["result"]
        body = json.loads(res["content"][0]["text"])
        return (not res["isError"]), body.get("result", body.get("error"))
    return app, tok, call


def test_inbox_push_cannot_set_watcher_fields_or_kinds(home):
    app, tok, call = make_app(home)
    t = tok("prod", Scopes(access="read", toolsets=["inbox"], inbox_push=True))
    for kind in ("needs_approval", "run_finished", "run_failed", "run_lost", "run_anything"):
        ok, e = call(t, "inbox_push", source="evil", kind=kind, summary="approve me")
        assert not ok and e["code"] == "invalid_arguments", kind
    for extra in ({"approval_id": "x"}, {"action": "rm -rf /"}, {"rationale": "trust me"}, {"run_id": "r"},
                  {"requester": "claude"}, {"origin": "watcher"}, {"dedupe_key": "k"}):
        ok, e = call(t, "inbox_push", source="evil", kind="info", summary="s", **extra)
        assert not ok and e["code"] == "invalid_arguments", extra
    ok, r = call(t, "inbox_push", source="s", kind="info", summary="fine")
    assert ok and r["event"]["origin"] == "push" and r["event"]["untrusted_fields"][0] == "source"


# ---- chat_send registers the watch ---------------------------------------------------------------
class Gateway(BaseHTTPRequestHandler):
    seen, routes = [], {}

    def _do(self, method):
        n = int(self.headers.get("Content-Length", 0) or 0)
        body = json.loads(self.rfile.read(n) or b"{}") if n else None
        Gateway.seen.append((method, self.path, dict(self.headers), body))
        code, out = Gateway.routes.get((method, self.path), (404, {"error": "unknown"}))
        data = json.dumps(out).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

    def do_GET(self): self._do("GET")
    def do_POST(self): self._do("POST")
    def log_message(self, *a): pass


@pytest.fixture()
def gateway():
    Gateway.seen, Gateway.routes = [], {}
    srv = HTTPServer(("127.0.0.1", 0), Gateway)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, Gateway
    srv.shutdown()


def served(home, profiles):
    (home / "gateway_state.json").write_text(json.dumps({"pid": ME, "start_time": 1, "gateway_state": "running",
                                                         "platforms": {}, "served_profiles": profiles}))


def test_chat_send_registers_watch_with_idempotency_key(home, gateway):
    srv, gw = gateway
    gw.routes[("POST", "/v1/runs")] = (202, {"run_id": "run_9", "status": "queued"})
    gw.routes[("POST", "/p/researcher/v1/runs")] = (202, {"run_id": "run_p", "status": "queued"})
    app, tok, call = make_app(home)
    app.settings.hermes_api_url = f"http://127.0.0.1:{srv.server_port}"
    served(home, ["default", "researcher"])
    t = tok("claude", Scopes(access="write", toolsets=["chat", "inbox"]))
    ok, r = call(t, "chat_send", message="hi", wait_seconds=0)
    assert ok and r["watch"]["registered"] is True
    idem = gw.seen[0][2].get("Idempotency-Key")
    assert idem and idem.startswith("hermcp-") and len(idem) > 20
    ok, r2 = call(t, "chat_send", message="again", wait_seconds=0)
    posts = [x for x in gw.seen if x[0] == "POST"]
    assert posts[1][2]["Idempotency-Key"] != idem and r2["request_key"] != r["request_key"]   # new message, new key
    w = app and get_inbox(app.settings).get_watch("run_9")
    assert w["profile"] == "default" and w["key_name"] == "claude" and w["done"] == 0 and w["key_id"]
    ok, r = call(t, "chat_send", message="hi", wait_seconds=0, profile="researcher")
    assert ok and r["watch"]["registered"] and get_inbox(app.settings).get_watch("run_p")["profile"] == "researcher"


def test_chat_send_does_not_watch_unconfirmed_or_unserved_profiles(home, gateway):
    srv, gw = gateway
    gw.routes[("POST", "/v1/runs")] = (202, {"run_id": "run_9", "status": "queued"})
    app, tok, call = make_app(home)
    app.settings.hermes_api_url = f"http://127.0.0.1:{srv.server_port}"
    t = tok("claude", Scopes(access="write", toolsets=["chat", "inbox"]))
    ok, r = call(t, "chat_send", message="hi", wait_seconds=0)               # no gateway_state.json: served unknown
    assert ok and r["watch"]["registered"] is False and "not confirmed" in r["watch"]["reason"]
    assert get_inbox(app.settings).get_watch("run_9") is None
    served(home, ["default"])
    ok, e = call(t, "chat_send", message="hi", wait_seconds=0, profile="researcher")   # not served: refused before any call
    assert not ok and e["code"] == "profile_not_active"


def test_chat_send_watch_limit_does_not_break_the_run(home, gateway):
    srv, gw = gateway
    app, tok, call = make_app(home, HERMCP_INBOX_WATCH_MAX_PER_KEY="1")
    app.settings.hermes_api_url = f"http://127.0.0.1:{srv.server_port}"
    served(home, ["default"])
    t = tok("claude", Scopes(access="write", toolsets=["chat"]))
    gw.routes[("POST", "/v1/runs")] = (202, {"run_id": "run_1", "status": "queued"})
    assert call(t, "chat_send", message="a", wait_seconds=0)[1]["watch"]["registered"] is True
    gw.routes[("POST", "/v1/runs")] = (202, {"run_id": "run_2", "status": "queued"})
    ok, r = call(t, "chat_send", message="b", wait_seconds=0)
    assert ok and r["run_id"] == "run_2" and r["watch"]["registered"] is False and "already" in r["watch"]["reason"]


# ---- approvals: once/deny only, request_id verified against the run ------------------------------
def pending(rid="rq1", status="waiting_for_approval"):
    return (200, {"run_id": "run_1", "status": status,
                  **({"approval": {"request_id": rid, "command": "rm x", "description": "d"}} if status == "waiting_for_approval" else {})})


def test_chat_run_approval_checks_request_id_against_run(home, gateway):
    srv, gw = gateway
    app, tok, call = make_app(home)
    app.settings.hermes_api_url = f"http://127.0.0.1:{srv.server_port}"
    t = tok("claude", Scopes(access="write", toolsets=["chat"], approvals=True))
    gw.routes[("GET", "/v1/runs/run_1")] = pending("rq1")
    gw.routes[("POST", "/v1/runs/run_1/approval")] = (200, {"resolved": 1})
    ok, e = call(t, "chat_run_approval", run_id="run_1", choice="once", request_id="forged-from-inbox")
    assert not ok and e["code"] == "approval_not_pending" and e["pending_request_id"] == "rq1"
    assert not [s for s in gw.seen if s[0] == "POST"]                   # nothing was sent to the gateway
    ok, p = call(t, "chat_run_approval", run_id="run_1", choice="once", request_id="rq1", dry_run=True)
    assert ok and p["dry_run"] and not [s for s in gw.seen if s[0] == "POST"]
    ok, r = call(t, "chat_run_approval", run_id="run_1", choice="deny", request_id="rq1")
    assert ok and r["applied"] and gw.seen[-1][3] == {"choice": "deny", "request_id": "rq1"}
    for status in ("running", "completed"):                              # nothing pending any more
        gw.routes[("GET", "/v1/runs/run_1")] = pending(status=status)
        ok, e = call(t, "chat_run_approval", run_id="run_1", choice="once", request_id="rq1")
        assert not ok and e["code"] == "approval_not_pending" and e["run_status"] == status
    gw.routes[("GET", "/v1/runs/run_1")] = pending("rq1")                # race: answered elsewhere between check and POST
    gw.routes[("POST", "/v1/runs/run_1/approval")] = (409, {"error": "approval_not_pending"})
    ok, e = call(t, "chat_run_approval", run_id="run_1", choice="once", request_id="rq1")
    assert not ok and e["code"] == "approval_not_pending"
    gw.routes[("GET", "/v1/runs/nope")] = (404, {})
    assert call(t, "chat_run_approval", run_id="nope", choice="once", request_id="x")[1]["code"] == "not_found"


def test_choices_once_session_deny_and_always_for_keys_with_the_flag(home, gateway):
    srv, gw = gateway
    app, tok, call = make_app(home)
    app.settings.hermes_api_url = f"http://127.0.0.1:{srv.server_port}"
    adm = tok("admin", Scopes(access="admin", toolsets=["*"], approvals=True, approvals_always=True))
    adm0 = tok("adm0", Scopes(access="admin", toolsets=["*"], approvals=True))       # admin without the opt-in
    wr = tok("wr", Scopes(access="write", toolsets=["chat"], approvals=True))
    wap = tok("wap", Scopes(access="write", approvals=True))          # write key reaching the approvals toolset
    gw.routes[("GET", "/v1/runs/run_1")] = (200, {"status": "waiting_for_approval", "approval": {
        "request_id": "rq1", "command": "rm x", "choices": ["once", "session", "deny", "always"]}})
    gw.routes[("POST", "/v1/runs/run_1/approval")] = (200, {"resolved": 1})
    for choice in ("once", "session", "deny", "always"):        # admin key with the flag: every choice the run offers
        ok, r = call(adm, "chat_run_approval", run_id="run_1", choice=choice, request_id="rq1")
        assert ok and gw.seen[-1][3] == {"choice": choice, "request_id": "rq1"}, choice
    n = len([s for s in gw.seen if s[0] == "POST"])
    for who in (wr, adm0):                                     # write key and admin without the flag: refused
        ok, e = call(who, "chat_run_approval", run_id="run_1", choice="always", request_id="rq1")
        assert not ok and e["code"] == "choice_not_allowed" and "approvals_always" in e["message"]
    assert len([s for s in gw.seen if s[0] == "POST"]) == n       # nothing sent
    ok, e = call(wr, "chat_run_approval", run_id="run_1", choice="session", request_id="rq1")
    assert not ok and e["code"] == "forbidden"                                  # session: admin only
    assert call(wr, "chat_run_approval", run_id="run_1", choice="once", request_id="rq1")[0]
    # the gateway decides what it offers: no `session` for a smart-denied request
    gw.routes[("GET", "/v1/runs/run_1")] = (200, {"status": "waiting_for_approval", "approval": {
        "request_id": "rq1", "command": "rm x", "choices": ["once", "deny"]}})
    ok, e = call(adm, "chat_run_approval", run_id="run_1", choice="session", request_id="rq1")
    assert not ok and e["code"] == "choice_not_available" and e["offered"] == ["once", "deny"]
    ok, e = call(adm, "chat_run_approval", run_id="run_1", choice="always", request_id="rq1")
    assert not ok and e["code"] == "choice_not_available" and e["offered"] == ["once", "deny"]
    for t in ("chat_run_approval", "approvals_answer"):
        assert app.registry.tools[t].input_schema()["properties"]["choice"]["enum"] == ["once", "session", "deny", "always"]
    ok, e = call(wap, "approvals_answer", session_key="s", request_id="x", choice="always")
    assert not ok and e["code"] == "choice_not_allowed"


def test_watcher_runs_in_service_lifespan_with_real_http(home, gateway):
    """End to end: lifespan starts the watcher, it polls the fake gateway and fills the inbox."""
    srv, gw = gateway
    gw.routes[("GET", "/v1/runs/run_7")] = (200, {"run_id": "run_7", "status": "failed", "error": "nope"})
    app, tok, call = make_app(home, HERMCP_INBOX_WATCH_INTERVAL="0.5")
    app.settings.hermes_api_url = f"http://127.0.0.1:{srv.server_port}"
    get_inbox(app.settings).add_watch(run_id="run_7", profile="default", session_id=None, key_id="k", key_name="claude")
    t = tok("voice", Scopes(access="read", toolsets=["inbox"]))
    with TestClient(app.app):
        for _ in range(40):
            ok, r = call(t, "inbox_poll")
            if r["events"]:
                break
            time.sleep(0.25)
    assert r["events"][0]["kind"] == "run_failed" and "nope" in r["events"][0]["summary"]
    assert r["untrusted"] is True and r["events"][0]["requester"] == "claude"
