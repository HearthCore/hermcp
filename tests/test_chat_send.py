"""chat_send as fire-and-forget (request_key idempotency, start_unconfirmed, bounded wait) + run activity.

The fake gateway mirrors the real api_server contract that matters here (verified in gateway/platforms/
api_server_runs.py): POST /v1/runs with an Idempotency-Key is scoped per (profile, API key); the same key with
the same body replays the ORIGINAL run (202, replayed true), the same key with another body is HTTP 409.
"""
import hashlib
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from starlette.testclient import TestClient

from hermcp.adapters.inbox_store import get_inbox
from hermcp.adapters.run_watcher import ACTIVITY, RunWatcher, activity_label
from hermcp.app import create_app
from hermcp.scopes import Scopes
from hermcp.settings import load_settings
from hermcp.tools import chat

ME = os.getpid()


class GW(BaseHTTPRequestHandler):
    """state lives on the class; reset per test"""
    runs: dict = {}          # run_id -> status dict
    idem: dict = {}          # (profile, key) -> (fingerprint, run_id)
    posts: list = []         # every POST body + headers
    post_delay = 0.0         # seconds to sleep AFTER creating the run, before answering (client sees a timeout)
    hang_first = 0           # the first N POSTs create the run but answer only after post_delay
    post_code = None         # force an HTTP error code instead of creating a run
    status_override: dict = {}
    n_post = 0

    def _profile(self):
        return "researcher" if self.path.startswith("/p/researcher") else "default"

    def _send(self, code, obj, hdr=None):
        data = json.dumps(obj).encode()
        try:
            self.send_response(code); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            for k, v in (hdr or {}).items():
                self.send_header(k, v)
            self.end_headers(); self.wfile.write(data)
        except OSError:
            pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        GW.posts.append((body, dict(self.headers)))
        GW.n_post += 1
        if GW.post_code:
            return self._send(GW.post_code, {"error": {"message": "nope"}})
        key = self.headers.get("Idempotency-Key", "")
        fp = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        slot = (self._profile(), key)
        if key and slot in GW.idem:
            old_fp, rid = GW.idem[slot]
            if old_fp != fp:
                return self._send(409, {"error": {"code": "idempotency_key_conflict", "message": "different payload"}})
            self._maybe_hang()
            return self._send(202, {"run_id": rid, "status": GW.runs[rid]["status"], "replayed": True},
                              {"Idempotency-Replayed": "true"})
        rid = f"run_{len(GW.runs) + 1}"
        GW.runs[rid] = {"object": "hermes.run", "run_id": rid, "status": "queued", "session_id": body.get("session_id") or rid}
        if key:
            GW.idem[slot] = (fp, rid)
        self._maybe_hang()
        return self._send(202, {"run_id": rid, "status": "queued", "replayed": False})

    def _maybe_hang(self):
        if GW.hang_first and GW.n_post <= GW.hang_first:
            time.sleep(GW.post_delay)

    def do_GET(self):
        rid = self.path.rsplit("/", 1)[-1]
        st = GW.status_override.get(rid) or GW.runs.get(rid)
        return self._send(200, st) if st else self._send(404, {"error": "unknown"})

    def log_message(self, *a):
        pass


@pytest.fixture()
def gw():
    GW.runs, GW.idem, GW.posts, GW.status_override = {}, {}, [], {}
    GW.post_delay, GW.hang_first, GW.post_code, GW.n_post = 0.0, 0, None, 0
    srv = ThreadingHTTPServer(("127.0.0.1", 0), GW)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()


@pytest.fixture()
def rig(home, gw, monkeypatch):
    (home / "gateway_state.json").write_text(json.dumps({"pid": ME, "start_time": 1, "gateway_state": "running",
                                                         "platforms": {}, "served_profiles": ["default", "researcher"]}))
    app = create_app(load_settings({"HERMES_HOME": str(home), "HERMCP_ALLOWED_HOSTS": "testserver,127.0.0.1"}))
    app.settings.hermes_api_url = f"http://127.0.0.1:{gw.server_port}"
    c = TestClient(app.app)

    class R:
        pass
    r = R()
    r.app, r.home = app, home

    counter = [0]

    def tok(name=None, **sc):
        counter[0] += 1
        return app.keystore.create(name or f"key{counter[0]}", Scopes(**{"access": "write", "toolsets": ["chat", "inbox"], **sc}))[1]

    def call(token, tool, /, **args):
        resp = c.post("/mcp", headers={"Authorization": f"Bearer {token}"},
                      json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}})
        res = resp.json()["result"]
        body = json.loads(res["content"][0]["text"])
        return (not res["isError"]), body.get("result", body.get("error"))
    r.tok, r.call = tok, call
    return r


# ---- 1. start confirmation, no waiting ---------------------------------------------------------
def test_start_returns_at_once_with_uniform_shape(rig):
    GW.runs.clear()
    t = rig.tok()
    t0 = time.monotonic()
    ok, r = rig.call(t, "chat_send", message="hello")
    assert ok and time.monotonic() - t0 < 3
    assert set(r) >= {"run_id", "session_id", "profile", "status", "watch", "request_key"}
    assert r["run_id"] == "run_1" and r["profile"] == "default" and r["status"] == "queued"
    assert r["session_id"] == "run_1" and r["request_key"].startswith("req-") and r["watch"]["registered"] is True
    assert "output" not in r and "run" not in r
    assert len(GW.posts) == 1 and GW.posts[0][0] == {"input": "hello"}


def test_wait_seconds_limits_and_default(rig):
    t = rig.tok()
    ok, e = rig.call(t, "chat_send", message="x", wait_seconds=21)
    assert not ok and e["code"] == "invalid_arguments"
    assert chat.MAX_WAIT == 20
    props = rig.app.registry.tools["chat_send"].properties["wait_seconds"]
    assert props["default"] == 0 and props["maximum"] == 20


def test_uniform_shape_with_wait_finished_running_and_interrupted(rig):
    t = rig.tok()
    # finished within the wait: same keys, run_id on top, output included
    ok, r = rig.call(t, "chat_send", message="a", request_key="req-finished-001")
    GW.runs[r["run_id"]].update(status="completed", output="pong")
    ok, r = rig.call(t, "chat_send", message="a", wait_seconds=3, request_key="req-finished-001")
    assert ok and r["run_id"] == "run_1" and r["status"] == "completed" and r["output"] == "pong" and r["replayed"] is True
    assert set(r) >= {"run_id", "session_id", "profile", "status", "watch", "request_key"}
    # still running: returns when the wait is over, status running, no output
    ok, r = rig.call(t, "chat_send", message="slow", request_key="req-running-001")
    rid = r["run_id"]
    GW.runs[rid]["status"] = "running"
    t0 = time.monotonic()
    ok, r2 = rig.call(t, "chat_send", message="slow", wait_seconds=2, request_key="req-running-001")
    assert ok and r2["run_id"] == rid and r2["status"] == "running" and "output" not in r2
    assert 1.5 < time.monotonic() - t0 < 6
    # interrupted is a final state: returns before the wait is over
    ok, r = rig.call(t, "chat_send", message="boom", request_key="req-interrupted-001")
    GW.runs[r["run_id"]].update(status="interrupted", error="Gateway shutdown interrupted the run.")
    t0 = time.monotonic()
    ok, r3 = rig.call(t, "chat_send", message="boom", wait_seconds=10, request_key="req-interrupted-001")
    assert ok and r3["status"] == "interrupted" and "interrupted" in r3["error"] and time.monotonic() - t0 < 4


def test_approval_waiting_is_returned_not_waited_out(rig):
    t = rig.tok()
    ok, r = rig.call(t, "chat_send", message="rm")
    GW.runs[r["run_id"]].update(status="waiting_for_approval", approval={"request_id": "rq1", "command": "x"})
    t0 = time.monotonic()
    ok, r2 = rig.call(t, "chat_send", message="rm", wait_seconds=10, request_key=r["request_key"])
    assert r2["status"] == "waiting_for_approval" and r2["approval"]["request_id"] == "rq1" and time.monotonic() - t0 < 4


# ---- 2. start_unconfirmed, retry, idempotency ---------------------------------------------------
def test_timeout_after_creation_is_retried_with_same_key_and_confirms(rig, monkeypatch):
    monkeypatch.setattr(chat, "START_TIMEOUT", 0.4)
    GW.hang_first, GW.post_delay = 1, 1.2                     # 1st POST: run created, answer too late
    t = rig.tok()
    ok, r = rig.call(t, "chat_send", message="hi")
    assert ok and r["status"] == "queued" and r["replayed"] is True and r["run_id"] == "run_1"
    assert len(GW.runs) == 1                                  # the retry replayed, it did not start a second run
    assert GW.posts[0][1]["Idempotency-Key"] == GW.posts[1][1]["Idempotency-Key"]
    assert r["watch"]["registered"] is True


def test_start_unconfirmed_then_retry_with_same_request_key_creates_no_second_run(rig, monkeypatch):
    monkeypatch.setattr(chat, "START_TIMEOUT", 0.3)
    GW.hang_first, GW.post_delay = 2, 1.0                     # both attempts time out, run exists after the first
    t = rig.tok()
    ok, r = rig.call(t, "chat_send", message="important")
    assert ok and r["status"] == "start_unconfirmed" and r["run_id"] is None
    assert r["request_key"].startswith("req-") and "chat_conversations" in r["next"] and "inbox_poll" in r["next"]
    assert "request_key" in r["next"] and r["watch"]["registered"] is False
    assert get_inbox(rig.app.settings).active_watches() == []
    time.sleep(1.3)                                           # let the slow handlers finish
    monkeypatch.setattr(chat, "START_TIMEOUT", 5)
    ok, r2 = rig.call(t, "chat_send", message="important", request_key=r["request_key"])
    assert ok and r2["status"] == "queued" and r2["run_id"] == "run_1" and r2["replayed"] is True
    assert len(GW.runs) == 1                                  # ONE run in total
    assert r2["watch"]["registered"] is True


def test_same_request_key_other_message_is_refused(rig):
    t = rig.tok()
    ok, r = rig.call(t, "chat_send", message="one", request_key="req-fixed_key_001")
    ok, e = rig.call(t, "chat_send", message="two", request_key="req-fixed_key_001")
    assert not ok and e["code"] == "request_key_conflict" and len(GW.runs) == 1
    ok, e = rig.call(t, "chat_send", message="one", request_key="req-fixed_key_001", session_id="s_other")
    assert not ok and e["code"] == "request_key_conflict"


def test_idempotency_key_is_stable_and_scoped():
    a = chat.idempotency_key("k1", "default", "req-abcdefgh")
    assert a == chat.idempotency_key("k1", "default", "req-abcdefgh") and a.startswith("hermcp-") and len(a) < 100
    assert a != chat.idempotency_key("k2", "default", "req-abcdefgh")
    assert a != chat.idempotency_key("k1", "researcher", "req-abcdefgh")
    assert a != chat.idempotency_key("k1", "default", "req-abcdefgh2")


def test_request_key_is_validated(rig):
    t = rig.tok()
    for bad in ("short", "has space in it", "x" * 81):
        ok, e = rig.call(t, "chat_send", message="x", request_key=bad)
        assert not ok and e["code"] == "invalid_arguments", bad


@pytest.mark.parametrize("code,expect", [(500, "unconfirmed"), (502, "unconfirmed"), (400, "api_error"),
                                         (404, "api_error"), (401, "api_auth")])
def test_gateway_error_classes(rig, code, expect):
    GW.post_code = code
    t = rig.tok()
    ok, r = rig.call(t, "chat_send", message="x")
    if expect == "unconfirmed":
        assert ok and r["status"] == "start_unconfirmed" and "gateway error" in r["reason"] and r["request_key"]
    else:
        assert not ok and r["code"] == expect            # definite refusal: no run, nothing to confirm


def test_gateway_down_is_unconfirmed_and_fast(rig, monkeypatch):
    rig.app.settings.hermes_api_url = "http://127.0.0.1:1"
    t0 = time.monotonic()
    ok, r = rig.call(rig.tok(), "chat_send", message="x")
    assert ok and r["status"] == "start_unconfirmed" and time.monotonic() - t0 < 5


def test_errors_after_the_post_never_lose_the_run_id(rig, monkeypatch):
    monkeypatch.setattr(chat, "_observe", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    ok, r = rig.call(rig.tok(), "chat_send", message="x", wait_seconds=5)
    assert ok and r["run_id"] == "run_1" and any("chat_run_status" in n for n in r["notes"])
    monkeypatch.setattr(chat, "_register_watch", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db locked")))
    ok, r = rig.call(rig.tok(), "chat_send", message="y")
    assert ok and r["run_id"] == "run_2" and r["watch"]["registered"] is False


def test_time_budget_is_far_below_the_server_limit(rig, monkeypatch):
    s = rig.app.settings
    assert chat.TOOL_BUDGET <= 40 and chat.TOOL_BUDGET < s.call_timeout - 15
    # worst case: two start attempts, then the bounded wait with its last status read
    assert 2 * chat.START_TIMEOUT + 0.5 + (chat.TOOL_BUDGET - chat.STATUS_TIMEOUT) <= s.call_timeout - 15 or \
        2 * chat.START_TIMEOUT + 0.5 <= chat.TOOL_BUDGET
    # behavioural: dead status endpoint + wait 20 stays inside the budget
    monkeypatch.setattr(chat, "TOOL_BUDGET", 3.0)
    monkeypatch.setattr(chat, "STATUS_TIMEOUT", 0.5)
    GW.status_override = {}
    orig = GW.do_GET
    GW.do_GET = lambda self: time.sleep(5)                    # status reads hang
    try:
        t0 = time.monotonic()
        ok, r = rig.call(rig.tok(), "chat_send", message="x", wait_seconds=20)
        assert ok and r["run_id"] == "run_1" and time.monotonic() - t0 < 3.0 + 1.5
    finally:
        GW.do_GET = orig


def test_dry_run_returns_a_request_key_and_starts_nothing(rig):
    ok, r = rig.call(rig.tok(), "chat_send", message="x", dry_run=True)
    assert ok and r["dry_run"] and r["request_key"].startswith("req-") and GW.posts == []


# ---- 3. activity from the run status (no hook) --------------------------------------------------
class Src:
    def __init__(self):
        self.resp = {}

    def __call__(self, profile, run_id):
        return self.resp.get((profile, run_id), (404, None))


def mk_watcher(home):
    s = load_settings({"HERMES_HOME": str(home), "HERMCP_ALLOWED_HOSTS": "testserver"})
    box = get_inbox(s)
    clock = [1_000_000.0]
    src = Src()
    w = RunWatcher(s, fetch=src, clock=lambda: clock[0])
    box.add_watch(run_id="run_a", profile="default", session_id=None, key_id="k1", key_name="claude")

    def tick(status="running", last_event=None, dt=3.0, updated=None, **kw):
        clock[0] += dt
        body = {"run_id": "run_a", "status": status, "updated_at": updated if updated is not None else clock[0], **kw}
        if last_event is not None:
            body["last_event"] = last_event
        src.resp[("default", "run_a")] = (200, body)
        return w.tick()
    return box, w, tick, clock


def act(box):
    r = box.get_watch("run_a")
    return r["activity"], r["activity_at"]


def test_activity_follows_last_event_with_fixed_labels(home):
    box, w, tick, clock = mk_watcher(home)
    seq = [("queued", "run.queued", "queued"), ("running", "run.started", "started"),
           ("running", "tool.started", "running a tool"), ("running", "tool.completed", "between tools"),
           ("running", "reasoning.available", "thinking"), ("running", "subagent.start", "delegating to a subagent"),
           ("running", "subagent.complete", "subagent finished"), ("running", "message.delta", "working"),
           ("waiting_for_approval", "approval.request", "waiting for approval")]
    for status, ev, label in seq:
        tick(status, ev, approval={"request_id": "rq", "command": "x"} if status == "waiting_for_approval" else None)
        assert act(box)[0] == label, (status, ev)
        assert act(box)[1] is not None


def test_activity_never_copies_gateway_text(home):
    box, w, tick, clock = mk_watcher(home)
    tick("running", "tool.started: curl -H 'Authorization: Bearer SYNTHVALUE0123456789ABCDEF'",
         tool="terminal", preview="rm -rf /", args={"command": "secret"}, output="SYNTHVALUE0123456789ABCDEF")
    label, _ = act(box)
    assert label == "working" and "SYNTH" not in json.dumps(box.get_watch("run_a")) and label in set(ACTIVITY.values()) | {"working"}
    assert activity_label("running", None) == "working" and activity_label("running", 42) == "working"


def test_activity_changes_never_flood_the_inbox(home):
    box, w, tick, clock = mk_watcher(home)
    for i in range(300):                                      # a tool-heavy run: 300 status changes
        tick("running", "tool.started" if i % 2 == 0 else "tool.completed", dt=2.1)
    assert box.list(limit=200)["events"] == []                # NOT ONE event for activity
    assert box.poll()["unread_total"] == 0


def test_activity_writes_are_throttled_when_unchanged(home, monkeypatch):
    box, w, tick, clock = mk_watcher(home)
    writes = []
    orig = box.set_activity
    monkeypatch.setattr(type(box), "set_activity", lambda self, rid, a, at: (writes.append(a), orig(rid, a, at))[1])
    for i in range(50):
        tick("running", "tool.started", dt=1.0, updated=1_000_000.0 + 3 + i)      # same label for 50 s of gateway time
    assert 3 <= len(writes) <= 8                              # first write + one refresh per 10 s, not one per tick


def test_events_only_for_approval_and_terminal_states(home):
    box, w, tick, clock = mk_watcher(home)
    tick("running", "run.started"); tick("running", "tool.started"); tick("running", "reasoning.available")
    assert box.list()["events"] == []
    for _ in range(3):                                        # approval stays pending over many ticks: one event
        tick("waiting_for_approval", "approval.request", approval={"request_id": "rq1", "command": "x", "description": "d"})
    evs = box.list()["events"]
    assert [e["kind"] for e in evs] == ["needs_approval"]
    tick("running", "tool.started"); tick("running", "tool.completed")
    tick("completed", "run.completed", output="done")
    tick("completed", "run.completed", output="done")
    kinds = [e["kind"] for e in box.list(limit=50)["events"]][::-1]
    assert kinds == ["needs_approval", "run_finished"]
    assert box.get_watch("run_a")["activity"] == "finished" and box.get_watch("run_a")["done"] == 1


@pytest.mark.parametrize("status,kind,label", [("failed", "run_failed", "failed"), ("cancelled", "run_cancelled", "cancelled"),
                                               ("interrupted", "run_failed", "interrupted")])
def test_terminal_states_still_emit_and_set_activity(home, status, kind, label):
    box, w, tick, clock = mk_watcher(home)
    tick("running", "tool.started")
    tick(status, "run." + status, error="x")
    assert [e["kind"] for e in box.list()["events"]] == [kind] and act(box)[0] == label


def test_migration_adds_activity_columns_to_old_watches_table(tmp_path):
    import sqlite3
    from hermcp.adapters.inbox_store import Inbox
    db = tmp_path / "inbox.db"
    c = sqlite3.connect(db)
    c.execute("""CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, source TEXT NOT NULL,
        kind TEXT NOT NULL, summary TEXT NOT NULL, ref TEXT, severity TEXT NOT NULL DEFAULT 'info',
        acked INTEGER NOT NULL DEFAULT 0, acked_at TEXT, pushed_by TEXT)""")
    c.execute("""CREATE TABLE watches (run_id TEXT PRIMARY KEY, profile TEXT NOT NULL, session_id TEXT, key_id TEXT NOT NULL,
        key_name TEXT NOT NULL, started_at REAL NOT NULL, expires_at REAL NOT NULL, done INTEGER NOT NULL DEFAULT 0,
        done_at REAL, last_status TEXT)""")
    c.execute("INSERT INTO watches VALUES('run_old','default',NULL,'k','n',1,9999999999,0,NULL,'running')")
    c.commit(); c.close()
    box = Inbox(db)
    Inbox(db)
    assert box.get_watch("run_old")["activity"] is None
    box.set_activity("run_old", "thinking", 5.0)
    assert box.get_watch("run_old")["activity"] == "thinking"


# ---- 4. runs_active ----------------------------------------------------------------------------
def test_runs_active_lists_own_running_runs_with_activity(rig):
    t = rig.tok("claude")
    other = rig.tok("other")
    ok, a = rig.call(t, "chat_send", message="one")
    ok, b = rig.call(other, "chat_send", message="two")
    box = get_inbox(rig.app.settings)
    box.set_activity(a["run_id"], "running a tool", time.time() - 7)
    box.update_watch(a["run_id"], last_status="running")
    ok, r = rig.call(t, "runs_active")
    assert ok and r["count"] == 1
    run = r["runs"][0]
    assert run["run_id"] == a["run_id"] and run["profile"] == "default" and run["status"] == "running"
    assert run["activity"] == "running a tool" and 5 <= run["idle_seconds"] <= 12 and run["age_seconds"] >= 0
    assert run["started_at"].endswith("Z") and run["activity_at"].endswith("Z") and "started_by" not in run
    ok, e = rig.call(t, "runs_active", all_keys=True)
    assert not ok and e["code"] == "forbidden"
    admin = rig.tok("root", access="admin")
    ok, r = rig.call(admin, "runs_active", all_keys=True)
    assert ok and r["count"] == 2 and {x["started_by"] for x in r["runs"]} == {"claude", "other"}


def test_runs_active_new_run_and_finished_run(rig):
    t = rig.tok()
    ok, a = rig.call(t, "chat_send", message="one")
    ok, r = rig.call(t, "runs_active")
    assert r["runs"][0]["activity"] == "starting" and r["runs"][0]["idle_seconds"] is None
    get_inbox(rig.app.settings).update_watch(a["run_id"], last_status="completed", done=True)
    assert rig.call(t, "runs_active")[1]["count"] == 0


def test_runs_active_works_for_a_read_only_key(rig):
    ro = rig.tok("voice", access="read", toolsets=["inbox"])
    ok, r = rig.call(ro, "runs_active")
    assert ok and r["count"] == 0


# ---- client / client_session attribution ---------------------------------------------------------
def test_chat_send_records_client_session_on_the_run(rig):
    t = rig.tok()
    c = TestClient(rig.app.app)
    resp = c.post("/mcp", headers={"Authorization": f"Bearer {t}", "x-anthropic-client": "ClaudeCode"},
                  json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": "chat_send", "arguments": {"message": "hi", "client_session": "cc-sess-42"}}})
    body = json.loads(resp.json()["result"]["content"][0]["text"])["result"]
    assert body["client"] == "ClaudeCode" and body["client_session"] == "cc-sess-42"
    w = get_inbox(rig.app.settings).get_watch(body["run_id"])
    assert w["client"] == "ClaudeCode" and w["client_session"] == "cc-sess-42"
    ok, runs = rig.call(t, "runs_active")
    mine = [r for r in runs["runs"] if r["run_id"] == body["run_id"]][0]
    assert mine["client"] == "ClaudeCode" and mine["client_session"] == "cc-sess-42"
    assert "client_session" not in GW.posts[0][0]        # attribution stays in hermcp, never sent to the gateway


def test_chat_send_rejects_a_malformed_client_session(rig):
    ok, e = rig.call(rig.tok(), "chat_send", message="x", client_session="no spaces allowed")
    assert not ok and e["code"] == "invalid_arguments"
