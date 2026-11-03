"""status_gateway / hermes_health must ignore leftovers of dead gateway processes."""
import json
import os

from hermcp.tools.status import _gateway_state

ME, START = os.getpid(), 432073751


def _live(state="connected", **kw):
    return {"state": state, "error_message": None, "needs_attention": False, "updated_at": "2026-10-21T04:41:38+00:00",
            "writer_pid": ME, "writer_start_time": START, **kw}


def _write(home, platforms, **head):
    st = {"pid": ME, "start_time": START, "gateway_state": "running", "platforms": platforms, **head}
    (home / "gateway_state.json").write_text(json.dumps(st))
    return home / "gateway_state.json"


def _zombies():
    return {
        "buzz": {"state": "retrying", "error_message": "No adapter available for enabled buzz", "needs_attention": True,
                 "updated_at": "2026-10-11T11:14:40+00:00", "writer_pid": 1922890, "writer_start_time": 347984008},
        "inkbox": {"state": "retrying", "error_message": "connect() got an unexpected keyword argument",
                   "needs_attention": None, "updated_at": "2026-06-29T19:28:27+00:00",
                   "writer_pid": None, "writer_start_time": None},
    }


def test_zombies_are_stale_and_do_not_degrade(home):
    plats = {"telegram": _live(), **_zombies(),
             "rocketchat": {"state": "disconnected", "error_message": None, "needs_attention": None,
                            "writer_pid": None, "writer_start_time": None}}
    f = _write(home, plats)
    before = f.read_bytes()
    r = _gateway_state(home)
    assert set(r["platforms"]) == {"telegram", "rocketchat"}
    assert set(r["stale_platforms"]) == {"buzz", "inkbox"}
    assert r["stale_platforms"]["buzz"]["stale"] is True
    assert r["stale_platforms"]["buzz"]["updated_at"].startswith("2026-10-11")
    assert "1922890" in r["stale_platforms"]["buzz"]["stale_reason"]
    assert "writer_pid" in r["stale_platforms"]["inkbox"]["stale_reason"]
    assert r["attention"] == [] and r["health"] == "ok"
    assert r["platforms"]["rocketchat"]["needs_attention"] is None
    assert f.read_bytes() == before  # read-only


def test_pid_reuse_and_dead_writer_are_stale(home):
    _write(home, {"a": _live(writer_start_time=1), "b": _live(writer_pid=2 ** 22 + 12345)})
    r = _gateway_state(home)
    assert set(r["stale_platforms"]) == {"a", "b"} and r["health"] == "ok"


def test_live_failure_still_degrades(home):
    _write(home, {"telegram": _live("retrying", needs_attention=True, error_message="boom")})
    r = _gateway_state(home)
    assert r["attention"] == ["telegram"] and r["health"] == "degraded"


def test_config_disabled_platform_not_a_problem(home):
    (home / "config.yaml").write_text("gateway:\n  platforms:\n    buzz:\n      enabled: false\n"
                                      "plugins:\n  disabled: [inkbox-platform]\n")
    _write(home, {"buzz": _live("retrying", needs_attention=True), "inkbox": _live("retrying")})
    r = _gateway_state(home)
    assert r["attention"] == [] and r["health"] == "ok"
    assert r["platforms"]["buzz"]["config_disabled"] and not r["platforms"]["buzz"]["needs_attention"]
    assert r["platforms"]["inkbox"]["config_disabled"]


def test_dead_gateway_is_degraded(home):
    _write(home, {"telegram": _live()}, pid=2 ** 22 + 12345)
    assert _gateway_state(home)["health"] == "degraded"


# ---- profiles: served vs configured ------------------------------------------------------
import pytest  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from hermcp.keys import Scopes  # noqa: E402

DISK = ["profile-a", "infra-intern", "researcher", "developer", "media-agent", "llm-ops", "qa-reviewer"]


@pytest.fixture()
def pcall(happ, home):
    for n in DISK:
        (home / "profiles" / n).mkdir(exist_ok=True)
        (home / "profiles" / n / "config.yaml").write_text("model:\n  default: m\n")
    c = TestClient(happ.app)
    _, token = happ.keystore.create("t", Scopes(access="admin", approvals=True, secrets_write=True, dangerous=True))

    def _call(tool, /, **args):
        r = c.post("/mcp", headers={"Authorization": f"Bearer {token}"},
                   json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}})
        res = r.json()["result"]
        body = json.loads(res["content"][0]["text"])
        return (not res["isError"]), body.get("result", body.get("error"))
    _call.home = home
    return _call


def test_profiles_list_separates_served(pcall):
    _write(pcall.home, {}, served_profiles=["default", "researcher"])
    ok, r = pcall("profiles_list")
    assert ok and r["count"] == 8
    assert [p["name"] for p in r["profiles"][:2]] == ["default", "researcher"]
    assert all(p["reachable"] is True for p in r["profiles"][:2])
    assert all(p["reachable"] is False for p in r["profiles"][2:])
    assert r["reachable_profiles"] == ["default", "researcher"] and "infra-intern" in r["inactive_profiles"]
    assert r["served_source"]["status"] == "known" and "settings" in r["note"]


def test_profiles_list_unknown_without_state(pcall):
    ok, r = pcall("profiles_list")
    assert ok and all(p["reachable"] == "unknown" for p in r["profiles"])
    assert r["served_source"]["status"] == "unknown" and "unreadable" in r["served_source"]["reason"]
    (pcall.home / "gateway_state.json").write_text(json.dumps({"pid": ME, "platforms": {}}))
    assert pcall("profiles_list")[1]["served_source"]["reason"].endswith("no served_profiles field")
    _write(pcall.home, {}, served_profiles=["default"], pid=2 ** 22 + 12345)   # dead gateway -> stale
    ok, r = pcall("profiles_list")
    assert all(p["reachable"] == "unknown" for p in r["profiles"]) and "stale" in r["served_source"]["reason"]


def test_chat_tools_reject_inactive_profile(pcall):
    _write(pcall.home, {}, served_profiles=["default", "researcher"])
    for tool, args in [("chat_send", {"message": "hi"}), ("chat_run_status", {"run_id": "r1"}),
                       ("chat_run_stop", {"run_id": "r1"}), ("chat_conversations", {}),
                       ("chat_history", {"session_id": "x"}), ("chat_send", {"message": "hi", "dry_run": True})]:
        ok, r = pcall(tool, profile="infra-intern", **args)
        assert not ok and r["code"] == "profile_not_active", tool
        assert "only configured" in r["message"] and "default, researcher" in r["message"]
        assert r["reachable_profiles"] == ["default", "researcher"]
    ok, r = pcall("chat_send", message="hi", dry_run=True, profile="researcher")
    assert ok  # served profile passes the gate


def test_sessions_read_marks_profile_active(pcall, monkeypatch):
    import contextlib

    from hermcp.adapters import hsessions

    class FakeDB:
        def list_sessions_rich(self, **kw):
            return []

    @contextlib.contextmanager
    def fake_open(profile, read_only=True):
        yield FakeDB()
    monkeypatch.setattr(hsessions, "open_db", fake_open)
    _write(pcall.home, {}, served_profiles=["default", "researcher"])
    for prof, want in [("researcher", True), ("infra-intern", False)]:
        ok, r = pcall("sessions_list", profile=prof)
        assert ok and r["profile"] == prof and r["profile_active"] is want   # still readable when inactive


def test_hermes_health_splits_profiles(pcall):
    _write(pcall.home, {}, served_profiles=["default", "researcher"])
    ok, r = pcall("hermes_health")
    assert ok and r["reachable_profiles"] == ["default", "researcher"]
    assert set(r["inactive_profiles"]) == set(DISK) - {"researcher"} and r["unknown_profiles"] == []
    assert r["profiles"][:2] == ["default", "researcher"] and len(r["profiles"]) == 8   # legacy field, active first
    assert r["served_source"]["status"] == "known" and "settings only" in r["profiles_note"]


def test_hermes_health_profiles_unknown_without_state(pcall):
    ok, r = pcall("hermes_health")
    assert ok and r["reachable_profiles"] == [] and r["inactive_profiles"] == []
    assert len(r["unknown_profiles"]) == 8 and r["served_source"]["status"] == "unknown"
