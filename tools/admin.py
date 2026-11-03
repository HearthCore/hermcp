"""admin toolset: audit tail and key overview. Key *management* (create/rotate/revoke) stays in the
local CLI on purpose: a remote key must not be able to mint keys."""
from __future__ import annotations

from ..audit import AuditLog
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
