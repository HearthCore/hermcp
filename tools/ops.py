"""ops toolset: gateway lifecycle (restart) and gateway logs.

`gateway_restart` is deliberately the only "big hammer" in the surface: it kills every
running agent session of this profile. It is admin-only, needs `confirm=true`, and its
dry_run shows exactly what would happen.

IMPORTANT deployment note: hermcp must run OUTSIDE the gateway's process tree (systemd unit,
see deploy/hermcp.service.template). A child of the gateway would be SIGTERMed together with
it and could never report the restart.
"""
from __future__ import annotations

import shutil
import subprocess

from ..registry import ToolError
from ._cli import run_write  # noqa: F401
from . import reg

UNIT = {"type": "string", "pattern": "^[A-Za-z0-9@._-]{1,80}$", "description": "systemd unit"}


def _norm(v) -> str:
    return "user" if v == "user" else "system"


def _scope(settings) -> str:
    """Manager of the Hermes gateway unit: `system` (default) or `user` (HERMCP_SYSTEMD_SCOPE)."""
    return _norm(getattr(settings, "systemd_scope", "system"))


def _self_scope(settings) -> str:
    """Manager of hermcp.service itself (HERMCP_SELF_SYSTEMD_SCOPE), independent of the gateway's."""
    return _norm(getattr(settings, "self_systemd_scope", "system"))


def _timer_scope(gateway_scope: str, self_scope: str) -> str:
    """Manager that hosts the self-restart timer: the user manager as soon as one exists in the setup
    (either scope is `user`): a transient timer there needs no authorization. Otherwise the system manager."""
    return "user" if "user" in (gateway_scope, self_scope) else "system"


def _manager_flags(scope: str) -> list[str]:
    return ["--user"] if scope == "user" else []


def _systemd_env(scope: str) -> dict | None:
    """Environment for a call that talks to the *user* manager (systemctl/journalctl/systemd-run --user): it needs
    XDG_RUNTIME_DIR to find the user bus. A unit started without a login session does not have it, so derive it
    from the uid at run time (no uid is ever written into a unit file); an existing value is kept.
    Scope `system`: None, the process environment stays untouched."""
    if scope != "user":
        return None
    import os
    env = dict(os.environ)
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return env


def _systemctl(argv: list[str], timeout: float = 60.0, scope: str = "system") -> subprocess.CompletedProcess:
    """Run systemctl against the system manager (default) or the user manager of the service account
    (scope=user, e.g. a user unit with Linger=yes)."""
    try:
        return subprocess.run(["systemctl", *_manager_flags(scope), *argv],
                              capture_output=True, text=True, timeout=timeout, env=_systemd_env(scope))
    except FileNotFoundError as exc:  # pragma: no cover - non-systemd host
        raise ToolError("adapter_error", f"systemctl not available: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ToolError("timeout", f"systemctl {' '.join(argv)} timed out after {timeout}s") from exc


@reg.tool("gateway_restart", toolset="ops", access="admin", core=True,
          description=(("Restart the Hermes gateway unit. DESTRUCTIVE: running agent sessions are drained for a while and then "
                        "interrupted mid-turn (approval prompts included), and runs started with chat_send end as "
                        "interrupted. Needs confirm=true; without it, or with dry_run, only the plan is returned (applied "
                        "false), and the plan lists the agents that are running (`running_agents`, same data as "
                        "agents_live) so you see who would be cut off. The MCP connection itself survives.")),
          properties={"unit": {**UNIT, "default": "hermes-gateway.service"},
                      "confirm": {"type": "boolean", "default": False,
                                  "description": "Must be true to actually restart"}})
def gateway_restart(ctx, unit="hermes-gateway.service", confirm=False):
    if ".." in unit or unit.startswith("-"):
        raise ToolError("invalid_arguments", "bad unit name")
    from ..adapters import active_work as AW
    scope = _scope(ctx.settings)
    cmd = " ".join(["systemctl", *_manager_flags(scope), "restart", unit])
    agents = AW.restart_preview()          # taken BEFORE anything happens: who is running
    if ctx.dry_run or not confirm:
        return ctx.plan(
            f"would run: {cmd}",
            unit=unit, command=cmd, systemd_scope=scope,
            warning=("terminates running agent sessions: the gateway stops taking new turns and drains in-flight work "
                     "for agent.restart_drain_timeout seconds (config), then interrupts what "
                     "is left (chat turns resume on the next message; interrupted cron jobs are recorded as failed); "
                     "systemd's TimeoutStopSec ends the rest. Unlike `hermes gateway restart`, which refuses while "
                     "sessions are live unless --yes is given, this runs `systemctl restart` directly."),
            running_agents=agents,
            needed="confirm=true",
        )
    cp = _systemctl(["restart", unit], scope=scope)
    ok = cp.returncode == 0
    return {"applied": ok, "unit": unit, "exit_code": cp.returncode,
            "stderr": (cp.stderr or "").strip()[-800:],
            "running_agents_at_request": agents,
            "note": "in-flight runs of the agent were terminated" if ok else "restart failed"}


@reg.tool("ops_gateway_logs", toolset="ops", description=("Last N lines of the gateway's systemd journal (read-only, max 2000 lines; output is cut to the "
                                                          "last 20000 characters). Log lines can contain personal data; do not repeat them unnecessarily."),
          properties={"lines": {"type": "integer", "minimum": 1, "maximum": 2000, "default": 120},
                      "unit": {**UNIT, "default": "hermes-gateway.service"},
                      "since": {"type": "string", "description": "journalctl --since value"}})
def ops_gateway_logs(ctx, lines=120, unit="hermes-gateway.service", since=None):
    scope = _scope(ctx.settings)          # a user-unit gateway logs to the user journal: journalctl --user
    argv = ["journalctl", *_manager_flags(scope), "-u", unit, "-n", str(lines), "--no-pager", "--output=short-iso"]
    if since:
        argv += ["--since", since]
    try:
        cp = subprocess.run(argv, capture_output=True, text=True, timeout=30, env=_systemd_env(scope))
    except subprocess.TimeoutExpired as exc:
        raise ToolError("timeout", "journalctl timed out") from exc
    return {"unit": unit, "lines": lines, "exit_code": cp.returncode,
            "log": (cp.stdout or cp.stderr or "").strip()[-20000:]}


# ---- hermcp self-restart (admin) ----------------------------------------------------------
# hermcp restarts ITSELF. The unit name is fixed (no parameter), the gateway and the dashboard are
# never touched. The restart is handed to a transient systemd timer unit (systemd-run), so it runs
# outside hermcp's cgroup and survives hermcp's own stop; the tool answers before it fires.
HERMCP_UNIT = "hermcp.service"
RESTART_DELAY_S = 3
RESTART_MIN_INTERVAL_S = 60
# Transient timers created by systemd-run inherit the DefaultTimerAccuracySec of 1 min, so an
# "--on-active=3s" timer may fire up to a minute late (measured: 39-59 s). That made the tool
# announce "scheduled in 3 s" while the restart really happened ~1 min later, which reads like a
# failed restart. Pinning the accuracy makes the announced delay true.
RESTART_ACCURACY = "100ms"
RESTART_TIMER_STATES = ("active", "activating")
RESTART_VERIFY_TRIES = 3


def _restart_state_path():
    from ..settings import data_dir
    return data_dir() / "restart_state.json"


def _restart_last_requested() -> float:
    import json
    try:
        return float(json.loads(_restart_state_path().read_text(encoding="utf-8")).get("last_requested", 0.0))
    except (OSError, ValueError, TypeError):
        return 0.0


def _restart_record(now: float) -> None:
    import json
    p = _restart_state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"last_requested": now}), encoding="utf-8")


def _restart_unit_name(stamp: int) -> str:
    return f"hermcp-selfrestart-{stamp}"


def _restart_argv(stamp: int, self_scope: str = "system", timer_scope: str | None = None) -> list[str]:
    """systemd-run argv for the delayed self-restart.

    The TIMER lives in `timer_scope` (default: the user manager when hermcp itself is a user unit, else the system
    manager). The PAYLOAD `systemctl [--user] restart hermcp.service` addresses the manager of hermcp.service
    (`self_scope`). Both can differ: a timer in the user manager may restart a system unit (authorised by e.g. a
    polkit rule pinned to the unit and the verb), a timer in the system manager cannot restart a user unit."""
    timer_scope = timer_scope or ("user" if self_scope == "user" else "system")
    systemctl = shutil.which("systemctl") or "/usr/bin/systemctl"
    payload = [systemctl, *_manager_flags(self_scope), "restart", HERMCP_UNIT]
    return ["systemd-run", *_manager_flags(timer_scope), "--collect", "--no-ask-password",
            f"--unit={_restart_unit_name(stamp)}",
            f"--on-active={RESTART_DELAY_S}s", f"--timer-property=AccuracySec={RESTART_ACCURACY}", *payload]


def _timer_state(unit_name: str, scope: str = "system") -> str:
    """ActiveState of the queued timer, or 'unknown' when systemctl cannot be asked at all."""
    systemctl = shutil.which("systemctl") or "/usr/bin/systemctl"
    try:
        cp = subprocess.run([systemctl, *_manager_flags(scope), "is-active", f"{unit_name}.timer"],
                            capture_output=True, text=True, timeout=10, env=_systemd_env(scope))
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return (cp.stdout or "").strip() or "unknown"


@reg.tool("hermcp_restart", toolset="ops", access="admin", core=True,
          description=("Restart the hermcp service itself (hermcp.service only, never the gateway or the dashboard). "
                       "DESTRUCTIVE for this MCP connection: hermcp exits and comes back after ~"
                       f"{RESTART_DELAY_S} s. The restart is queued as a transient systemd timer with pinned "
                       f"timer accuracy ({RESTART_ACCURACY}) so it fires at the announced delay; the answer is "
                       "returned first, and applied=true is only reported after the timer was confirmed active. "
                       "Needs confirm=true; without it, or with dry_run, only the plan is returned (applied false). "
                       "Rate limit: one real restart per "
                       f"{RESTART_MIN_INTERVAL_S} s. To verify success call hermes_health later: "
                       "_meta.hermcp.started_at must be newer than old_started_at and "
                       "_meta.hermcp_older_than_hermes_build must be false. Watches and the event inbox live in "
                       "SQLite and survive the restart; in-memory backoff timers of the run watcher are reset."),
          properties={"confirm": {"type": "boolean", "default": False,
                                  "description": "Must be true to actually restart"}})
def hermcp_restart(ctx, confirm=False):
    import os
    import time
    from .. import meta
    now = time.time()
    old_pid = os.getpid()
    old_started = meta.PROCESS_STARTED
    since_last = now - _restart_last_requested()
    retry_after = max(0.0, RESTART_MIN_INTERVAL_S - since_last)
    stamp = int(now)
    unit_name = _restart_unit_name(stamp)
    self_scope = _self_scope(ctx.settings)
    scope = _timer_scope(_scope(ctx.settings), self_scope)      # manager of the timer (and of its probe)
    argv = _restart_argv(stamp, self_scope, scope)
    extra_error = {"applied": False, "unit": HERMCP_UNIT, "old_pid": old_pid,
                   "old_started_at": round(old_started, 1), "command": " ".join(argv),
                   "timer_unit": f"{unit_name}.timer"}
    base = {"unit": HERMCP_UNIT, "systemd_scope": self_scope, "timer_scope": scope, "old_pid": old_pid, "old_started_at": round(old_started, 1),
            "command": " ".join(argv), "delay_s": RESTART_DELAY_S, "timer_unit": f"{unit_name}.timer",
            "side_effects": ["hermcp drops this MCP connection and all open streams",
                             "in-memory run-watcher backoff timers reset; watches and inbox events persist in SQLite",
                             "gateway and dashboard are not touched"],
            "verify": "call hermes_health: _meta.hermcp.started_at > old_started_at and "
                      "_meta.hermcp_older_than_hermes_build == false"}
    if ctx.dry_run or not confirm:
        from ..adapters import active_work as AW
        prev = AW.restart_preview()
        base["running_agents"] = ({"available": True, "active_agents": prev["active_agents"],
                                   "by_kind": prev["by_kind"], "by_profile": prev["by_profile"],
                                   "interrupted_by_this_restart": 0,
                                   "note": "the gateway keeps running; agents are not touched, only hermcp's own "
                                           "MCP connections and run watchers restart"}
                                  if prev["available"] else prev)
    if ctx.dry_run or not confirm:
        extra = {} if confirm else {"needed": "confirm=true", "refused": "no restart without confirm=true; this is the plan only"}
        return ctx.plan(f"would restart {HERMCP_UNIT} in {RESTART_DELAY_S} s (old pid {old_pid})",
                        **base, rate_limit_retry_after_s=round(retry_after, 1), **extra)
    if retry_after > 0:
        raise ToolError("rate_limited", f"hermcp was restart-requested {since_last:.0f} s ago; "
                                        f"wait {retry_after:.0f} s", retry_after_s=round(retry_after, 1),
                        **extra_error)
    try:
        cp = subprocess.run(argv, capture_output=True, text=True, timeout=20, env=_systemd_env(scope))
    except FileNotFoundError as exc:
        raise ToolError("adapter_error", f"systemd-run not available: {exc}", **extra_error) from exc
    except subprocess.TimeoutExpired as exc:
        raise ToolError("timeout", "systemd-run timed out; no restart was scheduled", **extra_error) from exc
    if cp.returncode != 0:
        raise ToolError("restart_failed", (cp.stderr or cp.stdout or "systemd-run failed").strip()[-400:],
                        exit_code=cp.returncode, **extra_error)
    # exit code 0 only means "systemd accepted the unit". Confirm the timer actually exists before
    # claiming success - a queued restart that never fires must never look like applied=true.
    state = "unknown"
    for attempt in range(RESTART_VERIFY_TRIES):
        state = _timer_state(unit_name, scope)
        if state in RESTART_TIMER_STATES:
            break
        if attempt + 1 < RESTART_VERIFY_TRIES:
            time.sleep(0.25)
    if state not in RESTART_TIMER_STATES and state != "unknown":
        raise ToolError("restart_failed",
                        f"systemd-run returned 0 but timer {unit_name}.timer is {state}; no restart was queued",
                        timer_state=state, exit_code=cp.returncode, **extra_error)
    # rate limit is consumed only for a restart that was really queued
    _restart_record(now)
    return {"applied": True, "scheduled_in_s": RESTART_DELAY_S, "timer_state": state, **base}
