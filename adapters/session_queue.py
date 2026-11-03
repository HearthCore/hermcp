"""Read-only view of the Hermes session queue (FIFO of turns per session).

Hermes 0.22.5 admits every /v1/runs turn into the session authority's FIFO: the `session_admissions` table in the
profile's state.db (columns seq, request_id, principal_id, target_session_id, status). For an API run the
`request_id` IS the run_id (gateway/platforms/api_server_authority_runs.run_admission). One `started` row per
session runs, the `queued` rows wait in `seq` order. Turns from other doors (Telegram, TUI, cron) sit in the same
FIFO under another principal: they count for the position but have no run_id we could show or control.

The api_server has no HTTP endpoint that lists a session's queue, so this reads the table directly, strictly
read-only (sqlite `mode=ro`, short timeout). Every function returns None when the table cannot be read
(older Hermes, no state.db, locked) so callers fall back to what hermcp itself knows.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .common import profile_home

OPEN = ("queued", "started", "unknown")


def _connect(profile: str) -> sqlite3.Connection | None:
    try:
        path: Path = profile_home(profile) / "state.db"
    except Exception:  # noqa: BLE001 - unknown profile
        return None
    if not path.is_file():
        return None
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2.0)
        db.row_factory = sqlite3.Row
        return db
    except sqlite3.Error:
        return None


def _rows(profile: str, sql: str, args: tuple) -> list[dict[str, Any]] | None:
    db = _connect(profile)
    if db is None:
        return None
    try:
        return [dict(r) for r in db.execute(sql, args)]
    except sqlite3.Error:
        return None
    finally:
        db.close()


def session_queue(profile: str, session_id: str) -> list[dict[str, Any]] | None:
    """Open turns of one session in FIFO order: [{run_id|None, state: running|queued|unknown, position}].
    position 0 = the turn that runs now, 1 = next in line, ... Foreign turns have run_id None."""
    rows = _rows(profile, "SELECT seq, request_id, principal_id, status FROM session_admissions "
                          "WHERE target_session_id=? AND status IN ('queued','started','unknown') ORDER BY seq",
                 (session_id,))
    if rows is None:
        return None
    out, pos = [], 0
    for r in rows:
        api = r["principal_id"] == "api"
        state = {"started": "running", "queued": "queued"}.get(r["status"], "unknown")
        if state == "queued":
            pos += 1
        out.append({"run_id": r["request_id"] if api else None, "state": state,
                    "position": 0 if state != "queued" else pos,
                    **({} if api else {"origin": "non-api turn (other door: chat platform, TUI, cron)"})})
    return out


def running_run_id(profile: str, session_id: str) -> tuple[bool, str | None]:
    """(readable, run_id of the API turn that currently runs in this session or None)."""
    q = session_queue(profile, session_id)
    if q is None:
        return False, None
    for t in q:
        if t["state"] == "running":
            return True, t["run_id"]
    return True, None


def positions(profile: str, run_ids: list[str]) -> dict[str, dict[str, Any]] | None:
    """run_id -> {session_id, queue_position, queue_length} for the given API runs that are still open.
    queue_position 0 = running, n = n-th in line; queue_length = turns waiting in that session."""
    if not run_ids:
        return {}
    marks = ",".join("?" * len(run_ids))
    mine = _rows(profile, f"SELECT request_id, target_session_id FROM session_admissions WHERE principal_id='api' "
                          f"AND request_id IN ({marks}) AND status IN ('queued','started','unknown')", tuple(run_ids))
    if mine is None:
        return None
    out: dict[str, dict[str, Any]] = {}
    for sid in {r["target_session_id"] for r in mine}:
        q = session_queue(profile, sid)
        if q is None:
            return None
        waiting = sum(1 for t in q if t["state"] == "queued")
        for t in q:
            if t["run_id"] in run_ids:
                out[t["run_id"]] = {"session_id": sid, "queue_position": t["position"], "queue_length": waiting}
    return out
