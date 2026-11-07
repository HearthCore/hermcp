"""client (header-derived label) and client_session (client-chosen handle): attribution only."""
import json

from starlette.testclient import TestClient

from hermcp.adapters.inbox_store import get_inbox
from hermcp.client_id import clean_handle, derive_client
from hermcp.scopes import Scopes


def rpc(c, token, method, params=None, headers=None, id_=1):
    h = {"Authorization": f"Bearer {token}", **(headers or {})}
    return c.post("/mcp", json={"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}}, headers=h)


def test_derive_client_prefers_anthropic_header_then_user_agent():
    assert derive_client({"X-Anthropic-Client": "ClaudeCode", "User-Agent": "Claude-User"}) == "ClaudeCode"
    assert derive_client({"user-agent": "Claude-User/1.0 (x)"}) == "Claude-User"
    assert derive_client({}) == "unknown"
    # sanitized and capped: no control characters, spaces or markup survive
    assert derive_client({"x-anthropic-client": "<b>evil\n client</b>" + "x" * 80}) == "bevilclientbx" + "x" * 27


def test_clean_handle_accepts_only_the_documented_shape():
    assert clean_handle("sess-01:abc_2.x") == "sess-01:abc_2.x"
    for bad in (None, 5, "", "a b", "x" * 81, "../etc", "a;b"):
        assert clean_handle(bad) is None


def test_audit_records_client_and_client_session(happ):
    c = TestClient(happ.app)
    tok = happ.keystore.create("a", Scopes(access="admin"))[1]
    rpc(c, tok, "tools/call", {"name": "runs_active", "arguments": {}},
        headers={"x-anthropic-client": "Cowork"})
    recs = [json.loads(ln) for ln in happ.audit.path.read_text().splitlines()]
    mine = [r for r in recs if r["tool"] == "runs_active"]
    assert mine and mine[-1]["client"] == "Cowork"
    assert "client_session" not in mine[-1]          # none given: field absent, old log shape unchanged


def test_watch_keeps_client_and_client_session_and_runs_active_shows_them(happ):
    inbox = get_inbox(happ.settings)
    inbox.add_watch(run_id="run_x1", profile="default", session_id=None, key_id="k1", key_name="n1",
                    client="ClaudeAI", client_session="voice-7")
    inbox.add_watch(run_id="run_x2", profile="default", session_id=None, key_id="k1", key_name="n1")
    rows = {r["run_id"]: r for r in inbox.running(key_id="k1")}
    assert rows["run_x1"]["client"] == "ClaudeAI" and rows["run_x1"]["client_session"] == "voice-7"
    assert rows["run_x2"]["client"] is None and rows["run_x2"]["client_session"] is None


def test_chat_send_schema_offers_client_session(happ):
    schema = happ.registry.tools["chat_send"].input_schema()
    assert schema["properties"]["client_session"]["pattern"] == "^[A-Za-z0-9_.:-]{1,80}$"
    assert "client_session" not in schema.get("required", [])


# ---- ledger: client profiles, cards, native alias ----------------------------------------------------
def card_call(c, tok, name, headers=None, **args):
    r = rpc(c, tok, "tools/call", {"name": name, "arguments": args}, headers=headers)
    res = r.json()["result"]
    body = json.loads(res["content"][0]["text"])
    return (not res["isError"]), body.get("result", body.get("error"))


def test_handles_are_shaped_by_the_kind_of_client():
    from hermcp.client_profiles import new_handle, prefix_for
    assert prefix_for("ClaudeCode") == "cc" and prefix_for("Cowork") == "cw" and prefix_for("ClaudeAI") == "ai"
    assert prefix_for("ClaudeAI", "voice") == "vo"           # chat and voice are both ClaudeAI: kind refines
    assert prefix_for("ClaudeCode", "voice") == "cc"         # a known non-chat client keeps its own prefix
    assert prefix_for("Claude-User") == "ai"                 # User-Agent alias without the header
    assert prefix_for("SomeNewClient").startswith("som") and prefix_for("") == "x"
    assert new_handle("ClaudeCode").startswith("cc-") and len(new_handle("Cowork")) == 11


def test_card_register_update_list_and_visibility(happ):
    c = TestClient(happ.app)
    a = happ.keystore.create("a", Scopes(access="read", toolsets=["inbox"]))[1]
    b = happ.keystore.create("b", Scopes(access="read", toolsets=["inbox"]))[1]
    adm = happ.keystore.create("adm", Scopes(access="admin"))[1]
    hdr = {"x-anthropic-client": "ClaudeAI"}
    ok, r = card_call(c, a, "session_card_set", hdr, kind="voice", purpose="  live   voice\nagent ", role="orchestrator")
    assert ok and r["created"] and r["card"]["client_session"].startswith("vo-")     # a read-only key may register
    assert r["card"]["purpose"] == "live voice agent" and r["card"]["role"] == "orchestrator"
    h = r["card"]["client_session"]
    ok, r = card_call(c, a, "session_card_set", hdr, client_session=h, purpose="now: review")
    assert ok and not r["created"] and r["card"]["purpose"] == "now: review"
    card_call(c, b, "session_card_set", hdr)
    ok, mine = card_call(c, a, "session_cards_list", hdr, client_session=h)
    assert ok and mine["count"] == 1 and mine["cards"][0]["you"] is True           # own key only
    ok, e = card_call(c, a, "session_cards_list", hdr, scope="all")
    assert not ok and e["code"] == "forbidden"
    ok, allc = card_call(c, adm, "session_cards_list", hdr, scope="all")
    assert ok and allc["count"] == 2 and {x["registered_by"] for x in allc["cards"]} == {"a", "b"}
    ok, e = card_call(c, a, "session_card_set", hdr, client_session="vo-00000000")
    assert not ok and e["code"] == "unknown_client_session"


def test_native_id_makes_registration_idempotent_per_key(happ):
    c = TestClient(happ.app)
    a = happ.keystore.create("a", Scopes(access="read", toolsets=["inbox"]))[1]
    b = happ.keystore.create("b", Scopes(access="read", toolsets=["inbox"]))[1]
    hdr = {"x-anthropic-client": "ClaudeCode"}
    _, r1 = card_call(c, a, "session_card_set", hdr, native_id="abc-123", purpose="first")
    _, r2 = card_call(c, a, "session_card_set", hdr, native_id="abc-123", purpose="after resume")
    assert r1["card"]["client_session"] == r2["card"]["client_session"] and r1["created"] and not r2["created"]
    assert r2["card"]["purpose"] == "after resume" and r2["card"]["native_id"] == "abc-123"
    _, r3 = card_call(c, b, "session_card_set", hdr, native_id="abc-123")           # another key: its own card
    assert r3["created"] and r3["card"]["client_session"] != r1["card"]["client_session"]


def test_a_header_borne_session_id_becomes_the_alias_without_client_changes(happ, monkeypatch):
    from hermcp import client_profiles
    monkeypatch.setitem(client_profiles.PROFILES, "ClaudeCode",
                        {**client_profiles.PROFILES["ClaudeCode"], "native_headers": ["x-session-id"]})
    c = TestClient(happ.app)
    a = happ.keystore.create("a", Scopes(access="read", toolsets=["inbox"]))[1]
    hdr = {"x-anthropic-client": "ClaudeCode", "x-session-id": "sess-from-header"}
    _, r1 = card_call(c, a, "session_card_set", hdr)
    _, r2 = card_call(c, a, "session_card_set", hdr)
    assert r1["card"]["native_id"] == "sess-from-header" and r1["card"]["client_session"] == r2["card"]["client_session"]
    _, r3 = card_call(c, a, "session_card_set", {"x-anthropic-client": "ClaudeCode", "x-session-id": "bad value!"})
    assert r3["created"] and "native_id" not in r3["card"]                           # malformed header is ignored


def test_session_count_per_key_is_capped(happ, monkeypatch):
    from hermcp.adapters import inbox_store
    monkeypatch.setattr(inbox_store, "MAX_SESSIONS_PER_KEY", 3)
    c = TestClient(happ.app)
    a = happ.keystore.create("a", Scopes(access="read", toolsets=["inbox"]))[1]
    for _ in range(3):
        assert card_call(c, a, "session_card_set")[0]
    ok, e = card_call(c, a, "session_card_set")
    assert not ok and e["code"] == "session_limit"


def test_recover_previews_then_takes_over_with_a_new_handle(happ):
    c = TestClient(happ.app)
    a = happ.keystore.create("a", Scopes(access="read", toolsets=["inbox"]))[1]
    b = happ.keystore.create("b", Scopes(access="read", toolsets=["inbox"]))[1]
    hdr = {"x-anthropic-client": "ClaudeAI"}
    _, r0 = card_call(c, a, "session_card_set", hdr, kind="chat", purpose="hermcp planning", role="planner")
    old = r0["card"]["client_session"]
    _, pre = card_call(c, a, "session_card_set", hdr, recover_from=old)
    assert pre["recover"] == "preview" and pre["applied"] is False and pre["from"]["purpose"] == "hermcp planning"
    assert "warning" in pre                                   # just used: two threads could act as one session
    assert card_call(c, a, "session_cards_list", hdr)[1]["count"] == 1                     # preview changed nothing
    _, done = card_call(c, a, "session_card_set", hdr, recover_from=old, confirm=True, purpose="continued")
    new = done["card"]["client_session"]
    assert done["created"] and new != old and new.startswith("ai-")
    assert done["card"]["continues"] == old and done["card"]["purpose"] == "continued"
    assert done["card"]["role"] == "planner"                  # not given: inherited from the card it continues
    _, lst = card_call(c, a, "session_cards_list", hdr)
    by = {x["client_session"]: x for x in lst["cards"]}
    assert by[old]["taken_over_by"] == new
    ok, e = card_call(c, a, "session_card_set", hdr, recover_from=old, confirm=True)       # no second takeover
    assert not ok and e["code"] == "already_recovered" and e["successor"] == new
    ok, e = card_call(c, b, "session_card_set", hdr, recover_from=old, confirm=True)       # another key: not its session
    assert not ok and e["code"] == "unknown_client_session"
    ok, e = card_call(c, a, "session_card_set", hdr, recover_from=old, client_session=new)
    assert not ok and e["code"] == "invalid_arguments"


def test_recover_moves_the_native_alias_with_the_session(happ):
    c = TestClient(happ.app)
    a = happ.keystore.create("a", Scopes(access="read", toolsets=["inbox"]))[1]
    hdr = {"x-anthropic-client": "ClaudeCode"}
    _, r0 = card_call(c, a, "session_card_set", hdr, native_id="cc-native-1")
    old = r0["card"]["client_session"]
    ok, r1 = card_call(c, a, "session_card_set", hdr, recover_from=old, confirm=True, native_id="cc-native-1")
    assert ok and r1["card"]["native_id"] == "cc-native-1"
    _, again = card_call(c, a, "session_card_set", hdr, native_id="cc-native-1")           # alias now resolves to the new card
    assert again["card"]["client_session"] == r1["card"]["client_session"] and not again["created"]
