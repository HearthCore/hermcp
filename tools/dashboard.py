"""ops toolset: Hermes web dashboard status and restart.

`dashboard_restart` follows `gateway_restart`: admin, needs `confirm=true`, `dry_run` / missing confirm return only the plan
(applied false). hermcp is its own process, so it can still verify and recover after the dashboard restart went wrong.
The gateway and hermcp itself are never a restart target.
"""
from __future__ import annotations


from ..adapters import dashboard as D
from ..registry import ToolError
from . import reg

PROFILE = {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$", "default": "default",
           "description": "Which override of the hermcp dashboard config applies (default: 'default')"}


def get_env() -> D.Env:          # replaced in tests
    return D.Env()


@reg.tool("dashboard_status", toolset="ops", core=True,
          description=("How the Hermes web dashboard runs and whether it is healthy (read-only). Detection order, first manager "
                       "found wins: config override, `hermes dashboard --status`, the unit name Hermes itself restarts "
                       "(hermes-dashboard.service, user then system scope), any other systemd unit that owns a dashboard PID, "
                       "a free-standing process (argv, cwd, environment names from /proc). Returns `stages` (which stage hit "
                       "and why the others did not), mode (systemd, process, none, ambiguous), unit or PID, port, start command "
                       "without secrets, HTTP status codes of / and /api/status (no credentials), memory and CPU. "
                       "`hermes dashboard --status` can take a few seconds."),
          properties={"profile": PROFILE})
def dashboard_status(ctx, profile="default"):
    cfg, err = D.load_config(profile)
    return D.status(get_env(), profile, cfg, err)


@reg.tool("dashboard_restart", toolset="ops", access="admin", core=True,
          description=("Restart the Hermes web dashboard only (not the gateway, not hermcp). Mode comes from dashboard_status: "
                       "systemd restarts exactly the detected unit; a free-standing process is restarted only when its start "
                       "recipe (command, cwd, environment) is complete, otherwise the answer is a plan with 'manual / config "
                       "needed'. Needs confirm=true; without it, or with dry_run, only the plan is returned (applied false) with "
                       "the exact commands and side effects. After a real restart the result is verified (new PID, port, HTTP "
                       "status, error lines in the log) with one recovery attempt."),
          properties={"confirm": {"type": "boolean", "default": False, "description": "Must be true to actually restart"},
                      "profile": PROFILE})
def dashboard_restart(ctx, confirm=False, profile="default"):
    env = get_env()
    cfg, err = D.load_config(profile)
    det = D.detect(env, cfg, profile, err)
    pl = D.plan(env, det)
    base = {"detected_stage": det.get("stage"), "mode": det["mode"], "plan": pl}
    if det.get("config_error"):
        base["config_error"] = det["config_error"]
    if ctx.dry_run or not confirm:
        extra = {} if confirm else {"needed": "confirm=true",
                                    "refused": "no restart without confirm=true; this is the plan only"}
        return ctx.plan(pl.get("blocked") or ("would restart the dashboard: " + "; ".join(pl["commands"])), **base, **extra)
    if not pl["executable"]:
        raise ToolError("not_restartable", pl.get("blocked") or "dashboard cannot be restarted automatically", **base)
    res = D.restart(env, det)
    if res.get("applied"):
        return res
    raise ToolError("restart_failed", res.get("error") or res.get("blocked") or "restart failed",
                    **{k: v for k, v in res.items() if k not in ("error",)})
