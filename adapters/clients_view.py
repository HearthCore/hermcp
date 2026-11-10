"""Who serves which session: sessions grouped by client, each with its open runs.

Read-only view over hermcp's own session cards and run watches (inbox.db). It is shared by the
`clients_overview` tool and the Ledger page, so both say the same thing. Cards are free labels:
nothing here is a permission.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

ACTIVE_WINDOW_S = 15 * 60        # a session seen within this window counts as active


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if ts else None


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def build(box, *, key_id: str | None = None, allowed_profiles=None, now: float | None = None,
          limit: int = 200) -> dict[str, Any]:
    """Sessions of `key_id` (None = every key) grouped by client. `allowed_profiles` (a set, None = all) narrows
    the listed runs to a profile-restricted key's profiles; the cards themselves are labels and stay visible."""
    now = now or time.time()
    cards = box.sessions_list(key_id=key_id, limit=limit)
    runs_by_session: dict[str, list[dict[str, Any]]] = {}
    unclaimed: list[dict[str, Any]] = []
    for r in box.running(key_id=key_id, now=now):
        if allowed_profiles is not None and r["profile"] not in allowed_profiles:
            continue
        row = {"run_id": r["run_id"], "profile": r["profile"], "age_seconds": int(now - r["started_at"]),
               "activity": r.get("activity") or "starting"}
        if r.get("client_session"):
            runs_by_session.setdefault(r["client_session"], []).append(row)
        else:
            unclaimed.append({**row, "client": r.get("client")})
    clients: dict[str, dict[str, Any]] = {}
    handed_over = 0
    for c in cards:
        if c.get("successor"):
            handed_over += 1
            continue
        idle = int(now - c["last_seen"])
        runs = runs_by_session.get(c["handle"], [])
        s = {"client_session": c["handle"], "kind": c.get("kind"), "purpose": c.get("purpose"), "role": c.get("role"),
             "last_seen": _iso(c["last_seen"]), "idle_seconds": idle,
             "state": "active" if idle <= ACTIVE_WINDOW_S or runs else "idle", "open_runs": runs}
        g = clients.setdefault(c["client"], {"client": c["client"], "sessions": [], "open_runs": 0, "active_sessions": 0})
        g["sessions"].append(s)
        g["open_runs"] += len(runs)
        g["active_sessions"] += s["state"] == "active"
    groups = sorted(clients.values(), key=lambda g: (-g["active_sessions"], -g["open_runs"], g["client"]))
    return {"generated_at": _iso(now), "active_window_minutes": ACTIVE_WINDOW_S // 60, "clients": groups,
            "unclaimed_runs": unclaimed[:20], "unclaimed_count": len(unclaimed), "handed_over_hidden": handed_over,
            "summary": summary(groups, len(unclaimed))}


def summary(groups: list[dict[str, Any]], unclaimed: int) -> str:
    """One short sentence per client, meant to be read out loud."""
    if not groups and not unclaimed:
        return "No sessions are registered."
    parts = []
    for g in groups:
        n = len(g["sessions"])
        parts.append(f"{g['client']}: {_plural(n, 'session')} ({g['active_sessions']} active), "
                     f"{_plural(g['open_runs'], 'open run')}")
    if unclaimed:
        parts.append(f"{_plural(unclaimed, 'open run')} started without a session")
    return "; ".join(parts) + "."
