"""The one place that knows what kinds of MCP clients exist and how their sessions are named.

hermcp is the ledger of valid `client_session` handles (see sessions in adapters/inbox_store.py). This module only
decides how a handle looks for a given client and where a client's *own* session identifier could come from, so a
new client, or a client that starts exposing a session id later, is a change in this table and nowhere else.

Attribution only: nothing here is authentication or an access decision.
"""
from __future__ import annotations

import re
import secrets

# label (as derive_client() writes it) -> profile. `native_env` documents where the client itself knows a session
# id (a client can pass it as native_id when it registers); `native_headers` are request headers that carry one
# (empty today: no client sends a session identifier, see Plan v2 discovery). Fill them in when that changes and
# every registered card of that client picks the value up as its alias.
PROFILES: dict[str, dict] = {
    "ClaudeCode": {"prefix": "cc", "native_env": "CLAUDE_CODE_SESSION_ID", "native_headers": []},
    "Cowork": {"prefix": "cw", "native_env": None, "native_headers": []},
    "ClaudeAI": {"prefix": "ai", "native_env": None, "native_headers": []},
}
ALIASES = {"claude-user": "ClaudeAI", "claude-code": "ClaudeCode"}      # User-Agent products without the header

# `kind` refines the prefix when the same client label covers different surfaces (chat and voice are both ClaudeAI)
KINDS = ("chat", "voice", "code", "cowork", "agent", "script")
KIND_PREFIX = {"voice": "vo", "chat": "ai", "code": "cc", "cowork": "cw", "agent": "ag", "script": "sc"}

NATIVE_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")


def profile_for(client: str) -> dict | None:
    if client in PROFILES:
        return PROFILES[client]
    canon = ALIASES.get((client or "").lower())
    return PROFILES.get(canon) if canon else None


def prefix_for(client: str, kind: str | None = None) -> str:
    """Handle prefix: the kind wins for clients that cover several surfaces, else the client's own prefix, else
    the first letters of its label so unknown clients stay recognizable in the ledger."""
    prof = profile_for(client)
    if kind and kind in KIND_PREFIX and (prof is None or client in ("ClaudeAI", "Claude-User")):
        return KIND_PREFIX[kind]
    if prof:
        return prof["prefix"]
    letters = re.sub(r"[^a-z0-9]", "", (client or "").lower())[:3]
    return letters or "x"


def new_handle(client: str, kind: str | None = None) -> str:
    return f"{prefix_for(client, kind)}-{secrets.token_hex(4)}"


def native_from_headers(client: str, headers) -> str | None:
    """A client's own session id from a request header, if its profile names one and the client sent it."""
    prof = profile_for(client)
    if not prof:
        return None
    low = {str(k).lower(): str(v) for k, v in dict(headers).items()}
    for name in prof.get("native_headers", []):
        v = low.get(name.lower(), "").strip()
        if v and NATIVE_RE.match(v):
            return v
    return None
