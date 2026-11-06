"""Which profiles does the running gateway/API server actually serve? (read-only)

Source: `served_profiles` in <HERMES_HOME>/gateway_state.json, trusted only while the gateway
process named in the file is alive. Anything else yields status "unknown" — never a guess.
The file belongs to the gateway; this module only reads it.
"""
from __future__ import annotations

import json
import os

from ..registry import ToolError
from ..settings import hermes_home


def _alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def served_info() -> dict:
    """{"status": "known"|"unknown", "served": [names], "reason": str|None, "gateway_pid": int|None}"""
    p = hermes_home() / "gateway_state.json"
    try:
        st = json.loads(p.read_text())
    except (OSError, ValueError):
        return {"status": "unknown", "served": [], "gateway_pid": None,
                "reason": "gateway_state.json missing or unreadable"}
    pid = st.get("pid")
    sp = st.get("served_profiles")
    if not isinstance(sp, list):
        return {"status": "unknown", "served": [], "gateway_pid": pid,
                "reason": "gateway_state.json has no served_profiles field"}
    if not sp:
        # A single-profile gateway publishes an EMPTY roster: the record sits in the home it serves, so it
        # proves that home (same rule as Hermes' cron.scheduler_ownership.record_serves_profile).
        if not _alive(pid):
            return {"status": "unknown", "served": [], "gateway_pid": pid,
                    "reason": f"gateway pid {pid} is not alive; gateway_state.json is stale"}
        home = hermes_home()
        own = home.name if home.parent.name == "profiles" else "default"
        return {"status": "known", "served": [own], "gateway_pid": pid, "reason": None}
    if not _alive(pid):
        return {"status": "unknown", "served": [], "gateway_pid": pid,
                "reason": f"gateway pid {pid} is not alive; gateway_state.json is stale"}
    return {"status": "known", "served": [str(x) for x in sp], "gateway_pid": pid, "reason": None}


def reachable(name: str, info: dict | None = None):
    """True / False / "unknown"."""
    info = info or served_info()
    if info["status"] != "known":
        return "unknown"
    return name in info["served"]


def require_served(profile: str) -> None:
    """Raise a readable error instead of letting the api_server answer 404 for an unserved profile.
    Unknown state does not block (the call goes through as before)."""
    info = served_info()
    if info["status"] == "known" and profile not in info["served"]:
        raise ToolError(
            "profile_not_active",
            f"Profile {profile!r} is only configured (settings on disk) but not served by the running "
            f"gateway, so it cannot be reached via MCP. Reachable profiles: {', '.join(info['served'])}.",
            profile=profile, reachable_profiles=info["served"])


def annotate(result: dict, profile: str) -> dict:
    """Mark a read result with the profile and whether it is active (True/False/"unknown")."""
    return {**result, "profile": profile, "profile_active": reachable(profile)}


def split_profiles(names: list[str], info: dict | None = None) -> dict:
    """Group profile names by reachability: active first (default first), then unknown, then inactive."""
    info = info or served_info()
    rank = {True: 0, "unknown": 1, False: 2}
    ordered = sorted(names, key=lambda n: (rank[reachable(n, info)], n != "default", n))
    return {"ordered": ordered,
            "reachable_profiles": [n for n in ordered if reachable(n, info) is True],
            "inactive_profiles": [n for n in ordered if reachable(n, info) is False],
            "unknown_profiles": [n for n in ordered if reachable(n, info) == "unknown"],
            "served_source": {"status": info["status"], "reason": info["reason"], "gateway_pid": info["gateway_pid"],
                              "file": "gateway_state.json (served_profiles)"}}
