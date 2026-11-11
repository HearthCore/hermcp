"""Settlement of failed runs: a failure leaves the attention view once its session moved on.

Rules under test (see Inbox.settle_failures / _attention_sql):
- a later run of the SAME session completed      -> superseded_by_completed, out of attention
- a later run of the same session also failed    -> superseded_by_failure, only the newest counts
- a later run is still running                   -> the failure stays visible
- ack by a consumer                              -> out of THAT consumer's attention count, not the others'
- interrupted by a restart after producing output -> own kind `run_interrupted`, never a real failure
"""
import json

import pytest

from hermcp.adapters.inbox_store import Inbox, get_inbox
from hermcp.adapters.run_watcher import RunWatcher
from hermcp.settings import load_settings


class Status:
    """Scripted status source: set .resp[(profile, run_id)] = (code, body)."""

    def __init__(self):
        self.resp = {}

    def __call__(self, profile, run_id):
        return self.resp.get((profile, run_id), (404, None))


class Rig:
    def __init__(self, home):
        self.settings = load_settings({"HERMES_HOME": str(home), "HERMCP_ALLOWED_HOSTS": "testserver"})
        self.box = get_inbox(self.settings)
        self.src = Status()
        self.clock = [1_000_000.0]
        self.w = RunWatcher(self.settings, fetch=self.src, clock=lambda: self.clock[0])

    def watch(self, run_id="run_a"):
        self.box.add_watch(run_id=run_id, profile="default", session_id=None, key_id="k1", key_name="claude")

    def tick(self, dt=3.0):
        self.clock[0] += dt
        return self.w.tick()

    def status(self, run_id, status, session_id="sess1", **kw):
        self.src.resp[("default", run_id)] = (200, {"object": "hermes.run", "run_id": run_id,
                                                   "status": status, "session_id": session_id, **kw})

    def fail(self, run_id="run_a", session_id="sess1", error="iteration limit"):
        self.watch(run_id)
        self.status(run_id, "running", session_id)
        self.tick()
        self.status(run_id, "failed", session_id, error=error)
        self.tick()

    def events(self, **kw):
        return [e for e in self.box.list(limit=200, **kw)["events"][::-1] if e["kind"] != "approval"]

    def attention(self, consumer="c"):
        return [e for e in self.box.poll(consumer=consumer, attention_only=True, limit=50)["events"]
                if e["kind"] != "approval"]


@pytest.fixture()
def rig(home):
    return Rig(home)


def test_successor_completed_settles_the_failure(rig):
    rig.fail()
    assert [e["kind"] for e in rig.attention()] == ["run_failed"]
    assert rig.box.digest(consumer="c")["failed_runs_24h"] == 1

    rig.watch("run_b")
    rig.status("run_b", "completed", "sess1", output="x" * 40)
    assert rig.tick() == 1
    failed = [e for e in rig.events() if e["kind"] == "run_failed"][0]
    assert failed["settled_reason"] == "superseded_by_completed"
    assert failed["superseded_by"] == "run_b" and failed["settled_at"]
    assert rig.attention() == []                                  # nothing needs a human any more
    assert rig.box.digest(consumer="c")["failed_runs_24h"] == 0
    hist = [e for e in rig.events() if e["kind"] == "run_failed"][0]   # history keeps it, with the mark
    assert hist["settled_reason"] == "superseded_by_completed" and hist["superseded_by"] == "run_b"


def test_successor_still_running_keeps_the_failure_visible(rig):
    rig.fail()
    rig.watch("run_b")
    rig.status("run_b", "running", "sess1")
    rig.tick()
    assert [e["kind"] for e in rig.attention()] == ["run_failed"]
    assert rig.box.digest(consumer="c")["failed_runs_24h"] == 1
    assert all(not e.get("settled_reason") for e in rig.events())


def test_failed_successor_leaves_only_the_newest_open(rig):
    rig.fail("run_a")
    rig.fail("run_b")                                            # same session, also fails
    kinds = {e["run_id"]: e for e in rig.events() if e["kind"] == "run_failed"}
    assert kinds["run_a"]["settled_reason"] == "superseded_by_failure"
    assert kinds["run_a"]["superseded_by"] == "run_b"
    assert not kinds["run_b"].get("settled_reason")               # the newest failure is the one that counts
    assert [e["run_id"] for e in rig.attention()] == ["run_b"]
    assert rig.box.digest(consumer="c")["failed_runs_24h"] == 1


def test_cancelled_successor_does_not_settle(rig):
    rig.fail()
    rig.watch("run_b")
    rig.status("run_b", "cancelled", "sess1")
    rig.tick()
    assert [e["kind"] for e in rig.attention()] == ["run_failed"]     # nothing was achieved


def test_ack_takes_a_failure_out_of_that_consumers_attention_only(rig):
    rig.fail()
    ev = [e for e in rig.events() if e["kind"] == "run_failed"][0]
    assert rig.box.digest(consumer="c")["failed_runs_24h"] == 1
    rig.box.ack([ev["id"]], consumer="c")
    assert rig.box.digest(consumer="c")["failed_runs_24h"] == 0
    assert rig.attention("c") == []
    assert rig.box.digest(consumer="other")["failed_runs_24h"] == 1   # another consumer keeps its own view
    assert [e["run_id"] for e in rig.attention("other")] == ["run_a"]


def test_interrupted_with_output_is_its_own_class(rig):
    rig.watch("run_i")
    rig.status("run_i", "running", "sess1")
    rig.tick()
    rig.status("run_i", "interrupted", "sess1", output="the partial answer",
               error="Gateway shutdown interrupted the run.")
    assert rig.tick() == 1
    ev = [e for e in rig.events() if e["run_id"] == "run_i"][0]
    assert ev["kind"] == "run_interrupted" and ev["severity"] == "warning"
    assert "characters of output" in ev["summary"] and "the partial answer" not in json.dumps(ev)
    assert rig.attention() == [] and rig.box.digest(consumer="c")["failed_runs_24h"] == 0


def test_interrupted_without_output_stays_a_failure(rig):
    rig.watch("run_i")
    rig.status("run_i", "running", "sess1")
    rig.tick()
    rig.status("run_i", "interrupted", "sess1", error="Gateway shutdown interrupted the run.")
    rig.tick()
    ev = [e for e in rig.events() if e["run_id"] == "run_i"][0]
    assert ev["kind"] == "run_failed" and "before it produced any output" in ev["summary"]
    assert [e["kind"] for e in rig.attention()] == ["run_failed"]


def test_backfill_settles_old_failures_once(home):
    """Idempotent, and it uses the same rule plus the documented operator rows."""
    settings = load_settings({"HERMES_HOME": str(home)})
    box = get_inbox(settings)
    with box._db() as db:
        # a session where the follow-up run failed (older failure is superseded by the newer one)
        for run, sid, ts in (("run_old", "sess_x", "2026-11-11T10:00:00Z"),):
            db.execute("INSERT INTO events(ts,source,kind,summary,severity,origin,run_id,session_id,profile,"
                       "requester,dedupe_key) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       (ts, "hermes", "run_failed", "Run failed.", "error", "watcher", run, sid, "default",
                        "k", f"{run}:run_failed"))
        db.execute("INSERT INTO events(ts,source,kind,summary,severity,origin,run_id,session_id,profile,"
                   "requester,dedupe_key) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                   ("2026-11-11T10:10:00Z", "hermes", "run_failed", "Run failed.", "error", "watcher",
                    "run_newer", "sess_x", "default", "k", "run_newer:run_failed"))
        # the documented operator row
        db.execute("INSERT INTO events(ts,source,kind,summary,severity,origin,run_id,session_id,profile,"
                   "requester,dedupe_key) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                   ("2026-11-11T16:41:25Z", "hermes", "run_failed", "Run failed.", "error", "watcher",
                    "run_5506c3849ae349719adcde38731b8883", "sess_y", "default", "k", "legacy:run_failed"))
        db.execute("DELETE FROM approval_decisions WHERE actor='hermcp:backfill-failed-runs'")
        Inbox._backfill_failed_runs(db)
        Inbox._backfill_failed_runs(db)                            # second run: nothing left to do
        rows = {r["run_id"]: dict(r) for r in db.execute("SELECT * FROM events")}
    assert rows["run_old"]["settled_reason"] == "superseded_by_failure"
    assert rows["run_old"]["superseded_by"] == "run_newer"
    assert not rows["run_newer"]["settled_reason"]
    assert rows["run_5506c3849ae349719adcde38731b8883"]["settled_reason"].startswith("operator_backfill_20261111")
