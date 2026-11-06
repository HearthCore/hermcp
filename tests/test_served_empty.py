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
