"""Locate the Hermes checkout and install the optional hermcp auto-restart units.

Hermes writes `install-stamp.json` into its checkout at the end of every update. The auto-restart
units (deploy/systemd/hermcp-restart.*) watch that file, so they need the checkout directory. It is
not hardcoded here: it is derived from the `hermes` executable of the running install.

Resolution order (first hit wins), see `resolve_agent_dir`:
  1. HERMCP_HERMES_AGENT_DIR (explicit override)
  2. /usr/local/lib/hermes-agent, if it exists (the reference install, unchanged behaviour)
  3. detected from the `hermes` executable: where its interpreter imports `hermes_cli` from, else the
     venv layout of the resolved executable (<checkout>/venv/bin/hermes)
  4. /usr/local/lib/hermes-agent as the last resort (the caller then finds no stamp and says so)
"""
from __future__ import annotations

import functools
import os
import shutil
import subprocess
import sys
from pathlib import Path

STAMP_NAME = "install-stamp.json"
LEGACY_AGENT_DIR = Path("/usr/local/lib/hermes-agent")
UNIT_NAMES = ("hermcp-restart.path", "hermcp-restart.service")

_PKG = Path(__file__).resolve().parent
_SYSTEMD_DIR = _PKG / "deploy" / "systemd"
_SPEC_SNIPPET = "import importlib.util as u; s = u.find_spec('hermes_cli'); print(s.origin if s and s.origin else '')"


# -- locating the Hermes checkout -----------------------------------------------------------------------
def _candidate_bins() -> list[Path]:
    """`hermes` executables to ask, most specific first. Deliberately independent of agent_dir()."""
    from .settings import load_settings
    cands: list[Path] = []
    cfg = load_settings().hermes_bin
    if cfg:
        cands.append(Path(cfg))
    cands.append(Path(sys.executable).parent / "hermes")
    found = shutil.which("hermes")
    if found:
        cands.append(Path(found))
    seen: set[Path] = set()
    out: list[Path] = []
    for c in cands:
        if c.exists() and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _checkout(root: Path) -> Path | None:
    return root if (root / "hermes_cli").is_dir() else None


def _interpreter_of(binary: Path) -> Path | None:
    """The Python behind a `hermes` launcher: its shebang, else a `python` next to it."""
    try:
        with open(binary, "rb") as fh:
            first = fh.readline(512).decode("utf-8", errors="replace").strip()
    except OSError:
        first = ""
    if first.startswith("#!"):
        tokens = first[2:].split()
        if tokens:
            exe = tokens[0]
            if Path(exe).name == "env" and len(tokens) > 1:
                exe = shutil.which(tokens[1]) or ""
            if exe and Path(exe).exists():
                return Path(exe)
    for name in ("python", "python3"):
        sib = binary.parent / name
        if sib.exists():
            return sib
    return None


def _from_import(binary: Path) -> Path | None:
    """Ask the install's own interpreter where `hermes_cli` lives (find_spec: nothing is imported)."""
    py = _interpreter_of(binary)
    if py is None:
        return None
    try:
        cp = subprocess.run([str(py), "-I", "-c", _SPEC_SNIPPET], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    lines = cp.stdout.strip().splitlines() if cp.returncode == 0 else []
    origin = lines[-1].strip() if lines else ""
    if not origin:
        return None
    return _checkout(Path(origin).resolve().parent.parent)


def _from_symlink(binary: Path) -> Path | None:
    """Venv layout of the resolved launcher: <checkout>/venv/bin/hermes (or .venv)."""
    real = binary.resolve()
    if real.parent.name == "bin" and real.parent.parent.name in ("venv", ".venv"):
        return _checkout(real.parent.parent.parent)
    return None


@functools.lru_cache(maxsize=1)
def detect_agent_dir() -> Path | None:
    """Checkout of the Hermes install that the `hermes` executable belongs to, or None."""
    for binary in _candidate_bins():
        for finder in (_from_import, _from_symlink):
            found = finder(binary)
            if found:
                return found
    return None


def resolve_agent_dir() -> Path:
    env = os.environ.get("HERMCP_HERMES_AGENT_DIR")
    if env:
        return Path(env)
    if LEGACY_AGENT_DIR.is_dir():
        return LEGACY_AGENT_DIR
    return detect_agent_dir() or LEGACY_AGENT_DIR


# -- the auto-restart units -----------------------------------------------------------------------------
def _unit_value(v: str) -> str:
    return v.replace("%", "%%")   # systemd specifiers: a literal % must be written %%


def render_units(stamp: Path, script: Path, python: str, systemctl: str) -> dict[str, str]:
    """Fill deploy/systemd/*.template with the paths of THIS install."""
    subs = {"@STAMP@": _unit_value(str(stamp)), "@SCRIPT@": _unit_value(str(script)),
            "@PYTHON@": _unit_value(python), "@SYSTEMCTL@": _unit_value(systemctl)}
    out: dict[str, str] = {}
    for name in UNIT_NAMES:
        text = (_SYSTEMD_DIR / f"{name}.template").read_text(encoding="utf-8")
        for key, val in subs.items():
            text = text.replace(key, val)
        out[name] = text
    return out


def plan_restart_units(agent_dir: Path | None = None, python: str | None = None,
                       systemctl: str | None = None) -> tuple[dict[str, str], list[str]]:
    """(rendered units, messages). Units are empty when auto-restart cannot be set up here."""
    agent = agent_dir or resolve_agent_dir()
    stamp = agent / STAMP_NAME
    if not stamp.is_file():
        return {}, [f"auto-restart: no {STAMP_NAME} at {stamp}, skipped (set HERMCP_HERMES_AGENT_DIR to the Hermes "
                    "checkout, or pass --no-auto-restart to silence this)"]
    script = _SYSTEMD_DIR / "hermcp-restart.sh"
    if not script.is_file():
        return {}, [f"auto-restart: {script} is missing, skipped"]
    if not os.access(script, os.X_OK):
        return {}, [f"auto-restart: {script} is not executable, skipped (chmod +x it)"]
    if any(ch.isspace() for ch in str(script)):
        return {}, [f"auto-restart: the path {script} contains whitespace, skipped (systemd ExecStart needs a plain path)"]
    units = render_units(stamp, script, python or sys.executable,
                         systemctl or shutil.which("systemctl") or "/usr/bin/systemctl")
    return units, [f"auto-restart: watching {stamp}"]


def install_restart_units(unit_dir: Path, agent_dir: Path | None = None, python: str | None = None,
                          systemctl: str | None = None) -> tuple[list[str], list[str]]:
    """Write the rendered units into unit_dir. Returns (names written, messages).

    A unit that is already a symlink (`systemctl link` of the repo's static files, the manual route) is left
    alone: writing through it would edit a tracked file of the repo.
    """
    units, msgs = plan_restart_units(agent_dir, python, systemctl)
    written: list[str] = []
    for name, text in units.items():
        dest = unit_dir / name
        if dest.is_symlink():
            msgs.append(f"auto-restart: {dest} is a symlink (systemctl link), left as is")
            continue
        if dest.exists() and dest.read_text(encoding="utf-8") == text:
            msgs.append(f"auto-restart: {dest} already up to date")
            continue
        dest.write_text(text, encoding="utf-8")
        written.append(name)
        msgs.append(f"installed {dest}")
    return written, msgs
