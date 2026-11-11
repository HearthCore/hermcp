"""Approval hooks -> inbox: ONE approval row per prompt, whose state changes. Never an answer path."""
import pytest

from hermcp import approvals_hook as hook
from hermcp.adapters.inbox_store import get_inbox, is_reserved_kind
from hermcp.settings import load_settings

SECRET = "sk-" + "A" * 40
PRE = dict(command="git push --force origin main", description="Force-push to main",
           pattern_key="git_force_push", pattern_keys=["git_force_push"], session_key="cli:abc123",
           surface="telegram", profile=None)


@pytest.fixture(autouse=True)
def clean_state():
    hook._seen.clear()
    hook._seq.clear()
    yield
    hook._seen.clear()
    hook._seq.clear()


def store(home):
    return get_inbox(load_settings({"HERMES_HOME": str(home)}))


def events(home, **kw):
    return store(home).poll(**kw)["events"]


def test_pre_opens_one_approval_row(home):
    hook.handle_pre_approval_request(**PRE)
    rows = events(home)
    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == "approval" and row["origin"] == "hook"
    assert row["approval_state"] == "waiting"
    assert row["severity"] == "warning" and row["source"] == "hermes"
    assert row["session_key"] == "cli:abc123" and row["approval_surface"] == "telegram"
    assert row["action"] == "git push --force origin main"
    assert row["rationale"] == "Force-push to main"
    assert "approvals_pending" in row["summary"] and "telegram" in row["summary"]
    assert row["untrusted_fields"] == ["summary", "action", "rationale"]
    assert "request_id" not in row          # the answer must come from approvals_pending, never from an event
    assert row.get("approval_id") is None   # the hook payload carries none; nothing is invented


def test_same_prompt_is_recorded_once(home):
    hook.handle_pre_approval_request(**PRE)
    hook.handle_pre_approval_request(**PRE)          # a re-fired prompt is not a new approval
    assert len(events(home)) == 1
    hook.handle_pre_approval_request(**{**PRE, "command": "rm -rf /tmp/x"})
    assert len(events(home)) == 2


def test_coalesced_follower_and_disabled_switch_are_quiet(home):
    hook.handle_pre_approval_request(**PRE, coalesced=True)
    assert events(home) == []
    hook.handle_pre_approval_request(**PRE)
    assert len(events(home)) == 1
    hook._seen.clear()
    from hermcp import settings as settings_mod
    real = settings_mod.load_settings
    settings_mod.load_settings = lambda env=None: real({"HERMES_HOME": str(home), "HERMCP_INBOX_APPROVALS": "0"})
    try:
        hook.handle_pre_approval_request(**{**PRE, "command": "ls"})
    finally:
        settings_mod.load_settings = real
    assert len(events(home)) == 1                  # unchanged: the switch disables new events


def test_post_settles_the_same_row_instead_of_adding_one(home):
    hook.handle_post_approval_response(**PRE, choice="once")   # never announced -> stay quiet
    assert events(home) == []
    hook.handle_pre_approval_request(**PRE)
    hook.handle_post_approval_response(**PRE, choice="once")
    rows = events(home)
    assert [r["kind"] for r in rows] == ["approval"]          # ONE row, not a pending/resolved pair
    row = rows[0]
    assert row["approval_state"] == "approved" and row["approval_decision"] == "once"
    assert row["approval_surface"] == "telegram" and row["approval_closed_at"]
    assert "granted" in row["summary"].lower() and row["severity"] == "info"
    hook.handle_post_approval_response(**PRE, choice="once")   # already settled -> no second row
    assert len(events(home)) == 1
    assert hook._seen == {}


def test_deny_and_timeout_map_to_distinct_states(home):
    hook.handle_pre_approval_request(**PRE)
    hook.handle_post_approval_response(**PRE, choice="smart_deny")
    assert store(home).open_approvals() == []
    row = events(home)[0]
    assert row["approval_state"] == "denied" and "denied" in row["summary"].lower()

    other = {**PRE, "command": "rm -rf /var/lib/x"}
    hook.handle_pre_approval_request(**other)
    hook.handle_post_approval_response(**other, choice="timeout")
    latest = store(home).list(limit=1)["events"][0]
    assert latest["approval_state"] == "expired"


def test_two_identical_prompts_get_two_rows(home):
    hook.handle_pre_approval_request(**PRE)
    hook.handle_post_approval_response(**PRE, choice="once")
    hook.handle_pre_approval_request(**PRE)          # the very same prompt again, later
    rows = events(home)
    assert len(rows) == 2
    assert sorted(r["approval_state"] for r in rows) == ["approved", "waiting"]
    assert rows[0]["approval_key"] != rows[1]["approval_key"]


def test_both_raw_signals_are_kept_in_the_decision_protocol(home):
    hook.handle_pre_approval_request(**PRE)
    key = events(home)[0]["approval_key"]
    hook.handle_post_approval_response(**PRE, choice="session")
    protocol = store(home).approval_decisions(key)
    assert [p["outcome"] for p in protocol] == ["applied"]     # the post signal settled it
    assert protocol[0]["decision"] == "session" and protocol[0]["surface"] == "telegram"


def test_user_and_chat_are_stored_only_when_hermes_supplies_them(home):
    hook.handle_pre_approval_request(**PRE)
    hook.handle_post_approval_response(**{**PRE, "user_id": 4711, "chat_id": -100123}, choice="once")
    row = events(home)[0]
    assert row["approval_user_id"] == "4711" and row["approval_chat"] == "-100123"

    other = {**PRE, "command": "ls -la"}
    hook.handle_pre_approval_request(**other)
    hook.handle_post_approval_response(**other, choice="once")
    latest = store(home).list(limit=1)["events"][0]
    assert "approval_user_id" not in latest and "approval_chat" not in latest    # nothing invented


def test_request_id_is_stored_when_the_payload_carries_one(home):
    hook.handle_pre_approval_request(**PRE, request_id="abc123")
    assert events(home)[0]["approval_id"] == "abc123"
    # ... and stays absent when it does not (the gateway-wait payload never has one)
    hook.handle_pre_approval_request(**{**PRE, "command": "df -h"})
    assert store(home).list(limit=1)["events"][0].get("approval_id") is None


def test_command_text_is_redacted_and_single_lined(home):
    hook.handle_pre_approval_request(**{**PRE, "command": f"curl -H 'x: {SECRET}'\nrm -rf x"})
    row = events(home)[0]
    assert SECRET not in (row["action"] or "") and "\ufffd" not in row["action"]
    assert "\n" not in row["action"] and "\x00" not in row["action"]


def test_other_profiles_cannot_see_the_approval(home, monkeypatch):
    monkeypatch.setattr(hook, "_profile", lambda: "default")
    hook.handle_pre_approval_request(**PRE)
    assert len(store(home).poll(profiles=["default"])["events"]) == 1
    assert store(home).poll(profiles=["researcher"])["events"] == []
    assert len(store(home).poll()["events"]) == 1          # unrestricted keys still see it


def test_unresolvable_profile_is_fail_closed(home, monkeypatch):
    """No resolvable profile -> profile stays NULL -> a profile-restricted key never sees the row."""
    import hermes_constants
    monkeypatch.setattr(hermes_constants, "profile_name_for_home", lambda path: None)
    monkeypatch.delenv("HERMES_PROFILE", raising=False)
    assert hook._profile() is None
    hook.handle_pre_approval_request(**PRE)
    rows = store(home).poll()["events"]
    assert rows and rows[0]["profile"] is None                   # unattributed, not guessed
    assert store(home).poll(profiles=["default"])["events"] == []
    assert store(home).poll(profiles=["researcher"])["events"] == []


def test_profile_env_is_the_fallback(monkeypatch):
    """A process whose home carries no profile id still reports one when Hermes pins HERMES_PROFILE."""
    import hermes_constants
    monkeypatch.setattr(hermes_constants, "profile_name_for_home", lambda path: None)
    monkeypatch.setenv("HERMES_PROFILE", "media-agent")
    assert hook._profile() == "media-agent"


def test_hook_kinds_are_reserved_and_failures_stay_silent(home, monkeypatch):
    assert is_reserved_kind("approval_pending") and is_reserved_kind("approval_resolved")
    assert is_reserved_kind("approval") and is_reserved_kind("needs_approval")
    monkeypatch.setattr(hook, "_profile", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    hook.handle_pre_approval_request(**PRE)        # must not raise into the prompt
    monkeypatch.undo()
    from hermcp import adapters
    monkeypatch.setattr(adapters.inbox_store, "get_inbox", lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))
    hook.handle_pre_approval_request(**{**PRE, "command": "ls"})
    hook.handle_post_approval_response(**{**PRE, "command": "ls"}, choice="deny")


def test_register_hooks_attaches_both_observers():
    class Ctx:
        def __init__(self):
            self.hooks = {}

        def register_hook(self, name, fn):
            self.hooks[name] = fn

    ctx = Ctx()
    assert hook.register_hooks(ctx) is True
    assert set(ctx.hooks) == {"pre_approval_request", "post_approval_response"}
    for fn in ctx.hooks.values():
        assert fn(command="x") is None             # observers: always return None, never a decision
