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
