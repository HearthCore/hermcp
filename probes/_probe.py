"""Fresh-process probe: answers ONE question about ONE profile and exits.

Why a subprocess: Hermes keeps module-level caches (config, skills catalog), reads process env such as
HERMES_PLATFORM, and resolves the project skill tier from the working directory. A long-lived server that
answers many profiles in one interpreter inherits all of that. A fresh interpreter per call cannot.

Usage: python -I _probe.py <agent_dir> <op> '<json args>'   (HERMES_HOME is set by the caller)
Output: one JSON document on stdout. Nothing is ever written.
"""
import json
import os
import sys

agent_dir, op, args = sys.argv[1], sys.argv[2], json.loads(sys.argv[3] or "{}")
sys.path.append(agent_dir)
os.environ.setdefault("HERMES_DISABLE_LAZY_INSTALLS", "1")


def _hub_and_builtin():
    hub, builtin = {}, set()
    try:
        from tools.skills_hub import HubLockFile
        hub = {e["name"]: e for e in HubLockFile().list_installed()}
    except Exception:  # noqa: BLE001
        pass
    try:
        from tools.skills_sync import _read_manifest
        builtin = set(_read_manifest())
    except Exception:  # noqa: BLE001
        pass
    return hub, builtin


def _classify(r, hub, builtin):
    from agent.skill_utils import TIER_EXTERNAL, TIER_PROJECT
    tier, decl = r.get("tier"), r.get("name")
    if tier == TIER_EXTERNAL:
        return "external", str(r.get("root"))
    if tier == TIER_PROJECT:
        return "project", str(r.get("root"))
    if decl in hub:
        return "hub", str(hub[decl].get("source", "hub"))
    if decl in builtin:
        return "builtin", None
    return "local", None


def op_skill_resolve():
    from tools import skills_tool as st
    want = args["name"]
    hub, builtin = _hub_and_builtin()
    out = []
    for r in st._skill_catalog(skip_disabled=True, include_hidden=True):
        if want in (r.get("load_name"), r.get("name"), r.get("relative_path")):
            source, detail = _classify(r, hub, builtin)
            out.append({"load_name": r.get("load_name"), "declared_name": r.get("name"), "status": r.get("status"),
                        "path": str(r.get("path")), "root": str(r.get("root")), "source": source,
                        "source_detail": detail, "category": r.get("category")})
    print(json.dumps({"candidates": out, "pid": os.getpid()}))


def op_skills():
    import hermes_cli.config as c
    from agent.skill_utils import ESSENTIAL_SKILLS, TIER_EXTERNAL, TIER_LOCAL, TIER_PROJECT, is_disabled_entry
    from hermes_cli import skills_config as sc
    from tools import skills_tool as st
    cfg = c.load_config()
    skills_cfg = cfg.get("skills") if isinstance(cfg.get("skills"), dict) else {}
    pd = skills_cfg.get("platform_disabled") if isinstance(skills_cfg.get("platform_disabled"), dict) else {}
    platforms = list(dict.fromkeys(args.get("platforms") or ["cli", "telegram", "api_server", "cron", "oneshot"]))
    platforms += [p for p in pd if p not in platforms]
    rows = st._skill_catalog(skip_disabled=True)
    rows = [r for r in rows if r.get("load_name")]
    hub, builtin = _hub_and_builtin()
    glob = sc.get_disabled_skills(cfg, None)
    per_platform = {p: sc.get_disabled_skills(cfg, p) for p in platforms}
    skills = []
    for r in rows:
        decl = r.get("name")
        source, detail = _classify(r, hub, builtin)
        en_global = not is_disabled_entry(r, glob)
        off_on = [p for p in platforms if en_global and is_disabled_entry(r, per_platform[p])]
        try:
            rel = str(r["path"].relative_to(r["root"]))
        except Exception:  # noqa: BLE001
            rel = str(r.get("relative_path") or r.get("path"))
        skills.append({"name": r["load_name"], "declared_name": decl, "category": r.get("category"),
                       "category_null": not r.get("category"), "source": source, "source_detail": detail,
                       "path": rel, "status": r.get("status"), "enabled": en_global, "disabled_on": off_on,
                       "description": (r.get("description") or "")[:200]})
    eff = {"global": sum(1 for s in skills if s["enabled"])}
    for p in platforms:
        eff[p] = sum(1 for s in skills if s["enabled"] and p not in s["disabled_on"])
    roots, active = st.skill_search_dirs()
    print(json.dumps({
        "skills": skills, "total": len(skills), "global_disabled_total": len(glob),
        "platform_disabled_counts": {p: len(skills_cfg_list) for p, skills_cfg_list in
                                      ((p, sc._normalize_skill_names(pd.get(p))) for p in pd)},
        "effective_enabled_count": eff, "platforms_evaluated": platforms,
        "essential_skills_never_disabled": sorted(ESSENTIAL_SKILLS),
        "search_roots": [{"tier": {TIER_PROJECT: "project", TIER_LOCAL: "profile", TIER_EXTERNAL: "external"}.get(t, str(t)),
                          "dir": str(d)} for t, d in roots],
        "hermes_home": os.environ.get("HERMES_HOME"), "pid": os.getpid()}, default=str))


def op_catalog_visible():
    """What an agent session on the platform in HERMES_PLATFORM really lists (runtime path, disabled applied)."""
    from tools import skills_tool as st
    rows = st.find_all_skills()
    print(json.dumps({"names": sorted(r["name"] for r in rows), "count": len(rows),
                      "platform": os.environ.get("HERMES_PLATFORM"), "pid": os.getpid()}))


def op_config():
    import hermes_cli.config as c
    print(json.dumps({"config": c.load_config(), "pid": os.getpid()}, default=str))


def op_config_raw():
    import hermes_cli.config as c
    from pathlib import Path
    print(json.dumps({"raw": c.read_user_config_raw(Path(c.get_config_path())), "pid": os.getpid()}, default=str))


def op_cron_jobs():
    import cron.jobs as cj
    from hermes_constants import get_hermes_home
    with cj.use_cron_store(get_hermes_home()):
        print(json.dumps({"jobs": cj.list_jobs(include_disabled=True), "pid": os.getpid()}, default=str))


{"catalog_visible": op_catalog_visible, "skill_resolve": op_skill_resolve, "skills": op_skills, "config": op_config, "config_raw": op_config_raw, "cron_jobs": op_cron_jobs}[op]()
