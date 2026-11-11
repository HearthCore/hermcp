"""Parity: the Ledger page and the MCP tools must answer from ONE classification.

The bug these tests lock down: the page asked `inbox_list kind=run_failed` without the attention
predicate and rendered failures a later run had already settled, while `hermes_status.inbox`
counted them correctly. Two readers, two answers.

Now both go through `Inbox.attention_view` (buckets + counts) and `adapters/attention.py`
(the classification). These tests exercise the real store, the real tools and the real page
builder against one fixture database, and assert the numbers agree.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from hermcp import ledger_page as LB
from hermcp.adapters import attention as AT
from hermcp.adapters.inbox_store import Inbox
from hermcp.registry import Principal
from hermcp.scopes import Scopes
from hermcp.settings import load_settings


def iso(hours_ago: float) -> str:
    return datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() - hours_ago * 3600,
                                  timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture()
def box(tmp_path):
    return Inbox(tmp_path / "inbox.db")


def _event(box, *, kind, run_id, session_id=None, profile="default", hours_ago=1.0, summary="x"):
    """Insert a watcher event the way the run watcher does (no HTTP, no token)."""
    with box._db() as db:
        cur = db.execute(
            "INSERT INTO events(ts,source,kind,summary,ref,severity,pushed_by,run_id,session_id,profile,origin,"
            "dedupe_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (iso(hours_ago), "hermes", kind, summary, run_id, "error", "hermcp:watcher", run_id,
             session_id or run_id, profile, "watcher", f"t:{kind}:{run_id}"))
        return cur.lastrowid


def _settle(box, run_id, reason="superseded_by_completed", superseded_by="run_after"):
    with box._db() as db:
        db.execute("UPDATE events SET settled_reason=?, settled_at=?, superseded_by=? WHERE run_id=?",
                   (reason, iso(0.5), superseded_by, run_id))


def _approval(box, key, state, *, profile="default", hours_ago=1.0):
    with box._db() as db:
        db.execute(
            "INSERT INTO events(ts,source,kind,summary,ref,severity,pushed_by,profile,origin,dedupe_key,"
            "approval_key,approval_state,approval_decision,approval_surface,approval_closed_at,run_id) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (iso(hours_ago), "hermes", "approval", "needs approval", None, "info", "hermcp:hook", profile,
             "hook", f"a:{key}", key, state,
             "once" if state != "waiting" else None,
             "telegram" if state != "waiting" else None,
             iso(hours_ago) if state != "waiting" else None,
             f"run_{key}"))


# ---------------------------------------------------------------------------
# the classification itself (pure)
# ---------------------------------------------------------------------------

def test_classify_puts_every_row_in_exactly_one_bucket():
    assert AT.classify({"kind": "run_failed"}) == AT.ATTENTION
    assert AT.classify({"kind": "run_failed", "settled_reason": "superseded_by_completed"}) == AT.SETTLED
    assert AT.classify({"kind": "run_lost"}) == AT.ATTENTION
    assert AT.classify({"kind": "run_interrupted"}) == AT.INTERRUPTED
    assert AT.classify({"kind": "run_finished"}) == AT.NOISE
    assert AT.classify({"kind": "run_cancelled"}) == AT.NOISE
    assert AT.classify({"kind": "approval", "approval_key": "k", "approval_state": "waiting"}) == AT.WAITING
    assert AT.classify({"kind": "approval", "approval_key": "k", "approval_state": "approved"}) == AT.HISTORY
    assert AT.classify({"kind": "approval", "approval_key": "k", "approval_state": "expired"}) == AT.HISTORY
    assert AT.classify({"kind": "needs_approval"}) == AT.NOISE
    assert AT.classify({"kind": "deploy_finished"}) == AT.ATTENTION   # a producer's own notice stays visible
    assert AT.classify({}) == AT.ATTENTION


def test_settled_text_names_what_settled_it():
    assert "abgeschlossen" in AT.settled_text({"settled_reason": "superseded_by_completed"})
    assert "neueren Fehlschlag" in AT.settled_text({"settled_reason": "superseded_by_failure"})
    op = AT.settled_text({"settled_reason": "operator_backfill_20261111: result delivered in full"})
    assert "Operator" in op and "result delivered in full" in op
    assert AT.settled_text({}) == "erledigt"


def test_counts_come_from_the_buckets():
    buckets = {AT.WAITING: [1, 2], AT.ATTENTION: [1], AT.INTERRUPTED: [1, 2, 3], AT.SETTLED: [1]}
    assert AT.counts(buckets) == {"failed_runs_24h": 1, "approvals_waiting": 2, "interrupted_24h": 3,
                                  "settled_24h": 1, "attention_total": 3}


# ---------------------------------------------------------------------------
# the store: one query, both readers
# ---------------------------------------------------------------------------

def test_attention_view_separates_settled_from_open(box):
    _event(box, kind="run_failed", run_id="run_open", hours_ago=2)
    _event(box, kind="run_failed", run_id="run_done", hours_ago=3)
    _settle(box, "run_done")
    _event(box, kind="run_interrupted", run_id="run_int", hours_ago=4)
    _approval(box, "ap-wait", "waiting")
    _approval(box, "ap-done", "approved")

    view = box.attention_view(consumer="k1")
    assert [r["run_id"] for r in view["attention"]] == ["run_open"]
    assert [r["run_id"] for r in view["settled"]] == ["run_done"]
    assert [r["run_id"] for r in view["interrupted"]] == ["run_int"]
    assert [a["approval_key"] for a in view["waiting"]] == ["ap-wait"]
    assert [a["approval_key"] for a in view["history"]] == ["ap-done"]
    assert view["history"][0]["protocol"] == []
    assert view["counts"]["failed_runs_24h"] == 1
    assert view["counts"]["approvals_waiting"] == 1


def test_digest_and_attention_view_agree(box):
    """The parity that was broken: hermes_status.inbox vs. the page."""
    _event(box, kind="run_failed", run_id="run_a", hours_ago=1)
    _event(box, kind="run_failed", run_id="run_b", hours_ago=2)
    _settle(box, "run_b")
    _event(box, kind="run_lost", run_id="run_c", hours_ago=3)
    _approval(box, "ap-1", "waiting")
    _approval(box, "ap-2", "waiting")
    _approval(box, "ap-3", "denied")

    for consumer in ("k1", "other"):
        dg = box.digest(consumer=consumer)
        view = box.attention_view(consumer=consumer)
        assert dg["failed_runs_24h"] == view["counts"]["failed_runs_24h"] == 2
        assert dg["approvals_waiting"] == view["counts"]["approvals_waiting"] == 2


def test_a_settled_failure_leaves_the_digest_count(box):
    _event(box, kind="run_failed", run_id="run_a", hours_ago=1)
    assert box.digest(consumer="k1")["failed_runs_24h"] == 1
    _settle(box, "run_a")
    assert box.digest(consumer="k1")["failed_runs_24h"] == 0
    view = box.attention_view(consumer="k1")
    assert view["attention"] == [] and len(view["settled"]) == 1


def test_an_acknowledged_failure_leaves_attention_but_stays_in_history(box):
    eid = _event(box, kind="run_failed", run_id="run_a", hours_ago=1)
    box.ack([eid], consumer="k1")
    view = box.attention_view(consumer="k1")
    assert view["attention"] == []
    assert box.digest(consumer="k1")["failed_runs_24h"] == 0
    # another consumer still sees it: read state is per consumer
    assert len(box.attention_view(consumer="k2")["attention"]) == 1


def test_attention_view_respects_the_profile_scope(box):
    _event(box, kind="run_failed", run_id="run_d", profile="default", hours_ago=1)
    _event(box, kind="run_failed", run_id="run_r", profile="researcher", hours_ago=1)
    view = box.attention_view(profiles=["researcher"], consumer="k1")
    assert [r["run_id"] for r in view["attention"]] == ["run_r"]


def test_attention_view_is_read_only(box):
    _event(box, kind="run_failed", run_id="run_a", hours_ago=1)
    before = box.attention_view(consumer="k1")
    after = box.attention_view(consumer="k1")
    assert before["counts"] == after["counts"]
    with box._db() as db:
        assert db.execute("SELECT COUNT(*) FROM reads").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# the page builder uses the same rows
# ---------------------------------------------------------------------------

class _StubRegistry:
    """Answers `inbox_attention` from the real store — no HTTP, no MCP round trip."""

    def __init__(self, box, profiles=("default",)):
        self.box, self.profiles, self.calls = box, list(profiles), []

    def invoke(self, name, args, principal, settings, audit, via=""):
        self.calls.append(name)
        if principal.scopes.access != "read":
            return {"ok": False, "error": {"code": "forbidden", "message": "read only"}}
        if name == "profiles_list":
            return {"ok": True, "result": {"profiles": [{"name": p, "reachable": True} for p in self.profiles]}}
        if name == "inbox_attention":
            return {"ok": True, "result": self.box.attention_view(
                profiles=None if "*" in principal.scopes.profiles else list(principal.scopes.profiles),
                consumer=principal.id, hours=float(args.get("hours") or 24))}
        if name == "sessions_list":
            return {"ok": True, "result": {"count": 0, "sessions": []}}
        if name == "cron_runs":
            return {"ok": True, "result": {"count": 0, "runs": []}}
        return {"ok": True, "result": {"count": 0}}


def _settings():
    return load_settings({"HERMES_HOME": "/tmp/hermcp-parity-test", "HERMCP_LEDGER_AUTH": "key"})


def test_page_and_digest_report_the_same_numbers(box, monkeypatch):
    """The parity test the brief asks for: same data, same numbers, in both directions."""
    _event(box, kind="run_failed", run_id="run_open", hours_ago=1)
    _event(box, kind="run_failed", run_id="run_settled", hours_ago=2)
    _settle(box, "run_settled")
    _event(box, kind="run_interrupted", run_id="run_int", hours_ago=3)
    _approval(box, "ap-wait", "waiting")
    _approval(box, "ap-done", "approved")

    principal = Principal("k1", "parity", Scopes(access="admin", profiles=["*"]))
    payload = LB.build_overview(_StubRegistry(box), principal, _settings(), {}, "")
    att = payload["attention"]
    dg = box.digest(consumer="k1")

    assert att["digest"]["failed_runs_24h"] == dg["failed_runs_24h"] == 1
    assert att["digest"]["approvals_waiting"] == dg["approvals_waiting"] == 1
    assert len(att["failed_chat_runs"]) == dg["failed_runs_24h"]
    assert len(att["waiting"]) == dg["approvals_waiting"]
    # and the settled row is on the page, just not in attention
    assert [r["run_id"] for r in att["settled"]] == ["run_settled"]
    assert att["settled_count"] == 1
    assert [r["run_id"] for r in att["interrupted"]] == ["run_int"]
    assert [h["approval_key"] for h in att["approval_history"]] == ["ap-done"]
    # total = what needs a human, nothing else
    assert att["total"] == 2


def test_the_page_shows_a_settled_run_with_its_reason_and_link(box):
    _event(box, kind="run_failed", run_id="run_settled", hours_ago=2, summary="Run failed (max iterations)")
    _settle(box, "run_settled", reason="superseded_by_failure", superseded_by="run_newer")
    principal = Principal("k1", "p", Scopes(access="admin", profiles=["*"]))
    att = LB.build_overview(_StubRegistry(box), principal, _settings(), {}, "")["attention"]
    row = att["settled"][0]
    assert row["superseded_by"] == "run_newer"
    assert "neueren Fehlschlag" in row["settled_text"]
    assert "max iterations" in row["summary"]
    assert att["failed_chat_runs"] == []


def test_the_page_never_asks_inbox_list_for_its_attention(box):
    """One read path: the page must not re-derive the buckets from a raw event list."""
    principal = Principal("k1", "p", Scopes(access="admin", profiles=["*"]))
    reg = _StubRegistry(box)
    LB.build_overview(reg, principal, _settings(), {}, "")
    assert "inbox_list" not in reg.calls
    assert "inbox_attention" in reg.calls
