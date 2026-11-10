"""clients_overview: which client serves which session, grouped, with open runs. Read-only view over the cards."""
import dataclasses
import time

from hermcp import ledger_page as LB
from hermcp.adapters import clients_view
from hermcp.adapters.inbox_store import get_inbox
from hermcp.registry import Principal
from hermcp.scopes import Scopes


class _Offline:
    """Every Hermes read fails: the page still assembles, which is all these tests need (they look at `clients`)."""
    def invoke(self, tool, args, principal, settings, ctx):
        return {"ok": False, "error": {"message": "offline"}}


CONFIG: dict = {}


def _card(box, key, handle, client, kind="code", purpose="p"):
    box.session_register(key_id=key, key_name=key, client=client, kind=kind, purpose=purpose, role=None,
                         native_id=None, handle_factory=lambda: handle)


def _seed(happ):
    box = get_inbox(happ.settings)
    _card(box, "k1", "cw-1", "Cowork", purpose="hermcp Ledger")
    _card(box, "k1", "cw-2", "Cowork", purpose="alte Session")
    _card(box, "k1", "ai-1", "ClaudeAI", kind="voice", purpose="Voice")
    _card(box, "k2", "other-1", "Codex")
    with box._db() as db:
        db.execute("UPDATE sessions SET last_seen=? WHERE handle='cw-2'", (time.time() - 3 * 3600,))
    box.add_watch(run_id="run_a", profile="developer", session_id=None, key_id="k1", key_name="k1",
                  client="Cowork", client_session="cw-1")
    box.add_watch(run_id="run_free", profile="default", session_id=None, key_id="k1", key_name="k1")
    return box


def _call(happ, args=None, key="k1", access="read", **scope):
    p = Principal(key, key, Scopes(access=access, **scope))
    return happ.registry.invoke("clients_overview", args or {}, p, happ.settings, None)


def test_groups_sessions_by_client_with_state_and_runs(happ):
    _seed(happ)
    res = _call(happ)["result"]
    by = {g["client"]: g for g in res["clients"]}
    assert set(by) == {"Cowork", "ClaudeAI"}                  # the other key's Codex is not listed
    cw = {s["client_session"]: s for s in by["Cowork"]["sessions"]}
    assert cw["cw-1"]["state"] == "active" and cw["cw-1"]["open_runs"][0]["run_id"] == "run_a"
    assert cw["cw-2"]["state"] == "idle" and cw["cw-2"]["open_runs"] == []
    assert by["Cowork"]["open_runs"] == 1 and by["Cowork"]["active_sessions"] == 1
    assert res["unclaimed_count"] == 1 and res["unclaimed_runs"][0]["run_id"] == "run_free"
    assert res["clients"][0]["client"] == "Cowork"            # most active client first
    assert "Cowork: 2 sessions (1 active), 1 open run" in res["summary"]


def test_scope_all_is_admin_only(happ):
    _seed(happ)
    denied = _call(happ, {"scope": "all"})
    assert not denied["ok"] and denied["error"]["code"] == "forbidden"
    res = _call(happ, {"scope": "all"}, access="admin")["result"]
    assert {g["client"] for g in res["clients"]} == {"Cowork", "ClaudeAI", "Codex"}


def test_profile_restricted_key_sees_only_runs_of_its_profiles(happ):
    _seed(happ)
    res = _call(happ, profiles=["default"])["result"]
    assert all(not s["open_runs"] for g in res["clients"] for s in g["sessions"])      # run_a is on developer
    assert res["unclaimed_count"] == 1                                                    # run_free is on default


def test_handed_over_sessions_are_counted_not_listed(happ):
    box = _seed(happ)
    box.session_recover(old="cw-2", key_id="k1", key_name="k1", client="Cowork", kind="code", purpose="neu",
                        role=None, native_id=None, handle_factory=lambda: "cw-3")
    res = _call(happ)["result"]
    handles = [s["client_session"] for g in res["clients"] for s in g["sessions"]]
    assert "cw-2" not in handles and "cw-3" in handles and res["handed_over_hidden"] == 1


def test_empty_ledger_says_so(happ):
    res = _call(happ)["result"]
    assert res["clients"] == [] and res["summary"] == "No sessions are registered."


def test_ledger_page_lists_clients_and_validates_the_kanban_link(happ):
    _seed(happ)
    for url, expect in (("https://desktop.example.dev/#/kanban", "https://desktop.example.dev/#/kanban"),
                        ("javascript:alert(1)", None), ("", None)):
        settings = dataclasses.replace(happ.settings, ledger_kanban_url=url)
        assert LB.build_overview(_Offline(), Principal("k1", "k1", Scopes(access="read")), settings,
                                 CONFIG)["kanban_url"] == expect


def test_ledger_page_scopes_clients_to_the_caller_unless_admin_or_private_page(happ):
    _seed(happ)
    def clients(principal):
        out = LB.build_overview(_Offline(), principal, happ.settings, CONFIG)
        return {g["client"] for g in out["clients"]["clients"]}
    assert clients(Principal("k1", "k1", Scopes(access="read"))) == {"Cowork", "ClaudeAI"}
    assert clients(Principal("k1", "k1", Scopes(access="admin"))) == {"Cowork", "ClaudeAI", "Codex"}
    assert clients(Principal(LB.PAGE_PRINCIPAL_ID, "page", Scopes(access="read"))) == {"Cowork", "ClaudeAI", "Codex"}


def test_summary_is_short_and_speakable():
    g = [{"client": "Cowork", "sessions": [1, 2], "active_sessions": 1, "open_runs": 0}]
    assert clients_view.summary(g, 2) == "Cowork: 2 sessions (1 active), 0 open runs; 2 open runs started without a session."
