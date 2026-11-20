"""adapters.approval_ledger: the bookkeeping behind an approval answer.

Both answer paths (`approvals_answer`, `chat_run_approval`) go through here, and the module's contract is
that bookkeeping never breaks an answer that already went through. The tests pin both halves: the outcome
that lands in the decision protocol, and the swallowing when the inbox is unavailable.
"""
import pytest

from hermcp.adapters import approval_ledger
from hermcp.adapters.inbox_store import get_inbox
from hermcp.registry import Principal
from hermcp.scopes import Scopes
from hermcp.settings import load_settings


class Ctx:
    """The two attributes the ledger reads off a real CallContext."""

    def __init__(self, settings, client=""):
        self.settings = settings
        self.principal = Principal("k1", "admin-key", Scopes(access="admin", approvals=True), client=client)


@pytest.fixture()
def rig(home):
    st = load_settings({"HERMES_HOME": str(home)})
    box = get_inbox(st)

    def open_row(**kw):
        args = {"approval_key": "ap-1", "summary": "run a command", "profile": "default", "origin": "hook",
                "session_key": "agent:main:s1", "run_id": "run-1", "approval_id": "req-1"}
        args.update(kw)
        return box.open_approval(**args)

    return box, st, open_row


def _outcomes(box, key="ap-1"):
    return [d["outcome"] for d in box.approval_decisions(key)]


# ---- surface_of --------------------------------------------------------------------------------
def test_surface_of_names_the_client_label_when_there_is_one(rig):
    _, st, _ = rig
    assert approval_ledger.surface_of(Ctx(st, "claude-code")) == "mcp:claude-code"


def test_surface_of_falls_back_to_plain_mcp(rig):
    _, st, _ = rig
    assert approval_ledger.surface_of(Ctx(st)) == "mcp"


# ---- settle: the answer that went through ------------------------------------------------------
def test_settle_records_the_decision_on_the_waiting_row(rig):
    box, st, open_row = rig
    open_row()
    row = approval_ledger.settle(Ctx(st, "claude-code"), session_key="agent:main:s1", approval_id="req-1",
                                 choice="approve", note="looks fine")
    assert row is not None and row["approval_state"] == "approved" and row["approval_decision"] == "approve"
    assert row["approval_surface"] == "mcp:claude-code"
    assert _outcomes(box) == ["applied"]
    assert box.approval_decisions("ap-1")[0]["actor"] == "admin-key"


def test_settle_finds_the_row_by_run_id(rig):
    box, st, open_row = rig
    open_row()
    row = approval_ledger.settle(Ctx(st), run_id="run-1", approval_id="req-1", choice="deny")
    assert row["approval_state"] == "denied" and _outcomes(box) == ["applied"]


def test_settle_on_an_already_settled_row_is_logged_as_too_late(rig):
    box, st, open_row = rig
    open_row()
    assert approval_ledger.settle(Ctx(st), session_key="agent:main:s1", approval_id="req-1", choice="approve")
    assert approval_ledger.settle(Ctx(st), session_key="agent:main:s1", approval_id="req-1", choice="deny") is None
    assert _outcomes(box) == ["applied", "too_late_already_decided"]
    assert box.get_approval("ap-1")["approval_state"] == "approved"      # first answer wins


def test_settle_without_a_row_returns_none_and_records_nothing(rig):
    box, st, _ = rig
    assert approval_ledger.settle(Ctx(st), session_key="agent:main:nope", approval_id="req-9", choice="approve") is None
    assert box.approval_decisions("ap-1") == []


def test_settle_never_raises_when_the_inbox_is_unavailable(rig, monkeypatch):
    _, st, _ = rig
    monkeypatch.setattr(approval_ledger, "get_inbox", lambda s: (_ for _ in ()).throw(RuntimeError("db gone")))
    assert approval_ledger.settle(Ctx(st), session_key="agent:main:s1", approval_id="req-1", choice="approve") is None


# ---- note_attempt: the answer that did not go through ------------------------------------------
def test_note_attempt_records_a_refusal_and_leaves_the_row_waiting(rig):
    box, st, open_row = rig
    open_row()
    approval_ledger.note_attempt(Ctx(st), session_key="agent:main:s1", approval_id="req-1", outcome="refused",
                                 choice="always", note="needs the approvals_always scope")
    assert _outcomes(box) == ["refused"]
    assert box.get_approval("ap-1")["approval_state"] == "waiting"


def test_note_attempt_on_a_settled_row_is_rewritten_to_too_late(rig):
    box, st, open_row = rig
    open_row()
    approval_ledger.settle(Ctx(st), session_key="agent:main:s1", approval_id="req-1", choice="approve")
    approval_ledger.note_attempt(Ctx(st), session_key="agent:main:s1", approval_id="req-1", outcome="error")
    assert _outcomes(box) == ["applied", "too_late_already_decided"]


def test_note_attempt_keeps_an_explicit_too_late_outcome(rig):
    box, st, open_row = rig
    open_row()
    approval_ledger.settle(Ctx(st), session_key="agent:main:s1", approval_id="req-1", choice="approve")
    approval_ledger.note_attempt(Ctx(st), session_key="agent:main:s1", approval_id="req-1",
                                 outcome="too_late_already_decided")
    assert _outcomes(box) == ["applied", "too_late_already_decided"]


def test_note_attempt_without_a_row_does_nothing(rig):
    box, st, _ = rig
    approval_ledger.note_attempt(Ctx(st), session_key="agent:main:nope", approval_id="req-9", outcome="error")
    assert box.approval_decisions("ap-1") == []


def test_note_attempt_never_raises_when_the_inbox_is_unavailable(rig, monkeypatch):
    _, st, _ = rig
    monkeypatch.setattr(approval_ledger, "get_inbox", lambda s: (_ for _ in ()).throw(RuntimeError("db gone")))
    approval_ledger.note_attempt(Ctx(st), session_key="agent:main:s1", approval_id="req-1", outcome="error")
