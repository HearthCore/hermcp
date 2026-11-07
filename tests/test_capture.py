"""Discovery capture: records what clients send, never credentials, argument values or addresses."""
import json

from starlette.testclient import TestClient

from hermcp.app import create_app
from hermcp.capture import clean_headers, report, read_records, summarize_message
from hermcp.scopes import Scopes
from hermcp.settings import load_settings


def rpc(client, token, method, params=None, id_=1, headers=None):
    h = {"Authorization": f"Bearer {token}"}
    h.update(headers or {})
    return client.post("/mcp", json={"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}}, headers=h)


def mk(app, name, **sc):
    return app.keystore.create(name, Scopes(**sc))[1]


SECRET_ARG = "correct-horse-battery-staple-9f8e7d"


def make_app(home, **extra):
    env = {"HERMES_HOME": str(home), "HERMCP_ALLOWED_HOSTS": "testserver,127.0.0.1", **extra}
    return create_app(load_settings(env))


def test_off_by_default_writes_nothing(happ):
    c = TestClient(happ.app)
    t = mk(happ, "a", access="admin")
    rpc(c, t, "ping")
    assert not (happ.settings.data / "capture.jsonl").exists()


def test_records_headers_meta_clientinfo_but_no_secrets(home):
    app = make_app(home, HERMCP_DEBUG_CAPTURE="1")
    c = TestClient(app.app)
    t = mk(app, "voice", access="admin")
    hdr = {"X-Claude-Session": "conv-abc123", "Mcp-Session-Id": "sess-1", "X-Forwarded-For": "203.0.113.9",
           "User-Agent": "claude-test/1.0"}
    rpc(c, t, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {"roots": {}},
                              "clientInfo": {"name": "claude-ai", "version": "9"}}, headers=hdr)
    rpc(c, t, "tools/call", {"name": "audit_tail", "arguments": {"lines": 1, "key": SECRET_ARG},
                              "_meta": {"progressToken": 7, "conversation": "conv-abc123"}}, headers=hdr)
    raw = (app.settings.data / "capture.jsonl").read_text()
    assert t not in raw and SECRET_ARG not in raw and "203.0.113.9" not in raw
    recs = read_records(app.settings.data / "capture.jsonl")
    assert len(recs) == 2
    h = recs[0]["headers"]
    assert h["authorization"] == "[present]" and h["x-claude-session"] == "conv-abc123"
    assert h["x-forwarded-for"] == "[present]"
    assert recs[0]["messages"][0]["clientInfo"] == {"name": "claude-ai", "version": "9"}
    m = recs[1]["messages"][0]
    assert m["tool"] == "audit_tail" and m["argument_names"] == ["key", "lines"]
    assert m["params_meta"]["conversation"] == "conv-abc123"
    assert (app.settings.data / "capture.jsonl").stat().st_mode & 0o777 == 0o600


def test_report_groups_by_client_and_flags_session_candidates(home):
    app = make_app(home, HERMCP_DEBUG_CAPTURE="1")
    c = TestClient(app.app)
    t = mk(app, "voice", access="admin")
    rpc(c, t, "initialize", {"clientInfo": {"name": "claude-ai"}}, headers={"User-Agent": "A", "X-Conv": "c1"})
    rpc(c, t, "ping", headers={"User-Agent": "B", "Mcp-Session-Id": "s"})
    rep = report(read_records(app.settings.data / "capture.jsonl"))
    by_ua = {x["user_agent"]: x for x in rep["clients"]}
    assert set(by_ua) == {"A", "B"}
    assert by_ua["A"]["non_standard_headers"] == {"x-conv": ["c1"]} and by_ua["A"]["mcp_session_id_seen"] is False
    assert by_ua["B"]["mcp_session_id_seen"] is True
    assert by_ua["A"]["client_info"] == {"name": "claude-ai"}


def test_capture_report_tool(home):
    app = make_app(home, HERMCP_DEBUG_CAPTURE="1")
    c = TestClient(app.app)
    t = mk(app, "adm", access="admin")
    rpc(c, t, "ping", headers={"User-Agent": "UA"})
    r = rpc(c, t, "tools/call", {"name": "hermes_call", "arguments": {"name": "capture_report", "args": {"last": 1}}})
    body = json.loads(r.json()["result"]["content"][0]["text"]) if r.json()["result"]["content"][0]["text"].startswith("{") \
        else r.json()["result"]["content"][0]["text"]
    assert "UA" in json.dumps(body) and "enabled" in json.dumps(body)
    ro = mk(app, "ro", access="read")
    r = rpc(c, ro, "tools/call", {"name": "hermes_call", "arguments": {"name": "capture_report", "args": {}}})
    assert r.json()["result"]["isError"] is True      # admin only


def test_expires_by_itself(home):
    app = make_app(home, HERMCP_DEBUG_CAPTURE="1", HERMCP_DEBUG_CAPTURE_HOURS="0")
    c = TestClient(app.app)
    rpc(c, mk(app, "a", access="admin"), "ping")
    assert not (app.settings.data / "capture.jsonl").exists()


def test_secret_looking_value_keeps_only_a_fingerprint():
    tok = "hmcp_live_" + "a1b2c3d4" * 6
    out = clean_headers({"X-Thing": tok, "Cookie": "sid=1", "X-Api-Key": "k"})
    assert out["cookie"] == "[present]" and out["x-api-key"] == "[present]"
    assert isinstance(out["x-thing"], dict) and out["x-thing"]["masked"] is True and tok not in json.dumps(out)
    assert out["x-thing"] == clean_headers({"x-thing": tok})["x-thing"]      # stable fingerprint


def test_summarize_message_drops_argument_values():
    s = summarize_message({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                           "params": {"name": "x", "arguments": {"a": SECRET_ARG}}})
    assert SECRET_ARG not in json.dumps(s) and s["argument_names"] == ["a"]
    assert summarize_message(["not", "a", "dict"]) == {"kind": "list"}


def test_bad_capture_never_breaks_requests(home, monkeypatch):
    app = make_app(home, HERMCP_DEBUG_CAPTURE="1")
    monkeypatch.setattr("hermcp.capture.summarize_message", lambda m: 1 / 0)
    c = TestClient(app.app)
    assert rpc(c, mk(app, "a", access="admin"), "ping").status_code == 200
