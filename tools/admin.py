"""admin toolset: audit tail and key overview. Key *management* (create/rotate/revoke) stays in the
local CLI on purpose: a remote key must not be able to mint keys."""
from __future__ import annotations

from ..audit import AuditLog
from ..capture import FILE_NAME as CAPTURE_FILE, read_records, report
from ..keys import KeyStore
from . import reg


@reg.tool("audit_tail", toolset="admin", access="admin", core=True,
          description="Tail the hermcp audit log (who called which tool, args redacted, status, duration).",
          properties={"lines": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50},
                      "tool": {"type": "string", "maxLength": 80},
                      "key": {"type": "string", "maxLength": 80, "description": "key id or name"}})
def audit_tail(ctx, lines=50, tool=None, key=None):
    log = ctx.audit or AuditLog(ctx.settings.data / "audit.jsonl")
    recs = log.tail(lines, tool=tool, key=key)
    return {"count": len(recs), "records": recs}


@reg.tool("keys_list", toolset="admin", access="admin", core=True,
          description="List hermcp keys with scopes and last use (never the secrets).")
def keys_list(ctx):
    return {"keys": [r.public() for r in KeyStore(ctx.settings.data / "keys.json").list()]}


@reg.tool("capture_report", toolset="admin", access="admin",
          description="Discovery aid for HERMCP_DEBUG_CAPTURE=1: what MCP clients really send with their requests. "
                      "Groups the recorded requests by client (User-Agent + clientInfo) and shows header names, "
                      "non-standard headers, whether an Mcp-Session-Id was ever sent, params._meta paths, RPC methods "
                      "and tool names. Tool argument values, credentials and IPs are never recorded. "
                      "Reports enabled=false when capture is off.",
          properties={"last": {"type": "integer", "minimum": 0, "maximum": 50, "default": 10,
                               "description": "also return this many raw newest records (0 = summary only)"}})
def capture_report(ctx, last=10):
    s = ctx.settings
    return {"enabled": s.debug_capture, "file": CAPTURE_FILE, "max_records": s.debug_capture_max,
            "stops_after_hours": s.debug_capture_hours,
            **report(read_records(s.data / CAPTURE_FILE), last=last),
            "note": "Headers are what hermcp sees AFTER any reverse proxy. Equal masked values share the same fp."}
