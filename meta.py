"""Provenance and freshness block (`_meta`) attached to every tool result.

Why: a client (Claude) must be able to tell where an answer came from and how old it is. The block is an
ADDITIONAL top-level field, so old clients that only read known fields keep working.

  source        primary origin: live-inproc | subprocess | file | gateway-state | socket | computed
  sources       every origin touched while producing the answer (ordered, unique)
  generated_at  unix time the answer was produced (never replayed from a cache: hermcp has no result cache)
  profile       profile the call really ran against (None for global tools)
  profile_home  directory that profile resolved to
  gateway_pid / gateway_alive / gateway_started_at   from gateway_state.json (the running gateway, read now)
  hermes_version  version of the Hermes checkout on disk (read now); gateway_code_version = what the gateway runs
  files         [{path, mtime, age_s}] for every file the answer was read from
  hermcp        {version, started_at, uptime_s}: how long this server process has been up (module state age)

Collection is done with a ContextVar so adapters report their origin without every tool being touched.
"""
from __future__ import annotations

import json
import os
import time
import contextvars
from contextvars import ContextVar
from pathlib import Path
from typing import Any

PROCESS_STARTED = time.time()
_MAX_FILES = 12


class Collector:
    __slots__ = ("sources", "files", "notes")

    def __init__(self) -> None:
        self.sources: list[str] = []
        self.files: dict[str, float | None] = {}
        self.notes: dict[str, Any] = {}


_current: ContextVar[Collector | None] = ContextVar("hermcp_meta_collector", default=None)


def begin() -> tuple[Collector, Any]:
    col = Collector()
    return col, _current.set(col)


def end(token: Any) -> None:
    try:
        _current.reset(token)
    except (ValueError, LookupError):
        pass


def note_source(name: str) -> None:
    col = _current.get()
    if col is not None and name not in col.sources:
        col.sources.append(name)


def note_file(path: str | Path, source: str = "file") -> None:
    """Record that data was read from `path` (its mtime is reported)."""
    col = _current.get()
    if col is None:
        return
    note_source(source)
    p = str(path)
    if p not in col.files and len(col.files) < _MAX_FILES:
        try:
            col.files[p] = os.stat(p).st_mtime
        except OSError:
            col.files[p] = None


def note(key: str, value: Any) -> None:
    col = _current.get()
    if col is not None:
        col.notes[key] = value


def read_text(path: str | Path, source: str = "file") -> str:
    """Read a text file and record it as a source with its mtime."""
    note_file(path, source)
    return Path(path).read_text(encoding="utf-8", errors="replace")


def read_json(path: str | Path, source: str = "file") -> Any:
    return json.loads(read_text(path, source))


# -- environment facts, read live on every call (cheap: two small files) ---------------------------------


def hermes_stamp() -> dict[str, Any]:
    """Version facts of the Hermes checkout on disk, read now from its install stamp."""
    from .adapters.common import agent_dir
    try:
        st = json.loads((agent_dir() / "install-stamp.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return {"version": st.get("displayVersion") or st.get("baseVersion"), "commit": st.get("commit"),
            "built_at": st.get("builtAt")}


def _iso_epoch(v: Any) -> float | None:
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def hermes_version_on_disk() -> str | None:
    return hermes_stamp().get("version")


def _pid_alive(pid: Any) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def gateway_head() -> dict[str, Any]:
    from .settings import hermes_home
    p = hermes_home() / "gateway_state.json"
    try:
        st = json.loads(p.read_text())
    except (OSError, ValueError):
        return {"gateway_pid": None, "gateway_alive": None, "gateway_state_file": "missing or unreadable"}
    pid = st.get("pid")
    return {"gateway_pid": pid, "gateway_alive": _pid_alive(pid), "gateway_state": st.get("gateway_state"),
            "gateway_code_version": st.get("code_version"), "gateway_state_updated_at": st.get("updated_at")}


def build(profile: str | None, col: Collector | None, started_at: float, *, version: str) -> dict[str, Any]:
    from .adapters.common import profile_home
    now = time.time()
    col = col or Collector()
    sources = col.sources or ["computed"]
    out: dict[str, Any] = {"source": sources[0], "sources": list(sources), "generated_at": round(now, 3),
                           "duration_ms": round((now - started_at) * 1000, 1), "profile": profile}
    if profile:
        try:
            out["profile_home"] = str(profile_home(profile))
        except Exception:  # noqa: BLE001 - meta must never fail a call
            out["profile_home"] = None
    out.update(gateway_head())
    stamp = hermes_stamp()
    out["hermes_version"] = stamp.get("version")
    out["hermes_commit"] = (stamp.get("commit") or "")[:12] or None
    built = _iso_epoch(stamp.get("built_at"))
    if built:
        # modules imported by this long-lived process predate the Hermes build on disk -> in-process answers may
        # come from old code; restart hermcp. (subprocess-backed tools are unaffected)
        out["hermcp_older_than_hermes_build"] = PROCESS_STARTED < built
        if out["hermcp_older_than_hermes_build"]:
            # Visible in every _meta (hermes_health, orchestration_overview, ...): the warning survives a Hermes update
            # until hermcp itself restarts, because the flag is recomputed from the process start time on every call.
            out["warnings"] = [f"hermcp process started before the Hermes build on disk (built {stamp.get('built_at')}): "
                               "in-process answers may come from old code. Restart hermcp.service with the hermcp_restart tool "
                               "(admin, confirm=true); hermcp-restart.path does it after a Hermes update."]
    if col.files:
        out["files"] = [{"path": f, "mtime": m, "age_s": None if m is None else round(now - m, 1)}
                        for f, m in col.files.items()]
    out["hermcp"] = {"version": version, "started_at": round(PROCESS_STARTED, 1),
                     "uptime_s": round(now - PROCESS_STARTED, 1)}
    notes = dict(col.notes)
    lst = notes.pop("list", None)
    if lst:
        out["list"] = lst              # totals of a list tool: what the default view showed and what it hid
    if notes:
        out["notes"] = notes
    return out


def submit(executor, fn, *args, **kwargs):
    """executor.submit that carries the collector into the worker thread (each submit gets its own context copy)."""
    return executor.submit(contextvars.copy_context().run, lambda: fn(*args, **kwargs))
