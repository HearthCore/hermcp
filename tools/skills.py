"""skills toolset: list, view, enable/disable (skills.disabled), install/uninstall (admin)."""
from __future__ import annotations

from pathlib import Path

from ..adapters import hconfig as H
from .. import meta
from ..adapters import listing as L
from ..adapters import safe
from ..settings import hermes_home
from ..adapters.common import call_inproc, captured, ensure_hermes_importable, hermes_scope, profile_home, run_probe
from ..registry import AdapterError, ToolError
from ._cli import run_write
from . import reg

NAME = {"type": "string", "pattern": "^[A-Za-z0-9_.-]{1,100}$", "description": "Skill name"}


def _sc():
    ensure_hermes_importable()
    try:
        import hermes_cli.skills_config as sc
        return sc
    except ImportError as exc:
        raise AdapterError(f"hermes_cli.skills_config unavailable: {exc}") from exc


def _row_status(s, platform):
    """enabled | disabled. A skill that a platform switches off is 'disabled' for that platform."""
    if not s["enabled"]:
        return "disabled"
    return "disabled" if platform and platform in s["disabled_on"] else "enabled"


@reg.tool("skills_list", toolset="skills", profile_aware=True, core=True,
          description=('Skills of a profile, computed in a fresh process (no cache). DEFAULT: compact, only ACTIVE skills (n'
                       'ame, category, source, status); `_meta.list` counts what was hidden (changed: it used to list all). '
                       'include=all adds disabled ones (status; disabled_on = platforms such as api_server, cron, oneshot th'
                       'at switch it off). view=summary = counters per category and source. profiles=all or a comma list = m'
                       "atrix skill x profile (on | off | absent), each profile read live. `platform` makes 'active' mean ac"
                       "tive there; effective_enabled_count is what a session really sees. 'dir/name' means two skills of a "
                       'tier share a name. Filters: query, category, source, limit/offset; total, returned, truncated are al'
                       'ways stated. detail=true adds path and declared_name; descriptions=true short descriptions.'),
          properties={**L.props(), "source": {"type": "string", "enum": ["local", "builtin", "hub", "external", "project"]},
                      "enabled_only": {"type": "boolean", "default": False, "description": "legacy: same as include=active"},
                      "platform": {"type": "string", "pattern": "^[a-z0-9_]{1,40}$",
                                   "description": "api_server, cron, oneshot, cli, telegram, ... : apply that platform's disabled list"},
                      "detail": {"type": "boolean", "default": False},
                      "descriptions": {"type": "boolean", "default": False}})
def skills_list(ctx, query=None, category=None, enabled_only=False, limit=100, platform=None, source=None, view="list",
                include="active", offset=0, profiles=None, detail=False, descriptions=False):
    matrix = L.pick_profiles(ctx, profiles)
    if matrix is not None:
        return _skills_matrix(ctx, matrix, query, category, source, platform, limit, offset, include, view)
    data = run_probe(ctx.profile, "skills", {"platforms": [platform] if platform else None})
    allrows = [s for s in data["skills"]
               if (not category or s.get("category") == category) and (not source or s["source"] == source)
               and L.matches(query, s["name"], s["declared_name"], s.get("description"))]
    for s in allrows:
        s["_active"] = _row_status(s, platform) == "enabled"
    n_active = sum(1 for s in allrows if s["_active"])
    shown_rows = allrows if (include == "all" and not enabled_only) else [s for s in allrows if s["_active"]]
    common = {"installed_total": data["total"], "disabled_total": data["global_disabled_total"],
              "platform_disabled_counts": data["platform_disabled_counts"],
              "effective_enabled_count": data["effective_enabled_count"], "platform": platform, "include": include}
    L.note(tool="skills_list", include=include, view=view, total=len(allrows), active=n_active,
           disabled=len(allrows) - n_active, shown=len(shown_rows), hidden_by_default=len(allrows) - n_active,
           extra={"profile_total_skills": data["total"], "platform": platform})
    if view == "summary":
        return {**common, "matching": len(allrows), "active": n_active, "disabled": len(allrows) - n_active,
                "by_category": L.tally([{**s, "category": s["category"] or "(none)"} for s in allrows], "category", "_active"),
                "by_source": L.tally(allrows, "source", "_active")}
    out = []
    for s in shown_rows:
        row = {"name": s["name"], "category": s["category"], "source": s["source"], "status": _row_status(s, platform),
               "enabled": s["enabled"]}
        if s["disabled_on"]:
            row["disabled_on"] = s["disabled_on"]
        if s["status"] != "unique":
            row["duplicate_name"] = True
        if detail:
            row.update(declared_name=s["declared_name"], category_null=s["category_null"], path=s["path"])
            if s["source_detail"]:
                row["source_detail"] = s["source_detail"]
        if detail or descriptions:
            row["description"] = s["description"] if detail else (s["description"] or "")[:80]
        out.append(row)
    rows, pg = L.page(out, limit, offset)
    return {"count": pg["total"], **pg, "skills": rows, **common, "search_roots": data["search_roots"]}


def _skills_matrix(ctx, names, query, category, source, platform, limit, offset, include, view):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {n: meta.submit(ex, run_probe, n, "skills", {"platforms": [platform] if platform else None}) for n in names}
        data = {n: f.result() for n, f in futs.items()}
    table: dict[str, dict] = {}
    for n, d in data.items():
        for s in d["skills"]:
            r = table.setdefault(s["name"], {"name": s["name"], "category": s["category"], "source": s["source"],
                                             "profiles": {}, "platform_off": {}})
            r["profiles"][n] = "on" if s["enabled"] else "off"
            if s["enabled"] and s["disabled_on"]:
                r["platform_off"][n] = s["disabled_on"]
    rows = []
    for r in table.values():
        if (category and r["category"] != category) or (source and r["source"] != source) or not L.matches(query, r["name"]):
            continue
        for n in names:
            r["profiles"].setdefault(n, "absent")
        eff_on = [n for n in names if r["profiles"][n] == "on" and not (platform and platform in r["platform_off"].get(n, []))]
        r["active_in"] = eff_on
        r["_active"] = bool(eff_on)
        rows.append(r)
    n_active = sum(1 for r in rows if r["_active"])
    shown = rows if include == "all" else [r for r in rows if r["_active"]]
    per_profile = {n: {"total": d["total"], "global_enabled": d["effective_enabled_count"]["global"],
                       "effective_by_platform": d["effective_enabled_count"],
                       "platform_disabled_counts": d["platform_disabled_counts"]} for n, d in data.items()}
    L.note(tool="skills_list", include=include, view=view, total=len(rows), active=n_active, disabled=len(rows) - n_active,
           shown=len(shown), hidden_by_default=len(rows) - n_active, extra={"matrix_profiles": names, "platform": platform})
    base = {"profiles": names, "platform": platform, "include": include, "per_profile": per_profile}
    if view == "summary":
        return {**base, "matching": len(rows), "active": n_active,
                "by_category": L.tally([{**r, "category": r["category"] or "(none)"} for r in rows], "category", "_active"),
                "by_source": L.tally(rows, "source", "_active")}
    out = [{k: v for k, v in r.items() if k != "_active" and (k != "platform_off" or v)} for r in sorted(shown, key=lambda r: r["name"])]
    page_rows, pg = L.page(out, limit, offset)
    return {"count": pg["total"], **pg, "skills": page_rows, **base,
            "legend": "profiles: on = enabled, off = disabled, absent = not installed in that profile; "
                      "platform_off = platforms that switch an enabled skill off; active_in = profiles where it is active"}


def _find_skill_md(profile: str, name: str) -> Path:
    """Profile-local SKILL.md for skills_edit (edits never touch hub copies or external dirs)."""
    home = profile_home(profile)
    hits = sorted((home / "skills").rglob(f"{name}/SKILL.md"))
    if not hits:
        raise ToolError("not_found", f"skill {name!r} not found in the profile's own skills dir (external skills are read-only)")
    return hits[0]


def _resolve(profile: str, name: str) -> dict:
    cands = run_probe(profile, "skill_resolve", {"name": name})["candidates"]
    live = [c for c in cands if c["status"] != "shadowed"] or cands
    exact = [c for c in live if name == c["load_name"]] or live
    if not exact:
        raise ToolError("not_found", f"skill {name!r} not found for profile {profile!r} (local, hub, external)")
    return {**exact[0], "other_candidates": [c["path"] for c in cands if c["path"] != exact[0]["path"]]}


@reg.tool("skills_view", toolset="skills", profile_aware=True,
          description=("Read a skill's SKILL.md for a profile: local, hub-installed and external (omh) skills, resolved "
                       "like Hermes does. Returns path, source, source_detail, the files next to SKILL.md and the "
                       "content (cut at max_chars; `truncated` says so). `file` reads one linked text file inside the "
                       "skill directory instead (references/..., scripts/...); paths outside it are refused."),
          properties={"name": NAME, "max_chars": {"type": "integer", "minimum": 500, "maximum": 60000, "default": 12000},
                      "file": {"type": "string", "maxLength": 200,
                               "description": "relative file inside the skill directory, e.g. references/api.md"}},
          required=["name"])
def skills_view(ctx, name, max_chars=12000, file=None):
    r = _resolve(ctx.profile, name)
    md = Path(r["path"])
    root = Path(r["root"])
    # A skill dir may be a symlink into the shared skills store of the default profile (profiles/<p>/skills/devops/litellm ->
    # ~/.hermes/skills/devops/litellm). That is legitimate: the link itself lives inside the profile's skills root and its
    # target is inside a skills store. Anything else (a link or path that leaves both) is still refused.
    stores = [root.resolve(), (hermes_home() / "skills").resolve()]
    real = md.resolve()
    inside_root = root in md.parents or root.resolve() in md.parents
    if not inside_root or not any(st == real or st in real.parents for st in stores):
        raise ToolError("forbidden", "resolved skill path is outside the profile's skills root and the shared skills store")
    sdir = md.parent
    target = md
    if file:
        target = safe.resolve_inside(sdir, file, what="file")
    cap = safe.read_capped(target, max_chars)
    meta.note_file(target)
    return {"name": name, "load_name": r["load_name"], "declared_name": r["declared_name"], "path": str(target),
            "skill_dir": str(sdir), "source": r["source"], "source_detail": r["source_detail"],
            "category": r["category"], "status": r["status"], "other_candidates": r["other_candidates"],
            "files": safe.list_dir_names(sdir) if not file else None,
            "truncated": cap["truncated"], "size_bytes": cap["size_bytes"], "content": cap["content"]}


def _set_enabled(profile, names, enable, dry_run):
    with hermes_scope(profile):
        sc = _sc()
        import hermes_cli.config as c
        cfg = call_inproc(c.load_config)
        known = {s["name"] for s in call_inproc(sc._list_all_skills)}
        unknown = [n for n in names if n not in known]
        if unknown:
            raise ToolError("not_found", f"unknown skills: {unknown}")
        before = set(call_inproc(sc.get_disabled_skills, cfg))
        after = (before - set(names)) if enable else (before | set(names))
        plan = {"enabled": sorted(names) if enable else [], "disabled": [] if enable else sorted(names),
                "disabled_before": len(before), "disabled_after": len(after)}
        if dry_run or after == before:
            return {**plan, "dry_run": dry_run, "applied": False}
    backup = H.make_backup(profile)
    with hermes_scope(profile):
        with captured():
            call_inproc(sc.save_disabled_skills, cfg, after)
    return {**plan, "applied": True, "backup": backup, "warnings": H.validate_file(profile)}


@reg.tool("skills_enable", toolset="skills", access="write", profile_aware=True, description="Enable skills.",
          properties={"names": {"type": "array", "items": NAME, "minItems": 1}}, required=["names"])
def skills_enable(ctx, names):
    return _set_enabled(ctx.profile, names, True, ctx.dry_run)


@reg.tool("skills_disable", toolset="skills", access="write", profile_aware=True, description="Disable skills (they stay installed).",
          properties={"names": {"type": "array", "items": NAME, "minItems": 1}}, required=["names"])
def skills_disable(ctx, names):
    return _set_enabled(ctx.profile, names, False, ctx.dry_run)


@reg.tool("skills_edit", toolset="skills", access="write", profile_aware=True,
          description=(("Edit a skill's SKILL.md: pass `content` to replace the whole file, or `old` and `new` for one "
                        "exact replacement. Writes at once (no backup); dry_run returns a unified diff and writes "
                        "nothing. Refuses paths outside the profile's skills directory.")),
          properties={"name": NAME,
                      "content": {"type": "string", "maxLength": 200000,
                                  "description": "full new SKILL.md (omit when using old/new)"},
                      "old": {"type": "string", "maxLength": 20000, "description": "exact text to replace"},
                      "new": {"type": "string", "maxLength": 200000, "description": "replacement text"}},
          required=["name"])
def skills_edit(ctx, name, content=None, old=None, new=None):
    import difflib
    p = _find_skill_md(ctx.profile, name)
    root = (profile_home(ctx.profile) / "skills").resolve()
    if not str(p.resolve()).startswith(str(root)):
        raise ToolError("forbidden", "resolved skill path escapes the skills directory")
    before = p.read_text(encoding="utf-8", errors="replace")
    if content is not None:
        after = content
    elif old is not None and new is not None:
        if before.count(old) != 1:
            raise ToolError("invalid_arguments",
                            f"'old' must occur exactly once in {name}/SKILL.md (found {before.count(old)})")
        after = before.replace(old, new, 1)
    else:
        raise ToolError("invalid_arguments", "pass either content, or both old and new")
    if after == before:
        return {"applied": False, "reason": "no change", "path": str(p)}
    diff = "".join(difflib.unified_diff(before.splitlines(keepends=True),
                                        after.splitlines(keepends=True),
                                        fromfile=f"{name}/SKILL.md (before)",
                                        tofile=f"{name}/SKILL.md (after)", n=3))[:60000]
    if ctx.dry_run:
        return ctx.plan(f"would edit {name}/SKILL.md", path=str(p), diff=diff,
                        bytes_before=len(before), bytes_after=len(after))
    p.write_text(after, encoding="utf-8")
    return {"applied": True, "path": str(p), "bytes_before": len(before), "bytes_after": len(after),
            "diff": diff[:20000]}


@reg.tool("skills_search", toolset="skills", description=("Search public skill registries (skills.sh, GitHub, ...). Uses the network; results are free "
                                                          "text."),
          properties={"query": {"type": "string", "minLength": 1}}, required=["query"])
def skills_search(ctx, query):
    from ..adapters.common import cli_text
    if query.startswith("-"):
        raise ToolError("invalid_arguments", "query must not start with '-'")
    return {"results": cli_text(["skills", "search", query], None, 90).strip()[-10000:]}


@reg.tool("skills_install", toolset="skills", access="admin", profile_aware=True,
          description=("Install a skill from a registry id or SKILL.md URL (the security scan is not overridden). "
                       "Installed skills are instructions the agent will follow, so review them afterwards."),
          properties={"identifier": {"type": "string", "minLength": 1, "maxLength": 400},
                      "name": NAME, "category": {"type": "string"}}, required=["identifier"])
def skills_install(ctx, identifier, name=None, category=None):
    if identifier.startswith("-"):
        raise ToolError("invalid_arguments", "identifier must not start with '-'")
    argv = ["skills", "install", identifier, "--yes"]
    if name:
        argv += ["--name", name]
    if category:
        argv += ["--category", category]
    return run_write(ctx, f"install skill {identifier}", argv, timeout=180)


@reg.tool("skills_uninstall", toolset="skills", access="admin", profile_aware=True,
          description="Uninstall a hub-installed skill.", properties={"name": NAME}, required=["name"])
def skills_uninstall(ctx, name):
    return run_write(ctx, f"uninstall skill {name}", ["skills", "uninstall", name], timeout=60)
