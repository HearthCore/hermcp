"""Session metadata resolver: one place that answers "which session is this, whose is it, what is it about".

Every list tool that shows single entries (`agents_live`, the runs list, `sessions_list` and the Ledger's
session/run lists) reports the same four fields, always present, `null` when nothing is known:

    session_id     the Hermes conversation (state.db `sessions.id`), never a chat identity
    profile        the profile the session belongs to
    session_title  the session's title
    started_by     the client / external agent that started it (Telegram, CLI, api_server, a hermcp key
                   such as `claude-ai-cloud`), or None

Two rules shape this module:

* **Live, never cached.** The title is read from the profile's `state.db` on every request (sqlite
  `mode=ro`), so a rename in the database shows up in the very next call. Only within one request (one
  :class:`Resolver` instance) is a lookup remembered -- that keeps a list of 50 rows at one read per
  profile instead of 50.
* **Foreign is normal.** Nothing here filters by the caller's own runs or sessions: read access is what a
  key's profile scope allows, and the answer says who started the work instead of hiding it.

Managed units (`kind=managed`: a kanban or admission worker Hermes started itself) are the one case the
gateway does not label with a chat key. Their `active-work` entry names the *session id* it runs
(`local-<hex>`) plus a pid; :meth:`Resolver.managed` turns that id into the same four fields by looking it
up in the profile homes, and adds `resolution_source` (how it was found) and `pid_verified` (whether the
named process is really the one running that session -- see :func:`proc_started_at`).

The database access is deliberately direct (like ``session_queue`` and ``cron_ledger``): the class
interface of Hermes' session store keeps changing, the table does not, and every failure degrades to
``None`` instead of an error -- an unknown title must never cost a list call.
"""
from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .common import profile_home
from ..settings import hermes_home, ignored_profiles

FIELDS = ("session_id", "profile", "session_title", "started_by")

TITLE_CAP = 200
STARTED_BY_CAP = 80
_UNSET = object()

# What `resolution_source` can say about a managed unit.
RESOLVED = "gateway-session-id"          # the gateway named the session, a profile database answered
UNKNOWN_SESSION = "gateway-session-id-unknown"   # named, but no profile database knows that id
UNRESOLVED = "none"                       # the gateway named no session id: nothing to look up

GATEWAY_SESSION_ID = re.compile(r"^local-[0-9a-f]{16,80}$")
PID_SKEW_S = 2.0      # slack between a process start (jiffies since boot) and a lease's wall clock

# Columns the resolver reads. `description`/`display_name` vary between Hermes versions, so they are
# read separately and tolerated as missing.
_SESSION_SQL = "SELECT id, title, source FROM sessions WHERE id IN ({marks})"
_BY_KEY_SQL = ("SELECT id, title, source FROM sessions WHERE session_key=? "
               "ORDER BY (ended_at IS NULL) DESC, started_at DESC LIMIT 1")
_LEASE_SQL = "SELECT conversation_id, holder, acquired_at FROM session_turn_leases WHERE conversation_id IN ({marks})"


def _cap(value: Any, limit: int) -> str | None:
    return value[:limit] if isinstance(value, str) and value.strip() else None


def gateway_session_id(value: Any) -> str | None:
    """The session id out of a managed gateway entry's `session` field, or None.

    Chat entries carry a chat key (`agent:<profile>:<platform>:<chat_type>:<id>`); a managed unit carries
    the **id of the session it runs** (`local-<hex>`, the primary key of `sessions`) and no chat identity.
    Only that exact shape is accepted here -- a chat key is never read as an id.
    """
    return value if isinstance(value, str) and GATEWAY_SESSION_ID.match(value) else None


def candidate_profiles() -> list[str]:
    """Profile homes a managed session id may live in: `default` first, then every profile directory not ignored.

    Deliberately the same home the rest of this module reads (`hermes_home()`): a list derived from a
    different source than the databases it names could drift apart.
    """
    names = ["default"]
    try:
        names += sorted(d.name for d in (hermes_home() / "profiles").iterdir()
                        if d.is_dir() and not d.name.startswith("."))
    except OSError:                    # no profiles directory: the default home is all there is
        pass
    ignore = ignored_profiles()
    return [n for n in names if n not in ignore]


def _boot_time() -> float | None:
    """Boot time (epoch seconds) from `/proc/stat`, the base `/proc/<pid>/stat` counts from."""
    try:
        for line in Path("/proc/stat").read_text().splitlines():
            if line.startswith("btime "):
                return float(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return None


def proc_started_at(pid: Any) -> tuple[bool, float | None]:
    """`(does this process exist, when did it start)` -- read from `/proc`, never guessed.

    The start time is what makes a pid usable for an attribution: pids are reused, start times are not.
    Both values degrade to `(False, None)` / `(True, None)` instead of raising.
    """
    try:
        number = int(pid)
    except (TypeError, ValueError):
        return False, None
    try:
        stat = Path(f"/proc/{number}/stat").read_text()
    except OSError:
        return False, None
    try:
        # `comm` may contain spaces and parentheses, so field 1 ends at the LAST ")"
        ticks = int(stat.rsplit(")", 1)[1].split()[19])      # field 22 of /proc/<pid>/stat
        boot, hz = _boot_time(), os.sysconf("SC_CLK_TCK")
        return True, (boot + ticks / hz if boot is not None else None)
    except (IndexError, ValueError, OSError):
        return True, None


def read_leases(profile: str | None, session_ids: Iterable[str | None]) -> dict[str, dict[str, Any]]:
    """Turn leases of the given sessions of one profile, keyed by session id (the `holder` names the pid)."""
    ids = [s for s in {str(s) for s in session_ids if s}][:500]
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    rows = _fetch(profile, _LEASE_SQL.format(marks=marks), tuple(ids))
    return {str(r["conversation_id"]): r for r in rows}


def read_lease(profile: str | None, session_id: str | None) -> dict[str, Any] | None:
    """The turn lease of one session, or None (no lease, old schema, no database: all the same answer)."""
    if not session_id:
        return None
    return read_leases(profile, [session_id]).get(str(session_id))


def holder_pid(holder: Any) -> int | None:
    """The pid out of a lease holder (`pid=1234:pidns=...:turn=...:platform=kanban`)."""
    if not isinstance(holder, str):
        return None
    for part in holder.split(":"):
        if part.startswith("pid="):
            try:
                return int(part[4:])
            except ValueError:
                return None
    return None


def blank() -> dict[str, Any]:
    """The four fields, all None: the stable shape every list entry carries."""
    return dict.fromkeys(FIELDS)


def from_session_row(row: dict[str, Any] | None, profile: str | None = None) -> dict[str, Any]:
    """The four fields of one `sessions` row (state.db or a `sessions_list` entry) -- no extra read.

    Use this where the list query already delivered the row (the title it carries is as live as the
    query itself); use :class:`Resolver` when only an id or a session key is known.
    """
    row = row if isinstance(row, dict) else {}
    return {"session_id": _cap(row.get("id") or row.get("session_id"), 120),
            "profile": _cap(row.get("profile_name") or profile, 60),
            "session_title": _cap(row.get("title"), TITLE_CAP),
            "started_by": _cap(row.get("source") or row.get("started_by"), STARTED_BY_CAP)}


def _connect(profile: str | None) -> sqlite3.Connection | None:
    if not profile:
        return None
    try:
        path: Path = profile_home(profile) / "state.db"
    except Exception:  # noqa: BLE001 - unknown profile: nothing to resolve
        return None
    if not path.is_file():
        return None
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2.0)
        db.row_factory = sqlite3.Row
        return db
    except sqlite3.Error:
        return None


def _fetch(profile: str | None, sql: str, args: tuple) -> list[dict[str, Any]]:
    db = _connect(profile)
    if db is None:
        return []
    try:
        return [dict(r) for r in db.execute(sql, args)]
    except sqlite3.Error:      # older schema, locked database: no metadata beats a broken list
        return []
    finally:
        db.close()


def read_sessions(profile: str | None, session_ids: Iterable[str | None]) -> dict[str, dict[str, Any]]:
    """Live rows (id, title, source) of the given session ids of one profile, keyed by id."""
    ids = [s for s in {str(s) for s in session_ids if s}][:500]
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    rows = _fetch(profile, _SESSION_SQL.format(marks=marks), tuple(ids))
    return {str(r["id"]): r for r in rows}


def read_session_by_key(profile: str | None, session_key: str | None) -> dict[str, Any] | None:
    """The newest session of a gateway session key (`agent:<profile>:<platform>:...`), or None.

    One key can carry several sessions over time (resumes, hand-offs); the open one (no `ended_at`) wins,
    then the newest -- the gateway's `active-work` entry names a key, not an id.
    """
    if not session_key:
        return None
    rows = _fetch(profile, _BY_KEY_SQL, (session_key,))
    return rows[0] if rows else None


def profile_from_session_key(session_key: str | None) -> str | None:
    """The profile of a gateway session key (`agent:<profile>:<platform>:<chat_type>:<id>`).

    `agent:main:` is the default profile; a profile literally named "main" is written `agent:main~:`.
    """
    if not isinstance(session_key, str) or not session_key.startswith("agent:"):
        return None
    parts = session_key.split(":")
    if len(parts) < 2 or not parts[1]:
        return None
    return "default" if parts[1] == "main" else ("main" if parts[1] == "main~" else parts[1][:60])


class Resolver:
    """Request-scoped resolver. Create one per tool call; it does not outlive the answer it enriches.

    The titles it holds are only remembered for the lifetime of this object, so nothing goes stale
    between two calls: a title changed in the database is read again next time.
    """

    def __init__(self, settings=None):
        self.settings = settings
        self._by_id: dict[tuple[str | None, str], dict[str, Any]] = {}
        self._by_key: dict[tuple[str | None, str], dict[str, Any] | None] = {}
        self._sid_profile: dict[str, str] = {}       # managed: session id -> the profile home holding it
        self._sid_missing: set[str] = set()           # ... and the ids no home knows (one pass, then done)
        self._lease: dict[tuple[str | None, str], dict[str, Any] | None] = {}
        self._candidates: list[str] | None = None

    # -- live reads ----------------------------------------------------------------------------------
    def prime(self, profile: str | None, session_ids: Iterable[str | None]) -> None:
        """Read many sessions of one profile in a single query (the list case)."""
        ids = [s for s in session_ids if s and (profile, str(s)) not in self._by_id]
        for sid, row in read_sessions(profile, ids).items():
            self._by_id[(profile, sid)] = row

    def session_row(self, profile: str | None, session_id: str | None) -> dict[str, Any] | None:
        if not session_id:
            return None
        key = (profile, str(session_id))
        if key not in self._by_id:
            self._by_id[key] = read_sessions(profile, [session_id]).get(str(session_id), {})
        return self._by_id[key] or None

    def session_key_row(self, profile: str | None, session_key: str | None) -> dict[str, Any] | None:
        if not session_key:
            return None
        key = (profile, str(session_key))
        if key not in self._by_key:
            self._by_key[key] = read_session_by_key(profile, session_key)
        return self._by_key[key]

    def watch(self, run_id: str | None) -> dict[str, Any] | None:
        """The inbox watch record of a run hermcp started (`session_id`, `client`, `key_name`), or None."""
        if not run_id or self.settings is None:
            return None
        try:
            from .inbox_store import get_inbox
            return get_inbox(self.settings).get_watch(run_id)
        except Exception:  # noqa: BLE001 - no inbox database: the id simply stays unknown
            return None

    # -- managed units: a session id, no chat key ----------------------------------------------------
    def profiles(self) -> list[str]:
        """The profile homes a managed session id may live in (`default` first), read once per request."""
        if self._candidates is None:
            self._candidates = candidate_profiles()
        return self._candidates

    def prime_managed(self, session_ids: Iterable[Any]) -> None:
        """Look up every managed session id of a list in ONE query per profile (the list case).

        A managed unit names its session but not its profile, so the id has to be searched. Doing that
        per row would open one database per row; this walks the profile homes once for the whole list.
        """
        wanted = [s for s in {gateway_session_id(v) for v in session_ids}
                  if s and s not in self._sid_profile and s not in self._sid_missing]
        if not wanted:
            return
        for profile in self.profiles():
            rest = [s for s in wanted if s not in self._sid_profile]
            found = read_sessions(profile, rest)
            for sid, row in found.items():
                self._by_id[(profile, sid)] = row
                self._sid_profile.setdefault(sid, profile)
            leases = read_leases(profile, found)
            for sid in found:
                self._lease[(profile, sid)] = leases.get(sid)
        for sid in wanted:
            if sid not in self._sid_profile:
                self._sid_missing.add(sid)

    def _lease_of(self, profile: str | None, session_id: str) -> dict[str, Any] | None:
        key = (profile, str(session_id))
        if key not in self._lease:
            self._lease[key] = read_lease(profile, session_id)
        return self._lease[key]

    def _find_managed(self, session_id: str) -> tuple[str | None, dict[str, Any] | None]:
        """(profile, session row) of a managed session id -- one pass over the homes, then remembered."""
        known = self._sid_profile.get(session_id)
        if known is not None:
            return known, self.session_row(known, session_id)
        if session_id in self._sid_missing:
            return None, None
        for profile in self.profiles():
            row = self.session_row(profile, session_id)
            if row:
                self._sid_profile[session_id] = profile
                return profile, row
        self._sid_missing.add(session_id)
        return None, None

    def verify_pid(self, profile: str | None, session_id: str, pid: Any) -> bool | None:
        """Is the process the gateway names really the one running this session?

        Positive evidence only: the session's turn lease names the same pid, that process exists, and it
        started no later than the lease was taken -- a reused pid would have started *after* it, which is
        exactly the mix-up this guards against. `False` is a contradiction (another pid, the process is
        gone, or it started after the lease), `None` means "cannot be checked" (no lease, no `/proc`, no
        pid). It is never a guess, and an unflagged `True` is never handed out on a hunch.
        """
        lease = self._lease_of(profile, session_id)
        leased = holder_pid((lease or {}).get("holder"))
        if leased is None:
            return None
        try:
            given = int(pid)
        except (TypeError, ValueError):
            return None
        if leased != given:
            return False
        exists, started = proc_started_at(given)
        if not exists:
            return False
        acquired = (lease or {}).get("acquired_at")
        if started is None or not isinstance(acquired, (int, float)):
            return None
        return started <= float(acquired) + PID_SKEW_S

    def managed(self, row: dict[str, Any], *, session_id: Any = None, pid: Any = None) -> dict[str, Any]:
        """Merge a managed unit's context into its `agents_live` row (in place, and return it).

        A managed unit (kanban worker, admission worker) is started by Hermes itself, so the gateway hands
        out one thing: the id of the session it runs. From that id the profile, the live title and the door
        it came through follow. `resolution_source` says how far that went, `pid_verified` whether the
        process the gateway names is really the one running the session.
        """
        sid = gateway_session_id(session_id)
        if sid is None:
            for name in FIELDS:
                row[name] = None
            row["resolution_source"] = UNRESOLVED
            row["pid_verified"] = None
            return row
        profile, session = self._find_managed(sid)
        meta = from_session_row(session, profile)
        meta["session_id"] = _cap(sid, 120)
        for name in FIELDS:
            row[name] = meta[name]
        row["resolution_source"] = RESOLVED if session else UNKNOWN_SESSION
        row["pid_verified"] = self.verify_pid(profile, sid, pid) if session else None
        return row

    # -- the answer ----------------------------------------------------------------------------------
    def session(self, profile: str | None, session_id: str | None = None, *,
                session_key: str | None = None, started_by: Any = _UNSET) -> dict[str, Any]:
        """The four fields for one session, by id or by gateway session key.

        Without a profile the key's own profile is used (`agent:<profile>:...`), so a caller that only
        has the gateway entry still gets the title.
        """
        if profile is None and session_key:
            profile = profile_from_session_key(session_key)
        row = self.session_row(profile, session_id)
        if row is None and session_key:
            row = self.session_key_row(profile, session_key)
        out = from_session_row(row, profile)
        if session_id:
            out["session_id"] = _cap(session_id, 120)
        if started_by is not _UNSET and started_by:
            out["started_by"] = _cap(str(started_by), STARTED_BY_CAP)
        return out

    def run(self, profile: str | None, run_id: str | None = None, *,
            session_id: str | None = None, started_by: Any = _UNSET) -> dict[str, Any]:
        """The four fields for one run: session from the watch record when the caller has no id,
        `started_by` from the client that started it (then the hermcp key name, then the session's door)."""
        out = blank()
        return self.enrich(out, profile=profile, session_id=session_id, run_id=run_id,
                           started_by=started_by, overwrite=True)

    def enrich(self, row: dict[str, Any], *, profile: str | None = None, session_id: str | None = None,
               session_key: str | None = None, run_id: str | None = None, started_by: Any = _UNSET,
               overwrite: bool = False) -> dict[str, Any]:
        """Merge the four fields into an existing list entry (in place, and return it).

        Existing keys are kept unless `overwrite`: the tools' own vocabulary stays, the four standard
        fields are added next to it. Present-but-empty fields are filled even without `overwrite`.
        """
        prof = profile if profile is not None else row.get("profile")
        w = self.watch(run_id) or {}
        sid = session_id or w.get("session_id")
        meta = self.session(prof, sid, session_key=session_key)
        if started_by is not _UNSET and started_by:
            meta["started_by"] = _cap(str(started_by), STARTED_BY_CAP)
        else:
            # the authenticated key is the stable identity (one key per client, see the session plan); the
            # detected client ("claude-code") stays visible in the row's own `client` field
            meta["started_by"] = _cap(w.get("key_name") or w.get("client"), STARTED_BY_CAP) or meta["started_by"]
        for name in FIELDS:
            if overwrite or row.get(name) is None:
                row[name] = meta[name]
        return row
