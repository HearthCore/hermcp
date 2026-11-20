"""Event inbox: tools, scopes, HTTP endpoint, rotation, limits."""
import gc
import json
import os
import sqlite3
import warnings
from contextlib import closing
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from hermcp.app import create_app
from hermcp.keys import Scopes
from hermcp.settings import load_settings


def make(home, **env):
    base = {"HERMES_HOME": str(home), "HERMCP_ALLOWED_HOSTS": "testserver,127.0.0.1", **env}
    app = create_app(load_settings(base))
    c = TestClient(app.app)
    keys = {
        "voice": Scopes(access="read", toolsets=["inbox"]),
        "producer": Scopes(access="read", toolsets=["inbox"], inbox_push=True),
        "admin": Scopes(access="admin", approvals=True, secrets_write=True, dangerous=True),   # no inbox_push
        "nostatus": Scopes(access="read", toolsets=["status"], inbox_push=True),               # inbox toolset missing
    }
    tok = {n: app.keystore.create(n, sc)[1] for n, sc in keys.items()}

    def call(who, tool, /, **args):
        r = c.post("/mcp", headers={"Authorization": f"Bearer {tok[who]}"},
                   json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}})
        res = r.json()["result"]
        body = json.loads(res["content"][0]["text"])
        return (not res["isError"]), body.get("result", body.get("error"))

    def post(who, payload, raw=None):
        h = {"Authorization": f"Bearer {tok[who]}"} if who else {}
        return c.post("/inbox", headers=h, content=raw if raw is not None else json.dumps(payload))
    call.post, call.client, call.tok, call.app = post, c, tok, app
    return call


@pytest.fixture()
def box(home):
    return make(home)


def push(box, **kw):
    a = {"source": "hermes", "kind": "job_finished", "summary": "ok", **kw}
    return box("producer", "inbox_push", **a)


def test_push_poll_ack_roundtrip(box):
    ok, r = push(box, summary="run abc done", ref="run_abc", severity="warning")
    ev = r["event"]
    assert ok and ev["id"] == 1 and ev["ts"].endswith("Z") and ev["acked"] is False
    assert ev["ref"] == "run_abc" and ev["severity"] == "warning" and ev["pushed_by"] == "producer"
    ok, r = box("voice", "inbox_poll")
    assert ok and r["untrusted"] is True and "not instructions" in r["notice"]
    assert [e["id"] for e in r["events"]] == [1] and r["unread_total"] == 1
    assert box("voice", "inbox_poll")[1]["unread_total"] == 1          # poll does not ack
    ok, r = box("voice", "inbox_ack", ids=[1, 99])
    assert ok and r["acked"] == [1] and r["not_found"] == [99] and r["unread_total"] == 0
    assert box("voice", "inbox_poll")[1]["events"] == []
    ok, r = box("voice", "inbox_list")
    assert r["untrusted"] and r["events"][0]["acked"] is True and r["events"][0]["acked_at"]


def test_filters_limit_order(box):
    for i in range(5):
        push(box, source="hermes" if i % 2 == 0 else "script", kind="job_failed" if i < 2 else "info", summary=f"e{i}")
    ok, r = box("voice", "inbox_poll", limit=2)
    assert [e["summary"] for e in r["events"]] == ["e0", "e1"] and r["unread_total"] == 5   # oldest first
    assert {e["source"] for e in box("voice", "inbox_poll", source="script")[1]["events"]} == {"script"}
    assert len(box("voice", "inbox_poll", kind="job_failed")[1]["events"]) == 2
    assert [e["summary"] for e in box("voice", "inbox_poll", after_id=3)[1]["events"]] == ["e3", "e4"]
    box("voice", "inbox_ack", ids=[1, 2])
    ok, r = box("voice", "inbox_list", status="acked")
    assert [e["id"] for e in r["events"]] == [2, 1]                                          # newest first
    assert len(box("voice", "inbox_list", status="unread")[1]["events"]) == 3
    assert [e["id"] for e in box("voice", "inbox_list", before_id=3, limit=1)[1]["events"]] == [2]


def test_length_and_content_limits(box):
    ok, r = push(box, summary="x" * 501)
    assert not ok and r["code"] == "invalid_arguments"
    assert not push(box, source="s" * 65)[0] and not push(box, kind="k" * 49)[0]
    assert not push(box, ref="r" * 201)[0] and not push(box, severity="panic")[0]
    assert not push(box, kind="rm -rf /")[0]                       # kind/source: token charset only
    ok, r = push(box, summary="line1\nline2\x00\x07 key hmcp_abcdef12_" + "z" * 30 + " Bearer abc.def")
    s = r["event"]["summary"]
    assert ok and "\n" not in s and "\x00" not in s and "hmcp_abcdef12" not in s and "[redacted]" in s
    assert push(box, summary="x" * 500)[0]
    assert not box("producer", "inbox_push", source="a", kind="b")[0]        # summary missing


def test_rotation_by_count_and_age(home):
    b = make(home, HERMCP_INBOX_MAX_EVENTS="5")
    for i in range(8):
        b("producer", "inbox_push", source="s", kind="k", summary=f"e{i}")
    b("voice", "inbox_ack", ids=[1])        # acked event goes first when over the cap
    b("producer", "inbox_push", source="s", kind="k", summary="e8")
    ids = [e["id"] for e in b("voice", "inbox_list")[1]["events"]]
    assert len(ids) == 5 and 1 not in ids and ids[0] == 9
    # age: events older than retention are dropped on next push (read state is per consumer, so age decides)
    b.app.settings.inbox_retention_days = 1.0
    with closing(sqlite3.connect(b.app.settings.data / "inbox.db")) as db:
        db.execute("UPDATE events SET ts='2020-01-01T00:00:00Z' WHERE id=?", (ids[-1],))
        db.commit()
    b("producer", "inbox_push", source="s", kind="k", summary="trigger")
    left = [e["id"] for e in b("voice", "inbox_list")[1]["events"]]
    assert ids[-1] not in left


def test_scopes(box):
    ok, r = box("voice", "inbox_push", source="a", kind="b", summary="c")
    assert not ok and r["code"] == "forbidden" and "inbox_push" in r["message"]
    ok, r = box("admin", "inbox_push", source="a", kind="b", summary="c")      # admin + '*' does not imply it
    assert not ok and r["code"] == "forbidden"
    assert box("admin", "inbox_poll")[0]                                         # but admin may read
    ok, r = box("nostatus", "inbox_push", source="a", kind="b", summary="c")
    assert not ok and "toolset" in r["message"]
    assert not box("nostatus", "inbox_poll")[0]
    # visibility in tools/list
    def names(who):
        r = box.client.post("/mcp", headers={"Authorization": f"Bearer {box.tok[who]}"},
                            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        return {t["name"] for t in r.json()["result"]["tools"]}
    assert {"inbox_poll", "inbox_ack"} <= names("voice") and "inbox_push" not in names("voice")
    assert "inbox_push" in names("producer")


def test_push_rate_limit(home):
    b = make(home, HERMCP_INBOX_RATE_PER_MIN="3")
    res = [b("producer", "inbox_push", source="s", kind="k", summary=str(i)) for i in range(5)]
    assert [ok for ok, _ in res] == [True, True, True, False, False] and res[3][1]["code"] == "rate_limited"


def test_http_endpoint(box):
    r = box.post("producer", {"source": "cron", "kind": "info", "summary": "nightly done", "ref": "job1"})
    assert r.status_code == 201 and r.json()["result"]["event"]["source"] == "cron"
    assert "hmcp_" not in r.text                                                 # no key material in answers
    assert box.post(None, {}).status_code == 401
    assert box.post("voice", {"source": "a", "kind": "b", "summary": "c"}).status_code == 403
    assert box.post("producer", {"source": "a", "kind": "b", "summary": "x" * 600}).status_code == 400
    assert box.post("producer", None, raw="not json").status_code == 400
    assert box.post("producer", None, raw="[1]").status_code == 400
    assert box.post("producer", {"source": "a", "kind": "b", "summary": "x" * 9000}).status_code == 413
    assert box("voice", "inbox_poll")[1]["unread_total"] == 1                    # only the valid one landed


def test_http_rate_limit(home):
    b = make(home, HERMCP_INBOX_RATE_PER_MIN="1")
    p = {"source": "a", "kind": "b", "summary": "c"}
    assert b.post("producer", p).status_code == 201
    r = b.post("producer", p)
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1


def test_audit_has_no_token(box):
    push(box, summary="hello")
    log = (box.app.settings.data / "audit.jsonl").read_text()
    assert "inbox_push" in log and box.tok["producer"] not in log


def test_key_scope_roundtrip():
    sc = Scopes.from_dict({"access": "read", "toolsets": ["inbox"], "inbox_push": True})
    assert sc.inbox_push and Scopes.from_dict(sc.to_dict()).inbox_push
    assert Scopes().inbox_push is False


def test_ack_batch_limits(box):
    assert not box("voice", "inbox_ack", ids=[])[0]
    assert not box("voice", "inbox_ack", ids=list(range(1, 102)))[0]
    assert not box("voice", "inbox_ack", ids=["1"])[0]


def _age_all(box, minutes):
    """Pretend every stored event is `minutes` old (the store compares ISO timestamps)."""
    from datetime import datetime, timedelta, timezone
    ts = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with closing(sqlite3.connect(box.app.settings.data / "inbox.db")) as db:
        db.execute("UPDATE events SET ts=?", (ts,))
        db.commit()


def test_ack_is_per_key(box):
    """One consumer's ack must not hide the event from another key."""
    push(box, summary="shared")
    assert [e["id"] for e in box("voice", "inbox_poll")[1]["events"]] == [1]
    assert [e["id"] for e in box("producer", "inbox_poll")[1]["events"]] == [1]
    box("voice", "inbox_ack", ids=[1])
    assert box("voice", "inbox_poll")[1]["events"] == []
    ok, r = box("producer", "inbox_poll")
    assert [e["id"] for e in r["events"]] == [1] and r["unread_total"] == 1 and r["events"][0]["acked"] is False
    assert box("voice", "inbox_list", status="unread")[1]["events"] == []
    assert [e["id"] for e in box("producer", "inbox_list", status="unread")[1]["events"]] == [1]


def test_consumer_label_splits_sessions_of_one_key(box):
    push(box)
    box("voice", "inbox_ack", ids=[1], consumer="sess-a")
    assert box("voice", "inbox_poll", consumer="sess-a")[1]["events"] == []
    assert [e["id"] for e in box("voice", "inbox_poll", consumer="sess-b")[1]["events"]] == [1]
    assert [e["id"] for e in box("voice", "inbox_poll")[1]["events"]] == [1]       # unlabeled consumer is its own
    assert not box("voice", "inbox_poll", consumer="bad label")[0]               # pattern-checked


def test_new_consumer_skips_old_backlog(home):
    b = make(home, HERMCP_INBOX_JOIN_WINDOW_MIN="30")
    for i in range(3):
        push(b, summary=f"old{i}")
    _age_all(b, 120)
    push(b, summary="fresh")
    ok, r = b("voice", "inbox_poll")
    assert [e["summary"] for e in r["events"]] == ["fresh"]
    assert r["unread_total"] == 1 and r["older_unread"] == 3 and "include_older" in r["older_note"]
    ok, r = b("voice", "inbox_poll", include_older=True)
    assert [e["id"] for e in r["events"]] == [1, 2, 3, 4] and "older_unread" not in r
    # the cursor is sticky: a later poll does not re-hide or re-deliver anything wrongly
    push(b, summary="later")
    assert [e["summary"] for e in b("voice", "inbox_poll")[1]["events"]] == ["fresh", "later"]


def test_join_window_zero_and_ack_all(home):
    b = make(home, HERMCP_INBOX_JOIN_WINDOW_MIN="0")
    push(b)
    push(b)
    ok, r = b("voice", "inbox_poll")
    assert r["events"] == [] and r["unread_total"] == 0 and r["older_unread"] == 2
    ok, r = b("voice", "inbox_ack", all=True)
    assert ok and r["acked_count"] == 2 and "acked" not in r and r["unread_total"] == 0
    assert "older_unread" not in b("voice", "inbox_poll")[1]
    push(b)
    assert [e["id"] for e in b("voice", "inbox_poll")[1]["events"]] == [3]


def test_ack_needs_ids_or_all(box):
    ok, err = box("voice", "inbox_ack")
    assert not ok and err["code"] == "invalid_arguments"


def test_hermes_status_is_a_small_digest_with_next_steps(home):
    b2 = make(home)
    ok, r = b2("admin", "hermes_status")
    assert ok and r["verdict"] in ("ok", "degraded", "down")
    assert set(r) >= {"verdict", "gateway", "profiles", "runs", "gateway_agents", "approvals_pending", "inbox", "next"}
    assert "mine" in r["runs"]
    assert len(json.dumps(r)) < 1500                                  # level 0 stays small
    assert r["inbox"]["unread"] == 0 and r["inbox"]["failed_runs_24h"] == 0


def test_status_digest_counts_unread_by_kind_and_failed_runs(home):
    b = make(home)
    from hermcp.adapters.inbox_store import get_inbox
    box = get_inbox(b.app.settings)
    box.add_watch(run_id="r1", profile="p", session_id=None, key_id="k", key_name="k")
    box.push_system(kind="run_failed", summary="x", run_id="r1", profile="p", requester="k", dedupe_key="f1")
    push(b, summary="note")
    d = box.digest(consumer="c")
    assert d["unread"] == 2 and d["unread_by_kind"] == {"run_failed": 1, "job_finished": 1} \
        and d["failed_runs_24h"] == 1 and d["older_unread"] == 0
    box.ack([1], consumer="c")
    assert box.digest(consumer="c")["unread"] == 1
    assert box.digest(consumer="other")["unread"] == 2


def test_hermes_health_is_slim_by_default(home):
    b = make(home)
    ok, r = b("admin", "hermes_health", skills=False)
    assert ok and "profile_details" not in r
    ok, r = b("admin", "hermes_health", skills=False, detail=True)
    assert ok and "profile_details" in r


def test_runs_active_takes_progress_from_the_gateway(monkeypatch):
    """The gateway tracks idle_s / elapsed_s / current_tool itself; hermcp joins it by run_id."""
    from types import SimpleNamespace
    from hermcp.adapters import active_work as AW
    from hermcp.tools.inbox import _gateway_progress
    snap = {"available": True, "agents": [
        {"kind": "chat", "run_id": "run_a", "idle_s": 0.4, "elapsed_s": 61.9, "current_tool": "terminal"},
        {"kind": "api", "pid": 1}]}
    monkeypatch.setattr(AW, "snapshot", lambda **kw: snap)
    ctx = SimpleNamespace(restricted=False, principal=SimpleNamespace(scopes=SimpleNamespace(profiles=[])))
    runs = [{"run_id": "run_a", "idle_seconds": None}, {"run_id": "run_b", "idle_seconds": None}]
    _gateway_progress(ctx, runs)
    assert runs[0] == {"run_id": "run_a", "idle_seconds": 0, "running_seconds": 61, "current_tool": "terminal",
                       "gateway_detail": True}
    assert runs[1]["gateway_detail"] is False and runs[1]["idle_seconds"] is None
    monkeypatch.setattr(AW, "snapshot", lambda **kw: {"available": False})
    runs = [{"run_id": "run_a", "idle_seconds": None}]
    _gateway_progress(ctx, runs)
    assert runs == [{"run_id": "run_a", "idle_seconds": None}]          # no socket: nothing invented


def test_gateway_health_digest_counts_idle_and_wedged():
    from hermcp.adapters.active_work import health_digest
    snap = {"available": True, "active_agents": 4, "unlisted": 1, "by_kind": {"chat": 2, "cron": 1, "api": 1},
            "agents": [{"kind": "chat", "idle_s": 2.0}, {"kind": "chat", "idle_s": 900.0},
                       {"kind": "cron", "wedged": True, "idle_s": 10.0}]}
    d = health_digest(snap)
    assert d == {"available": True, "agents": 4, "by_kind": {"chat": 2, "cron": 1, "api": 1},
                 "no_detail": 1, "idle_5m": 1, "wedged": 1}
    assert health_digest({"available": False}) == {"available": False}


def _chunks(*parts):
    """A generator body: no Content-Length header, so only the capped stream read can stop it."""
    sent = []

    def gen():
        for p in parts:
            sent.append(len(p))
            yield p
    return gen(), sent


def test_inbox_body_cap_declared_length(box):
    h = {"Authorization": f"Bearer {box.tok['producer']}"}
    r = box.client.post("/inbox", headers=h, content=b"x" * 9000)
    assert r.status_code == 413 and r.json()["error"]["code"] == "too_large"


def test_inbox_body_cap_streamed_stops_early(box):
    h = {"Authorization": f"Bearer {box.tok['producer']}"}
    body, sent = _chunks(*[b"x" * 4096] * 200)                   # 800 KB offered, no Content-Length
    r = box.client.post("/inbox", headers=h, content=body)
    assert r.status_code == 413
    assert box.client.post("/inbox", headers=h, content=json.dumps(
        {"source": "a", "kind": "b", "summary": "c"})).status_code == 201   # still usable afterwards


def test_mcp_body_cap(box):
    from hermcp import server
    h = {"Authorization": f"Bearer {box.tok['voice']}"}
    big = b" " * (server.MAX_BODY + 1)
    r = box.client.post("/mcp", headers=h, content=big)                       # declared Content-Length
    assert r.status_code == 413 and r.json()["error"]["message"] == "request too large"
    body, _ = _chunks(*[b" " * 65536] * 20)                                   # ~1.3 MB, streamed
    assert box.client.post("/mcp", headers=h, content=body).status_code == 413
    ok = box.client.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert ok.status_code == 200


def test_read_capped_stops_reading_at_the_cap():
    import asyncio
    from hermcp.server import _read_capped

    class Req:
        headers = {}
        pulled = 0

        async def stream(self):
            for _ in range(1000):
                self.pulled += 1
                yield b"x" * 1000

    r = Req()
    assert asyncio.run(_read_capped(r, 5000)) is None
    assert r.pulled == 6                                                      # cut right after the cap, not 1000 chunks


# ---------------------------------------------------------------------------
# connection lifecycle (#93): every operation closes the connection it opened
# ---------------------------------------------------------------------------
# `sqlite3.Connection`'s context manager only commits or rolls back, so `with self._db() as db:`
# left every connection to the garbage collector: 38 call sites, thousands of open fds on a
# long-running `serve`, and `ResourceWarning: unclosed database` on Python 3.14. These tests pin
# the fix from three sides: the connection is closed when the call returns (behaviour), the fd
# table does not grow with the work done (the operator-visible symptom), and nothing is left for
# the GC to warn about (the CI symptom).

def _store(home, **kw):
    from hermcp.adapters.inbox_store import Inbox
    return Inbox(home / "inbox.db", **kw)


def _is_closed(conn) -> bool:
    try:
        conn.execute("SELECT 1")
    except sqlite3.ProgrammingError:
        return True
    return False


@pytest.fixture()
def opened(monkeypatch):
    """Every sqlite connection opened while the test runs, in order.

    Patches `sqlite3.connect` itself, not the store's own helper: the test then pins the behaviour
    (a connection is closed when the call returns) instead of the name of the method that opens it.
    """
    seen = []
    real = sqlite3.connect

    def record(*a, **kw):
        conn = real(*a, **kw)
        seen.append(conn)
        return conn
    monkeypatch.setattr(sqlite3, "connect", record)
    return seen


def test_every_inbox_operation_closes_its_connection(home, opened):
    """One connection per operation, closed before the call returns -- including early returns."""
    import itertools
    import time as _time

    box = _store(home)
    handles = itertools.count(1)

    def check(fn, *a, **kw):
        opened.clear()
        fn(*a, **kw)
        assert opened, f"{getattr(fn, '__name__', fn)} opened no connection"
        assert all(_is_closed(c) for c in opened), f"{getattr(fn, '__name__', fn)} left a connection open"

    check(box.push, source="s", kind="k", summary="e1", ref=None, severity="info", pushed_by="t")
    check(box.push_system, kind="run_finished", summary="done", run_id="run_a", profile="default",
          requester="k", dedupe_key="run_a:run_finished")
    check(box.push_system, kind="run_finished", summary="done", run_id="run_a", profile="default",
          requester="k", dedupe_key="run_a:run_finished")          # duplicate: returns None inside the block
    check(box.push_hook, kind="approval_pending", summary="waiting", profile="default",
          session_key="agent:default:cli:dm:1", dedupe_key="hook:1")
    check(box.push_ledger, kind="session_taken_over", summary="taken over", ref="run_a")
    check(box.open_approval, approval_key="run:run_a:req1", summary="needs approval", profile="default",
          origin="hook", run_id="run_a", session_key="agent:default:cli:dm:1")
    check(box.open_approval, approval_key="run:run_a:req1", summary="needs approval again", profile="default",
          origin="hook", run_id="run_a")                            # refresh path
    check(box.get_approval, "run:run_a:req1")
    check(box.find_approval, run_id="run_a")
    check(box.find_waiting_approval_for_hook, session_key="agent:default:cli:dm:1")
    check(box.settle_approval, approval_key="run:run_a:req1", decision="once", surface="cli")
    check(box.settle_approval, approval_key="run:run_a:req1", decision="once")   # already decided
    check(box.settle_approval, approval_key="run:missing", decision="once")      # not_pending
    check(box.approval_decisions, "run:run_a:req1")
    check(box.record_decision, approval_key="run:run_a:req1", outcome="refused", note="nope")
    check(box.close_run_approvals, "run_a")
    check(box.open_approvals)
    check(box.add_watch, run_id="run_a", profile="default", session_id="sess", key_id="k1", key_name="k1")
    check(box.active_watches)
    check(box.update_watch, "run_a", last_status="running")
    check(box.set_activity, "run_a", "thinking", _time.time())
    check(box.note_control, "run_a", "cancelled via chat_run_cancel")
    check(box.note_control, "run_a", steered=True)
    check(box.clear_control, "run_a")
    check(box.session_watches, "default", "sess")
    check(box.running)
    check(box.get_watch, "run_a")
    card, _ = box.session_register(key_id="k1", key_name="k1", client="Cowork", kind="voice", purpose="p",
                                  role=None, native_id="native-1", handle_factory=lambda: f"h{next(handles)}")
    check(box.session_register, key_id="k1", key_name="k1", client="Cowork", kind="voice", purpose="p",
          role=None, native_id="native-1", handle_factory=lambda: f"h{next(handles)}")   # resume path
    check(box.session_get, card["handle"])
    check(box.session_touch, card["handle"])
    check(box.session_update, card["handle"], purpose="renamed")
    check(box.sessions_list)
    check(box.unclaimed_runs, "k1")
    check(box.claim_runs, key_id="k1", handle=card["handle"], client="Cowork", run_ids=["run_a"])
    check(box.session_open_runs, card["handle"])
    check(box.run_sessions, ["run_a"])
    check(box.session_recover, old=card["handle"], key_id="k1", key_name="k1", client="Cowork", kind="voice",
          purpose="p", role=None, native_id=None, handle_factory=lambda: f"h{next(handles)}")
    check(box.poll, consumer="c")
    check(box.digest, consumer="c")
    check(box.attention_view, consumer="c")
    check(box.ack, [1], consumer="c")
    check(box.list)
    check(box.settle_failures)


def test_inbox_closes_the_connection_when_an_operation_raises(home, opened):
    """A refused write raises inside the block: the connection must still be closed."""
    from hermcp.registry import ToolError

    box = _store(home, watch_max_per_key=1)
    box.add_watch(run_id="run_a", profile="default", session_id=None, key_id="k1", key_name="k1")
    opened.clear()
    with pytest.raises(ToolError) as err:
        box.add_watch(run_id="run_b", profile="default", session_id=None, key_id="k1", key_name="k1")
    assert err.value.code == "watch_limit" and opened and all(_is_closed(c) for c in opened)

    opened.clear()
    with pytest.raises(ToolError) as err:
        box.session_recover(old="no-such-handle", key_id="k1", key_name="k1", client="Cowork", kind=None,
                            purpose=None, role=None, native_id=None, handle_factory=lambda: "h1")
    assert err.value.code == "unknown_client_session" and opened and all(_is_closed(c) for c in opened)


def test_inbox_operations_do_not_grow_the_fd_table(home):
    """The symptom in #93: fd usage followed GC timing instead of the work done."""
    fd_dir = Path("/proc/self/fd")
    if not fd_dir.is_dir():
        pytest.skip("no /proc/self/fd on this host")
    box = _store(home)
    box.push(source="s", kind="k", summary="warm-up", ref=None, severity="info", pushed_by="t")
    before = len(os.listdir(fd_dir))
    for i in range(200):
        box.push(source="s", kind="k", summary=f"e{i}", ref=None, severity="info", pushed_by="t")
        box.poll(consumer="c")
    assert len(os.listdir(fd_dir)) - before <= 2


def test_inbox_connections_leave_no_resource_warning(home, opened):
    """Nothing the store opened is left for the GC to warn about.

    Python 3.14 (CI) warns `unclosed database in <sqlite3.Connection object at 0x...>` when a
    connection is collected while open; 3.11 stays silent, so this guard only bites where the leak
    was visible. The warnings are matched against the addresses of the connections *this* test
    opened, so garbage from another test in the same process cannot redden it.
    """
    box = _store(home)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        for i in range(20):
            box.push(source="s", kind="k", summary=f"e{i}", ref=None, severity="info", pushed_by="t")
            box.poll(consumer="c")
        mine = {hex(id(c)) for c in opened}
        opened.clear()          # drop the strong refs: a leaked connection must be collectable here
        gc.collect()
    leaked = [str(w.message) for w in caught
              if issubclass(w.category, ResourceWarning) and any(a in str(w.message) for a in mine)]
    assert leaked == []
