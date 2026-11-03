"""plugins toolset: list, info, enable/disable, install/update/remove (admin)."""
from __future__ import annotations

from ..adapters import listing as L
from ..adapters.common import cli_json, cli_text
from ..registry import ToolError
from ._cli import add_opts, run_write
from . import reg

NAME = {"type": "string", "pattern": "^[A-Za-z0-9_./-]{1,100}$", "description": "Plugin name"}


def _p(ctx):
    return None if ctx.profile == "default" else ctx.profile


def _prow(p):
    return {"name": p.get("name"), "status": p.get("status"), "source": p.get("source"), "version": p.get("version"),
            "active": p.get("status") == "enabled"}


def _matches_source(p, source):
    return not source or str(p.get("source") or "").startswith(source)


@reg.tool("plugins_list", toolset="plugins", profile_aware=True, core=True,
          description=('Installed plugins of a profile. DEFAULT: compact, only ENABLED plugins (name, status, source, versio'
                       'n); `_meta.list` counts the hidden ones (changed: it used to list all with descriptions). include=al'
                       'l adds disabled ones. view=summary = counters per source. profiles=all or a comma list = matrix plug'
                       'in x profile (on | off | absent), each profile asked live via its own `hermes plugins list` (about 7'
                       ' s each, in parallel). Filters: query, source (prefix: bundled, user, git, catalog), limit/offset; t'
                       'otal, returned, truncated are always stated. detail=true adds descriptions.'),
          properties={**L.props(category=False), "user_only": {"type": "boolean", "default": False},
                      "enabled_only": {"type": "boolean", "default": False, "description": "legacy: same as include=active"},
                      "detail": {"type": "boolean", "default": False}})
def plugins_list(ctx, user_only=False, enabled_only=False, view="list", include="active", query=None, limit=100, offset=0,
                 source=None, profiles=None, detail=False):
    matrix = L.pick_profiles(ctx, profiles)
    if matrix is not None:
        from concurrent.futures import ThreadPoolExecutor
        from .. import meta
        with ThreadPoolExecutor(max_workers=4) as ex:
            futs = {n: meta.submit(ex, cli_json, ["plugins", "list", "--json"], None if n == "default" else n, 90) for n in matrix}
            data = {n: f.result() for n, f in futs.items()}
        table: dict[str, dict] = {}
        for n, rows_ in data.items():
            for p in rows_:
                r = table.setdefault(p["name"], {"name": p["name"], "source": p.get("source"), "profiles": {}})
                r["profiles"][n] = "on" if p.get("status") == "enabled" else "off"
        rows = []
        for r in table.values():
            if not _matches_source(r, source) or not L.matches(query, r["name"]):
                continue
            for n in matrix:
                r["profiles"].setdefault(n, "absent")
            r["active_in"] = [n for n in matrix if r["profiles"][n] == "on"]
            r["_active"] = bool(r["active_in"])
            rows.append(r)
        n_active = sum(1 for r in rows if r["_active"])
        shown = rows if include == "all" else [r for r in rows if r["_active"]]
        L.note(tool="plugins_list", include=include, view=view, total=len(rows), active=n_active, disabled=len(rows) - n_active,
               shown=len(shown), hidden_by_default=len(rows) - n_active, extra={"matrix_profiles": matrix})
        if view == "summary":
            return {"profiles": matrix, "matching": len(rows), "active": n_active,
                    "by_source": L.tally([{**r, "source": str(r["source"]).split("@")[0]} for r in rows], "source", "_active")}
        out = [{k: v for k, v in r.items() if k != "_active"} for r in sorted(shown, key=lambda r: r["name"])]
        pr, pg = L.page(out, limit, offset)
        return {"count": pg["total"], **pg, "plugins": pr, "profiles": matrix,
                "legend": "profiles: on = enabled, off = disabled or not enabled, absent = not installed in that profile"}
    argv = ["plugins", "list", "--json"]
    add_opts(argv, user=user_only, enabled=enabled_only)
    data = cli_json(argv, _p(ctx), 60)
    rows = [p for p in data if _matches_source(p, source) and L.matches(query, p.get("name"), p.get("description"))]
    n_active = sum(1 for p in rows if p.get("status") == "enabled")
    shown = rows if (include == "all" and not enabled_only) else [p for p in rows if p.get("status") == "enabled"]
    L.note(tool="plugins_list", include=include, view=view, total=len(rows), active=n_active, disabled=len(rows) - n_active,
           shown=len(shown), hidden_by_default=len(rows) - n_active, extra={"installed_total": len(data)})
    if view == "summary":
        return {"installed": len(data), "matching": len(rows), "active": n_active, "disabled": len(rows) - n_active,
                "by_source": L.tally([{**_prow(p), "source": str(p.get("source")).split("@")[0]} for p in rows], "source"),
                "by_status": L.tally([{"status": p.get("status"), "active": True} for p in rows], "status")}
    out = []
    for p in shown:
        r = _prow(p)
        if detail:
            r["description"] = p.get("description")
        out.append(r)
    pr, pg = L.page(out, limit, offset)
    return {"count": pg["total"], **pg, "plugins": pr, "installed": len(data)}


@reg.tool("plugins_info", toolset="plugins", profile_aware=True, description="Details of one installed plugin (free text).",
          properties={"name": NAME}, required=["name"])
def plugins_info(ctx, name):
    return {"name": name, "info": cli_text(["plugins", "show", name], _p(ctx), 60).strip()[-8000:]}


@reg.tool("plugins_check_updates", toolset="plugins", description="Check installed plugins for available updates (read-only; global, not per profile).")
def plugins_check_updates(ctx):
    return {"report": cli_text(["plugins", "check-updates"], None, 120).strip()[-8000:]}


@reg.tool("plugins_enable", toolset="plugins", access="write", profile_aware=True,
          description="Enable a plugin for this profile (never grants built-in tool override).",
          properties={"name": NAME}, required=["name"])
def plugins_enable(ctx, name):
    return run_write(ctx, f"enable plugin {name}", ["plugins", "enable", name, "--no-allow-tool-override"])


@reg.tool("plugins_disable", toolset="plugins", access="write", profile_aware=True,
          description="Disable a plugin without removing it.", properties={"name": NAME}, required=["name"])
def plugins_disable(ctx, name):
    return run_write(ctx, f"disable plugin {name}", ["plugins", "disable", name])


@reg.tool("plugins_install", toolset="plugins", access="admin",
          description=("Install a plugin from the catalog, a Git URL or owner/repo. Installs DISABLED unless "
                       "enable=true. Plugin code is third-party and runs inside the agent once enabled."),
          properties={"identifier": {"type": "string", "minLength": 1, "maxLength": 300},
                      "enable": {"type": "boolean", "default": False}, "ref": {"type": "string", "pattern": "^[0-9a-f]{40}$"}},
          required=["identifier"])
def plugins_install(ctx, identifier, enable=False, ref=None):
    if identifier.startswith("-"):
        raise ToolError("invalid_arguments", "identifier must not start with '-'")
    argv = ["plugins", "install", identifier, "--enable" if enable else "--no-enable", "--no-deps"]
    add_opts(argv, ref=ref)
    return run_write(ctx, f"install plugin {identifier}", argv, timeout=300)


@reg.tool("plugins_update", toolset="plugins", access="admin", description="Pull the latest changes of an installed plugin (third-party code).",
          properties={"name": NAME}, required=["name"])
def plugins_update(ctx, name):
    return run_write(ctx, f"update plugin {name}", ["plugins", "update", name], timeout=300)


@reg.tool("plugins_remove", toolset="plugins", access="admin", description="Remove an installed plugin directory.",
          properties={"name": NAME}, required=["name"])
def plugins_remove(ctx, name):
    return run_write(ctx, f"remove plugin {name}", ["plugins", "remove", name])
