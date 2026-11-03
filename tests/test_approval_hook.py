"""Approval hooks -> inbox: visibility events only, never an answer path."""
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
    yield
    hook._seen.clear()


def store(home):
    return get_inbox(load_settings({"HERMES_HOME": str(home)}))


def events(home, **kw):
    return store(home).poll(**kw)["events"]


def test_pre_records_a_pending_event(home):
    hook.handle_pre_approval_request(**PRE)
    rows = events(home)
    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == "approval_pending" and row["origin"] == "hook"
    assert row["severity"] == "warning" and row["source"] == "hermes"
    assert row["session_key"] == "cli:abc123"
    assert row["action"] == "git push --force origin main"
    assert row["rationale"] == "Force-push to main"
    assert "approvals_pending" in row["summary"] and "telegram" in row["summary"]
    assert row["untrusted_fields"] == ["summary", "action", "rationale"]
    assert "request_id" not in row          # the answer must come from approvals_pending, never from an event


def test_same_prompt_is_recorded_once(home):
    hook.handle_pre_approval_request(**PRE)
    hook.handle_pre_approval_request(**PRE)          # a re-fired prompt is not a new event
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


def test_post_records_the_resolution_and_drops_the_pending_state(home):
    hook.handle_post_approval_response(**PRE, choice="once")   # never announced -> stay quiet
    assert events(home) == []
    hook.handle_pre_approval_request(**PRE)
    hook.handle_post_approval_response(**PRE, choice="once")
    rows = events(home)
    assert [r["kind"] for r in rows] == ["approval_pending", "approval_resolved"]
    assert "once" in rows[1]["summary"] and rows[1]["severity"] == "info"
    hook.handle_post_approval_response(**PRE, choice="once")   # already settled -> no second event
    assert len(events(home)) == 2
    assert hook._seen == {}


def test_command_text_is_redacted_and_single_lined(home):
    hook.handle_pre_approval_request(**{**PRE, "command": f"curl -H 'Authorization: Bearer {SECRET}'\nrm -rf x"})
    row = events(home)[0]
    assert SECRET not in (row["action"] or "") and "\ufffd" not in row["action"]
    assert "\n" not in row["action"] and "\x00" not in row["action"]


def test_other_profiles_cannot_see_the_event(home, monkeypatch):
    monkeypatch.setattr(hook, "_profile", lambda: "default")
    hook.handle_pre_approval_request(**PRE)
    assert len(store(home).poll(profiles=["default"])["events"]) == 1
    assert store(home).poll(profiles=["researcher"])["events"] == []
    assert len(store(home).poll()["events"]) == 1          # unrestricted keys still see it


def test_unresolvable_profile_is_fail_closed(home, monkeypatch):
    """No resolvable profile -> profile stays NULL -> a profile-restricted key never sees the event."""
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
