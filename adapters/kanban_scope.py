"""Who owns a kanban task? (read-only sqlite lookups for profile-restricted keys)

Kanban boards are global; a task belongs to a profile through its `assignee`. A key bound to a profile may only see and
touch tasks assigned to that profile. Unknown task ids are reported exactly like foreign ones, so nothing leaks.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .. import meta
from ..registry import ToolError
from ..settings import hermes_home


def _board_dbs(board: str | None) -> list[Path]:
    root = hermes_home() / "kanban" / "boards"
    if board:
        if board == "default":
            return [hermes_home() / "kanban.db"]
        return [root / board / "kanban.db"]
    dbs = [hermes_home() / "kanban.db"]
    if root.is_dir():
        dbs += sorted(p / "kanban.db" for p in root.iterdir() if p.is_dir() and not p.name.startswith("_") and p.name != "default")
    return dbs


def assignee_of(task_id: str, board: str | None = None) -> tuple[str | None, bool]:
    """(assignee, found) of a task id, searched on `board` or on every board."""
    for db in _board_dbs(board):
        if not db.is_file():
            continue
        try:
            meta.note_file(db)
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
            try:
                row = con.execute("SELECT assignee FROM tasks WHERE id=?", (task_id,)).fetchone()
            finally:
                con.close()
        except sqlite3.Error:
            continue
        if row is not None:
            return row[0], True
    return None, False


def require_own_task(ctx, task_id: str, board: str | None = None) -> None:
    """Refuse (forbidden, no data) unless the task is assigned to a profile this key may act on."""
    if not ctx.restricted:
        return
    who, found = assignee_of(task_id, board)
    if not found or not ctx.own(who):
        raise ToolError("forbidden", f"task {task_id!r} is not assigned to this key's profile "
                                     f"({', '.join(ctx.principal.scopes.profiles)}) or does not exist. No data returned.")


def own_task_ids(ctx, ids: list[str], board: str | None = None) -> list[str]:
    return [i for i in ids if ctx.own(assignee_of(i, board)[0])]
