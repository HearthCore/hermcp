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


def brief(row: dict[str, Any], profile: str | None = None) -> dict[str, Any]:
    """One row for `sessions_list`: the tool's own vocabulary plus the standard session fields
    (session_id, profile, session_title, started_by) every list entry carries.

    No extra read: the title in `row` comes from the query that just ran, so a renamed session shows the
    new title immediately."""
    from .session_meta import from_session_row
    return {**slim({k: row[k] for k in LIST_FIELDS if k in row}, 160), **from_session_row(row, profile)}


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


def title(db, session_id: str) -> str | None:
    """A session's title, or None. `hermes_state.SessionDB` no longer carries `get_session_title` (Hermes
    deprecates the class for the storage interface and keeps only the methods its plugin guide lists), so
    read the row it does carry."""
    row = call_inproc(db.get_session, session_id)
    return None if row is None else row.get("title")


@contextlib.contextmanager
def store(profile: str, read_only: bool = True) -> Iterator[Any]:
    """The storage interface handle on a profile's state.db. Hermes >= 0.27 deprecates
    `hermes_state.SessionDB` for it and keeps only a subset of the class's methods; older Hermes has no
    storage interface at all, which is why every caller below tries the class method first."""
    try:
        from persistence_assembly import open_persistence, open_persistence_reader, profile_scope
    except ImportError as exc:
        raise AdapterError(f"no storage interface: {exc}") from exc
    home = profile_home(profile)
    with hermes_scope(profile):
        if read_only:
            with open_persistence_reader(home / "state.db") as handle:
                yield handle
        else:
            with open_persistence(profile_scope(home=home)) as handle:
                yield handle


def delete_targets(profile: str, db, session_id: str) -> list[str]:
    """The session ids `delete_session` would remove: the session, its local lineage and its recursive
    delegate children."""
    fn = getattr(db, "get_session_delete_targets", None)
    if fn is not None:
        return call_inproc(fn, session_id)
    with store(profile) as handle:
        return [str(sid) for sid in handle.sessions.delete_targets(session_id)]


def set_pinned(profile: str, db, session_id: str, pinned: bool) -> bool:
    fn = getattr(db, "set_session_pinned", None)
    if fn is not None:
        return bool(call_inproc(fn, session_id, pinned))
    with store(profile, read_only=False) as handle:
        return bool(handle.sessions.set_pinned(session_id, pinned))


def set_archived(profile: str, db, session_id: str, archived: bool) -> bool:
    fn = getattr(db, "set_session_archived", None)
    if fn is not None:
        return bool(call_inproc(fn, session_id, archived))
    with store(profile, read_only=False) as handle:
        return bool(handle.sessions.set_archived(session_id, archived).changed)
