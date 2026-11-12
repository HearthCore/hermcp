"""Run control: chat_run_steer, chat_run_cancel, chat_send mode=steer|interrupt, queue visibility.

The fake gateway mirrors the real api_server contract (verified live against Hermes 0.22.5):
POST /v1/runs -> 202 {run_id, status}; GET /v1/runs/{id}; POST /v1/runs/{id}/steer {"input"} -> 200
{accepted:true} or 409 run_not_accepting_steer when the run is not `running`; POST /v1/runs/{id}/stop ->
queued becomes `cancelled` at once, running becomes `stopping`.

The session FIFO is the gateway's `session_admissions` table (state.db): one `started` row per session,
the `queued` rows wait in `seq` order, `request_id` is the run_id for principal_id='api'.
"""
import json
import os
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from starlette.testclient import TestClient

from hermcp.adapters.inbox_store import get_inbox
from hermcp.adapters.run_watcher import RunWatcher
from hermcp.app import create_app
from hermcp.scopes import Scopes

ME = os.getpid()


class GW(BaseHTTPRequestHandler):
    runs: dict = {}          # run_id -> status dict
    calls: list = []         # (method, path, body)
    steer_code = None        # force an HTTP code for steer
    stop_code = None
    n_run = 0

    def _send(self, code, obj):
        data = json.dumps(obj).encode()
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except OSError:
            pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        path = self.path
        GW.calls.append(("POST", path, body))
        if path.endswith("/steer"):
            rid = path.split("/")[-2]
            if GW.steer_code:
                return self._send(GW.steer_code, {"error": {"message": "Run is not currently accepting steer input",
                                                            "code": "run_not_accepting_steer"}})
            st = GW.runs.get(rid)
            if not st or st.get("status") != "running":
                return self._send(409, {"error": {"message": "Run is not currently accepting steer input",
                                                  "code": "run_not_accepting_steer"}})
            st["last_event"] = "run.steered"
            return self._send(200, {"object": "hermes.run.steer", "run_id": rid, "accepted": True})
        if path.endswith("/stop"):
            rid = path.split("/")[-2]
            if GW.stop_code:
                return self._send(GW.stop_code, {"error": {"message": "nope"}})
            st = GW.runs.get(rid)
            if not st:
                return self._send(404, {"error": {"message": "unknown"}})
            if st["status"] == "queued":
                st.update(status="cancelled")
                return self._send(200, {"run_id": rid, "status": "cancelled"})
            if st["status"] in ("running", "waiting_for_approval"):
                st.update(status="stopping", last_event="run.stopping")
                return self._send(200, {"run_id": rid, "status": "stopping"})
            return self._send(200, st)
        # start a run
        GW.n_run += 1
        rid = f"run_{GW.n_run}"
        sid = body.get("session_id") or rid
        GW.runs[rid] = {"object": "hermes.run", "run_id": rid, "status": "queued", "session_id": sid}
        return self._send(202, {"run_id": rid, "status": "queued", "replayed": False})

    def do_GET(self):
        rid = self.path.rsplit("/", 1)[-1]
        GW.calls.append(("GET", self.path, None))
        st = GW.runs.get(rid)
        return self._send(200, st) if st else self._send(404, {"error": "unknown"})

    def log_message(self, *a):
        pass


@pytest.fixture()
def gw():
    GW.runs, GW.calls, GW.steer_code, GW.stop_code, GW.n_run = {}, [], None, None, 0
    srv = ThreadingHTTPServer(("127.0.0.1", 0), GW)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()


def make_state_db(home, rows, profile=None):
    """Minimal gateway state.db: sessions + session_admissions (only the columns hermcp reads)."""
    path = (home / "profiles" / profile / "state.db") if profile else (home / "state.db")
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS session_admissions (
            seq INTEGER PRIMARY KEY AUTOINCREMENT, admission_id TEXT NOT NULL UNIQUE,
            request_id TEXT NOT NULL, principal_id TEXT NOT NULL,
            target_session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE RESTRICT,
            status TEXT NOT NULL CHECK (status IN ('queued','started','unknown','terminal')),
            outcome TEXT, generation INTEGER);
    """)
    for i, (request_id, principal, sid, status) in enumerate(rows):
        db.execute("INSERT OR IGNORE INTO sessions (id) VALUES (?)", (sid,))
        db.execute("INSERT INTO session_admissions (admission_id, request_id, principal_id, target_session_id, "
                   "status, generation) VALUES (?,?,?,?,?,?)",
                   (f"adm{i}", request_id, principal, sid, status, 1 if status == "started" else None))
    db.commit()
    db.close()
    return path


@pytest.fixture()
def rig(home, gw):
    (home / "gateway_state.json").write_text(json.dumps({"pid": ME, "start_time": 1, "gateway_state": "running",
                                                         "platforms": {}, "served_profiles": ["default", "researcher"]}))
    app = create_app(__import__("hermcp.settings", fromlist=["load_settings"]).load_settings(
        {"HERMES_HOME": str(home), "HERMCP_ALLOWED_HOSTS": "testserver,127.0.0.1"}))
    app.settings.hermes_api_url = f"http://127.0.0.1:{gw.server_port}"
    c = TestClient(app.app)

    class R:
        pass
    r = R()
    r.app, r.home, r.settings = app, home, app.settings

    counter = [0]

    def tok(name=None, **sc):
        counter[0] += 1
        return app.keystore.create(name or f"key{counter[0]}",
                                   Scopes(**{"access": "write", "toolsets": ["chat", "inbox"], **sc}))[1]

    def call(token, tool, /, **args):
        resp = c.post("/mcp", headers={"Authorization": f"Bearer {token}"},
                      json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                            "params": {"name": tool, "arguments": args}})
        res = resp.json()["result"]
        body = json.loads(res["content"][0]["text"])
        return (not res["isError"]), body.get("result", body.get("error"))

    def posts(path_suffix=""):
        return [c for c in GW.calls if c[0] == "POST" and c[1].endswith(path_suffix)]

    r.tok, r.call, r.posts = tok, call, posts
    return r


def _send_run(rig, token, message, session_id=None):
    ok, out = rig.call(token, "chat_send", message=message, **({"session_id": session_id} if session_id else {}))
    assert ok, out
    return out


# ---- 1. chat_run_steer -------------------------------------------------------------------------
def test_steer_running_run_injects_without_new_run(rig):
    t = rig.tok()
    run = _send_run(rig, t, "long task")
    GW.runs[run["run_id"]].update(status="running")
    ok, r = rig.call(t, "chat_run_steer", run_id=run["run_id"], message="stop and answer HALLODU")
    assert ok and r["applied"] is True and r["accepted"] is True and r["status"] == "running"
    assert r["run_id"] == run["run_id"]
    assert rig.posts("/steer")[0][2] == {"input": "stop and answer HALLODU"}
    assert not rig.posts("/v1/runs") or len(rig.posts("/v1/runs")) == 1     # no second run
    assert get_inbox(rig.settings).get_watch(run["run_id"])["steer_count"] == 1


def test_steer_refuses_queued_run_and_says_what_to_do(rig):
    t = rig.tok()
    run = _send_run(rig, t, "waiting")                 # status queued in the fake
    ok, e = rig.call(t, "chat_run_steer", run_id=run["run_id"], message="x")
    assert not ok and e["code"] == "run_not_running" and e["run_status"] == "queued"
    assert "mode=queue" in e["message"]
    assert rig.posts("/steer") == []


def test_steer_refuses_waiting_for_approval_with_hint(rig):
    t = rig.tok()
    run = _send_run(rig, t, "rm")
    GW.runs[run["run_id"]].update(status="waiting_for_approval")
    ok, e = rig.call(t, "chat_run_steer", run_id=run["run_id"], message="x")
    assert not ok and e["code"] == "run_not_running" and "approval" in e["message"]


def test_steer_gateway_409_becomes_run_not_running(rig):
    t = rig.tok()
    run = _send_run(rig, t, "x")
    GW.runs[run["run_id"]].update(status="running")
    GW.steer_code = 409
    ok, e = rig.call(t, "chat_run_steer", run_id=run["run_id"], message="y")
    assert not ok and e["code"] == "run_not_running" and "Nothing was queued" in e["message"]


def test_steer_dry_run_plans_and_posts_nothing(rig):
    t = rig.tok()
    run = _send_run(rig, t, "x")
    GW.runs[run["run_id"]].update(status="running")
    before = len(rig.posts("/steer"))
    ok, r = rig.call(t, "chat_run_steer", run_id=run["run_id"], message="y", dry_run=True)
    assert ok and r["dry_run"] is True and r["applied"] is False and r["run_id"] == run["run_id"]
    assert len(rig.posts("/steer")) == before
    assert get_inbox(rig.settings).get_watch(run["run_id"])["steer_count"] == 0


def test_steer_unknown_run_is_not_found(rig):
    t = rig.tok()
    ok, e = rig.call(t, "chat_run_steer", run_id="run_nope", message="y")
    assert not ok and e["code"] == "not_found"


# ---- 2. chat_run_cancel ------------------------------------------------------------------------
def test_cancel_queued_run_removes_it_at_once(rig):
    t = rig.tok()
    run = _send_run(rig, t, "later")
    ok, r = rig.call(t, "chat_run_cancel", run_id=run["run_id"])
    assert ok and r["applied"] is True and r["status"] == "cancelled" and r["previous_status"] == "queued"
    assert r["session_id"] == run["session_id"] and "queue" in r["note"]
    assert rig.posts("/stop")[0][1].endswith(f"/v1/runs/{run['run_id']}/stop")


def test_cancel_running_run_reports_stopping(rig):
    t = rig.tok()
    run = _send_run(rig, t, "busy")
    GW.runs[run["run_id"]].update(status="running")
    ok, r = rig.call(t, "chat_run_cancel", run_id=run["run_id"])
    assert ok and r["applied"] is True and r["status"] == "stopping" and r["previous_status"] == "running"


def test_cancel_finished_run_is_left_alone(rig):
    t = rig.tok()
    run = _send_run(rig, t, "done")
    GW.runs[run["run_id"]].update(status="completed", output="x")
    ok, r = rig.call(t, "chat_run_cancel", run_id=run["run_id"])
    assert ok and r["applied"] is False and r["status"] == "completed"
    assert rig.posts("/stop") == []


def test_cancel_dry_run_plans_and_posts_nothing(rig):
    t = rig.tok()
    run = _send_run(rig, t, "x")
    ok, r = rig.call(t, "chat_run_cancel", run_id=run["run_id"], dry_run=True)
    assert ok and r["dry_run"] is True and r["would_become"] == "cancelled" and r["current_status"] == "queued"
    assert rig.posts("/stop") == []


def test_cancel_gateway_error_is_reported_not_swallowed(rig):
    t = rig.tok()
    run = _send_run(rig, t, "x")
    GW.stop_code = 500
    ok, e = rig.call(t, "chat_run_cancel", run_id=run["run_id"])
    assert not ok and e["code"] == "cannot_cancel" and e["run_status"] == "queued"
    assert get_inbox(rig.settings).get_watch(run["run_id"])["control"] is None   # note rolled back


# ---- 3. chat_send mode -------------------------------------------------------------------------
def test_mode_validation(rig):
    t = rig.tok()
    ok, e = rig.call(t, "chat_send", message="x", mode="steer")
    assert not ok and e["code"] == "invalid_arguments" and "session_id" in e["message"]
    ok, e = rig.call(t, "chat_send", message="x", session_id="s1", mode="bogus")
    assert not ok and e["code"] == "invalid_arguments"


def test_mode_queue_is_unchanged(rig):
    t = rig.tok()
    ok, r = rig.call(t, "chat_send", message="hello", mode="queue")
    assert ok and r["run_id"] == "run_1" and "mode" not in r and r["watch"]["registered"] is True


def test_mode_steer_uses_the_running_run_of_the_session(rig):
    t = rig.tok()
    make_state_db(rig.home, [("run_live", "api", "sess-1", "started")])
    GW.runs["run_live"] = {"object": "hermes.run", "run_id": "run_live", "status": "running", "session_id": "sess-1"}
    ok, r = rig.call(t, "chat_send", message="neue Richtung", session_id="sess-1", mode="steer")
    assert ok and r["mode"] == "steer" and r["run_id"] == "run_live" and r["accepted"] is True
    assert rig.posts("/steer")[0][2] == {"input": "neue Richtung"}
    assert GW.n_run == 0                                  # nothing new was started


def test_mode_steer_without_a_running_run_is_refused(rig):
    t = rig.tok()
    make_state_db(rig.home, [("run_q", "api", "sess-2", "queued")])
    ok, e = rig.call(t, "chat_send", message="x", session_id="sess-2", mode="steer")
    assert not ok and e["code"] == "no_running_run" and e["waiting"] == 1
    assert GW.n_run == 0


def test_mode_steer_names_a_foreign_turn_it_cannot_reach(rig):
    t = rig.tok()
    make_state_db(rig.home, [("cron:abc", "cron-owner", "sess-3", "started")])
    ok, e = rig.call(t, "chat_send", message="x", session_id="sess-3", mode="steer")
    assert not ok and e["code"] == "no_running_run" and "another door" in e["message"]


def test_mode_interrupt_starts_new_run_and_stops_the_running_one(rig):
    t = rig.tok()
    make_state_db(rig.home, [("run_old", "api", "sess-4", "started")])
    GW.runs["run_old"] = {"object": "hermes.run", "run_id": "run_old", "status": "running", "session_id": "sess-4"}
    get_inbox(rig.settings).add_watch(run_id="run_old", profile="default", session_id="sess-4", key_id="k", key_name="n")
    ok, r = rig.call(t, "chat_send", message="mach das andere", session_id="sess-4", mode="interrupt")
    assert ok and r["mode"] == "interrupt" and r["run_id"] != "run_old"
    assert r["interrupt"]["applied"] is True and r["interrupt"]["stopped_run_id"] == "run_old"
    assert GW.runs["run_old"]["status"] == "stopping"


def test_mode_interrupt_without_a_running_run_is_a_plain_start(rig):
    t = rig.tok()
    ok, r = rig.call(t, "chat_send", message="x", session_id="sess-5", mode="interrupt")
    assert ok and r["run_id"] == "run_1" and r["interrupt"]["applied"] is False
    assert "nothing else runs" in r["interrupt"]["reason"]


def test_mode_interrupt_dry_run_names_the_run_it_would_stop(rig):
    t = rig.tok()
    make_state_db(rig.home, [("run_old", "api", "sess-6", "started")])
    ok, r = rig.call(t, "chat_send", message="x", session_id="sess-6", mode="interrupt", dry_run=True)
    assert ok and r["would_stop_run"] == "run_old" and GW.n_run == 0


# ---- 4. queue visibility -----------------------------------------------------------------------
def test_chat_session_queue_reads_the_gateway_fifo(rig):
    t = rig.tok()
    make_state_db(rig.home, [("run_a", "api", "sess-q", "started"),
                             ("cron:x", "cron-owner", "sess-q", "queued"),
                             ("run_b", "api", "sess-q", "queued")])
    ok, r = rig.call(t, "chat_session_queue", session_id="sess-q")
    assert ok and r["source"] == "gateway" and r["running"] == 1 and r["waiting"] == 2
    assert [(x["run_id"], x["state"], x["position"]) for x in r["turns"]] == [
        ("run_a", "running", 0), (None, "queued", 1), ("run_b", "queued", 2)]
    assert "another door" not in r["turns"][0].get("origin", "")   # api turn carries no origin note


def test_chat_session_queue_hides_other_keys_runs_from_restricted_keys(rig):
    make_state_db(rig.home, [("run_a", "api", "sess-r", "queued")])
    other = rig.tok("other")
    _send_run(rig, other, "x")                     # a watch of another key exists, but for run_1
    get_inbox(rig.settings).add_watch(run_id="run_a", profile="default", session_id="sess-r",
                                      key_id="someone-else", key_name="x")
    mine = rig.tok("mine", profiles=["default"])
    ok, r = rig.call(mine, "chat_session_queue", session_id="sess-r")
    assert ok and r["turns"][0]["run_id"] is None and "another key" in r["turns"][0]["origin"]


def test_runs_active_adds_queue_position_and_length(rig):
    t = rig.tok()
    a = _send_run(rig, t, "one", session_id="sess-s")
    b = _send_run(rig, t, "two", session_id="sess-s")
    make_state_db(rig.home, [(a["run_id"], "api", "sess-s", "started"), (b["run_id"], "api", "sess-s", "queued")])
    ok, r = rig.call(t, "runs_active")
    by = {x["run_id"]: x for x in r["runs"]}
    assert by[a["run_id"]]["queue_position"] == 0 and by[a["run_id"]]["queue_length"] == 1
    assert by[b["run_id"]]["queue_position"] == 1 and by[a["run_id"]]["queue_source"] == "gateway"


def test_runs_active_falls_back_to_its_own_watches_without_a_state_db(rig):
    t = rig.tok()
    a = _send_run(rig, t, "one", session_id="sess-t")
    b = _send_run(rig, t, "two", session_id="sess-t")
    ok, r = rig.call(t, "runs_active")
    by = {x["run_id"]: x for x in r["runs"]}
    assert by[a["run_id"]]["queue_position"] == 0 and by[b["run_id"]]["queue_position"] == 1
    assert by[a["run_id"]]["queue_source"].startswith("hermcp")


# ---- 5. watcher -------------------------------------------------------------------------------
def test_watcher_reports_cancelled_with_the_control_note(rig):
    t = rig.tok()
    run = _send_run(rig, t, "busy")
    GW.runs[run["run_id"]].update(status="running")
    ok, _ = rig.call(t, "chat_run_cancel", run_id=run["run_id"])
    assert ok
    GW.runs[run["run_id"]].update(status="cancelled")
    box = get_inbox(rig.settings)
    RunWatcher(rig.settings).tick()
    ev = box.list(kind="run_cancelled")["events"]
    assert len(ev) == 1
    assert run["run_id"] in ev[0]["summary"] or "cancelled" in ev[0]["summary"]
    assert "chat_run_cancel" in ev[0]["summary"]
    assert ev[0]["run_id"] == run["run_id"] and ev[0]["session_id"] == run["session_id"]
    assert box.get_watch(run["run_id"])["done"] == 1


def test_watcher_labels_a_steer_as_activity(rig):
    t = rig.tok()
    run = _send_run(rig, t, "busy")
    GW.runs[run["run_id"]].update(status="running", last_event="run.steered", updated_at=time.time())
    RunWatcher(rig.settings).tick()
    assert get_inbox(rig.settings).get_watch(run["run_id"])["activity"] == "steered (guidance injected)"


# ---- 9. session ledger: hint, foreign-run guard, limit ---------------------------------------------------
def _register(rig, t, **kw):
    ok, r = rig.call(t, "session_card_set", **kw)
    assert ok, r
    return r["card"]["client_session"]


def test_send_without_handle_runs_but_points_to_registration(rig):
    t = rig.tok()
    out = _send_run(rig, t, "no handle")
    assert out["run_id"] and "session_card_set" in out["hint"]
    assert get_inbox(rig.settings).get_watch(out["run_id"])["client_session"] is None


def test_a_handle_belongs_to_the_key_that_registered_it(rig):
    a, b = rig.tok(), rig.tok()
    h = _register(rig, a)
    ok, e = rig.call(b, "chat_send", message="x", client_session=h)
    assert not ok and e["code"] == "forbidden"
    ok, e = rig.call(b, "session_card_set", client_session=h, purpose="hijack")
    assert not ok and e["code"] == "forbidden"


def test_acting_on_another_sessions_run_needs_force(rig):
    t = rig.tok()
    s1, s2 = _register(rig, t, purpose="orchestrator"), _register(rig, t, purpose="reviewer")
    run = rig.call(t, "chat_send", message="long", client_session=s1)[1]
    GW.runs[run["run_id"]].update(status="running")
    # the other session, and a caller without any handle, are stopped with a clear message
    for who in ({"client_session": s2}, {}):
        ok, e = rig.call(t, "chat_run_steer", run_id=run["run_id"], message="x", **who)
        assert not ok and e["code"] == "foreign_session_run" and s1 in e["message"] and "force=true" in e["message"]
        ok, e = rig.call(t, "chat_run_cancel", run_id=run["run_id"], **who)
        assert not ok and e["code"] == "foreign_session_run"
    assert rig.posts("/steer") == [] and rig.posts("/stop") == []
    # the owner is never asked; force overrides and says so
    ok, r = rig.call(t, "chat_run_steer", run_id=run["run_id"], message="mine", client_session=s1)
    assert ok and "forced" not in r
    ok, r = rig.call(t, "chat_run_steer", run_id=run["run_id"], message="override", client_session=s2, force=True)
    assert ok and r["forced"] == {"forced": True, "run_session": s1, "by_session": s2}
    ok, r = rig.call(t, "chat_run_cancel", run_id=run["run_id"], client_session=s2, force=True)
    assert ok and r["forced"]["run_session"] == s1


def test_runs_without_a_recorded_session_are_not_guarded(rig):
    t = rig.tok()
    s1 = _register(rig, t)
    old = _send_run(rig, t, "started before sessions existed")
    GW.runs[old["run_id"]].update(status="running")
    ok, r = rig.call(t, "chat_run_steer", run_id=old["run_id"], message="x", client_session=s1)
    assert ok and "forced" not in r


def test_open_runs_per_session_are_limited(rig):
    rig.settings.session_max_runs = 2
    t = rig.tok()
    s1, s2 = _register(rig, t), _register(rig, t)
    for i in range(2):
        assert rig.call(t, "chat_send", message=f"m{i}", client_session=s1)[0]
    ok, e = rig.call(t, "chat_send", message="m2", client_session=s1)
    assert not ok and e["code"] == "session_run_limit" and e["open_runs"] == 2 and e["limit"] == 2
    assert rig.call(t, "chat_send", message="other session", client_session=s2)[0]    # limit is per session


def test_recover_moves_open_runs_and_the_old_handle_is_retired(rig):
    t = rig.tok()
    old = _register(rig, t, purpose="orchestrator")
    run = rig.call(t, "chat_send", message="long", client_session=old)[1]
    done = rig.call(t, "chat_send", message="short", client_session=old)[1]
    GW.runs[run["run_id"]].update(status="running")
    get_inbox(rig.settings).update_watch(done["run_id"], done=True)               # finished: stays attributed to old
    ok, r = rig.call(t, "session_card_set", recover_from=old, confirm=True)
    assert ok and r["recovered"] == {"from": old, "open_runs_moved": 1}
    new = r["card"]["client_session"]
    box = get_inbox(rig.settings)
    assert box.get_watch(run["run_id"])["client_session"] == new and box.get_watch(done["run_id"])["client_session"] == old
    # the new session owns the moved run: no force needed; the retired handle is refused with a pointer
    ok, r = rig.call(t, "chat_run_steer", run_id=run["run_id"], message="carry on", client_session=new)
    assert ok and "forced" not in r
    ok, e = rig.call(t, "chat_send", message="x", client_session=old)
    assert not ok and e["code"] == "session_handed_over" and e["successor"] == new


def test_runs_active_without_handle_gives_the_situation_report(rig):
    t = rig.tok()
    handle = _register(rig, t, purpose="orchestrator")
    mine = rig.call(t, "chat_send", message="with handle", client_session=handle)[1]
    loose = rig.call(t, "chat_send", message="no handle")[1]
    ok, r = rig.call(t, "runs_active")
    assert ok
    ov = r["session_overview"]
    assert ov["you_passed_no_client_session"] is True
    assert [s["client_session"] for s in ov["sessions"]] == [handle] and ov["sessions"][0]["open_runs"] == 1
    assert ov["unclaimed_runs"] == [loose["run_id"]] and "recover_from" in ov["next"] and "claim_runs" in ov["next"]
    # with a handle: no overview, own runs are marked
    ok, r = rig.call(t, "runs_active", client_session=handle)
    assert ok and "session_overview" not in r
    assert {x["run_id"]: x.get("yours") for x in r["runs"]}[mine["run_id"]] is True


def test_claim_unclaimed_runs_needs_confirm_and_never_steals(rig):
    t = rig.tok()
    other = _register(rig, t, purpose="someone else")
    owned = rig.call(t, "chat_send", message="owned", client_session=other)[1]
    loose = rig.call(t, "chat_send", message="loose")[1]
    box = get_inbox(rig.settings)
    ok, r = rig.call(t, "session_card_set", claim_unclaimed=True)
    assert ok and r["claim"] == "preview" and r["applied"] is False
    assert [x["run_id"] for x in r["runs"]] == [loose["run_id"]]
    assert not box.get_watch(loose["run_id"])["client_session"]                 # preview changed nothing
    ok, e = rig.call(t, "session_card_set", claim_runs=[owned["run_id"]], confirm=True)
    assert not ok and e["code"] == "not_claimable"                              # owned by another session
    assert box.get_watch(owned["run_id"])["client_session"] == other
    ok, r = rig.call(t, "session_card_set", claim_runs=[loose["run_id"]], confirm=True, purpose="claimer")
    assert ok and r["applied"] is True and r["claimed"] == 1
    new = r["card"]["client_session"]
    assert new != other and box.get_watch(loose["run_id"])["client_session"] == new
    ok, r = rig.call(t, "runs_active", client_session=new)
    assert ok and not r["session_overview"]["unclaimed_runs"] if "session_overview" in r else True


def test_claim_into_an_existing_handle(rig):
    t = rig.tok()
    mine = _register(rig, t, purpose="me")
    loose = rig.call(t, "chat_send", message="loose")[1]
    ok, r = rig.call(t, "session_card_set", client_session=mine, claim_unclaimed=True, confirm=True)
    assert ok and r["claimed"] == 1 and r["card"]["client_session"] == mine
    assert get_inbox(rig.settings).get_watch(loose["run_id"])["client_session"] == mine
    ok, e = rig.call(t, "session_card_set", client_session="nope", claim_unclaimed=True, confirm=True)
    assert not ok and e["code"] == "unknown_client_session"


def test_former_owner_is_told_about_takeover_and_forced_access(rig):
    t = rig.tok()
    box = get_inbox(rig.settings)
    old = _register(rig, t, purpose="orchestrator")
    run = rig.call(t, "chat_send", message="long", client_session=old)[1]
    GW.runs[run["run_id"]].update(status="running")
    intruder = _register(rig, t, purpose="intruder")
    ok, _ = rig.call(t, "chat_run_steer", run_id=run["run_id"], message="x", client_session=intruder, force=True,
                     dry_run=True)
    assert ok and not [e for e in box.poll(limit=50)["events"] if e["kind"] == "foreign_run_forced"]  # dry run: silent
    ok, r = rig.call(t, "chat_run_steer", run_id=run["run_id"], message="x", client_session=intruder, force=True)
    assert ok and r["forced"]["run_session"] == old
    ok, r = rig.call(t, "session_card_set", recover_from=old, confirm=True)
    new = r["card"]["client_session"]
    kinds = {e["kind"]: e for e in box.poll(limit=50)["events"]}
    assert old in kinds["foreign_run_forced"]["summary"] and run["run_id"] == kinds["foreign_run_forced"]["ref"]
    assert old in kinds["session_taken_over"]["summary"] and new in kinds["session_taken_over"]["summary"]
    assert kinds["session_taken_over"]["origin"] == "watcher"                      # hermcp-written, not a push
    ok, e = rig.call(t, "inbox_push", source="x", kind="session_taken_over", summary="fake")
    assert not ok                                                                   # callers cannot fake the kind


def test_session_overview_advice_only_names_what_applies():
    from hermcp.tools.inbox import _overview_next

    new = _overview_next(False, False)
    assert "session_card_set" in new and "recover_from" not in new and "claim_runs" not in new
    assert "recover_from" in _overview_next(True, False) and "claim_runs" not in _overview_next(True, False)
    assert "claim_runs" in _overview_next(False, True)
