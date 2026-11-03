"""status toolset: health, version, gateway state, doctor, usage."""
from __future__ import annotations

import json
import os
import time

from .. import __version__, meta
from ..adapters import overview as OV
from ..adapters.common import cli_text, hermes_scope, list_profile_names, profile_home  # noqa: F401
from ..adapters.served import served_info, split_profiles
from ..settings import hermes_home
from . import reg


def _pid_alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


@reg.tool("hermes_health", toolset="status", core=True,
          description=("One-call overview: hermcp and Hermes version, the gateway process with `health` (ok or degraded, "
                       "based only on platforms the running gateway reports; entries left behind by dead processes are "
                       "listed under `stale_platforms` and never count; `attention` names platforms that need action), "
                       "and which profiles are served. Only profiles in `reachable_profiles` work with the chat tools; "
                       "`inactive_profiles` exist only as configuration, `unknown_profiles` could not be determined. "
                       "`profile_details` (detail=true, default) gives per profile the model, fallback chain, toolsets, "
                       "platform_toolsets, served / parked, live adapters, MCP set and effective skill counts per "
                       "platform; skills=false skips the skill counts. Every platform entry carries updated_at, "
                       "writer_pid and a stale flag."),
          properties={"detail": {"type": "boolean", "default": True}, "skills": {"type": "boolean", "default": True}})
def hermes_health(ctx, detail=True, skills=True):
    gw = scope_gateway(_gateway_state(profile_home("default")), ctx)
    sp = split_profiles([n for n in list_profile_names() if ctx.principal.scopes.profile_allowed(n)], served_info())
    ver = None
    try:
        ver = cli_text(["--version"], timeout=20).strip().splitlines()[0]
    except Exception as exc:  # noqa: BLE001
        ver = f"unavailable: {exc}"
    return {"hermcp": __version__, "hermes_version": ver,
            **({} if ctx.restricted else {"hermes_home": str(hermes_home())}),
            "gateway": gw, "profiles": sp["ordered"], "reachable_profiles": sp["reachable_profiles"],
            "inactive_profiles": sp["inactive_profiles"], "unknown_profiles": sp["unknown_profiles"],
            "served_source": sp["served_source"],
            "profiles_note": "profiles lists all on disk, active first; only reachable_profiles are served by the "
                             "running gateway (inactive ones are settings only; unknown = not determinable).",
            "time": time.time(),
            **({"profile_details": OV.profile_details(sp["ordered"], gw, with_skills=skills)} if detail else {})}


def scope_gateway(gw: dict, ctx) -> dict:
    """Profile-restricted keys see only the adapters / served entries of their own profile(s)."""
    if not ctx.restricted or not isinstance(gw, dict):
        return gw
    allowed = set(ctx.principal.scopes.profiles)

    def mine(key: str) -> bool:
        owner, _, _plat = key.rpartition(":")
        return (owner or "default") in allowed
    out = dict(gw)
    for k in ("platforms", "stale_platforms"):
        if isinstance(gw.get(k), dict):
            out[k] = {n: v for n, v in gw[k].items() if mine(n)}
    out["attention"] = [a for a in gw.get("attention") or [] if mine(a)]
    if isinstance(gw.get("served_profiles"), list):
        out["served_profiles"] = [p for p in gw["served_profiles"] if p in allowed]
    if isinstance(gw.get("parked_profiles"), dict):
        out["parked_profiles"] = {p: v for p, v in gw["parked_profiles"].items() if p in allowed}
    out["scoped_to"] = sorted(allowed)
    return out


_PROBLEM_STATES = {"retrying", "failed", "fatal", "error", "startup_failed"}


def _disabled_by_config(home, name: str) -> str | None:
    """Reason string if config explicitly disables platform `name` (profile prefix `p:` ignored)."""
    base = name.split(":", 1)[-1]
    try:
        import yaml
        cfg = yaml.safe_load((home / "config.yaml").read_text()) or {}
    except Exception:  # noqa: BLE001 - unreadable config: don't guess
        return None
    plat = ((cfg.get("gateway") or {}).get("platforms") or {}).get(base)
    if isinstance(plat, dict) and plat.get("enabled") is False:
        return f"gateway.platforms.{base}.enabled is false"
    disabled = (cfg.get("plugins") or {}).get("disabled") or []
    for cand in (base, f"{base}-platform", f"platforms/{base}"):
        if cand in disabled:
            return f"plugin '{cand}' is in plugins.disabled"
    return None


def _stale_reason(v: dict, head: dict) -> str | None:
    """Why a platform entry is NOT a current report of the running gateway (None = current)."""
    wpid = v.get("writer_pid")
    if wpid is None:
        if v.get("error_message") or v.get("needs_attention") or v.get("state") in _PROBLEM_STATES:
            return "no writer_pid on an entry that carries an error (leftover of an earlier process)"
        return None  # e.g. disconnected without error: harmless as before
    if wpid != head.get("pid"):
        return f"writer_pid {wpid} differs from running gateway pid {head.get('pid')}"
    if not _pid_alive(wpid):
        return f"writer process {wpid} is not alive"
    wst, hst = v.get("writer_start_time"), head.get("start_time")
    if wst is not None and hst is not None and wst != hst:
        return f"writer_start_time {wst} differs from gateway start_time {hst} (pid reused)"
    return None


def _gateway_state(home) -> dict:
    """Read-only view of gateway_state.json; the file belongs to the gateway and is never written."""
    p = home / "gateway_state.json"
    try:
        st = json.loads(meta.read_text(p, "gateway-state"))
    except (OSError, ValueError):
        return {"state": "unknown", "detail": "gateway_state.json missing or unreadable"}
    plats, stale, attention = {}, {}, []
    for k, v in (st.get("platforms") or {}).items():
        v = v or {}
        entry = {"state": v.get("state"), "needs_attention": v.get("needs_attention"),
                 "error": v.get("error_message"), "updated_at": v.get("updated_at"),
                 "writer_pid": v.get("writer_pid"), "stale": False}
        reason = _stale_reason(v, st)
        if reason:
            stale[k] = {"stale": True, "stale_reason": reason, "state": v.get("state"),
                        "updated_at": v.get("updated_at"), "writer_pid": v.get("writer_pid"),
                        "error": v.get("error_message")}
            continue
        off = _disabled_by_config(home, k)
        if off:
            entry.update(config_disabled=True, config_disabled_reason=off, needs_attention=False)
        elif v.get("needs_attention") or v.get("state") in _PROBLEM_STATES:
            attention.append(k)
        plats[k] = entry
    gw_alive = _pid_alive(st.get("pid"))
    running = st.get("gateway_state") == "running" and gw_alive
    return {"state": st.get("gateway_state"), "pid": st.get("pid"), "pid_alive": gw_alive,
            "active_agents": st.get("active_agents"), "restart_requested": st.get("restart_requested"),
            "exit_reason": st.get("exit_reason"), "platforms": plats, "stale_platforms": stale,
            "attention": attention, "health": "ok" if running and not attention else "degraded",
            "served_profiles": st.get("served_profiles"), "parked_profiles": st.get("parked_profiles"),
            "code_version": st.get("code_version"), "state_updated_at": st.get("updated_at")}


@reg.tool("status_version", toolset="status", description="Hermes version string only. hermes_health gives the full overview.")
def status_version(ctx):
    return {"version": cli_text(["--version"], timeout=20).strip()}


@reg.tool("status_gateway", toolset="status", profile_aware=True, core=True,
          description=("Gateway process state and per-platform connection state of a profile, read from the gateway's "
                       "state file (read-only). `platforms` are the current reports of the running process; "
                       "`stale_platforms` are leftovers of dead processes and are ignored for health; platforms disabled "
                       "in config are flagged, not reported as errors. `health` is ok or degraded; `attention` lists "
                       "platforms that need action."))
def status_gateway(ctx):
    return scope_gateway(_gateway_state(profile_home(ctx.profile)), ctx)


@reg.tool("status_doctor", toolset="status",
          description=("Run `hermes doctor` (read-only diagnosis, can take up to 90 s) for a profile and return the last "
                       "`max_chars` characters of its report as free text. It may contain paths and configuration "
                       "details; never repeat secrets."),
          properties={"max_chars": {"type": "integer", "minimum": 500, "maximum": 60000, "default": 12000}},
          profile_aware=True)
def status_doctor(ctx, max_chars: int = 12000):
    out = cli_text(["doctor"], profile=ctx.profile, timeout=90, ok_codes=(0, 1))
    return {"report": out[-max_chars:], "truncated": len(out) > max_chars}


@reg.tool("status_usage", toolset="status", profile_aware=True,
          description=("Usage and cost report (`hermes insights`) for the last N days as free text (last 20000 "
                       "characters)."),
          properties={"days": {"type": "integer", "minimum": 1, "maximum": 365, "default": 7}})
def status_usage(ctx, days: int = 7):
    return {"report": cli_text(["insights", "--days", str(days)], profile=ctx.profile, timeout=60)[-20000:]}
