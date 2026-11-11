"""Ledger detail: the pop-up, the session view and the sub-page lists.

What these tests hold down is the contract, not the pixels: the payload shapes the
page scripts consume, server-side truncation and masking, the shared classification
for the history lists, and that none of the new endpoints widens access.
"""
from __future__ import annotations

import json
import tempfile
import time
from datetime import datetime, timezone

import pytest
from starlette.testclient import TestClient

from hermcp import ledger_detail as LD
from hermcp import ledger_page as LB
from hermcp.app import create_app
from hermcp.registry import Principal
from hermcp.scopes import Scopes
from hermcp.settings import load_settings

NOW = time.time()


def ago(hours):
    return datetime.fromtimestamp(NOW - hours * 3600, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


SECRET = "hmcp_live_9f8e7d6c5b4a3210deadbeef"
XSS = "<script>alert('xss')</script>"


SESSIONS = {
    "default": [
        {"id": "local-aaaa", "title": XSS + " " + SECRET, "source": "desktop", "model": "m",
         "started_at": NOW - 600, "last_active": NOW - 30, "ended_at": None,
         "message_count": 9, "tool_call_count": 4, "profile_name": "default"},
        {"id": "local-bbbb", "title": "alte Session", "source": "telegram", "model": "m2",
         "started_at": NOW - 90000, "last_active": NOW - 80000, "ended_at": NOW - 80000,
         "message_count": 3, "tool_call_count": 1, "profile_name": "default"},
    ],
    "developer": [
        {"id": "dev-cccc", "title": "developer session", "source": "cli", "model": "m3",
         "started_at": NOW - 120, "last_active": NOW - 60, "ended_at": None,
         "message_count": 5, "tool_call_count": 2, "profile_name": "developer"},
    ],
}

MESSAGES = {
    "local-aaaa": [
        {"id": 1, "role": "user", "content": "Bitte Token " + SECRET + " prüfen", "timestamp": ago(1)},
        {"id": 2, "role": "assistant", "content": "Ich schaue nach.", "timestamp": ago(0.9),
         "tool_calls": [{"function": {"name": "sessions_list"}}]},
        {"id": 3, "role": "tool", "tool_name": "sessions_list", "content": "x" * 900, "timestamp": ago(0.8)},
        {"id": 4, "role": "system", "content": "error: gateway not reachable", "timestamp": ago(0.7)},
    ],
    "local-bbbb": [
        {"id": 5, "role": "user", "content": "hallo", "timestamp": ago(30)},
    ],
}


INBOX_EVENTS = [
    {"id": 10, "ts": ago(1), "source": "watcher", "kind": "run_failed", "profile": "default",
     "run_id": "run_x", "session_id": "local-aaaa", "summary": "Run failed (" + SECRET + ")"},
    {"id": 9, "ts": ago(2), "source": "watcher", "kind": "run_failed", "profile": "default",
     "run_id": "run_settled", "session_id": "local-bbbb", "summary": "alt", "settled_reason": "superseded_by_completed",
     "settled_at": ago(1)},
    {"id": 8, "ts": ago(3), "source": "watcher", "kind": "run_interrupted", "profile": "developer",
     "run_id": "run_int", "session_id": "dev-cccc", "summary": "unterbrochen"},
    {"id": 7, "ts": ago(4), "source": "hermes", "kind": "approval", "profile": "default",
     "approval_key": "ap-1", "approval_state": "waiting", "summary": "Freigabe angefragt",
     "action": "rm -rf /tmp/x",
     "rationale": "cleanup", "run_id": "run_x", "session_id": "local-aaaa"},
    {"id": 6, "ts": ago(5), "source": "hermes", "kind": "approval", "profile": "default",
     "approval_key": "ap-2", "approval_state": "approved", "summary": "Freigabe erteilt",
     "approval_decision": "once",
     "approval_surface": "telegram", "approval_closed_at": ago(4), "action": "restart unit"},
    {"id": 5, "ts": ago(6), "source": "watcher", "kind": "run_finished", "profile": "default",
     "run_id": "run_done", "session_id": "local-aaaa", "summary": "fertig"},
]

CRON_RUNS = [
    {"run_id": "cr1", "job_id": "j1", "job_name": "watchdog", "status": "completed",
     "started_at": ago(1), "duration_s": 2.0, "delivery_outcome": "delivered", "session_id": "local-aaaa"},
    {"run_id": "cr2", "job_id": "j2", "job_name": "relay", "status": "error",
     "started_at": ago(2), "duration_s": 12.5, "error": "timeout"},
]


class FakeRegistry:
    def __init__(self, fail=()):
        self.calls = []
        self.fail = set(fail)

    def invoke(self, name, args, principal, settings, audit, via=""):
        self.calls.append((name, dict(args or {}), principal))
        if name in self.fail:
            return {"ok": False, "error": {"code": "internal_error", "message": f"{name} broke"}}
        if principal.scopes.access != "read":
            return {"ok": False, "error": {"code": "forbidden", "message": "read only"}}
        profile = args.get("profile") or "default"
        if name == "profiles_list":
            return {"ok": True, "result": {"profiles": [
                {"name": "default", "reachable": True}, {"name": "developer", "reachable": True},
                {"name": "parked", "reachable": False}]}}
        if name == "status_gateway":
            return {"ok": True, "result": {
                "state": "running", "health": "ok", "pid": 4711, "active_agents": 2,
                "code_version": "0.22.7", "state_updated_at": ago(0.1),
                "platforms": {"telegram": {"state": "connected"},
                              "email": {"state": "disconnected", "needs_attention": True, "error": "boom"}},
                "stale_platforms": {}}}
        if name == "hermes_status":
            return {"ok": True, "result": {"verdict": "degraded", "profiles": {"reachable": 2, "total": 3},
                                           "_meta": {"hermes_version": "0.22.7+1.gabc"}}}
        if name == "sessions_list":
            rows = list(SESSIONS.get(profile, []))
            offset = int(args.get("offset") or 0)
            limit = int(args.get("limit") or 20)
            return {"ok": True, "result": {"count": len(rows[offset:offset + limit]),
                                           "sessions": rows[offset:offset + limit]}}
        if name == "sessions_get":
            sid = args.get("session_id")
            for rows in SESSIONS.values():
                for row in rows:
                    if row["id"] == sid:
                        return {"ok": True, "result": row}
            return {"ok": False, "error": {"code": "not_found", "message": f"no session {sid}"}}
        if name == "sessions_messages":
            sid = args.get("session_id")
            if sid not in MESSAGES:
                return {"ok": False, "error": {"code": "not_found", "message": "no session"}}
            limit = int(args.get("limit") or 40)
            return {"ok": True, "result": {"session_id": sid, "count": len(MESSAGES[sid]),
                                           "messages": MESSAGES[sid][-limit:]}}
        if name == "cron_runs":
            rows = list(CRON_RUNS) if profile == "default" else []
            if args.get("status"):
                rows = [r for r in rows if r["status"] == args["status"]]
            if args.get("job_id"):
                rows = [r for r in rows if r["job_id"] == args["job_id"]]
            return {"ok": True, "result": {"count": len(rows), "runs": rows, "truncated": False}}
        if name == "cron_list":
            return {"ok": True, "result": {"count": 1, "jobs": [
                {"id": "j1", "name": "watchdog", "schedule": "*/5 * * * *", "state": "scheduled",
                 "profile": "default", "next_run_at": ago(-1)}]}}
        if name == "approval_detail":
            key = args.get("approval_key")
            for row in INBOX_EVENTS:
                if row.get("approval_key") == key:
                    return {"ok": True, "result": {"approval": row, "protocol": [
                        {"ts": ago(4), "surface": "telegram", "outcome": "applied", "decision": "once"},
                        {"ts": ago(3), "surface": "cli", "outcome": "too_late_already_decided"}]}}
            return {"ok": False, "error": {"code": "not_found", "message": "no approval"}}
        if name == "keys_list":
            return {"ok": True, "result": {"keys": [
                {"id": "k1", "name": "ledger", "disabled": False, "last_used": ago(0.2),
                 "scopes": {"access": "read", "profiles": ["*"], "toolsets": ["sessions"]}}]}}
        return {"ok": True, "result": {"count": 0}}


def make_principal(**sc):
    sc.setdefault("access", "read")
    return Principal("k1", "ledger test", Scopes(**sc))


def make_settings(**env):
    # A per-call temp home: never a fixed path, and never the live one. `settings.data` resolves
    # inside this home (see test_settings_data_dir.py for why that matters).
    base = {"HERMES_HOME": tempfile.mkdtemp(prefix="hermcp-ledger-detail-"),
            "HERMCP_LEDGER_AUTH": "key"}
    base.update(env)
    return load_settings(base)


CONFIG = {"webapp": {"enabled": True, "public_url": "https://desktop.example.dev"}}


@pytest.fixture()
def inbox_seeded(monkeypatch, tmp_path):
    """Feed the real Inbox store with the fixture events (the page reads it read-only)."""
    from hermcp.adapters import inbox_store

    settings = load_settings({"HERMES_HOME": str(tmp_path), "HERMCP_LEDGER_AUTH": "key"})
    inbox_store._STORES.clear()          # a fresh store per test: the module caches per path
    box = inbox_store.get_inbox(settings)

    with box._db() as db:
        cols = ("id", "ts", "source", "kind", "summary", "profile", "run_id", "session_id", "severity",
                "approval_key", "approval_state", "approval_decision", "approval_surface",
                "approval_closed_at", "settled_reason", "settled_at", "action", "rationale", "origin")
        for row in INBOX_EVENTS:
            values = {c: row.get(c) for c in cols}
            values["severity"] = values["severity"] or "info"
            values["summary"] = values["summary"] or "—"
            values["origin"] = "watcher" if str(row.get("kind", "")).startswith("run_") else "hook"
            db.execute(f"INSERT OR REPLACE INTO events({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                       [values[c] for c in cols])
    # a clean store for every test: the module caches per path
    inbox_store._STORES.clear()
    return settings


# ---------------------------------------------------------------------------
# pop-up (peek)
# ---------------------------------------------------------------------------

def test_peek_session_has_facts_three_actions_and_links(inbox_seeded):
    d = LD.build_peek(FakeRegistry(), make_principal(), inbox_seeded, CONFIG, "session", "local-aaaa")
    assert d["ok"] is True and d["kind"] == "session"
    assert d["type_label"] == "SESSION"
    assert set(d["facts"]) == {"model", "profile", "surface", "last_active"}
    assert len(d["actions"]) == 3
    assert d["live"] is True
    assert d["links"]["session"] == "/ledger/session/local-aaaa"
    assert d["links"]["desktop"].startswith("https://desktop.example.dev")


def test_peek_text_is_redacted_and_truncated(inbox_seeded):
    d = LD.build_peek(FakeRegistry(), make_principal(), inbox_seeded, CONFIG, "session", "local-aaaa")
    blob = json.dumps(d)
    assert SECRET not in blob
    assert "hmcp_live" not in blob
    for action in d["actions"]:
        assert len(action["text"]) <= 161


def test_peek_never_emits_markup_and_keeps_the_title_as_plain_text(inbox_seeded):
    d = LD.build_peek(FakeRegistry(), make_principal(), inbox_seeded, CONFIG, "session", "local-aaaa")
    assert XSS in (d["title"] or "")          # the data is there …
    page = LB.page_html(15)
    assert XSS not in page                    # … but the shell interpolates no title at all


def test_peek_of_a_missing_session_is_a_readable_error(inbox_seeded):
    d = LD.build_peek(FakeRegistry(), make_principal(), inbox_seeded, CONFIG, "session", "gone-123")
    assert d["ok"] is False
    assert "sessions_get" in d["error"]


def test_peek_approval_carries_state_and_protocol(inbox_seeded):
    d = LD.build_peek(FakeRegistry(), make_principal(), inbox_seeded, CONFIG, "approval", "ap-2")
    assert d["ok"] is True
    assert d["type_label"] == "FREIGABE ENTSCHIEDEN"
    assert d["approval"]["state"] == "approved"
    assert [p["outcome"] for p in d["approval"]["protocol"]] == ["applied", "too_late_already_decided"]


def test_peek_cron_reports_the_run_and_its_error(inbox_seeded):
    d = LD.build_peek(FakeRegistry(), make_principal(), inbox_seeded, CONFIG, "cron", "cr2",
                      profile="default", job="j2")
    assert d["ok"] is True
    assert d["type_label"] == "CRON-FEHLER"
    assert d["cron"]["status"] == "error"
    assert d["cron"]["error"] == "timeout"


def test_peek_refuses_an_unknown_id_shape(inbox_seeded):
    d = LD.build_peek(FakeRegistry(), make_principal(), inbox_seeded, CONFIG, "session", "../../etc")
    assert d["ok"] is False and "ID" in d["error"]


# ---------------------------------------------------------------------------
# session full view
# ---------------------------------------------------------------------------

def test_session_view_has_history_metadata_usage_and_signals(inbox_seeded):
    d = LD.build_session(FakeRegistry(), make_principal(), inbox_seeded, CONFIG, "local-aaaa")
    assert d["ok"] is True
    assert d["session"]["id"] == "local-aaaa"
    assert d["stats"]["state"] == "aktiv"
    assert d["stats"]["runtime_s"] >= 0
    assert len(d["messages"]) == 4
    assert any(m.get("tool") == "sessions_list" for m in d["messages"])
    assert d["tool_usage"][0]["name"] == "sessions_list"
    assert d["signals"]
    assert d["links"]["session"] == "/ledger/session/local-aaaa"


def test_session_view_masks_secrets_in_message_bodies(inbox_seeded):
    d = LD.build_session(FakeRegistry(), make_principal(), inbox_seeded, CONFIG, "local-aaaa")
    assert SECRET not in json.dumps(d)
    assert "hmcp_live" not in json.dumps(d)


def test_session_view_truncates_long_tool_output(inbox_seeded):
    d = LD.build_session(FakeRegistry(), make_principal(), inbox_seeded, CONFIG, "local-aaaa")
    tool = next(m for m in d["messages"] if m["role"] == "tool")
    assert tool["truncated"] is True
    assert len(tool["text"]) <= 221


def test_session_view_of_a_missing_session_is_404_shaped(inbox_seeded):
    d = LD.build_session(FakeRegistry(), make_principal(), inbox_seeded, CONFIG, "nope-1")
    assert d["ok"] is False and d["error"]


# ---------------------------------------------------------------------------
# sub-page lists
# ---------------------------------------------------------------------------

def test_sessions_list_pages_and_filters(inbox_seeded):
    first = LD.list_sessions(FakeRegistry(), make_principal(), inbox_seeded, CONFIG,
                             {"limit": "2", "offset": "0"})
    assert first["total"] == 3 and len(first["items"]) == 2 and first["has_more"] is True
    second = LD.list_sessions(FakeRegistry(), make_principal(), inbox_seeded, CONFIG,
                              {"limit": "2", "offset": "2"})
    assert len(second["items"]) == 1 and second["has_more"] is False
    only_dev = LD.list_sessions(FakeRegistry(), make_principal(), inbox_seeded, CONFIG,
                                {"profile": "developer"})
    assert [r["session_id"] for r in only_dev["items"]] == ["dev-cccc"]
    active = LD.list_sessions(FakeRegistry(), make_principal(), inbox_seeded, CONFIG, {"status": "aktiv"})
    assert {r["session_id"] for r in active["items"]} == {"local-aaaa", "dev-cccc"}


def test_sessions_list_free_text_matches_the_title(inbox_seeded):
    d = LD.list_sessions(FakeRegistry(), make_principal(), inbox_seeded, CONFIG, {"q": "alte"})
    assert [r["session_id"] for r in d["items"]] == ["local-bbbb"]


def test_runs_list_carries_the_history_from_the_shared_classification(inbox_seeded):
    d = LD.list_runs(FakeRegistry(), make_principal(), inbox_seeded, CONFIG, {"limit": "50"})
    buckets = {r["id"]: r["bucket"] for r in d["items"]}
    assert buckets["run_x"] == "attention"
    assert buckets["run_settled"] == "settled"
    assert buckets["run_int"] == "interrupted"
    assert "run_done" not in buckets                     # bookkeeping is not history here
    settled = next(r for r in d["items"] if r["id"] == "run_settled")
    assert settled["settled_text"] and "ersetzt" in settled["settled_text"]


def test_runs_list_filters_by_bucket(inbox_seeded):
    d = LD.list_runs(FakeRegistry(), make_principal(), inbox_seeded, CONFIG, {"status": "settled"})
    assert [r["id"] for r in d["items"]] == ["run_settled"]


def test_approvals_list_keeps_the_decision_protocol(inbox_seeded):
    d = LD.list_approvals(FakeRegistry(), make_principal(), inbox_seeded, CONFIG, {"limit": "10"})
    keys = {r["id"]: r for r in d["items"]}
    assert keys["ap-1"]["state"] == "waiting"
    assert keys["ap-2"]["state"] == "approved"
    assert keys["ap-2"]["protocol"][0]["outcome"] == "applied"


def test_cron_list_filters_status_and_job(inbox_seeded):
    d = LD.list_cron(FakeRegistry(), make_principal(), inbox_seeded, CONFIG, {"status": "error"})
    assert [r["id"] for r in d["items"]] == ["cr2"]
    only_j1 = LD.list_cron(FakeRegistry(), make_principal(), inbox_seeded, CONFIG, {"job": "j1"})
    assert [r["id"] for r in only_j1["items"]] == ["cr1"]


def test_gateway_page_has_no_secret_material(inbox_seeded):
    d = LD.build_gateway(FakeRegistry(), make_principal(), inbox_seeded, CONFIG)
    assert d["gateway"]["state"] == "running"
    assert any(p["name"] == "default" for p in d["profiles"])
    blob = json.dumps(d)
    assert SECRET not in blob and "hash" not in blob and "salt" not in blob


# ---------------------------------------------------------------------------
# app links
# ---------------------------------------------------------------------------

def test_app_links_use_the_configured_templates_and_hide_when_unset():
    settings = make_settings(HERMCP_LEDGER_DESKTOP_URL="https://app.example.dev/s/{session_id}?p={profile}",
                             HERMCP_LEDGER_WEB_URL="https://web.example.dev/#/c/{session_id}")
    links = LD.app_links(settings, {"kind": "none"}, "default", "local-aaaa")
    assert links["desktop"] == "https://app.example.dev/s/local-aaaa?p=default"
    assert links["web"] == "https://web.example.dev/#/c/local-aaaa"

    plain = LD.app_links(make_settings(), {"kind": "none"}, "default", "local-aaaa")
    assert plain == {"desktop": None, "web": None}


def test_app_links_never_accept_a_non_http_template():
    settings = make_settings(HERMCP_LEDGER_DESKTOP_URL="javascript:alert('{session_id}')")
    assert LD.app_links(settings, {"kind": "none"}, "default", "local-aaaa")["desktop"] is None


def test_app_links_fall_back_to_the_existing_link_configuration():
    links = LD.app_links(make_settings(), {"kind": "desktop", "base_url": "https://desktop.example.dev"},
                         "default", "local-aaaa")
    assert links["desktop"] == "https://desktop.example.dev/?profile=default#/local-aaaa"


# ---------------------------------------------------------------------------
# routes: auth, view key, payload
# ---------------------------------------------------------------------------

def make_app(home, **env):
    env.setdefault("HERMCP_ALLOWED_HOSTS", "testserver,127.0.0.1")
    return create_app(load_settings({"HERMES_HOME": str(home), **env}))


def ip4(a, b, c, d):
    return ".".join(str(x) for x in (a, b, c, d))


def test_new_pages_and_endpoints_need_a_key_like_the_rest(home):
    app = make_app(home, HERMCP_LEDGER="1", HERMCP_LEDGER_AUTH="key")
    c = TestClient(app.app)
    token = app.keystore.create("ledger", Scopes(access="read", profiles=["*"]))[1]

    for path in ("/ledger/sessions", "/ledger/runs", "/ledger/cron", "/ledger/gateway",
                 "/ledger/session/local-aaaa"):
        assert c.get(path).status_code == 401, path
    for path in ("/ledger/api/peek?kind=session&id=x", "/ledger/api/list?kind=sessions",
                 "/ledger/api/session/x", "/ledger/api/gateway"):
        assert c.get(path).status_code == 401, path

    trade = c.get("/ledger/sessions", params={"key": token}, follow_redirects=False)
    assert trade.status_code == 303 and trade.headers["location"] == "/ledger/sessions"
    assert c.get("/ledger/sessions").status_code == 200
    page = c.get("/ledger/session/local-aaaa").text
    assert "Ledger" in page and "local-aaaa" in page


def test_assets_are_served_by_hermcp_and_only_from_the_whitelist(home):
    app = make_app(home, HERMCP_LEDGER="1", HERMCP_LEDGER_AUTH="none")
    c = TestClient(app.app, client=("127.0.0.1", 51000))
    css = c.get("/ledger/assets/app.css")
    assert css.status_code == 200 and "text/css" in css.headers["content-type"]
    assert b"--green" in css.content
    font = c.get("/ledger/assets/fonts/jetbrains-mono-latin.woff2")
    assert font.status_code == 200 and font.content[:4] == b"wOF2"
    assert c.get("/ledger/assets/fonts/../../../etc/passwd").status_code == 404
    assert c.get("/ledger/assets/fonts/nope.woff2").status_code == 404


def test_list_endpoint_rejects_an_unknown_kind(home):
    app = make_app(home, HERMCP_LEDGER="1", HERMCP_LEDGER_AUTH="none")
    c = TestClient(app.app, client=("127.0.0.1", 51000))
    r = c.get("/ledger/api/list", params={"kind": "secrets"})
    assert r.status_code == 400 and r.json()["error"] == "bad_kind"


def test_peek_follows_the_view_key(home, monkeypatch):
    app = make_app(home, HERMCP_LEDGER="1", HERMCP_LEDGER_AUTH="none")
    rec, _ = app.keystore.create("viewer", Scopes(access="read", profiles=["researcher"]))
    app.settings.ledger_view_key = rec.id
    seen = {}

    def spy(registry, principal, settings, config, kind, ident, *a, **kw):
        seen["id"], seen["profiles"] = principal.id, principal.scopes.profiles
        seen["access"] = principal.scopes.access
        return {"ok": True, "kind": kind, "id": ident, "facts": {}, "actions": [], "links": {}}

    monkeypatch.setattr(LD, "build_peek", spy)
    c = TestClient(app.app, client=(ip4(10, 0, 0, 5), 51000))
    assert c.get("/ledger/api/peek?kind=session&id=x").status_code == 200
    assert seen == {"id": rec.id, "profiles": ["researcher"], "access": "read"}


def test_an_unknown_view_key_stops_the_json_endpoints(home):
    app = make_app(home, HERMCP_LEDGER="1", HERMCP_LEDGER_AUTH="none")
    app.settings.ledger_view_key = "0000ffff"
    c = TestClient(app.app, client=(ip4(10, 0, 0, 5), 51000))
    for path in ("/ledger/api/peek?kind=session&id=x", "/ledger/api/list?kind=runs",
                 "/ledger/api/session/x", "/ledger/api/gateway"):
        r = c.get(path)
        assert r.status_code == 503 and r.json()["error"] == "view_key_unusable", path


def test_home_route_of_the_shell():
    assert LB.route_of("/ledger") == ("attention", "")
    assert LB.route_of("/ledger/") == ("attention", "")
    assert LB.route_of("/ledger/sessions") == ("sessions", "")
    assert LB.route_of("/ledger/runs") == ("runs", "")
    assert LB.route_of("/ledger/cron") == ("cron", "")
    assert LB.route_of("/ledger/gateway") == ("gateway", "")
    assert LB.route_of("/ledger/session/local-1") == ("session", "local-1")
    assert LB.route_of("/ledger/session/bad id") == ("session", "")
    assert LB.route_of("/ledger/whatever") == ("attention", "")
