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
    if ctx.principal.scopes.approvals and not (ctx.restricted and "default" in names):
        pending = {}
        from ..adapters.served import reachable
        from ..bridge import call as bridge_call, socket_path
        for n in names:
            if reachable(n, info) is not True:
                continue
            try:
                resp = bridge_call(socket_path(profile_home(n)), {"op": "list", "key_id": ctx.principal.id})
                pending[n] = len(resp.get("pending", [])) if resp.get("ok") else f"error: {resp.get('error')}"
            except Exception as exc:  # noqa: BLE001
                pending[n] = f"unavailable: {type(exc).__name__}"
        meta.note_source("socket")
        out["approvals_pending"] = pending
    else:
        out["approvals_pending"] = "not visible: key lacks the approvals scope or is bound to the default profile (shared bridge)"
    try:
        from .inbox import runs_active
        r = runs_active(ctx)
        out["runs_active"] = {"count": r["count"], "runs": r["runs"][:10]}
    except Exception as exc:  # noqa: BLE001
        errors["runs_active"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    if errors:
        out["section_errors"] = errors
    return out
