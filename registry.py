"""Tool registry: toolsets, scope filtering, schema validation, dry_run, audit, meta tools."""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .audit import AuditLog
from .client_id import clean_handle
from .scopes import Scopes
from .settings import Settings, ignored_profiles
from . import validation
from . import meta as _meta
from .redact import redact, redact_result, redact_text

PROFILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
MAX_RESULT_CHARS = 120_000


class ToolError(Exception):
    def __init__(self, code: str, message: str, **extra: Any):
        super().__init__(message)
        self.code, self.message, self.extra = code, message, extra


class AdapterError(ToolError):
    def __init__(self, message: str, **extra: Any):
        super().__init__("adapter_error", message, **extra)


@dataclass
class Principal:
    id: str
    name: str
    scopes: Scopes
    client: str = ""        # coarse label from request headers (client_id.derive_client); attribution only
    native_session: str = ""   # the client's own session id when a header carries one (client_profiles); usually empty


@dataclass
class CallContext:
    principal: Principal
    settings: Settings
    registry: "Registry"
    tool: "Tool"
    profile: str = "default"
    dry_run: bool = False
    audit: "AuditLog | None" = None

    @property
    def bound(self) -> str | None:
        """The one profile a profile-bound key is limited to (None for unrestricted keys)."""
        return self.principal.scopes.bound_profile()

    @property
    def restricted(self) -> bool:
        return self.principal.scopes.restricted

    def own(self, profile: str | None) -> bool:
        """May this key act on `profile`? (unrestricted keys: always)"""
        return bool(profile) and self.principal.scopes.profile_allowed(profile)

    def plan(self, summary: str, **details: Any) -> dict[str, Any]:
        """Standard dry-run payload: what *would* change, nothing applied."""
        return {"dry_run": True, "applied": False, "summary": summary, **details}


@dataclass
class Tool:
    name: str
    toolset: str
    access: str
    description: str
    handler: Callable[..., Any]
    properties: dict[str, Any] = field(default_factory=dict)
    required: list[str] = field(default_factory=list)
    profile_aware: bool = False
    core: bool = False
    secrets: bool = False          # needs secrets_write scope; args fully redacted in audit
    meta: bool = False             # meta tools: always advertised, never audited themselves
    requires_approvals: bool = False  # needs the explicit `approvals` scope (never implied by '*')
    dangerous: bool = False        # exfiltration/irreversible: needs the explicit `dangerous` scope
    requires_inbox_push: bool = False  # needs the explicit `inbox_push` scope (never implied by '*')

    def input_schema(self) -> dict[str, Any]:
        props = dict(self.properties)
        if self.profile_aware:
            props.setdefault("profile", {"type": "string",
                                         "description": "Hermes profile name. Defaults to the key's only profile if the key is "
                                                        "pinned to one profile, else 'default'. Chat tools need a profile "
                                                        "with reachable=true (see profiles_list)."})
        if self.access != "read":
            props.setdefault("dry_run", {"type": "boolean", "default": False,
                                         "description": "Return the planned change without applying it"})
        schema: dict[str, Any] = {"type": "object", "properties": props, "additionalProperties": False}
        if self.required:
            schema["required"] = list(self.required)
        return schema

    def descriptor(self) -> dict[str, Any]:
        tag = {"read": "", "write": " [write]", "admin": " [admin]"}[self.access]
        if self.dangerous:
            tag += " [dangerous]"
        return {"name": self.name, "description": f"{self.description}{tag}", "inputSchema": self.input_schema()}


class Registry:
    def __init__(self) -> None:
        self.tools: dict[str, Tool] = {}

    # -- registration -------------------------------------------------------------------
    def add(self, t: Tool) -> Tool:
        if t.name in self.tools:
            raise ValueError(f"duplicate tool {t.name}")
        self.tools[t.name] = t
        return t

    def tool(self, name: str, *, toolset: str, access: str = "read", description: str,
             properties: dict | None = None, required: list[str] | None = None,
             profile_aware: bool = False, core: bool = False, secrets: bool = False, meta: bool = False,
             requires_approvals: bool = False, dangerous: bool = False, requires_inbox_push: bool = False):
        def deco(fn):
            self.add(Tool(name=name, toolset=toolset, access=access, description=description, handler=fn,
                          properties=properties or {}, required=required or [], profile_aware=profile_aware,
                          core=core, secrets=secrets, meta=meta, requires_approvals=requires_approvals,
                          dangerous=dangerous, requires_inbox_push=requires_inbox_push))
            return fn
        return deco

    # -- visibility ---------------------------------------------------------------------
    def visible(self, scopes: Scopes) -> list[Tool]:
        return [t for t in self.tools.values() if scopes.tool_allowed(t)[0]]

    def advertised(self, scopes: Scopes) -> list[Tool]:
        """tools/list: meta tools + core tools within scope (everything else via hermes_call)."""
        return [t for t in self.visible(scopes) if t.core or t.meta]

    def toolsets(self, scopes: Scopes) -> dict[str, int]:
        out: dict[str, int] = {}
        for t in self.visible(scopes):
            if not t.meta:
                out[t.toolset] = out.get(t.toolset, 0) + 1
        return out

    # -- invocation ---------------------------------------------------------------------
    def invoke(self, name: str, args: dict[str, Any] | None, principal: Principal,
               settings: Settings, audit: AuditLog | None, via: str = "") -> dict[str, Any]:
        """Run one tool. Always returns a dict: {"ok": True, "result": …} or {"ok": False, "error": {…}}."""
        args = dict(args or {})
        t0 = time.monotonic()
        started = time.time()
        col, col_token = _meta.begin()
        tool = self.tools.get(name)
        profile: str | None = None
        status, detail = "ok", ""
        try:
            if tool is None:
                raise ToolError("unknown_tool", f"unknown tool {name!r}")
            ok, why = principal.scopes.tool_allowed(tool)
            if not ok:
                raise ToolError("forbidden", f"{name}: {why}")
            try:
                validation.validate(args, tool.input_schema())
            except validation.ValidationError as exc:
                raise ToolError("invalid_arguments", exc.message, path=list(exc.absolute_path)) from None
            dry = bool(args.pop("dry_run", False)) if tool.access != "read" else False
            if tool.profile_aware:
                profile = args.pop("profile", None) or principal.scopes.default_profile()
                if not PROFILE_RE.match(profile):
                    raise ToolError("invalid_arguments", f"bad profile name {profile!r}")
                if profile in ignored_profiles():   # host-wide ignore list, checked before key scope
                    from .adapters.common import ignored_error
                    raise ignored_error(profile)
                if not principal.scopes.profile_allowed(profile):
                    bound = principal.scopes.bound_profile()
                    raise ToolError("forbidden", (f"this key is bound to profile {bound!r}; requested profile {profile!r}. "
                                                  "No data returned.") if bound else
                                    f"profile {profile!r} not in key scope (allowed: {', '.join(principal.scopes.profiles)})")
            ctx = CallContext(principal=principal, settings=settings, registry=self, tool=tool,
                              profile=profile or "default", dry_run=dry, audit=audit)
            call_args = {**args, **({"profile": profile} if profile else {})}
            result = redact_result(tool.toolset, tool.name, tool.handler(ctx, **args), call_args, tool.access)   # output only, never data
            out_meta = None
            if not tool.meta and result is not None and not (isinstance(result, dict) and "_meta" in result):
                from . import __version__
                # added AFTER redaction (nothing in it is secret) and as an extra field: old clients ignore it
                out_meta = _meta.build(profile if tool.profile_aware else None, col, started, version=__version__)
                if isinstance(result, dict):
                    result = {**result, "_meta": out_meta}
                    out_meta = None
            # a list result keeps its shape (old clients); its provenance travels beside it as `meta` and the
            # MCP server sends it as a second content block
            return {"ok": True, "result": result, **({"meta": out_meta} if out_meta else {})}
        except ToolError as exc:
            status, detail = exc.code, exc.message
            return {"ok": False, "error": {"code": exc.code, "message": redact_text(exc.message),
                                           **redact(exc.extra, "", strict=True)}}
        except Exception as exc:  # noqa: BLE001 - adapters must never crash the server
            status, detail = "internal_error", f"{type(exc).__name__}: {exc}"
            return {"ok": False, "error": {"code": "internal_error", "message": redact_text(detail)}}
        finally:
            _meta.end(col_token)
            if audit is not None and not (tool and tool.meta):
                logged = {} if (tool and tool.secrets) else args
                try:
                    audit.write(key_id=principal.id, key_name=principal.name,
                                tool=name if tool else f"?{name}"[:80], args=logged, profile=profile,
                                status=status, duration_ms=(time.monotonic() - t0) * 1000,
                                detail=(f"via={via} " if via else "") + detail,
                                client=principal.client, client_session=clean_handle(args.get("client_session")) or "")
                except OSError:
                    pass


def render(result: dict[str, Any]) -> str:
    text = json.dumps(result, ensure_ascii=False, indent=1, default=str)
    if len(text) > MAX_RESULT_CHARS:
        text = json.dumps({"ok": result.get("ok"), "truncated": True, "original_chars": len(text),
                           "preview": text[:MAX_RESULT_CHARS - 500]}, ensure_ascii=False)
    return text


# ----------------------------------------------------------------------------------------
# Meta tools (bound to a registry instance)
# ----------------------------------------------------------------------------------------
def _score(tool: Tool, terms: list[str]) -> int:
    hay = f"{tool.name} {tool.toolset} {tool.description}".lower()
    s = 0
    for term in terms:
        if term in tool.name.lower():
            s += 3
        if term == tool.toolset:
            s += 3
        if term in hay:
            s += 1
    return s


def install_meta_tools(reg: Registry) -> None:
    @reg.tool("hermes_search_tools", toolset="meta", meta=True,
              description="Search all tools available to this key by keyword (name, toolset, description). "
                          "Use hermes_describe_tool for the schema, hermes_call to run.",
              properties={"query": {"type": "string"}, "toolset": {"type": "string"},
                          "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20}})
    def search(ctx: CallContext, query: str = "", toolset: str | None = None, limit: int = 20):
        terms = [w for w in re.split(r"\W+", query.lower()) if w]
        out = []
        for t in ctx.registry.visible(ctx.principal.scopes):
            if t.meta or (toolset and t.toolset != toolset):
                continue
            sc = _score(t, terms) if terms else 1
            if sc:
                out.append((sc, t))
        out.sort(key=lambda p: (-p[0], p[1].name))
        return {"count": len(out), "tools": [
            {"name": t.name, "toolset": t.toolset, "access": t.access, "description": t.description}
            for _, t in out[:limit]], "toolsets": ctx.registry.toolsets(ctx.principal.scopes)}

    @reg.tool("hermes_describe_tool", toolset="meta", meta=True,
              description="Description, access level and JSON input schema of one tool in your scope. Read it "
                          "before using a tool you have not called yet, especially write/admin tools.",
              properties={"name": {"type": "string"}}, required=["name"])
    def describe(ctx: CallContext, name: str):
        t = ctx.registry.tools.get(name)
        if t is None or not ctx.principal.scopes.tool_allowed(t)[0]:
            raise ToolError("unknown_tool", f"no such tool in your scope: {name!r}")
        return {**t.descriptor(), "toolset": t.toolset, "access": t.access, "profile_aware": t.profile_aware}

    @reg.tool("hermes_call", toolset="meta", meta=True,
              description="Run any tool in your scope by name, e.g. name='chat_run_approval', args={...}. Put "
                          "`profile` and `dry_run` inside `args`. Returns the tool's result, or its error code and "
                          "message plus detail fields (for example pending_request_id, offered, reachable_profiles, "
                          "retry_after).",
              properties={"name": {"type": "string"}, "args": {"type": "object"}}, required=["name"])
    def call(ctx: CallContext, name: str, args: dict | None = None):
        if name in ("hermes_call",):
            raise ToolError("invalid_arguments", "hermes_call cannot call itself")
        res = ctx.registry.invoke(name, args or {}, ctx.principal, ctx.settings, ctx.audit, via="hermes_call")
        if not res["ok"]:
            err = dict(res["error"])       # keep the detail fields (pending_request_id, offered, retry_after, ...)
            raise ToolError(err.pop("code"), err.pop("message"), **err)
        return res["result"]

    @reg.tool("hermes_whoami", toolset="meta", meta=True,
              description="Show the calling key's name, scopes and visible toolsets.")
    def whoami(ctx: CallContext):
        p = ctx.principal
        return {"key_id": p.id, "key_name": p.name, "scopes": p.scopes.to_dict(),
                "bound_profile": p.scopes.bound_profile(),
                "profile_restricted": p.scopes.restricted,
                "toolsets": ctx.registry.toolsets(p.scopes),
                "visible_tool_count": len([t for t in ctx.registry.visible(p.scopes) if not t.meta])}
