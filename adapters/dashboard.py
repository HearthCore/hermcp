"""Dashboard control: find out HOW the Hermes web dashboard runs, report on it and restart it.

Detection order (native ways first, custom afterwards; the first stage that identifies a manager wins):

  0. config     explicit override (mode systemd|process), only evaluated when mode != auto
  1. hermes     `hermes dashboard --status`  (lists live dashboard/serve PIDs; informational, exit 0, no JSON)
  2. known_unit the unit name Hermes itself restarts after `hermes update` (hermes-dashboard.service), user scope
                first, then system scope: the order of Hermes' `_restart_managed_dashboard_service`
  3. custom_unit any other systemd service that provably owns a live dashboard PID (cgroup + MainPID)
  4. process    free-standing process: argv, cwd and environment NAMES are read from /proc -> start recipe

Everything that touches the host goes through an `Env` object, so tests drive every stage with canned
systemctl / `hermes dashboard --status` / /proc data. Secrets: environment VALUES are never returned (only names),
commands are scrubbed with the central redactor before they leave this module.
"""
from __future__ import annotations

import ast
import contextlib
import json
import os
import re
import shlex
import signal
import socket
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import meta
from ..redact import redact_text
from ..settings import data_dir

KNOWN_UNIT = "hermes-dashboard.service"      # Hermes: hermes_cli/main_dashboard.py::_DASHBOARD_SYSTEMD_UNIT
DEFAULT_PORT = 9119                           # Hermes: `hermes dashboard --port` default
STAGES = ("config", "hermes", "known_unit", "custom_unit", "process")
MODES = ("auto", "systemd", "process")
SCOPES = ("system", "user")
UNIT_RE = re.compile(r"^[A-Za-z0-9@._-]{1,80}\.service$")
SHOW_PROPS = ("LoadState,ActiveState,SubState,MainPID,FragmentPath,ExecStart,MemoryMax,MemoryCurrent,CPUUsageNSec,"
              "Restart,WorkingDirectory,NRestarts,ActiveEnterTimestamp,ControlGroup,UnitFileState")
ERROR_LINE = re.compile(r"Traceback \(most recent|\bERROR\b|\bCRITICAL\b|Address already in use|"
                        r"\bException\b|\bFATAL\b|exited with|Failed to start", re.I)
VERIFY_TIMEOUT = 45.0


# ---------------------------------------------------------------------------------------------------------------
# host access (replaced by a fake in tests)
# ---------------------------------------------------------------------------------------------------------------
class Env:
    """Everything the detector reads from or does to the machine."""

    def run(self, argv: list[str], timeout: float = 15.0) -> tuple[int, str, str]:
        meta.note_source("subprocess")
        try:
            cp = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL,
                                env={**os.environ, "NO_COLOR": "1", "TERM": "dumb", "HERMES_DISABLE_LAZY_INSTALLS": "1"})
        except FileNotFoundError:
            return 127, "", f"{argv[0]}: not found"
        except subprocess.TimeoutExpired:
            return 124, "", f"timed out after {timeout:.0f}s"
        except OSError as exc:
            return 126, "", str(exc)
        return cp.returncode, cp.stdout or "", cp.stderr or ""

    def hermes_bin(self) -> str:
        from .common import hermes_bin
        return hermes_bin()

    def proc_file(self, pid: int, name: str) -> bytes | None:
        try:
            return Path(f"/proc/{pid}/{name}").read_bytes()
        except OSError:
            return None

    def proc_link(self, pid: int, name: str) -> str | None:
        try:
            return os.readlink(f"/proc/{pid}/{name}")
        except OSError:
            return None

    def proc_pids(self) -> list[int]:
        out = []
        with contextlib.suppress(OSError):
            for n in os.listdir("/proc"):
                if n.isdigit():
                    out.append(int(n))
        return sorted(out)

    def pid_alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    def kill(self, pid: int, sig: int) -> None:
        os.kill(pid, sig)

    def popen_detached(self, argv: list[str], cwd: str, env: dict[str, str], log: Path) -> int:
        log.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(log), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            p = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=fd, stderr=fd,
                                 start_new_session=True, close_fds=True)
        finally:
            os.close(fd)
        return p.pid

    def listening(self, host: str, port: int) -> bool:
        try:
            with socket.create_connection((host, port), timeout=1.5):
                return True
        except OSError:
            return False

    def http_status(self, host: str, port: int, path: str) -> int | None:
        """Status code only, no redirects followed, no credentials sent, body never read."""
        import http.client
        try:
            c = http.client.HTTPConnection(host, port, timeout=4)
            c.request("GET", path, headers={"User-Agent": "hermcp-dashboard-check", "Accept": "*/*"})
            code = c.getresponse().status
            c.close()
            return code
        except (OSError, http.client.HTTPException):
            return None

    def euid(self) -> int:
        return os.geteuid()

    def self_pids(self) -> set[int]:
        pids = {os.getpid(), os.getppid()}
        gw = meta.gateway_head().get("gateway_pid")
        if isinstance(gw, int):
            pids.add(gw)
        return pids

    def sleep(self, s: float) -> None:
        time.sleep(s)

    def now(self) -> float:
        return time.time()

    def read_log(self, path: Path, tail: int = 4000) -> str:
        try:
            data = path.read_bytes()[-tail:]
        except OSError:
            return ""
        return data.decode("utf-8", "replace")

    def log_path(self) -> Path:
        return data_dir() / "dashboard-process.log"


# ---------------------------------------------------------------------------------------------------------------
# configuration (hermcp's own keys, never Hermes' config.yaml)
# ---------------------------------------------------------------------------------------------------------------
@dataclass
class DashboardConfig:
    mode: str = "auto"                 # auto | systemd | process
    unit: str = ""                     # systemd: unit name (mode=systemd)
    scope: str = "system"              # systemd: system | user
    command: list[str] = field(default_factory=list)   # process: argv of the start command
    cwd: str = ""                      # process: working directory
    port: int = 0                      # 0 = take it from the command line, else 9119
    source: str = "defaults"           # where the effective values came from (for the result)

    def explicit(self) -> bool:
        return self.mode != "auto"


def _clean(raw: dict[str, Any], src: str) -> tuple[DashboardConfig | None, str]:
    """(config, error). Unknown keys and bad values are reported, never guessed."""
    cfg = DashboardConfig(source=src)
    known = {"mode", "unit", "scope", "command", "cwd", "port"}
    extra = set(raw) - known
    if extra:
        return None, f"unknown key(s) {sorted(extra)} in {src} (allowed: {sorted(known)})"
    mode = str(raw.get("mode") or "auto").strip().lower()
    if mode not in MODES:
        return None, f"mode must be one of {list(MODES)}, got {mode!r} ({src})"
    cfg.mode = mode
    cfg.unit = str(raw.get("unit") or "").strip()
    scope = str(raw.get("scope") or "system").strip().lower()
    if scope not in SCOPES:
        return None, f"scope must be one of {list(SCOPES)}, got {scope!r} ({src})"
    cfg.scope = scope
    cmd = raw.get("command") or []
    if isinstance(cmd, str):
        try:
            cmd = shlex.split(cmd)
        except ValueError as exc:
            return None, f"command is not parseable ({exc}) in {src}"
    if not isinstance(cmd, list) or not all(isinstance(x, str) for x in cmd):
        return None, f"command must be a string or a list of strings ({src})"
    cfg.command = [str(x) for x in cmd]
    cfg.cwd = str(raw.get("cwd") or "").strip()
    try:
        cfg.port = int(raw.get("port") or 0)
    except (TypeError, ValueError):
        return None, f"port must be an integer ({src})"
    if cfg.mode == "systemd":
        if not UNIT_RE.match(cfg.unit):
            return None, f"mode=systemd needs unit=<name>.service ({src})"
    if cfg.mode == "process":
        if not cfg.command:
            return None, f"mode=process needs command ({src})"
        if not os.path.isabs(cfg.command[0]) and "/" in cfg.command[0]:
            return None, f"command[0] must be an absolute path or a bare program name ({src})"
        if not any(t in ("dashboard", "serve") for t in cfg.command[1:4]):
            return None, f"command must be a `hermes dashboard ...` start command ({src})"
        if cfg.cwd and not os.path.isabs(cfg.cwd):
            return None, f"cwd must be absolute ({src})"
    return cfg, ""


def load_config(profile: str = "default", env: dict[str, str] | None = None, path: Path | None = None
                ) -> tuple[DashboardConfig, str]:
    """Effective override for `profile`: process env / .env keys for the default profile, then the JSON file.

    Keys (all optional; nothing set = mode auto):
      HERMCP_DASHBOARD_MODE, _UNIT, _SCOPE, _COMMAND, _CWD, _PORT        defaults for every profile
      HERMCP_DASHBOARD_CONFIG (path) or plugin-data/hermcp/dashboard.json  {"default": {...}, "profiles": {"x": {...}}}
    Returns (config, error); a broken override gives error text and an auto config, so the status tool still works.
    """
    from ..settings import PKG_DIR, _read_dotenv, hermes_home
    merged = _read_dotenv(PKG_DIR / ".env")
    merged.update(os.environ if env is None else env)
    g = merged.get
    base: dict[str, Any] = {}
    for key in ("mode", "unit", "scope", "command", "cwd", "port"):
        v = g("HERMCP_DASHBOARD_" + key.upper(), "")
        if v != "":
            base[key] = v
    src = "env" if base else "defaults"
    chosen = dict(base)
    cfg_path = Path(g("HERMCP_DASHBOARD_CONFIG", "") or (hermes_home() / "plugin-data" / "hermcp" / "dashboard.json")) \
        if path is None else path
    err = ""
    try:
        text = cfg_path.read_text(encoding="utf-8")
    except OSError:
        text = ""
    if text.strip():
        try:
            doc = json.loads(text)
            if not isinstance(doc, dict):
                raise ValueError("top level must be an object")
            per = (doc.get("profiles") or {}).get(profile) if profile != "default" else None
            layer = doc.get("default") if isinstance(doc.get("default"), dict) else {}
            for part in (layer, per if isinstance(per, dict) else {}):
                if part:
                    chosen.update(part)
                    src = f"{cfg_path.name}" + (f"[profiles.{profile}]" if part is per else "[default]")
        except ValueError as exc:
            err = f"{cfg_path.name} is not valid JSON ({exc}); override ignored"
    cfg, e2 = _clean(chosen, src)
    if cfg is None:
        return DashboardConfig(source="invalid"), (err + "; " if err else "") + e2 + "; override ignored"
    return cfg, err


# ---------------------------------------------------------------------------------------------------------------
# parsing helpers
# ---------------------------------------------------------------------------------------------------------------
_STATUS_LINE = re.compile(r"^\s*PID\s+(\d+)\s+\[(dashboard|serve)\]:\s*(.*)$")
_SYS_ARGV = re.compile(r"sys\.argv\s*=\s*(\[[^\]]*\])")
_SECRET_FLAG = re.compile(r"(token|secret|passw|api[-_]?key|credential|auth|key)", re.I)


def hermes_args(argv: list[str]) -> list[str] | None:
    """The hermes sub-command and flags of a dashboard/serve launch: ['dashboard', '--port', '9119', ...].

    Handles both the plain form (`hermes dashboard ...`) and the python launcher Hermes uses for its entry
    point (`python -c '... sys.argv = ["-c", "dashboard", ...] ...'`). None when this is not a dashboard launch.
    """
    if not argv:
        return None
    if "-c" in argv[:6]:
        i = argv.index("-c")
        code = argv[i + 1] if i + 1 < len(argv) else ""
        if "hermes_cli" not in code and "hermes_bootstrap" not in code:
            return None
        m = _SYS_ARGV.search(code)
        if not m:
            return None
        try:
            lst = ast.literal_eval(m.group(1))
        except (ValueError, SyntaxError):
            return None
        args = [str(x) for x in lst[1:]]
    else:
        base = os.path.basename(argv[0])
        if not (base.startswith("hermes") or base.startswith("python")):
            return None
        rest = argv[1:]
        if base.startswith("python"):
            # `python -m hermes_cli.main dashboard ...` / `python .../hermes dashboard ...`
            if not any("hermes" in a for a in rest[:3]):
                return None
            rest = [a for a in rest if "hermes" not in a or a.startswith("-")]
        # skip global flags such as `-p profile`
        i = 0
        while i < len(rest) and rest[i].startswith("-") and rest[i] not in ("-h",):
            i += 2 if rest[i] in ("-p", "--profile") else 1
        args = rest[i:]
    if not args or args[0] not in ("dashboard", "serve"):
        return None
    if any(a in ("--status", "--stop", "register", "-h", "--help") for a in args[1:]):
        return None            # inspection / maintenance commands are not a running dashboard
    return args


def flag_value(args: list[str], flag: str) -> str | None:
    for i, a in enumerate(args):
        if a == flag and i + 1 < len(args):
            return args[i + 1]
        if a.startswith(flag + "="):
            return a.split("=", 1)[1]
    return None


def _port_host(args: list[str], default_port: int = DEFAULT_PORT) -> tuple[int, str]:
    try:
        port = int(flag_value(args, "--port") or default_port)
    except ValueError:
        port = default_port
    return port, (flag_value(args, "--host") or "127.0.0.1")


def probe_host(host: str) -> str:
    h = (host or "127.0.0.1").strip().strip("[]")
    return "127.0.0.1" if h in ("", "0.0.0.0", "::") else h


def scrub_argv(argv: list[str]) -> list[str]:
    """argv without secrets: values of secret-looking flags are replaced, the rest goes through the central redactor."""
    out: list[str] = []
    hide_next = False
    for a in argv:
        if hide_next:
            out.append("[redacted]")
            hide_next = False
            continue
        if a.startswith("-") and _SECRET_FLAG.search(a) and not re.search(r"-(file|path|dir|url|name|env)$", a):
            if "=" in a:
                out.append(a.split("=", 1)[0] + "=[redacted]")
            else:
                out.append(a)
                hide_next = True
            continue
        out.append(redact_text(a))
    return out


def scrub_cmd(argv: list[str]) -> str:
    return " ".join(shlex.quote(a) if re.search(r"[\s'\"$`]", a) else a for a in scrub_argv(argv))


def _kv(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        k, sep, v = line.partition("=")
        if sep:
            out[k.strip()] = v.strip()
    return out


def exec_argv(show: dict[str, str]) -> list[str]:
    """argv of the first ExecStart= entry of `systemctl show` ({ path=... ; argv[]=... ; ignore_errors=... })."""
    m = re.search(r"argv\[\]=(.*?)\s;\s(?:ignore_errors|flags|start_time)=", show.get("ExecStart", ""))
    if not m:
        return []
    try:
        return shlex.split(m.group(1))
    except ValueError:
        return m.group(1).split()


def _num(v: str | None) -> int | None:
    try:
        n = int(v or "")
    except ValueError:
        return None
    return n if n < 2 ** 63 - 1 else None       # systemd prints 18446744073709551615 for "unset"


# ---------------------------------------------------------------------------------------------------------------
# stage helpers
# ---------------------------------------------------------------------------------------------------------------
@dataclass
class Proc:
    pid: int
    args: list[str]                    # hermes args: ['dashboard', '--port', ...]
    argv: list[str] = field(default_factory=list)   # real argv from /proc (may be the python launcher)
    port: int = DEFAULT_PORT
    host: str = "127.0.0.1"
    mode: str = "dashboard"
    listening: bool | None = None
    source: str = ""                   # hermes_status | proc_scan


def read_argv(env: Env, pid: int) -> list[str]:
    raw = env.proc_file(pid, "cmdline")
    if not raw:
        return []
    return [p.decode("utf-8", "replace") for p in raw.split(b"\x00") if p]


def _mk_proc(env: Env, pid: int, source: str, fallback_cmd: str = "") -> Proc | None:
    argv = read_argv(env, pid)
    args = hermes_args(argv)
    if args is None and fallback_cmd:
        # the status line carries the command as one string; the launcher form is parseable from it, too
        try:
            alt = shlex.split(fallback_cmd)
        except ValueError:
            alt = fallback_cmd.split()
        args = hermes_args(alt)
        argv = argv or alt
    if args is None:
        return None
    port, host = _port_host(args)
    return Proc(pid=pid, args=args, argv=argv, port=port, host=host, mode=args[0], source=source)


def stage_hermes(env: Env) -> tuple[list[Proc], dict[str, Any]]:
    """`hermes dashboard --status`: text only (no JSON), exit code always 0, lines `PID <n> [dashboard|serve]: <cmd>`."""
    st: dict[str, Any] = {"stage": "hermes", "name": "hermes dashboard --status", "hit": False}
    try:
        hb = env.hermes_bin()
    except Exception as exc:  # noqa: BLE001
        st["reason"] = f"hermes binary not found: {exc}"
        return [], st
    rc, out, err = env.run([hb, "dashboard", "--status"], timeout=45.0)
    st["exit_code"] = rc
    if rc != 0:
        st["reason"] = f"command failed (exit {rc}): {redact_text((err or out).strip())[-200:]}"
        return [], st
    procs: list[Proc] = []
    for line in out.splitlines():
        m = _STATUS_LINE.match(line)
        if not m:
            continue
        p = _mk_proc(env, int(m.group(1)), "hermes_status", m.group(3))
        if p:
            p.mode = m.group(2)
            p.listening = True       # hermes only lists backends that accept TCP connections
            procs.append(p)
    if procs:
        st["hit"] = True
        st["pids"] = [p.pid for p in procs]
    else:
        st["reason"] = ("no dashboard process reported: " + redact_text(out.strip().splitlines()[0] if out.strip() else "empty output")[:160])
    return procs, st


def proc_scan(env: Env) -> list[Proc]:
    """Fallback when the hermes CLI cannot answer: look at /proc ourselves (same token rules as hermes_args)."""
    me = env.self_pids()
    found = []
    for pid in env.proc_pids():
        if pid in me:
            continue
        raw = env.proc_file(pid, "cmdline")
        if not raw or (b"dashboard" not in raw and b"serve" not in raw):
            continue
        p = _mk_proc(env, pid, "proc_scan")
        if p:
            found.append(p)
    return found


def systemctl(env: Env, scope: str, *args: str, timeout: float = 15.0) -> tuple[int, str, str]:
    return env.run(["systemctl", *(["--user"] if scope == "user" else []), *args], timeout=timeout)


def show_unit(env: Env, unit: str, scope: str) -> tuple[dict[str, str] | None, str]:
    rc, out, err = systemctl(env, scope, "show", unit, "-p", SHOW_PROPS, "--no-pager")
    if rc != 0:
        return None, (err or out).strip()[:200] or f"exit {rc}"
    kv = _kv(out)
    if kv.get("LoadState") != "loaded":
        return None, f"LoadState={kv.get('LoadState', '?')}"
    return kv, ""


def _unit_info(unit: str, scope: str, kv: dict[str, str]) -> dict[str, Any]:
    argv = exec_argv(kv)
    return {"unit": unit, "scope": scope, "state": kv.get("ActiveState"), "substate": kv.get("SubState"),
            "main_pid": _num(kv.get("MainPID")) or None, "fragment": kv.get("FragmentPath"),
            "enabled": kv.get("UnitFileState"), "restart_policy": kv.get("Restart"),
            "working_directory": kv.get("WorkingDirectory"), "restarts": _num(kv.get("NRestarts")),
            "active_since": kv.get("ActiveEnterTimestamp"), "exec": scrub_cmd(argv) if argv else None,
            "_argv": argv, "_kv": kv}


def stage_known_unit(env: Env, name: str = KNOWN_UNIT) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    st: dict[str, Any] = {"stage": "known_unit", "name": f"unit {name} (user scope, then system scope)", "hit": False}
    hits, why = [], []
    for scope in ("user", "system"):
        kv, reason = show_unit(env, name, scope)
        if kv is None:
            why.append(f"{scope}: {reason}")
            continue
        info = _unit_info(name, scope, kv)
        if hermes_args(info["_argv"]) is None:
            why.append(f"{scope}: loaded, but ExecStart is not a `hermes dashboard` command")
            continue
        hits.append(info)
    if hits:
        st["hit"] = True
        st["scopes"] = [h["scope"] for h in hits]
    else:
        st["reason"] = "; ".join(why)
    return hits, st


def unit_of_pid(env: Env, pid: int) -> tuple[str, str] | None:
    """(unit, scope) of the service whose cgroup holds `pid` (as in Hermes' `_get_systemd_service_for_pid`)."""
    raw = env.proc_file(pid, "cgroup")
    if not raw:
        return None
    for line in raw.decode("utf-8", "replace").splitlines():
        _, _, path = line.partition("::")
        if path.endswith(".service"):
            scope = "user" if ("/user.slice/" in path or "/user@" in path or "/app.slice/" in path) else "system"
            return path.rsplit("/", 1)[-1], scope
    return None


def stage_custom_unit(env: Env, procs: list[Proc], known: list[dict[str, Any]]
                      ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    st: dict[str, Any] = {"stage": "custom_unit", "name": "any other service unit that owns a dashboard PID", "hit": False}
    if not procs:
        st["reason"] = "no dashboard process to attribute to a unit"
        return [], st
    owned = {h["main_pid"] for h in known if h.get("main_pid")}
    hits, why = [], []
    for p in procs:
        if p.pid in owned:
            continue
        cg = unit_of_pid(env, p.pid)
        if cg is None:
            why.append(f"pid {p.pid}: not in a service cgroup")
            continue
        unit, scope = cg
        kv, reason = show_unit(env, unit, scope)
        if kv is None:
            why.append(f"pid {p.pid}: unit {unit}: {reason}")
            continue
        if _num(kv.get("MainPID")) != p.pid:
            why.append(f"pid {p.pid}: runs inside {unit} but is not its MainPID (started from a shell/child): not owned")
            continue
        info = _unit_info(unit, scope, kv)
        info["pid"] = p.pid
        hits.append(info)
    if hits:
        st["hit"] = True
        st["units"] = [f"{h['unit']} ({h['scope']})" for h in hits]
    else:
        st["reason"] = "; ".join(why) or "all dashboard PIDs belong to the known unit"
    return hits, st


def proc_environ_names(env: Env, pid: int) -> list[str] | None:
    raw = env.proc_file(pid, "environ")
    if raw is None:
        return None
    return sorted({p.split(b"=", 1)[0].decode("utf-8", "replace") for p in raw.split(b"\x00") if b"=" in p})


def proc_environ(env: Env, pid: int) -> dict[str, str] | None:
    """Full environment for re-launching ONLY; values never leave this module's call chain."""
    raw = env.proc_file(pid, "environ")
    if raw is None:
        return None
    out = {}
    for part in raw.split(b"\x00"):
        k, sep, v = part.partition(b"=")
        if sep:
            out[k.decode("utf-8", "replace")] = v.decode("utf-8", "replace")
    return out


def recipe_for(env: Env, p: Proc, cfg: DashboardConfig | None = None) -> dict[str, Any]:
    """Start recipe of a free-standing dashboard. `complete` only when every piece is known and unambiguous."""
    missing: list[str] = []
    cwd = env.proc_link(p.pid, "cwd")
    if not cwd or not os.path.isabs(cwd):
        missing.append("cwd (unreadable)")
    names = proc_environ_names(env, p.pid)
    if names is None:
        missing.append("environment (unreadable)")
    exe_ok = bool(p.argv) and os.path.isabs(p.argv[0])
    if not exe_ok:
        missing.append("absolute executable")
    # What would be started: the original argv for a plain `hermes dashboard` launch, or the hermes entry point
    # plus the parsed args for the python launcher (`-c` wrapper).
    if p.argv and "-c" in p.argv[:6]:
        start = [_launcher(env), *p.args]
        if not start[0]:
            missing.append("hermes entry point")
    else:
        start = list(p.argv)
    if p.mode != "dashboard":
        missing.append(f"mode is {p.mode!r}: only `hermes dashboard` is restarted")
    if p.port <= 0:
        missing.append("port (0 = OS-assigned)")
    rec: dict[str, Any] = {"complete": not missing, "missing": missing, "cwd": cwd, "env_names": names,
                           "env_count": len(names) if names else 0, "start_command": scrub_cmd(start) if start else None,
                           "_start": start}
    return rec


def _launcher(env: Env) -> str:
    try:
        return env.hermes_bin()
    except Exception:  # noqa: BLE001
        return ""


# ---------------------------------------------------------------------------------------------------------------
# detection
# ---------------------------------------------------------------------------------------------------------------
def detect(env: Env, cfg: DashboardConfig | None = None, profile: str = "default", cfg_error: str = "") -> dict[str, Any]:
    cfg = cfg or DashboardConfig()
    stages: list[dict[str, Any]] = []
    det: dict[str, Any] = {"profile": profile, "configured_mode": cfg.mode, "config_source": cfg.source,
                           "mode": "none", "stage": None, "detected": False}
    if cfg_error:
        det["config_error"] = cfg_error

    # stage 0: config
    s0: dict[str, Any] = {"stage": "config", "name": f"override mode={cfg.mode}", "hit": False}
    if not cfg.explicit():
        s0["reason"] = "mode=auto (no override configured)"
    stages.append(s0)

    # stage 1: hermes
    procs, s1 = stage_hermes(env)
    stages.append(s1)
    if not procs:
        procs = proc_scan(env)
        if procs:
            s1["fallback"] = f"own /proc scan found {len(procs)} dashboard process(es)"
    me = env.self_pids()
    procs = [p for p in procs if p.pid not in me]

    # stage 2 / 3
    if cfg.mode == "systemd":
        kv, reason = show_unit(env, cfg.unit, cfg.scope)
        known, custom = [], []
        if kv is None:
            s0["reason"] = f"unit {cfg.unit} ({cfg.scope}) not usable: {reason}"
        else:
            info = _unit_info(cfg.unit, cfg.scope, kv)
            s0["hit"] = True
            s0["reason"] = f"configured unit {cfg.unit} ({cfg.scope}) loaded"
            known = [info]
        stages.append({"stage": "known_unit", "name": "skipped (override)", "hit": False, "reason": "mode=systemd override"})
        stages.append({"stage": "custom_unit", "name": "skipped (override)", "hit": False, "reason": "mode=systemd override"})
    else:
        known, s2 = stage_known_unit(env)
        stages.append(s2)
        custom, s3 = stage_custom_unit(env, procs, known)
        stages.append(s3)
        if cfg.mode == "process":
            known, custom = [], []
            s2["reason"] = (s2.get("reason") or "") + " (ignored: mode=process override)"
            s2["hit"] = s3["hit"] = False

    units = known + custom
    claimed = {u["main_pid"] for u in units if u.get("main_pid")}
    free = [p for p in procs if p.pid not in claimed]

    # stage 4: free-standing process
    s4: dict[str, Any] = {"stage": "process", "name": "free-standing dashboard process (/proc: argv, cwd, env names)",
                          "hit": False}
    if cfg.mode == "systemd":
        s4["reason"] = "skipped (mode=systemd override)"
        free = []
    elif not free:
        s4["reason"] = "no dashboard process outside a unit"
    else:
        s4["hit"] = True
        s4["pids"] = [p.pid for p in free]
        if cfg.mode == "process":
            s0["hit"] = True
            s0["reason"] = "mode=process override"
    stages.append(s4)
    det["stages"] = stages
    det["hermes_status_note"] = ("`hermes dashboard --status` is a flag (there is no `status` sub-command), prints text only, "
                                 "has no JSON option and always exits 0")

    # candidates: distinct managers; more than one = ambiguous, we never pick
    cands: list[dict[str, Any]] = []
    for u in units:
        cands.append({"kind": "systemd", "stage": "config" if cfg.mode == "systemd" else
                      ("known_unit" if u in known else "custom_unit"), "unit": u})
    for p in free:
        cands.append({"kind": "process", "stage": "config" if cfg.mode == "process" else "process", "proc": p})
    det["candidates"] = len(cands)

    if not cands:
        det["mode"] = "none"
        det["note"] = ("no dashboard found: nothing running and no dashboard unit installed"
                       + ("; override unit not usable" if cfg.mode == "systemd" else ""))
        return det
    if len(cands) > 1:
        det["mode"] = "ambiguous"
        det["detected"] = True
        det["options"] = [{"kind": c["kind"], "stage": c["stage"],
                           **({"unit": c["unit"]["unit"], "scope": c["unit"]["scope"], "state": c["unit"]["state"],
                               "main_pid": c["unit"]["main_pid"]} if c["kind"] == "systemd" else
                              {"pid": c["proc"].pid, "port": c["proc"].port})} for c in cands]
        det["note"] = "several managers/processes found; configure mode=systemd|process to choose, nothing is guessed"
        return det

    c = cands[0]
    det["detected"] = True
    det["stage"] = c["stage"]
    if c["kind"] == "systemd":
        u = c["unit"]
        det["mode"] = "systemd"
        pub = {k: v for k, v in u.items() if not k.startswith("_")}
        det["unit"] = pub
        argv = u["_argv"]
        args = hermes_args(argv) or []
        port, host = _port_host(args, cfg.port or DEFAULT_PORT)
        pid = u.get("main_pid")
        mp = next((p for p in procs if p.pid == pid), None)
        if mp:
            port, host = mp.port, mp.host
        det["pid"], det["port"], det["host"] = pid, port, host
        det["start_command"] = scrub_cmd(argv) if argv else None
        det["_unit"] = u
    else:
        p = c["proc"]
        det["mode"] = "process"
        det["pid"], det["port"], det["host"] = p.pid, p.port, p.host
        rec = recipe_for(env, p, cfg)
        if cfg.mode == "process":
            # configured recipe wins over the one read from /proc, but only when it is complete
            conf = _config_recipe(env, cfg, p)
            rec = conf
        det["recipe"] = {k: v for k, v in rec.items() if not k.startswith("_")}
        det["start_command"] = rec.get("start_command")
        det["cwd"] = rec.get("cwd")
        det["env_names"] = rec.get("env_names")
        det["process"] = {"pid": p.pid, "args": scrub_argv(p.args), "source": p.source,
                          "owner_uid": _uid_of(env, p.pid), "supervised_by": _supervisor(env, p.pid)}
        det["_recipe"] = rec
        det["_proc"] = p
    return det


def _supervisor(env: Env, pid: int) -> str | None:
    """`unit (scope)` when a service unit has exactly this PID as its MainPID (so systemd, not we, owns its lifecycle)."""
    cg = unit_of_pid(env, pid)
    if not cg:
        return None
    kv, _ = show_unit(env, cg[0], cg[1])
    if kv and _num(kv.get("MainPID")) == pid:
        return f"{cg[0]} ({cg[1]})"
    return None


def _uid_of(env: Env, pid: int) -> int | None:
    raw = env.proc_file(pid, "status")
    if not raw:
        return None
    m = re.search(rb"^Uid:\s+(\d+)", raw, re.M)
    return int(m.group(1)) if m else None


def _config_recipe(env: Env, cfg: DashboardConfig, running: Proc | None) -> dict[str, Any]:
    missing = []
    cwd = cfg.cwd or (env.proc_link(running.pid, "cwd") if running else None)
    if not cwd:
        missing.append("cwd (not configured, no running process to read it from)")
    names = proc_environ_names(env, running.pid) if running else None
    exe = cfg.command[0]
    if not os.path.isabs(exe):
        exe = _launcher(env) if os.path.basename(exe) == "hermes" else ""
        if not exe:
            missing.append("absolute executable")
    start = [exe or cfg.command[0], *cfg.command[1:]]
    return {"complete": not missing, "missing": missing, "cwd": cwd, "env_names": names,
            "env_count": len(names) if names else 0, "start_command": scrub_cmd(start), "_start": start,
            "from": "config"}


# ---------------------------------------------------------------------------------------------------------------
# status payload
# ---------------------------------------------------------------------------------------------------------------
def resources(env: Env, det: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    pid = det.get("pid")
    if pid:
        raw = env.proc_file(pid, "status")
        if raw:
            m = re.search(rb"^VmRSS:\s+(\d+)\s+kB", raw, re.M)
            if m:
                out["rss_mb"] = round(int(m.group(1)) / 1024, 1)
        st = env.proc_file(pid, "stat")
        if st:
            try:
                tail = st.decode("utf-8", "replace").rsplit(")", 1)[1].split()
                hz = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
                out["cpu_seconds_total"] = round((int(tail[11]) + int(tail[12])) / hz, 1)
            except (IndexError, ValueError, OSError):
                pass
    u = det.get("_unit")
    if u:
        kv = u["_kv"]
        mem, cpu = _num(kv.get("MemoryCurrent")), _num(kv.get("CPUUsageNSec"))
        if mem is not None:
            out["cgroup_memory_mb"] = round(mem / 1048576, 1)
        if cpu is not None:
            out["cgroup_cpu_seconds_total"] = round(cpu / 1e9, 1)
        mm = kv.get("MemoryMax")
        out["memory_max"] = "none (infinity)" if mm in (None, "infinity", "") else mm
    return out


def http_checks(env: Env, det: dict[str, Any]) -> dict[str, Any]:
    port, host = det.get("port"), probe_host(det.get("host") or "")
    if not port:
        return {}
    res: dict[str, Any] = {"host": host, "port": port, "listening": env.listening(host, port)}
    for path in ("/", "/api/status"):
        res[path] = env.http_status(host, port, path)
    return res


def public(det: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in det.items() if not k.startswith("_")}


def status(env: Env, profile: str = "default", cfg: DashboardConfig | None = None, cfg_error: str = "") -> dict[str, Any]:
    det = detect(env, cfg, profile, cfg_error)
    out = public(det)
    if det["mode"] in ("systemd", "process"):
        out["http"] = http_checks(env, det)
        out["resources"] = resources(env, det)
        pid = det.get("pid")
        out["running"] = bool(pid and env.pid_alive(pid))
    return out


# ---------------------------------------------------------------------------------------------------------------
# restart
# ---------------------------------------------------------------------------------------------------------------
def _systemctl_cmd(env: Env, unit: dict[str, Any], verb: str) -> list[str]:
    scope = unit["scope"]
    base = ["systemctl", *(["--user"] if scope == "user" else []), verb, unit["unit"]]
    if scope == "system" and env.euid() != 0:
        return ["sudo", "-n", *base]
    return base


def plan(env: Env, det: dict[str, Any]) -> dict[str, Any]:
    """What a restart would do. `executable` False = plan only, with the reason in `blocked`."""
    mode = det["mode"]
    p: dict[str, Any] = {"mode": mode, "stage": det.get("stage"), "executable": False, "commands": [], "side_effects": []}
    if mode == "none":
        p["blocked"] = "no dashboard found: nothing to restart. Start it manually or set the override in the hermcp config."
        return p
    if mode == "ambiguous":
        p["blocked"] = "ambiguous: several dashboards/managers found; manual action or an explicit override (mode=systemd|process) needed"
        p["options"] = det.get("options")
        return p
    me = env.self_pids()
    if mode == "systemd":
        u = det["_unit"]
        if det.get("pid") in me:
            p["blocked"] = "target PID is hermcp itself or the gateway: refused"
            return p
        p["commands"] = [" ".join(_systemctl_cmd(env, u, "restart"))]
        p["after"] = "verify: new MainPID, port, HTTP status of / and /api/status, journal lines with errors"
        p["fallback"] = " ".join(_systemctl_cmd(env, u, "reset-failed")) + " ; " + " ".join(_systemctl_cmd(env, u, "start"))
        p["executable"] = True
        p["side_effects"] = [
            f"only the unit {u['unit']} ({u['scope']} scope) is restarted; the gateway and hermcp are not touched",
            "open dashboard browser sessions drop and reconnect (a few seconds)",
            f"restart policy is {u.get('restart_policy') or '?'}: systemd would also bring it back after a crash"]
        return p
    # process
    pr: Proc = det["_proc"]
    rec = det["_recipe"]
    if pr.pid in me:
        p["blocked"] = "target PID is hermcp itself or the gateway: refused"
        return p
    p["recipe"] = {k: v for k, v in rec.items() if not k.startswith("_")}
    sup = (det.get("process") or {}).get("supervised_by")
    if sup:
        p["blocked"] = (f"PID {pr.pid} is the MainPID of {sup}: systemd would treat a kill as a clean stop and not bring it back. "
                        "Use mode=systemd with that unit instead of mode=process.")
        return p
    if not rec["complete"]:
        p["blocked"] = ("manual / config needed: the start recipe is incomplete (" + "; ".join(rec["missing"]) +
                        "). Nothing is guessed. Set mode=process with command and cwd (see README) or start it by hand.")
        return p
    p["commands"] = [f"kill -TERM {pr.pid}   (wait up to 15 s)",
                     f"cd {rec['cwd']} && {rec['start_command']}   (environment copied from the old process: "
                     f"{rec['env_count']} variables, names only: see recipe.env_names)"]
    p["after"] = "verify: new PID, port, HTTP status of / and /api/status, start log lines with errors"
    p["fallback"] = "one more start with the same recipe if the first start does not become healthy"
    p["executable"] = True
    p["side_effects"] = [
        f"the free-standing dashboard PID {pr.pid} is stopped; nothing supervises it, so it stays down if the start fails",
        "the new process runs detached (own session) but inside hermcp's cgroup: restarting hermcp.service stops it "
        "unless that unit has KillMode=process",
        "start output goes to plugin-data/hermcp/dashboard-process.log (0600)"]
    return p


def _wait_gone(env: Env, pid: int, seconds: float) -> bool:
    end = env.now() + seconds
    while env.now() < end:
        if not env.pid_alive(pid):
            return True
        env.sleep(0.5)
    return not env.pid_alive(pid)


def _verify(env: Env, det: dict[str, Any], old_pid: int | None, since: float, timeout: float,
            unit: dict[str, Any] | None) -> dict[str, Any]:
    """Poll until healthy or timeout. Healthy = new PID alive, port accepts connections, `/` answers."""
    end = env.now() + timeout
    port, host = det["port"], probe_host(det.get("host") or "")
    res: dict[str, Any] = {"healthy": False}
    while True:
        new_pid = None
        if unit:
            kv, _ = show_unit(env, unit["unit"], unit["scope"])
            if kv:
                new_pid = _num(kv.get("MainPID")) or None
                res["unit_state"] = kv.get("ActiveState")
        else:
            new_pid = det.get("_new_pid")
        res["new_pid"] = new_pid
        res["pid_changed"] = bool(new_pid and new_pid != old_pid)
        res["listening"] = env.listening(host, port)
        code = env.http_status(host, port, "/") if res["listening"] else None
        res["http"] = {"/": code}
        if new_pid and res["pid_changed"] and env.pid_alive(new_pid) and res["listening"] and code is not None:
            res["http"]["/api/status"] = env.http_status(host, port, "/api/status")
            res["healthy"] = True
            break
        if env.now() >= end:
            break
        env.sleep(1.5)
    res["log_errors"] = _log_errors(env, since, unit)
    return res


def _log_errors(env: Env, since: float, unit: dict[str, Any] | None) -> list[str]:
    if unit:
        argv = ["journalctl", *(["--user"] if unit["scope"] == "user" else []), "-u", unit["unit"],
                "--since", f"@{int(since)}", "-n", "80", "--no-pager", "-o", "cat"]
        rc, out, _ = env.run(argv, timeout=20.0)
        text = out if rc == 0 else ""
    else:
        text = env.read_log(env.log_path())
    lines = [redact_text(ln.strip())[:240] for ln in text.splitlines() if ERROR_LINE.search(ln)]
    return lines[-8:]


def restart(env: Env, det: dict[str, Any], timeout: float = VERIFY_TIMEOUT) -> dict[str, Any]:
    """Execute the plan. Caller has checked confirm/dry_run. Returns a result dict (never raises on a failed restart)."""
    pl = plan(env, det)
    if not pl["executable"]:
        return {"applied": False, "mode": det["mode"], "blocked": pl.get("blocked"), "plan": pl}
    t0 = env.now()
    old_pid = det.get("pid")
    out: dict[str, Any] = {"mode": det["mode"], "stage": det.get("stage"), "old_pid": old_pid, "steps": []}
    if det["mode"] == "systemd":
        u = det["_unit"]
        rc, so, se = env.run(_systemctl_cmd(env, u, "restart"), timeout=90.0)
        out["steps"].append({"cmd": " ".join(_systemctl_cmd(env, u, "restart")), "exit_code": rc,
                             "stderr": redact_text(se.strip())[-300:]})
        ver = _verify(env, det, old_pid, t0, timeout, u)
        if not ver["healthy"]:
            for verb in ("reset-failed", "start"):
                rc2, _, se2 = env.run(_systemctl_cmd(env, u, verb), timeout=90.0)
                out["steps"].append({"cmd": " ".join(_systemctl_cmd(env, u, verb)), "exit_code": rc2,
                                     "stderr": redact_text(se2.strip())[-300:]})
            ver = _verify(env, det, old_pid, t0, timeout, u)
            ver["retried"] = True
    else:
        pr: Proc = det["_proc"]
        rec = det["_recipe"]
        envv = proc_environ(env, pr.pid)
        if envv is None:
            return {"applied": False, "mode": "process", "blocked": "environment of the running process became unreadable",
                    "plan": pl}
        try:
            env.kill(pr.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except OSError as exc:
            return {"applied": False, "mode": "process", "error": f"cannot stop PID {pr.pid}: {exc}", "plan": pl}
        gone = _wait_gone(env, pr.pid, 15.0)
        out["steps"].append({"cmd": f"kill -TERM {pr.pid}", "stopped": gone})
        if not gone:
            return {"applied": False, "mode": "process", "old_pid": pr.pid,
                    "error": f"PID {pr.pid} did not stop within 15 s; no second process was started (no SIGKILL)",
                    "manual": f"inspect/kill PID {pr.pid} by hand, then start: {rec['start_command']}"}
        envv.setdefault("HERMES_HOME", str(os.environ.get("HERMES_HOME", "")))
        ver = {"healthy": False}
        for attempt in (1, 2):
            new_pid = env.popen_detached(rec["_start"], rec["cwd"], envv, env.log_path())
            det["_new_pid"] = new_pid
            out["steps"].append({"cmd": rec["start_command"], "cwd": rec["cwd"], "attempt": attempt, "new_pid": new_pid})
            ver = _verify(env, det, old_pid, t0, timeout, None)
            if ver["healthy"]:
                break
            ver["retried"] = attempt == 2
        # `listening` is evaluated by port; a still-running first attempt must not leave a second one behind
    out["verify"] = ver
    out["new_pid"] = ver.get("new_pid")
    out["applied"] = bool(ver["healthy"])
    if ver["healthy"]:
        out["note"] = "dashboard restarted and verified" + (" (log shows error-like lines, see verify.log_errors)"
                                                           if ver["log_errors"] else "")
    else:
        out["error"] = "restart did not produce a healthy dashboard"
        if det["mode"] == "systemd":
            un = det["_unit"]["unit"]
            out["manual"] = f"check `journalctl -u {un} -n 100` and `systemctl status {un}`"
        else:
            out["manual"] = ("see plugin-data/hermcp/dashboard-process.log; start by hand: "
                             f"{det['_recipe']['start_command']}")
    return out
