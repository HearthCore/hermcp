"""Runs and run events carry the plain-language identity of the session they were started under."""
from hermcp.adapters.inbox_store import get_inbox, session_brief
from hermcp.registry import Principal
from hermcp.scopes import Scopes
from hermcp.ledger_page import failed_run_view, session_view


def _call(happ, tool, args=None, key="k1", access="read"):
    p = Principal(key, key, Scopes(access=access))
    return happ.registry.invoke(tool, args or {}, p, happ.settings, None)


def _session(box, key, handle, **kw):
    box.session_register(key_id=key, key_name=key, client=kw.get("client", "Cowork"), kind=kw.get("kind", "code"),
                         purpose=kw.get("purpose", "Ledger bauen"), role=None, native_id=None,
                         handle_factory=lambda: handle)
    return handle


def test_brief_is_one_readable_line():
    b = session_brief({"handle": "cw-1", "client": "Cowork", "kind": "code", "purpose": "Ledger bauen"})
    assert b["label"] == "Cowork · code · Ledger bauen" and b["client_session"] == "cw-1"
    assert session_brief({"handle": "x", "client": "ClaudeAI", "kind": None, "purpose": None})["label"] == "ClaudeAI"


def test_runs_active_names_client_and_purpose(happ):
    box = get_inbox(happ.settings)
    h = _session(box, "k1", "cw-1")
    box.add_watch(run_id="run_a", profile="default", session_id=None, key_id="k1", key_name="k1",
                  client="Cowork", client_session=h)
    box.add_watch(run_id="run_b", profile="default", session_id=None, key_id="k1", key_name="k1")
    runs = {r["run_id"]: r for r in _call(happ, "runs_active")["result"]["runs"]}
    assert runs["run_a"]["session"]["label"] == "Cowork · code · Ledger bauen"
    assert "session" not in runs["run_b"]            # started without a session: nothing invented


def test_events_of_foreign_sessions_are_not_resolved_for_other_keys(happ):
    box = get_inbox(happ.settings)
    h = _session(box, "k1", "cw-1")
    box.add_watch(run_id="run_a", profile="default", session_id=None, key_id="k1", key_name="k1",
                  client="Cowork", client_session=h)
    assert box.run_sessions(["run_a"], key_id="k1")["run_a"]["client"] == "Cowork"
    assert box.run_sessions(["run_a"], key_id="k2") == {}
    assert box.run_sessions(["run_a"])["run_a"]["label"].startswith("Cowork")     # admin view: all keys
    assert box.run_sessions([]) == {}


def test_failed_run_row_on_the_ledger_page_shows_the_session():
    ev = {"kind": "run_failed", "profile": "default", "ts": "2026-11-10T08:00:00Z", "run_id": "run_a",
          "summary": "boom", "session": {"label": "Cowork · code · Ledger bauen"}}
    assert failed_run_view(ev, {})["session"] == "Cowork · code · Ledger bauen"
    assert failed_run_view({**ev, "session": None}, {})["session"] is None
    assert session_view("not a dict") is None
