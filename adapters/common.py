"""Shared adapter plumbing: locate Hermes, profile homes, `hermes` subprocess, in-process scopes.

Isolation rule: every adapter call goes through here so a Hermes upgrade that breaks one
internal only yields an `adapter_error` for that tool.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Iterator

from .. import meta
from ..registry import AdapterError, ToolError
from ..settings import hermes_home, load_settings

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_path_lock = threading.Lock()
_inproc_lock = threading.RLock()   # serialises in-process Hermes calls that touch HERMES_HOME overrides/stdout


def strip_ansi(s: str) -> str:
    return ANSI.sub("", s)


# -- locating Hermes --------------------------------------------------------------------
def agent_dir() -> Path:
    """Hermes checkout: HERMCP_HERMES_AGENT_DIR, the reference path if it exists, else detected from the
    `hermes` executable (see restart_units.py)."""
    from ..restart_units import resolve_agent_dir
    return resolve_agent_dir()


def ensure_hermes_importable() -> None:
    """The venv's editable finder misses top-level modules (e.g. hermes_yaml), so make the checkout
    importable explicitly. Appended (never prepended): our own names must not be shadowed."""
    d = str(agent_dir())
    # hermes_bootstrap may os.execv() the whole process into Hermes' managed interpreter when it
    # considers the install "not current" (self-update tail). That would replace this server with a
    # python lacking our deps, so opt out before any in-process Hermes import.
    os.environ.setdefault("HERMES_DISABLE_LAZY_INSTALLS", "1")
    with _path_lock:
        if d not in sys.path and Path(d).is_dir():
            sys.path.append(d)


def hermes_bin() -> str:
    cfg = load_settings().hermes_bin
    if cfg:
        return cfg
    for cand in (Path(sys.executable).parent / "hermes", agent_dir() / "venv" / "bin" / "hermes"):
        if cand.exists():
            return str(cand)
    found = shutil.which("hermes")
    if not found:
        raise AdapterError("hermes executable not found (set HERMCP_HERMES_BIN)")
    return found


# -- profiles ---------------------------------------------------------------------------
def profile_home(profile: str) -> Path:
    """Home directory of a profile. 'default' is HERMES_HOME itself."""
    home = hermes_home()
    if profile in ("", "default"):
        return home
    p = home / "profiles" / profile
    if not p.is_dir():
        raise ToolError("not_found", f"profile {profile!r} does not exist")
    return p


def list_profile_names() -> list[str]:
    root = hermes_home() / "profiles"
    names = ["default"]
    if root.is_dir():
        names += sorted(d.name for d in root.iterdir()
                        if d.is_dir() and not d.name.startswith(".") and d.name != "default")
    return names


def invalidate_hermes_caches() -> list[str]:
    """Drop every Hermes module-level cache that could outlive a change on disk or leak between profiles.

    Hermes keeps path-keyed, mtime-validated caches (config, raw config) and a skills catalog cache with a
    30 s TTL that only notices directory-level changes, not in-place SKILL.md edits. A long-lived server
    must not trust them, so each in-process scope starts and ends with all of them empty. Returns the names
    of the caches that were cleared (diagnostics).
    """
    cleared: list[str] = []
    mod = sys.modules.get("hermes_cli.config")
    if mod is not None:
        for name in ("_LOAD_CONFIG_CACHE", "_RAW_CONFIG_CACHE"):
            cache = getattr(mod, name, None)
            if isinstance(cache, dict):
                cache.clear()
                cleared.append(f"hermes_cli.config.{name}")
    sk = sys.modules.get("tools.skills_tool")
    if sk is not None:
        for name in ("_SKILLS_CACHE",):
            cache = getattr(sk, name, None)
            if isinstance(cache, dict):
                cache.clear()
                cleared.append(f"tools.skills_tool.{name}")
    return cleared


@contextlib.contextmanager
def hermes_scope(profile: str) -> Iterator[Path]:
    """Run in-process Hermes code with its home pointing at *profile*. Uses Hermes' own contextvar
    override (thread-safe) rather than mutating os.environ, which other request threads read.
    All Hermes module caches are cleared on entry and exit (see invalidate_hermes_caches)."""
    ensure_hermes_importable()
    home = profile_home(profile)
    try:
        import hermes_constants
    except ImportError as exc:
        raise AdapterError(f"cannot import hermes_constants: {exc}") from exc
    with _inproc_lock:     # stdout capture + module-level caches in Hermes are not re-entrant across threads
        meta.note_source("live-inproc")
        invalidate_hermes_caches()
        token = hermes_constants.set_hermes_home_override(home)
        try:
            yield home
        finally:
            with contextlib.suppress(Exception):
                hermes_constants.reset_hermes_home_override(token)
            invalidate_hermes_caches()


@contextlib.contextmanager
def captured() -> Iterator[io.StringIO]:
    """Capture stdout/stderr of Hermes helpers that print (config set etc.)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        yield buf


def call_inproc(fn, *a, **kw):
    """Call a Hermes function, translating SystemExit/ImportError into adapter errors."""
    try:
        return fn(*a, **kw)
    except SystemExit as exc:
        raise ToolError("rejected", f"hermes refused the operation (exit {exc.code})") from None
    except ImportError as exc:
        raise AdapterError(f"hermes internal import failed: {exc}") from exc
    except ToolError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise AdapterError(f"{type(exc).__name__}: {exc}") from exc


# -- subprocess -------------------------------------------------------------------------
def run_hermes(argv: list[str], profile: str | None = None, timeout: float = 30.0,
               stdin: str | None = None) -> subprocess.CompletedProcess:
    """Run `hermes [-p profile] <argv>` with a timeout. Returns CompletedProcess with ANSI stripped."""
    cmd = [hermes_bin()]
    if profile and profile != "default":
        cmd += ["-p", profile]
    cmd += argv
    meta.note_source("subprocess")
    env = {**os.environ, "NO_COLOR": "1", "TERM": "dumb", "HERMES_HOME": str(hermes_home())}
    try:
        cp = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, input=stdin, env=env,
                            stdin=None if stdin is not None else subprocess.DEVNULL, cwd=str(hermes_home()))
    except subprocess.TimeoutExpired:
        raise AdapterError(f"`hermes {' '.join(argv[:3])}` timed out after {timeout:.0f}s") from None
    except OSError as exc:
        raise AdapterError(f"cannot run hermes: {exc}") from exc
    cp.stdout, cp.stderr = strip_ansi(cp.stdout or ""), strip_ansi(cp.stderr or "")
    return cp


def cli_text(argv: list[str], profile: str | None = None, timeout: float = 30.0, ok_codes=(0,)) -> str:
    cp = run_hermes(argv, profile, timeout)
    if cp.returncode not in ok_codes:
        msg = (cp.stderr.strip() or cp.stdout.strip() or f"exit {cp.returncode}")[-1500:]
        raise ToolError("cli_error", f"`hermes {' '.join(argv)}` failed: {msg}", exit_code=cp.returncode)
    return cp.stdout if cp.stdout.strip() else cp.stderr


def cli_json(argv: list[str], profile: str | None = None, timeout: float = 30.0) -> Any:
    out = cli_text(argv, profile, timeout)
    start = min([i for i in (out.find("{"), out.find("[")) if i >= 0], default=-1)
    if start < 0:
        raise AdapterError(f"`hermes {' '.join(argv)}` returned no JSON", output=out[:500])
    try:
        return json.loads(out[start:])
    except ValueError as exc:
        raise AdapterError(f"`hermes {' '.join(argv)}` returned invalid JSON: {exc}", output=out[:500]) from exc


def plan_cli(ctx, summary: str, argv: list[str]) -> dict[str, Any]:
    """dry_run payload for a subprocess-backed write: the exact command that would run."""
    prof = [] if ctx.profile == "default" else ["-p", ctx.profile]
    return ctx.plan(summary, would_run=["hermes", *prof, *argv])


# -- fresh-process probe ------------------------------------------------------------------------------
PROBE = Path(__file__).resolve().parent.parent / "probes" / "_probe.py"


def run_probe(profile: str, op: str, args: dict | None = None, timeout: float = 90.0) -> dict[str, Any]:
    """Answer one read-only question in a brand-new interpreter pinned to *profile*'s HERMES_HOME.

    No module cache, singleton or environment variable of this long-lived server can reach the answer.
    The interpreter is the one running hermcp (it has Hermes' dependencies); `-I` keeps the plugin
    directory off sys.path so Hermes' own `tools` package is never shadowed.
    """
    home = profile_home(profile)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("HERMES_PLATFORM", "HERMES_SESSION"))}
    env.update({"HERMES_HOME": str(home), "NO_COLOR": "1", "HERMES_DISABLE_LAZY_INSTALLS": "1"})
    cmd = [sys.executable, "-I", str(PROBE), str(agent_dir()), op, json.dumps(args or {})]
    meta.note_source("subprocess")
    try:
        cp = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env, cwd=str(home),
                            stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise AdapterError(f"probe {op} for profile {profile!r} timed out after {timeout:.0f}s") from None
    except OSError as exc:
        raise AdapterError(f"cannot run probe: {exc}") from exc
    if cp.returncode != 0:
        raise AdapterError(f"probe {op} for profile {profile!r} failed", exit_code=cp.returncode,
                           stderr=(cp.stderr or "")[-600:])
    out = cp.stdout.strip().splitlines()
    try:
        return json.loads(out[-1])
    except (ValueError, IndexError) as exc:
        raise AdapterError(f"probe {op} returned invalid output: {exc}", output=cp.stdout[-300:]) from exc
