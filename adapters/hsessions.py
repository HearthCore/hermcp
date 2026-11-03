"""Sessions adapter: hermes_state.SessionDB opened directly at the profile's state.db."""
from __future__ import annotations

import contextlib
from typing import Any, Iterator

from ..registry import AdapterError, ToolError
from .common import call_inproc, ensure_hermes_importable, hermes_scope, profile_home  # noqa: F401

DROP = {"system_prompt", "_system_prompt_resolved", "model_config", "billing_base_url"}


def slim(row: dict[str, Any], limit: int = 400) -> dict[str, Any]:
    out = {}
    for k, v in row.items():
        if k in DROP:
            continue
        if isinstance(v, str) and len(v) > limit:
            v = v[:limit] + f"…[+{len(v) - limit}]"
        elif isinstance(v, (bytes, bytearray)):
            v = f"<{len(v)} bytes>"
        out[k] = v
    return out


LIST_FIELDS = ("id", "title", "source", "model", "started_at", "ended_at", "last_active", "message_count",
               "tool_call_count", "archived", "pinned", "preview", "estimated_cost_usd", "chat_type", "profile_name")


def brief(row: dict[str, Any]) -> dict[str, Any]:
    return slim({k: row[k] for k in LIST_FIELDS if k in row}, 160)


@contextlib.contextmanager
def open_db(profile: str, read_only: bool = True) -> Iterator[Any]:
    home = profile_home(profile)
    db_path = home / "state.db"
    if not db_path.exists():
        raise ToolError("not_found", f"profile {profile!r} has no state.db")
    with hermes_scope(profile):
        try:
            from hermes_state import SessionDB
        except ImportError as exc:
            raise AdapterError(f"cannot import hermes_state: {exc}") from exc
        db = call_inproc(SessionDB, db_path=db_path, read_only=read_only)
        try:
            yield db
        finally:
            with contextlib.suppress(Exception):
                db.close()


def resolve(db, session_id: str) -> str:
    sid = call_inproc(db.resolve_session_id, session_id)
    if not sid:
        raise ToolError("not_found", f"no session matching {session_id!r}")
    return sid
