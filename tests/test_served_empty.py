"""An EMPTY served_profiles roster is what a single-profile gateway publishes: it serves its own home.

The record lives in the home it serves, so a live gateway with an empty roster proves THAT profile (default),
never another one. A dead gateway makes the record stale -> unknown.
"""
import json
import os

import pytest

from hermcp.adapters.served import reachable, require_served, served_info
from hermcp.registry import ToolError

ME = os.getpid()


def _state(home, sp, pid=ME):
    (home / "gateway_state.json").write_text(json.dumps({"pid": pid, "gateway_state": "running", "served_profiles": sp}))


def test_empty_roster_with_live_gateway_serves_own_home(home):
    _state(home, [])
    info = served_info()
    assert info["status"] == "known" and info["served"] == ["default"] and info["reason"] is None
    assert reachable("default") is True
    require_served("default")  # must not raise


def test_empty_roster_does_not_serve_other_profiles(home):
    _state(home, [])
    assert reachable("researcher") is False
    with pytest.raises(ToolError):
        require_served("researcher")


def test_empty_roster_with_dead_gateway_is_unknown(home):
    _state(home, [], pid=2 ** 22 + 12345)
    info = served_info()
    assert info["status"] == "unknown" and "stale" in info["reason"]
    assert reachable("default") == "unknown"
    require_served("default")  # unknown never blocks


def test_non_empty_roster_unchanged(home):
    _state(home, ["default"])
    assert served_info()["status"] == "known" and reachable("default") is True
    require_served("default")
    with pytest.raises(ToolError):
        require_served("researcher")


def _invoke(home, tool, principal=None, **args):
    from hermcp.registry import Principal
    from hermcp.scopes import Scopes
    from hermcp.settings import load_settings
    from hermcp.tools import load_all
    who = principal or Principal("k", "admin-key", Scopes(access="admin"))
    r = load_all().invoke(tool, args, who, load_settings({"HERMES_HOME": str(home)}), None)
    assert r["ok"], r
    return r["result"]


def test_hermes_status_agrees_with_profiles_list_on_empty_roster(home):
    _state(home, [])
    status = _invoke(home, "hermes_status")["profiles"]
    listed = _invoke(home, "profiles_list", detail=False, skills=False)
    assert listed["reachable_profiles"] == ["default"]
    assert status["reachable"] == len(listed["reachable_profiles"]) == 1
    assert status["total"] == len(listed["profiles"])
    assert "default" not in status.get("not_reachable", [])


def test_hermes_status_empty_roster_respects_key_scope(home):
    from hermcp.registry import Principal
    from hermcp.scopes import Scopes
    _state(home, [])
    only_default = Principal("kd", "default-key", Scopes(access="write", profiles=["default"]))
    assert _invoke(home, "hermes_status", only_default)["profiles"] == {"reachable": 1, "total": 1}
    only_other = Principal("ko", "researcher-key", Scopes(access="write", profiles=["researcher"]))
    assert _invoke(home, "hermes_status", only_other)["profiles"] == {
        "reachable": 0, "total": 1, "not_reachable": ["researcher"]}
