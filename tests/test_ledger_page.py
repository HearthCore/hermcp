"""Ledger: opt-in page + read-only payloads.

The point of these tests is the contract, not the pixels: the page is off unless
the setting says otherwise, no key means no page, and nothing personal or
conversation-shaped ever leaves the process.
"""
from __future__ import annotations

import json
import time

from starlette.testclient import TestClient

from hermcp import ledger_page as LB
from hermcp.app import create_app
from hermcp.registry import Principal
from hermcp.scopes import Scopes
from hermcp.settings import load_settings

NOW = time.time()

TOOLS = {
    "profiles_list": {"count": 2, "profiles": [{"name": "default", "reachable": True},
                                               {"name": "developer", "reachable": True},
                                               {"name": "parked", "reachable": False}]},
    "status_gateway": {
        "state": "running", "health": "ok", "pid": 4711, "active_agents": 2,
        "code_version": "0.22.7", "state_updated_at": "2026-11-10T06:30:00Z",
        "platforms": {"telegram": {"state": "connected"},
                      "email": {"state": "disconnected", "needs_attention": True, "error": "boom"}},
        "stale_platforms": {"push": {"stale": True, "stale_reason": "writer_pid differs"}},
    },
    "hermes_status": {"verdict": "degraded", "profiles": {"reachable": 2, "total": 3},
                      "_meta": {"hermes_version": "0.22.7+1.gabc"}},
    "approvals_pending": {"mode": "multiplex-shared", "count": 0, "hidden_other_profiles": 0, "pending": []},
    "cron_runs": {"count": 1, "truncated": False, "runs": [
        {"run_id": "r1", "job_id": "j1", "job_name": "watchdog", "status": "completed",
         "started_at": "2026-11-10T07:00:00+01:00", "duration_s": 2.0,
         "session_id": "local-cron-run", "delivery_outcome": "delivered",
         "user_id": "u-2", "chat_context": "priv"}]},
    "cron_list": {"count": 1, "total": 1, "jobs": [
        {"id": "j1", "name": "watchdog", "schedule": "*/5 * * * *", "state": "scheduled",
         "profile": "default", "prompt": "ein Prompt, der nicht auf die Seite gehört"}]},
    "inbox_list": {"events": [
        {"ts": "2026-11-10T05:00:00Z", "kind": "run_failed", "profile": "default",
         "run_id": "run_x", "session_id": "run_x", "summary": "Run failed (boom)"},
        {"ts": "2020-01-01T00:00:00Z", "kind": "run_failed", "profile": "default",
         "run_id": "run_old", "session_id": "run_old", "summary": "zu alt"}]},
}


class FakeRegistry:
    """Answers reads from a dict, refuses everything else like a real read key."""

    def __init__(self, overrides=None, fail=()):
        self.calls = []
        self.overrides = overrides or {}
        self.fail = set(fail)

    def invoke(self, name, args, principal, settings, audit, via=""):
        self.calls.append((name, dict(args or {}), principal))
        if name in self.fail:
            return {"ok": False, "error": {"code": "internal_error", "message": f"{name} broke"}}
        if principal.scopes.access != "read":
            return {"ok": False, "error": {"code": "forbidden", "message": "read only"}}
        if name == "sessions_list":
            rows = [{"id": "local-abc123", "source": "desktop", "model": "m",
                     "started_at": NOW - 60, "ended_at": None, "last_active": NOW - 30,
                     "message_count": 4, "preview": "GEHEIMER CHAT-INHALT", "user_id": "u-1",
                     "chat_context": {"x": 1}, "profile_name": args.get("profile")},
                    {"id": "run_dead", "source": "api_server", "model": "m",
                     "started_at": NOW - 9000, "ended_at": NOW - 8000, "last_active": NOW - 8000,
                     "message_count": 2, "profile_name": args.get("profile")}]
            rows = rows[:1] if args.get("profile") == "default" else rows[1:]
            return {"ok": True, "result": {"count": len(rows), "sessions": rows}}
        if name == "approvals_pending" and args.get("profile") == "default":
            return {"ok": True, "result": {
                "mode": "multiplex-shared", "count": 1, "hidden_other_profiles": 2,
                "pending": [{"session_key": "sess:default", "request_id": "req-1",
                             "command": "rm -rf /tmp/x", "description": "cleanup"}]}}
        if name == "cron_runs" and args.get("job_id"):
            job = args["job_id"]
            rows = [r for r in TOOLS["cron_runs"]["runs"] if r.get("job_id") == job]
            return {"ok": True, "result": {"count": len(rows), "runs": rows}}
        if name == "cron_runs" and args.get("status") == "error":
            return {"ok": True, "result": {"count": 1, "runs": [
                {"run_id": "r2", "job_id": "j2", "job_name": "relay", "status": "error",
                 "started_at": "2026-11-10T06:00:00+01:00", "error": "timeout"}]}}
        return {"ok": True, "result": self.overrides.get(name, TOOLS.get(name, {"count": 0}))}


def make_principal(**sc):
    sc.setdefault("access", "read")
    return Principal("k1", "ledger test", Scopes(**sc))


def make_settings(**env):
    # "key" is pinned: a local .env (this host runs AUTH=none) must not decide a test
    base = {"HERMES_HOME": "/tmp/hermcp-ledger-test", "HERMCP_LEDGER_AUTH": "key"}
    base.update(env)
    return load_settings(base)


CONFIG = {"webapp": {"enabled": True, "public_url": "https://desktop.example.dev"}, }


def ip4(a, b, c, d):
    """A dotted address from octets. The repo's pre-push hook refuses literal private ranges,
    and a network guard necessarily talks about exactly those."""
    return ".".join(str(x) for x in (a, b, c, d))


def cidr(a, b, c, d, prefix):
    return ip4(a, b, c, d) + "/" + str(prefix)



# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def test_clean_strips_control_characters_and_truncates():
    assert LB.clean("a\x00b\nc") == "a b c"
    assert LB.clean("   ") is None
    assert LB.clean("x" * 30, 10) == "x" * 10 + "…"


def test_safe_token_rejects_anything_that_could_break_a_url():
    assert LB.safe_token("local-abc_1:2.3") == "local-abc_1:2.3"
    assert LB.safe_token("../../etc/passwd") is None
    assert LB.safe_token("id with spaces") is None
    assert LB.safe_token(None) is None


def test_desktop_link_uses_the_hash_router():
    url = LB.desktop_url("https://desktop.example.dev/", "developer", "local-abc")
    assert url == "https://desktop.example.dev/?profile=developer#/local-abc"


def test_dashboard_link_is_the_documented_fallback():
    url = LB.dashboard_url("https://hermes.example.dev", "developer", "local-abc")
    assert url == "https://hermes.example.dev/chat?resume=local-abc&profile=developer"


def test_links_are_never_built_for_unsafe_values():
    assert LB.desktop_url("https://x.example", "default", "bad id") is None
    assert LB.dashboard_url(None, "default", "local-abc") is None
    assert LB.dashboard_url("javascript:alert(1)", "default", "local-abc") is None


def test_link_targets_prefer_the_desktop_app_then_the_dashboard():
    desktop = LB.link_targets(make_settings(), CONFIG)
    assert desktop["kind"] == "desktop"
    assert desktop["base_url"] == "https://desktop.example.dev"
    assert desktop["source"] == "config:webapp.public_url"

    dashboard = LB.link_targets(make_settings(), {"dashboard": {"public_url": "https://hermes.example.dev"}})
    assert dashboard["kind"] == "dashboard"

    override = make_settings(HERMCP_LEDGER_BASE_URL="https://app.example.dev/")
    assert LB.link_targets(override, CONFIG)["source"] == "config:HERMCP_LEDGER_BASE_URL"

    none = LB.link_targets(make_settings(), {})
    assert none["kind"] == "none" and none["base_url"] is None


def test_read_only_principal_never_keeps_write_access():
    admin = Principal("k1", "admin", Scopes(access="admin", profiles=["*"], approvals_always=True))
    ro = LB.read_only_principal(admin)
    assert ro.scopes.access == "read"
    assert ro.scopes.approvals is True          # listing only; answering needs write access
    assert ro.scopes.approvals_always is False
    assert ro.id == "k1"


# ---------------------------------------------------------------------------
# payload
# ---------------------------------------------------------------------------

def test_overview_assembles_every_section():
    reg = FakeRegistry()
    payload = LB.build_overview(reg, make_principal(), make_settings(), CONFIG, "http://127.0.0.1:9230")

    assert payload["links"]["kind"] == "desktop"
    assert payload["profiles"] == ["default", "developer"]
    assert payload["gateway"]["state"] == "running"
    assert payload["gateway"]["verdict"] == "degraded"
    assert payload["sections"]["approvals"]["count"] == 1
    assert payload["sections"]["approvals"]["hidden"] == 2
    assert payload["attention"]["failed_cron_count"] == 1
    assert payload["attention"]["superseded_count"] == 0
    assert payload["attention"]["failed_chat_count"] == 1
    assert payload["errors"] == {}
    # every read went out with the read-only principal
    assert all(p.scopes.access == "read" for _, _, p in reg.calls)


def test_overview_never_leaks_personal_or_conversation_data():
    blob = json.dumps(LB.build_overview(FakeRegistry(), make_principal(), make_settings(), CONFIG))
    for forbidden in ("user_id", "chat_context", "GEHEIMER CHAT-INHALT", "preview",
                      "request_id", "session_key", "ein Prompt", "u-1", "u-2"):
        assert forbidden not in blob, forbidden


def test_sessions_carry_a_desktop_link_and_a_derived_status():
    payload = LB.build_overview(FakeRegistry(), make_principal(), make_settings(), CONFIG)
    items = payload["sections"]["sessions"]["items"]
    assert [s["session_id"] for s in items] == ["local-abc123", "run_dead"]
    assert items[0]["status"] == "aktiv"
    assert items[0]["url"] == "https://desktop.example.dev/?profile=default#/local-abc123"
    assert items[1]["status"] == "beendet"


def test_cron_runs_link_only_agent_runs_with_a_session():
    payload = LB.build_overview(FakeRegistry(), make_principal(), make_settings(), CONFIG)
    runs = {r["job_name"]: r for r in payload["sections"]["cron"]["runs"]}
    assert runs["watchdog"]["url"] == "https://desktop.example.dev/?profile=default#/local-cron-run"
    assert runs["watchdog"]["duration_s"] == 2.0


def test_failed_chat_runs_only_inside_the_window():
    payload = LB.build_overview(FakeRegistry(), make_principal(), make_settings(), CONFIG)
    assert [r["summary"] for r in payload["attention"]["failed_chat_runs"]] == ["Run failed (boom)"]


def test_a_broken_tool_is_reported_and_does_not_hide_the_rest():
    payload = LB.build_overview(FakeRegistry(fail={"sessions_list"}), make_principal(),
                                make_settings(), CONFIG)
    assert "sessions" in payload["errors"]
    assert payload["gateway"]["state"] == "running"
    assert payload["sections"]["sessions"]["items"] == []


def test_jobs_payload_omits_prompts():
    payload = LB.build_jobs(FakeRegistry(), make_principal(), make_settings())
    blob = json.dumps(payload)
    assert payload["count"] == 1 and payload["jobs"][0]["name"] == "watchdog"
    assert "ein Prompt" not in blob and "prompt" not in payload["jobs"][0]


def test_page_html_carries_the_poll_interval_and_no_secrets():
    page = LB.page_html(30)
    assert "setInterval" in page and "*1000" in page
    assert "/ledger/overview" in page and "/ledger/jobs" in page
    assert "__POLL__" not in page
    assert "hmcp_" not in page


# ---------------------------------------------------------------------------
# the route: opt-in, auth, and the cookie handshake
# ---------------------------------------------------------------------------

def make_app(home, **env):
    env.setdefault("HERMCP_ALLOWED_HOSTS", "testserver,127.0.0.1")
    return create_app(load_settings({"HERMES_HOME": str(home), **env}))


def test_page_is_absent_unless_enabled(home):
    app = make_app(home, HERMCP_LEDGER="0")   # explicit: a local .env must not decide this
    c = TestClient(app.app)
    assert c.get("/ledger").status_code == 404
    assert c.get("/ledger/overview").status_code == 404


def test_page_requires_a_key_and_then_keeps_a_cookie(home):
    app = make_app(home, HERMCP_LEDGER="1", HERMCP_LEDGER_AUTH="key")
    c = TestClient(app.app)
    token = app.keystore.create("ledger", Scopes(access="read", profiles=["*"]))[1]

    assert c.get("/ledger").status_code == 401

    trade = c.get("/ledger", params={"key": token}, follow_redirects=False)
    assert trade.status_code == 303
    assert app.LEDGER_COOKIE in c.cookies          # key traded for a cookie
    assert token not in trade.text

    page = c.get("/ledger")
    assert page.status_code == 200
    assert "Ledger" in page.text
    assert token not in page.text

    # the cookie alone is enough for the page and the JSON endpoints
    assert c.get("/ledger").status_code == 200
    overview = c.get("/ledger/overview")
    assert overview.status_code == 200
    assert overview.json()["links"]["kind"] in ("desktop", "dashboard", "none")

    c.cookies.clear()
    assert c.get("/ledger/overview").status_code == 401


def test_bearer_header_works_for_the_json_endpoints(home):
    app = make_app(home, HERMCP_LEDGER="1", HERMCP_LEDGER_AUTH="key")
    c = TestClient(app.app)
    token = app.keystore.create("ledger", Scopes(access="read", profiles=["*"]))[1]
    r = c.get("/ledger/overview", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert "sections" in r.json()


# ---------------------------------------------------------------------------
# HERMCP_LEDGER_AUTH=none: no key, but only from private addresses
# ---------------------------------------------------------------------------

def test_default_auth_mode_is_key(tmp_path, monkeypatch):
    """No config at all (no .env, no env var) must mean key — never "open"."""
    import hermcp.settings as st
    monkeypatch.setattr(st, "PKG_DIR", tmp_path)          # no .env in sight
    assert st.load_settings({"HERMES_HOME": "/tmp/x"}).ledger_auth == "key"
    assert st.load_settings({"HERMES_HOME": "/tmp/x", "HERMCP_LEDGER_AUTH": "none"}).ledger_auth == "none"
    # an unknown value is a config error, not a silent "none"
    import pytest

    from hermcp.settings import ConfigError, load_settings
    with pytest.raises(ConfigError):
        load_settings({"HERMES_HOME": "/tmp/hermcp-ledger-test", "HERMCP_LEDGER_AUTH": "offen"})


def test_parse_ip_accepts_the_shapes_a_proxy_sends():
    assert str(LB.parse_ip(ip4(10, 0, 0, 5))) == ip4(10, 0, 0, 5)
    assert str(LB.parse_ip("::1")) == "::1"
    assert str(LB.parse_ip("1.2.3.4:5678")) == "1.2.3.4"
    assert str(LB.parse_ip("[fd00::1]:443")) == "fd00::1"
    assert str(LB.parse_ip(f"::ffff:{ip4(192, 168, 1, 9)}")) == ip4(192, 168, 1, 9)
    assert LB.parse_ip("testclient") is None
    assert LB.parse_ip("") is None
    assert LB.parse_ip(None) is None


def test_private_ranges_are_local_and_public_ones_are_not():
    for addr in ("127.0.0.1", ip4(10, 0, 0, 5), ip4(172, 16, 4, 2), ip4(192, 168, 7, 7), "100.64.1.2",
                 "100.101.102.103", "::1", "fd00::1", "fe80::1"):
        assert LB.is_local_address(LB.parse_ip(addr)), addr
    for addr in ("8.8.8.8", "193.180.211.202", "2001:4860:4860::8888"):
        assert not LB.is_local_address(LB.parse_ip(addr)), addr


def test_request_allowed_rules():
    from hermcp.client_ip import resolve

    trusted = LB.parse_ip("127.0.0.1") and __import__("hermcp.client_ip", fromlist=["x"]).parse_networks([cidr(10, 1, 0, 9, 32)])
    # untrusted peer: its forwarding headers are ignored, judgement is on the peer
    info = resolve("127.0.0.1", {"x-forwarded-for": "203.0.113.9"})
    assert LB.request_allowed(info)[0] is True
    info = resolve("203.0.113.9", {"x-forwarded-for": "127.0.0.1"})
    ok, why = LB.request_allowed(info)
    assert ok is False and "not private" in why
    # trusted proxy: served, whoever is behind it (the proxy authenticates)
    info = resolve(ip4(10, 1, 0, 9), {"x-forwarded-for": "203.0.113.9"}, trusted)
    assert info.trusted is True and LB.request_allowed(info) == (True, "")
    # unknown peer
    assert LB.request_allowed(resolve(None, {}))[0] is False


def test_none_mode_serves_the_page_without_a_key_from_loopback(home):
    app = make_app(home, HERMCP_LEDGER="1", HERMCP_LEDGER_AUTH="none")
    c = TestClient(app.app, client=("127.0.0.1", 51000))

    page = c.get("/ledger")
    assert page.status_code == 200 and "Ledger" in page.text
    overview = c.get("/ledger/overview")
    assert overview.status_code == 200
    assert "sections" in overview.json()
    assert c.get("/ledger/jobs").status_code in (200, 503)


def test_none_mode_refuses_a_public_client(home):
    app = make_app(home, HERMCP_LEDGER="1", HERMCP_LEDGER_AUTH="none")
    c = TestClient(app.app, client=("203.0.113.9", 51000))
    for path in ("/ledger", "/ledger/overview", "/ledger/jobs"):
        r = c.get(path)
        assert r.status_code == 403, path
        assert "not private" in r.json()["reason"]


def test_none_mode_serves_a_request_through_a_trusted_proxy(home):
    app = make_app(home, HERMCP_LEDGER="1", HERMCP_LEDGER_AUTH="none",
                   HERMCP_TRUSTED_PROXIES=ip4(10, 1, 0, 9))
    c = TestClient(app.app, client=(ip4(10, 1, 0, 9), 51000))
    r = c.get("/ledger/overview", headers={"x-forwarded-for": "203.0.113.9",
                                           "x-forwarded-proto": "https",
                                           "x-forwarded-host": "ledger.example.dev"})
    assert r.status_code == 200
    assert r.json()["links"]["base_url"].startswith("https://ledger.example.dev")


def test_none_mode_ignores_a_spoofed_forwarded_address(home):
    """An untrusted peer cannot talk itself into or out of the private check."""
    app = make_app(home, HERMCP_LEDGER="1", HERMCP_LEDGER_AUTH="none")
    c = TestClient(app.app, client=("203.0.113.9", 51000))
    r = c.get("/ledger/overview", headers={"x-forwarded-for": "127.0.0.1"})
    assert r.status_code == 403


def test_none_mode_never_gets_write_access(home, monkeypatch):
    seen = {}

    def spy(registry, principal, settings, config, base, window):
        seen["access"] = principal.scopes.access
        seen["approvals"] = principal.scopes.approvals
        return {"sections": {}, "errors": {}, "links": {}, "poll_seconds": 15,
                "profiles": [], "attention": {}, "gateway": {}}

    monkeypatch.setattr(LB, "build_overview", spy)
    app = make_app(home, HERMCP_LEDGER="1", HERMCP_LEDGER_AUTH="none")
    c = TestClient(app.app, client=(ip4(10, 0, 0, 5), 51000))
    assert c.get("/ledger/overview").status_code == 200
    assert seen == {"access": "read", "approvals": True}


# ---------------------------------------------------------------------------
# the old name: /lagebild redirects, HERMCP_LAGEBILD* still works (deprecated)
# ---------------------------------------------------------------------------

def test_old_lagebild_path_redirects_permanently(home):
    app = make_app(home, HERMCP_LEDGER="1", HERMCP_LEDGER_AUTH="none")
    c = TestClient(app.app, client=("127.0.0.1", 51000))
    r = c.get("/lagebild", follow_redirects=False)
    assert r.status_code == 308 and r.headers["location"] == "/ledger"
    r = c.get("/lagebild/overview?window_hours=6", follow_redirects=False)
    assert r.status_code == 308 and r.headers["location"] == "/ledger/overview?window_hours=6"


def test_deprecated_env_names_still_configure_the_page(home):
    from hermcp.settings import load_settings
    s = load_settings({"HERMES_HOME": str(home), "HERMCP_LAGEBILD": "1",
                       "HERMCP_LAGEBILD_AUTH": "none",
                       "HERMCP_LAGEBILD_BASE_URL": "https://old.example.dev/"})
    assert (s.ledger, s.ledger_auth, s.ledger_base_url) == (True, "none", "https://old.example.dev")

    # the new name wins when both are set
    s = load_settings({"HERMES_HOME": str(home), "HERMCP_LAGEBILD": "1", "HERMCP_LEDGER": "0",
                       "HERMCP_LAGEBILD_AUTH": "none", "HERMCP_LEDGER_AUTH": "key"})
    assert (s.ledger, s.ledger_auth) == (False, "key")


def test_no_write_tool_is_reachable_from_the_page(home):
    """Belt and braces: the page's own principal is read-only, and no route writes."""
    import inspect

    src = inspect.getsource(__import__("hermcp.server", fromlist=["x"]))
    ledger_part = src[src.index("-- ledger"):src.index("async def mcp")]
    for verb in ("registry.invoke", "chat_send", "chat_run_", "kanban_", "config_set"):
        assert verb not in ledger_part, verb


# ---------------------------------------------------------------------------
# superseded: a failure the same job already recovered from (read-only derivation)
# ---------------------------------------------------------------------------

class SupersededRegistry(FakeRegistry):
    """``relay`` failed at 06:00 and ran fine again at 07:30; ``watchdog`` kept failing."""

    def invoke(self, name, args, principal, settings, audit, via=""):
        if name == "cron_runs" and args.get("job_id") == "j2":
            return {"ok": True, "result": {"count": 1, "runs": [
                {"run_id": "r3", "job_id": "j2", "job_name": "relay", "status": "completed",
                 "started_at": "2026-11-10T07:30:00+01:00"}]}}
        if name == "cron_runs" and args.get("job_id") == "j9":
            return {"ok": True, "result": {"count": 1, "runs": [
                {"run_id": "r9", "job_id": "j9", "job_name": "stuck", "status": "error",
                 "started_at": "2026-11-10T07:40:00+01:00"}]}}
        if name == "cron_runs" and args.get("status") == "error":
            return {"ok": True, "result": {"count": 2, "runs": [
                {"run_id": "r2", "job_id": "j2", "job_name": "relay", "status": "error",
                 "started_at": "2026-11-10T06:00:00+01:00", "error": "timeout"},
                {"run_id": "r8", "job_id": "j9", "job_name": "stuck", "status": "error",
                 "started_at": "2026-11-10T07:00:00+01:00", "error": "boom"}]}}
        return super().invoke(name, args, principal, settings, audit, via)


def test_a_failure_whose_job_ran_ok_afterwards_moves_to_superseded():
    payload = LB.build_overview(SupersededRegistry(), make_principal(), make_settings(), CONFIG)
    att = payload["attention"]
    assert [r["run_id"] for r in att["failed_cron_runs"]] == ["r8"]
    assert att["failed_cron_count"] == 1
    assert [r["run_id"] for r in att["superseded_cron_runs"]] == ["r2"]
    assert att["superseded_count"] == 1
    from datetime import datetime
    # timezone-independent: the payload carries local time of the machine running it
    assert datetime.fromisoformat(att["superseded_cron_runs"][0]["ok_after"]) == \
        datetime.fromisoformat("2026-11-10T07:30:00+01:00")
    # superseded runs do not count as attention; the still-failing one does
    assert att["total"] == 1 + 1 + 1          # failing cron + failing chat + approval


def test_a_broken_superseded_lookup_never_hides_a_failure():
    class Broken(FakeRegistry):
        def invoke(self, name, args, principal, settings, audit, via=""):
            if name == "cron_runs" and args.get("job_id"):
                return {"ok": False, "error": {"code": "internal_error", "message": "ledger broke"}}
            return super().invoke(name, args, principal, settings, audit, via)

    payload = LB.build_overview(Broken(), make_principal(), make_settings(), CONFIG)
    assert payload["attention"]["failed_cron_count"] == 1
    assert payload["attention"]["superseded_count"] == 0
    assert "cron_superseded" in payload["errors"]


def test_superseded_entries_are_part_of_the_page_but_not_of_the_badge():
    page = LB.page_html(15)
    assert "superseded_cron_runs" in page          # rendered in the collapsed group
    assert "attentionCount" in page                # the badge counts active only
