"""orchestration_overview: one compact call that orients an agent about the whole Hermes setup."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from .. import meta
from ..adapters import overview as OV
from ..adapters.common import cli_json, list_profile_names, profile_home
from ..adapters.served import served_info
from . import reg
from .status import _gateway_state, scope_gateway


def _plugins_config_summary() -> dict:
    cfg = OV.raw_config("default").get("plugins")
    cfg = cfg if isinstance(cfg, dict) else {}
    return {"source": "config.yaml plugins.enabled / plugins.disabled (bundled auto-enabled plugins are not listed "
                      "here; pass plugins_exact=true or use plugins_list for the runtime status)",
            "enabled_in_config": sorted(cfg.get("enabled") or [])[:80], "disabled_in_config_count": len(cfg.get("disabled") or [])}


def _plugins_summary() -> dict:
    data = cli_json(["plugins", "list", "--json"], None, 60)
    by: dict[str, int] = {}
    for p in data:
        by[p.get("status") or "?"] = by.get(p.get("status") or "?", 0) + 1
    return {"total": len(data), "by_status": by,
            "enabled": sorted(p["name"] for p in data if p.get("status") == "enabled")[:60]}


def _kanban_summary() -> dict:
    return {"config": OV.kanban_config("default"), "boards": OV.kanban_boards()}


def approvals_overview(ctx, names, info):
    """Pending approvals per reachable profile (counts), or the reason the key cannot see them."""
    if ctx.principal.scopes.approvals and not (ctx.restricted and "default" in names):
        pending = {}
        from ..adapters.served import reachable
        from ..bridge import call as bridge_call, entries_for_profile, resolve as bridge_resolve, socket_path
        from ..settings import hermes_home
        for n in names:
            if reachable(n, info) is not True:
                continue
            try:
                # Same resolver as the approvals tools: on a multiplex gateway every profile answers
                # on the launch home's shared socket, which lists ALL profiles' approvals.
                target = bridge_resolve(n, own_socket=socket_path(profile_home(n)), root_home=hermes_home(),
                                        known_profiles=names)
                resp = bridge_call(target.socket, {"op": "list", "key_id": ctx.principal.id})
                if not resp.get("ok"):
                    pending[n] = f"error: {resp.get('error')}"
                    continue
                entries = resp.get("pending", [])
                if target.shared:      # one socket, every profile's approvals: count only this profile's
                    entries = entries_for_profile(entries, n)
                pending[n] = len(entries)
            except Exception as exc:  # noqa: BLE001
                pending[n] = f"unavailable: {type(exc).__name__}"
        meta.note_source("socket")
        return pending
    return ("not visible: key lacks the approvals scope, or it is a profile-restricted key bound to "
            "the launch home (default), whose socket is shared with every profile in multiplex mode; "
            "approvals_pending returns such a key its own rows")


@reg.tool("orchestration_overview", toolset="status", core=True,
          description=("Orientation in ONE call, compact, with provenance in `_meta`: profiles (model, fallback "
                       "models, served / parked, live adapters, MCP servers, toolsets, effective skill counts per "
                       "platform), kanban config and boards, cron summary (active / paused / jobs whose last run "
                       "failed), plugin summary, MCP servers of the default profile, gateway platforms with "
                       "updated_at, writer_pid and a stale flag, pending approvals (needs the approvals scope) and "
                       "runs that are still going. A section that cannot be read is reported in `section_errors` "
                       "and never hides the others. skills=false skips the skill counts (faster)."),
          properties={"skills": {"type": "boolean", "default": True},
                      "plugins_exact": {"type": "boolean", "default": False,
                                        "description": "run `hermes plugins list` for exact runtime status (about 7 s)"}})
def orchestration_overview(ctx, skills=True, plugins_exact=False):
    errors: dict[str, str] = {}
    out: dict = {}
    gw = scope_gateway(_gateway_state(profile_home("default")), ctx)
    names = [n for n in list_profile_names() if ctx.principal.scopes.profile_allowed(n)]
    info = served_info()

    with ThreadPoolExecutor(max_workers=3) as ex:
        f_plug = meta.submit(ex, (lambda: {"hidden": "plugin inventory is global; use plugins_list (own profile)"}) if ctx.restricted else (_plugins_summary if plugins_exact else _plugins_config_summary))
        f_kan = meta.submit(ex, (lambda: {"hidden": "kanban config and boards are global; use kanban_list (own tasks)"}) if ctx.restricted else _kanban_summary)
        f_prof = meta.submit(ex, OV.profile_details, names, gw, with_skills=skills)
        for key, fut in (("plugins", f_plug), ("kanban", f_kan), ("profiles", f_prof)):
            try:
                out[key] = fut.result()
            except Exception as exc:  # noqa: BLE001
                errors[key] = f"{type(exc).__name__}: {str(exc)[:200]}"

    if "profiles" in out:                         # compact rows instead of the full detail dicts
        rows = {}
        for n, d in out["profiles"].items():
            sk = d.get("skills") or {}
            rows[n] = {"model": d["model"], "provider": d["provider"],
                       "fallback": [f"{e.get('provider')}/{e.get('model')}" for e in d["fallback_chain"]],
                       "served": d["served"], "parked": d["parked"],
                       "adapters_connected": d["adapters"]["connected"], "adapters_stale": d["adapters"]["stale_entries"],
                       "toolsets": d["toolsets"], "platform_toolsets": sorted(d["platform_toolsets"]),
                       "mcp_enabled": d["mcp"]["enabled"], "mcp_disabled_count": len(d["mcp"]["disabled"]),
                       "skills_total": sk.get("total"), "skills_effective": sk.get("effective_by_platform"),
                       **({"skills_error": sk["error"]} if sk.get("error") else {})}
        out["profiles"] = rows
    try:
        crons = {"default": OV.cron_summary("default")}
        for n in names:
            if n != "default":
                c = OV.cron_summary(n)
                if c.get("jobs"):
                    crons[n] = c
        out["cron"] = crons
    except Exception as exc:  # noqa: BLE001
        errors["cron"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    try:
        out["mcp_default"] = OV.mcp_sets(OV.raw_config(names[0] if ctx.restricted else "default"))
    except Exception as exc:  # noqa: BLE001
        errors["mcp"] = str(exc)[:200]

    out["gateway"] = {"state": gw.get("state"), "pid": gw.get("pid"), "pid_alive": gw.get("pid_alive"),
                      "health": gw.get("health"), "active_agents": gw.get("active_agents"),
                      "code_version": gw.get("code_version"), "attention": gw.get("attention"),
                      "served_profiles": gw.get("served_profiles"), "parked_profiles": gw.get("parked_profiles"),
                      "served_source": {"status": info["status"], "reason": info["reason"]},
                      "platforms": {**{k: {"state": v["state"], "updated_at": v["updated_at"],
                                           "writer_pid": v["writer_pid"], "stale": False,
                                           **({"config_disabled": True} if v.get("config_disabled") else {})}
                                       for k, v in (gw.get("platforms") or {}).items()},
                                    **{k: {"state": v["state"], "updated_at": v["updated_at"],
                                           "writer_pid": v["writer_pid"], "stale": True,
                                           "stale_reason": v["stale_reason"]}
                                       for k, v in (gw.get("stale_platforms") or {}).items()}}}
    out["approvals_pending"] = approvals_overview(ctx, names, info)
    try:
        from .inbox import runs_active
        r = runs_active(ctx)
        out["runs_active"] = {"count": r["count"], "runs": r["runs"][:10]}
    except Exception as exc:  # noqa: BLE001
        errors["runs_active"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    if errors:
        out["section_errors"] = errors
    return out


@reg.tool("hermes_status", toolset="status", core=True,
          description=("START HERE. Level-0 digest in one cheap call (well under 1 KB): `verdict` (ok, degraded, down), "
                       "gateway state and platforms that are not connected, how many profiles are reachable, runs still "
                       "going: `runs.mine` (started with your key; admin keys also see `other_keys`) and `gateway_agents` (everything the "
                       "gateway itself runs, by kind, with `idle_5m` / `wedged` when something has been silent), pending approvals (counts, needs the approvals scope) "
                       "and YOUR inbox: unread events by kind, `older_unread` backlog and failed runs of the last 24 h. "
                       "Counts only, never event text. `next` names the tool to call for whatever needs attention; "
                       "details live one level down (inbox_poll, runs_active, approvals_pending, status_gateway), the "
                       "whole setup in orchestration_overview, per-profile configuration in hermes_health "
                       "detail=true."),
          properties={"consumer": {"type": "string", "minLength": 1, "maxLength": 48,
                                   "pattern": "^[A-Za-z0-9][A-Za-z0-9_.:@/-]*$",
                                   "description": "Same consumer label you pass to inbox_poll (see there)"}})
def hermes_status(ctx, consumer=None):
    from ..adapters.inbox_store import get_inbox
    from .inbox import _consumer, _profiles, runs_active
    gw = scope_gateway(_gateway_state(profile_home("default")), ctx)
    info = served_info()
    names = [n for n in list_profile_names() if ctx.principal.scopes.profile_allowed(n)]
    reach = [n for n in names if n in (gw.get("served_profiles") or [])] if gw.get("state") == "running" else []
    platforms = gw.get("platforms") or {}
    bad = {k: v.get("state") for k, v in platforms.items() if v.get("state") != "connected"}
    state = gw.get("state")
    verdict = "down" if state != "running" else ("degraded" if gw.get("health") != "ok" or bad or gw.get("attention")
                                                  else "ok")
    out: dict = {"verdict": verdict,
                 "gateway": {"state": state, "pid": gw.get("pid"),
                             "platforms_connected": len(platforms) - len(bad), "platforms_total": len(platforms),
                             **({"not_connected": bad} if bad else {}),
                             **({"attention": gw.get("attention")} if gw.get("attention") else {})},
                 "profiles": {"reachable": len(reach), "total": len(names),
                              **({"not_reachable": sorted(set(names) - set(reach))} if set(names) - set(reach) else {})}}
    try:
        from ..adapters import active_work as AW
        mine = runs_active(ctx)["runs"]
        out["runs"] = {"mine": len(mine),
                       "waiting_for_approval": sum(1 for r in mine if "approval" in str(r.get("activity"))
                                                   or r.get("status") == "waiting_for_approval")}
        if ctx.principal.scopes.access == "admin":     # other keys' runs started through hermcp (counts only)
            allr = get_inbox(ctx.settings).running(key_id=None)
            out["runs"]["other_keys"] = max(0, len(allr) - len(mine))
        out["gateway_agents"] = AW.health_digest(
            AW.snapshot(allowed_profiles=set(ctx.principal.scopes.profiles) if ctx.restricted else None))
    except Exception as exc:  # noqa: BLE001
        out["runs"] = {"error": f"{type(exc).__name__}"}
    appr = approvals_overview(ctx, names, info)
    if isinstance(appr, dict):
        counts = {n: c for n, c in appr.items() if isinstance(c, int) and c}
        out["approvals_pending"] = {"total": sum(counts.values()), **({"by_profile": counts} if counts else {})}
    else:
        out["approvals_pending"] = "not visible to this key"
    out["inbox"] = get_inbox(ctx.settings).digest(profiles=_profiles(ctx), consumer=_consumer(ctx, consumer))
    nxt = []
    if verdict != "ok":
        nxt.append("status_gateway")
    if out["inbox"]["unread"]:
        nxt.append("inbox_poll")
    if out["inbox"]["older_unread"]:
        nxt.append("inbox_ack all=true (or inbox_poll include_older=true)")
    if out["inbox"]["failed_runs_24h"]:
        nxt.append("inbox_list kind=run_failed, then chat_run_status <run_id>")
    if isinstance(out["approvals_pending"], dict) and out["approvals_pending"]["total"]:
        nxt.append("approvals_pending")
    ga = out.get("gateway_agents") or {}
    if ga.get("idle_5m") or ga.get("wedged"):
        nxt.append("agents_live (idle_s / wedged per agent)")
    if out["runs"].get("mine"):
        nxt.append("runs_active")
    out["next"] = nxt or ["nothing needs attention"]
    return out
