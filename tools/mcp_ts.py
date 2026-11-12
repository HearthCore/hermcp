"""mcp toolset: configured MCP servers (list/add/remove/enable/disable/test). Secrets are masked."""
from __future__ import annotations

from ..adapters import hconfig as H
from ..adapters import listing as L
from ..redact import looks_secret, redact
from ..registry import ToolError
from ._cli import run_write
from . import reg

SERVER = {"type": "string", "pattern": "^[A-Za-z0-9_.-]{1,64}$", "description": "MCP server name"}
SECRETY = ("env", "headers", "auth", "oauth")


def _mask_server(cfg):
    """READ style (edge markers); env/headers/auth/oauth values: ${VAR} refs and harmless values stay, rest masked."""
    return redact(cfg or {}, "", strict=True, partial=True)


def _servers(profile):
    return H.raw_get(profile, "mcp_servers") or {}


def _transport(c):
    c = c or {}
    return "url" if c.get("url") else ("stdio" if c.get("command") else "unknown")


@reg.tool("mcp_list", toolset="mcp", profile_aware=True, core=True,
          description=('Configured MCP servers of a profile. DEFAULT: compact, only ENABLED servers (name -> enabled, transp'
                       'ort); `_meta.list` counts the hidden disabled ones. '
                       'include=all adds them. view=summary = counters per transport. profiles=all or a comma list = matrix '
                       'server x profile (on | off | absent), read live per profile. detail=true restores the full config (e'
                       'nv, header, auth, oauth values masked as abcd********wxyz / ********; ${VAR} references stay). Filters: query, source = transport'
                       ' (url | stdio), limit/offset; total, returned, truncated are always stated.'),
          properties={**L.props(category=False), "source": {"type": "string", "enum": ["url", "stdio"],
                                                            "description": "transport"},
                      "detail": {"type": "boolean", "default": False}})
def mcp_list(ctx, view="list", include="active", query=None, limit=100, offset=0, source=None, profiles=None, detail=False):
    matrix = L.pick_profiles(ctx, profiles)
    if matrix is not None:
        table: dict[str, dict] = {}
        per = {}
        for prof in matrix:
            srv = _servers(prof)
            per[prof] = {"configured": len(srv), "enabled": sum(1 for c in srv.values() if (c or {}).get("enabled", True))}
            for n, c in srv.items():
                r = table.setdefault(n, {"name": n, "transport": _transport(c), "profiles": {}})
                r["profiles"][prof] = "on" if (c or {}).get("enabled", True) else "off"
        rows = []
        for r in table.values():
            if (source and r["transport"] != source) or not L.matches(query, r["name"]):
                continue
            for prof in matrix:
                r["profiles"].setdefault(prof, "absent")
            r["active_in"] = [p for p in matrix if r["profiles"][p] == "on"]
            r["_active"] = bool(r["active_in"])
            rows.append(r)
        n_active = sum(1 for r in rows if r["_active"])
        shown = rows if include == "all" else [r for r in rows if r["_active"]]
        L.note(tool="mcp_list", include=include, view=view, total=len(rows), active=n_active, disabled=len(rows) - n_active,
               shown=len(shown), hidden_by_default=len(rows) - n_active, extra={"matrix_profiles": matrix})
        if view == "summary":
            return {"profiles": matrix, "per_profile": per, "matching": len(rows), "active": n_active,
                    "by_transport": L.tally(rows, "transport", "_active")}
        out = [{k: v for k, v in r.items() if k != "_active"} for r in sorted(shown, key=lambda r: r["name"])]
        pr, pg = L.page(out, limit, offset)
        return {"count": pg["total"], **pg, "servers": pr, "profiles": matrix, "per_profile": per,
                "legend": "profiles: on = enabled, off = disabled, absent = not configured"}
    srv = _servers(ctx.profile)
    rows = [{"name": n, "enabled": (c or {}).get("enabled", True), "transport": _transport(c), "_c": c}
            for n, c in sorted(srv.items()) if (not source or _transport(c) == source) and L.matches(query, n)]
    n_active = sum(1 for r in rows if r["enabled"])
    shown = rows if include == "all" else [r for r in rows if r["enabled"]]
    L.note(tool="mcp_list", include=include, view=view, total=len(rows), active=n_active, disabled=len(rows) - n_active,
           shown=len(shown), hidden_by_default=len(rows) - n_active)
    if view == "summary":
        return {"configured": len(srv), "matching": len(rows), "active": n_active, "disabled": len(rows) - n_active,
                "by_transport": L.tally(rows, "transport", "enabled")}
    pr, pg = L.page(shown, limit, offset)
    servers = {}
    for r in pr:
        c = r["_c"]
        servers[r["name"]] = ({"enabled": r["enabled"], **_mask_server(c)} if detail
                              else {"enabled": r["enabled"], "transport": r["transport"]})
    return {"count": pg["total"], **pg, "servers": servers, "configured": len(srv)}


@reg.tool("mcp_get", toolset="mcp", profile_aware=True, description=("One MCP server's configuration. Env, header, auth and oauth values are masked (abcd********wxyz, "
                                                                     "or ******** when shorter than 16; ${VAR} references stay visible)."),
          properties={"name": SERVER}, required=["name"])
def mcp_get(ctx, name):
    srv = _servers(ctx.profile)
    if name not in srv:
        raise ToolError("not_found", f"no MCP server {name!r}")
    return {"name": name, **_mask_server(srv[name])}


@reg.tool("mcp_add", toolset="mcp", access="write", profile_aware=True,
          description=("Add an MCP server entry: `url` (HTTP/SSE) or `command` with optional `args` (stdio), exactly "
                       "one. Env and header values that look secret must be ${VAR} references; store the value itself "
                       "with secret_set. Fails if the name already exists."),
          properties={"name": SERVER, "url": {"type": "string"}, "command": {"type": "string"},
                      "args": {"type": "array", "items": {"type": "string"}},
                      "env": {"type": "object", "additionalProperties": {"type": "string"},
                              "description": "env vars for stdio servers; values must be ${VAR} refs or plain non-secret text"},
                      "headers": {"type": "object", "additionalProperties": {"type": "string"},
                                  "description": "HTTP headers; secret values must be ${VAR} refs"},
                      "enabled": {"type": "boolean", "default": True}}, required=["name"])
def mcp_add(ctx, name, url=None, command=None, args=None, env=None, headers=None, enabled=True):
    if bool(url) == bool(command):
        raise ToolError("invalid_arguments", "give exactly one of url or command")
    for label, d in (("env", env), ("headers", headers)):
        for k, v in (d or {}).items():
            if looks_secret(k, v):
                raise ToolError("rejected", f"{label}.{k} looks like a secret; use a ${{VAR}} reference "
                                            "and store the value with secret_set")
    entry = {"url": url} if url else {"command": command, **({"args": args} if args else {})}
    if env:
        entry["env"] = env
    if headers:
        entry["headers"] = headers
    if not enabled:
        entry["enabled"] = False

    def mut(cur):
        cur = dict(cur or {})
        if name in cur:
            raise ToolError("conflict", f"MCP server {name!r} already exists; remove it first")
        cur[name] = entry
        return cur
    return H.modify(ctx.profile, "mcp_servers", mut, ctx.dry_run)


@reg.tool("mcp_remove", toolset="mcp", access="admin", profile_aware=True, description="Remove an MCP server entry.",
          properties={"name": SERVER}, required=["name"])
def mcp_remove(ctx, name):
    def mut(cur):
        cur = dict(cur or {})
        if name not in cur:
            raise ToolError("not_found", f"no MCP server {name!r}")
        del cur[name]
        return cur
    return H.modify(ctx.profile, "mcp_servers", mut, ctx.dry_run)


def _enabled(name, value, desc):
    @reg.tool(name, toolset="mcp", access="write", profile_aware=True, description=desc,
              properties={"name": SERVER}, required=["name"])
    def _fn(ctx, name):
        def mut(cur):
            cur = dict(cur or {})
            if name not in cur:
                raise ToolError("not_found", f"no MCP server {name!r}")
            cur[name] = {**cur[name], "enabled": value}
            return cur
        return H.modify(ctx.profile, "mcp_servers", mut, ctx.dry_run)
    return _fn


_enabled("mcp_enable", True, "Enable an MCP server (takes effect for new sessions).")
_enabled("mcp_disable", False, "Disable an MCP server without removing it (takes effect for new sessions).")


@reg.tool("mcp_test", toolset="mcp", access="write", profile_aware=True,
          description=("Test the connection to a configured MCP server (can take up to 90 s). Stdio servers are "
                       "launched, so their command runs; remote servers are contacted."),
          properties={"name": SERVER}, required=["name"])
def mcp_test(ctx, name):
    return run_write(ctx, f"test mcp {name}", ["mcp", "test", name], timeout=90)
