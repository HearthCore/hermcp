"""profiles toolset: list, show, describe; create/delete/rename (admin)."""
from __future__ import annotations

from ..adapters import overview as OV
from ..adapters.common import (call_inproc, ensure_hermes_importable, hermes_scope, list_profile_names, profile_home)
from ..adapters.served import reachable, served_info
from ..registry import AdapterError, ToolError
from ._cli import add_opts, run_write
from . import reg

NAME = {"type": "string", "pattern": "^[a-z0-9][a-z0-9_-]{0,63}$", "description": "Profile name"}


def _meta(name):
    ensure_hermes_importable()
    try:
        from hermes_cli.profiles import read_profile_meta
        with hermes_scope("default"):
            return call_inproc(read_profile_meta, profile_home(name))
    except ImportError as exc:
        raise AdapterError(f"hermes_cli.profiles unavailable: {exc}") from exc


def _summary(name, info=None):
    from .. import _yaml as yaml
    home = profile_home(name)
    model = None
    try:
        cfg = yaml.safe_load((home / "config.yaml").read_text()) or {}
        m = cfg.get("model")
        model = m.get("default") if isinstance(m, dict) else m
    except (OSError, yaml.YAMLError):
        pass
    meta = _meta(name)
    return {"name": name, "description": meta.get("description"), "display_name": meta.get("display_name") or None,
            "model": model, "reachable": reachable(name, info), "has_state_db": (home / "state.db").exists(), "path": str(home)}


NOTE = ("reachable=true: served by the running gateway/API server, usable with chat_send and friends. "
        "reachable=false: the profile exists only as settings on disk and is NOT active; chat tools reject it "
        "(its state.db stays readable via sessions_*). reachable=\"unknown\": served_profiles could not be "
        "determined (see served_source).")


@reg.tool("profiles_list", toolset="profiles", core=True,
          description=("List the profiles this key may use. `reachable` is true if the running gateway serves the "
                       "profile: only those work with chat_send and the other chat tools. false = the profile is only "
                       "configuration on disk (its saved sessions stay readable with sessions_*); \"unknown\" = the "
                       "gateway state could not be read. `reachable_profiles` is the ready-made list of usable names. "
                       "With detail=true (default) every profile also carries `details`: model, provider, "
                       "fallback_chain, toolsets, platform_toolsets, served / parked, live adapters (and stale "
                       "entries), enabled and disabled MCP servers, and skill counts computed in a fresh process "
                       "(`effective_by_platform` is what a session on that platform really sees). "
                       "skills=false skips the skill counts (faster)."),
          properties={"detail": {"type": "boolean", "default": True}, "skills": {"type": "boolean", "default": True}})
def profiles_list(ctx, detail=True, skills=True):
    info = served_info()
    names = [n for n in list_profile_names() if ctx.principal.scopes.profile_allowed(n)]
    rank = {True: 0, "unknown": 1, False: 2}
    rows = sorted((_summary(n, info) for n in names), key=lambda r: (rank[r["reachable"]], r["name"] != "default", r["name"]))
    if detail:
        from .status import _gateway_state
        det = OV.profile_details([r["name"] for r in rows], _gateway_state(profile_home("default")), with_skills=skills)
        for r in rows:
            r["details"] = det[r["name"]]
    return {"count": len(rows), "profiles": rows,
            "reachable_profiles": [r["name"] for r in rows if r["reachable"] is True],
            "inactive_profiles": [r["name"] for r in rows if r["reachable"] is False],
            "served_source": {"status": info["status"], "reason": info["reason"], "gateway_pid": info["gateway_pid"],
                              "file": "gateway_state.json (served_profiles)"},
            "note": NOTE}


@reg.tool("profiles_show", toolset="profiles", description="Details of one profile: description, model, reachable flag and whether it has a state database.",
          properties={"name": NAME}, required=["name"])
def profiles_show(ctx, name):
    if not ctx.principal.scopes.profile_allowed(name):
        raise ToolError("forbidden", f"profile {name!r} not in key scope")
    s = _summary(name)
    s["skills_dir_entries"] = len(list((profile_home(name) / "skills").glob("*"))) if (profile_home(name) / "skills").is_dir() else 0
    return s


@reg.tool("profiles_describe", toolset="profiles", access="write",
          description="Set a profile's description (used by the kanban orchestrator for routing).",
          properties={"name": NAME, "text": {"type": "string", "minLength": 1, "maxLength": 600}},
          required=["name", "text"])
def profiles_describe(ctx, name, text):
    profile_home(name)
    return run_write(ctx, f"describe {name}", ["profile", "describe", name, "--text", text])


@reg.tool("profiles_create", toolset="profiles", access="admin",
          description="Create a profile (optionally cloning config/skills from another).",
          properties={"name": NAME, "clone_from": NAME, "description": {"type": "string", "maxLength": 600},
                      "no_skills": {"type": "boolean", "default": False}}, required=["name"])
def profiles_create(ctx, name, clone_from=None, description=None, no_skills=False):
    argv = ["profile", "create", name, "--no-alias"]
    add_opts(argv, clone_from=clone_from, description=description, no_skills=no_skills)
    return run_write(ctx, f"create profile {name}", argv, timeout=120)


@reg.tool("profiles_rename", toolset="profiles", access="admin", description="Rename a profile.",
          properties={"old_name": NAME, "new_name": NAME}, required=["old_name", "new_name"])
def profiles_rename(ctx, old_name, new_name):
    return run_write(ctx, f"rename {old_name} -> {new_name}", ["profile", "rename", old_name, new_name])


@reg.tool("profiles_delete", toolset="profiles", access="admin", dangerous=True,
          description="Delete a profile and its data (irreversible). Use dry_run first.",
          properties={"name": NAME}, required=["name"])
def profiles_delete(ctx, name):
    if name == "default":
        raise ToolError("rejected", "the default profile cannot be deleted")
    if ctx.dry_run:
        home = profile_home(name)
        return ctx.plan(f"delete profile {name}", path=str(home), has_state_db=(home / "state.db").exists(),
                        would_run=["hermes", "profile", "delete", name, "--yes"])
    return run_write(ctx, f"delete profile {name}", ["profile", "delete", name, "--yes"])
