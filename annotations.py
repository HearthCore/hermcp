"""MCP ToolAnnotations for every tool: derived from the registry tier, exceptions explicit.

The MCP spec (2025-03-26, unchanged in 2026-07-28) gives every tool four hints plus a human
`title`: readOnlyHint, destructiveHint, idempotentHint, openWorldHint. Their defaults are
pessimistic (write + destructive + open world), so an unannotated tool is treated as the
riskiest thing on the surface. We set all four explicitly on every tool.

They are HINTS, not enforcement. The enforceable layer is the key's scopes:
`Scopes.tool_allowed()` decides what a key may call and `tools/list` only advertises what
that key may use. The hints exist so a client can show the right prompt before the call
reaches that gate — never encode a tier here or read one out of here.

How a tool gets its four hints
------------------------------
1. The registry tier decides the default: read -> R, write -> C, admin -> D, and any tool
   flagged `dangerous=True` -> D. That covers 101 of 148 tools with no per-tool decision.
2. `OVERRIDES` holds the 48 tools where the tier default would be wrong — the hint-lie scan
   (a read-declared tool that writes or reaches the internet) and the admin/write tools
   whose real effect is additive, idempotent, destructive or open-world.

The four booleans come from a closed set of classes, so they cannot drift apart:

    R    read-only, idempotent, closed world          -> T F T F
    R-o  read-only, idempotent, reaches the internet  -> T F T T
    C    additive write (create/send/queue), closed   -> F F F F
    C-i  additive write, repeating it changes nothing -> F F T F
    C-o  additive write that talks to the internet    -> F F F T
    D    destructive write (delete/overwrite/abort)   -> F T F F
    D-i  destructive write, repeating it is a no-op   -> F T T F
    G    generic dispatcher: arbitrary target, so the -> F T F T
         conservative union of every class it can reach

readOnlyHint=true only if the tool itself cannot mutate anything, directly or through a
dispatch/passthrough. That is how the three read-declared mutators in the registry stay
honest instead of being re-tiered (they are read so that reading needs no approval, and the
`inbox_push` / `dangerous` scopes still gate them):

    hermes_call        access=read, dispatches everything in scope  -> G
    inbox_push         access=read, appends to another inbox        -> C
    inbox_ack          access=read, marks events read               -> C-i
    session_card_set   access=read, registers/updates a card        -> C-i

openWorldHint=true where the tool's *own* I/O leaves our network: a registry or package
fetch, a provider API call, an OAuth exchange, a connection test, or `hermes doctor`'s
connectivity probes. Work the tool only queues locally stays closed — chat_send and
kanban_create hand the job to the local gateway/dispatcher, so the call itself talks to
nobody outside.

tests/test_annotations.py enforces this: every tool resolves, every override names a real
tool and actually differs from its tier default, readOnly never pairs with destructive, no
readOnly tool can mutate, and the served descriptor carries title + all four hints.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - avoids an import cycle for type checkers only
    from .registry import Tool


@dataclass(frozen=True)
class ToolAnnotations:
    """The `title` plus the four MCP ToolAnnotations hints, as served to a client."""

    title: str
    read_only: bool
    destructive: bool
    idempotent: bool
    open_world: bool
    code: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "readOnlyHint": self.read_only,
            "destructiveHint": self.destructive,
            "idempotentHint": self.idempotent,
            "openWorldHint": self.open_world,
        }


#: class code -> (read_only, destructive, idempotent, open_world)
CLASSES: dict[str, tuple[bool, bool, bool, bool]] = {
    "R": (True, False, True, False),
    "R-o": (True, False, True, True),
    "C": (False, False, False, False),
    "C-i": (False, False, True, False),
    "C-o": (False, False, False, True),
    "D": (False, True, False, False),
    "D-i": (False, True, True, False),
    "G": (False, True, False, True),
}

#: registry tier -> default class. `dangerous=True` always means D.
TIER_DEFAULT: dict[str, str] = {"read": "R", "write": "C", "admin": "D"}

#: tool name -> class code, only where the tier default would be wrong.
OVERRIDES: dict[str, str] = {
    # -- read-declared tools that write or reach the internet (the hint-lie scan) --------
    "hermes_call": "G",             # dispatches any tool in scope, admin included
    "inbox_push": "C",              # appends events to another consumer's inbox
    "inbox_ack": "C-i",             # marks this consumer's events read
    "session_card_set": "C-i",      # registers/updates a session card
    "skills_search": "R-o",         # `hermes skills search` queries public registries
    "models_list": "R-o",           # live provider model list when reachable
    "plugins_check_updates": "R-o",  # asks upstream for new plugin versions
    "status_doctor": "R-o",         # `hermes doctor` probes provider connectivity
    # -- admin-declared tools that only read --------------------------------------------
    "audit_tail": "R",
    "keys_list": "R",
    # -- admin/write tools whose real effect is additive, not destructive ----------------
    "capture_report": "C",          # writes a debug report, destroys nothing
    "providers_add": "C-i",
    "kanban_board_create": "C",
    "profiles_create": "C",
    "models_fallback_add": "C-i",
    "cron_model_set": "C-i",
    "mcp_add": "C-i",
    "secret_set": "C-i",
    # -- flags and setters: repeating the same call changes nothing ---------------------
    "sessions_pin": "C-i",
    "sessions_archive": "C-i",
    "sessions_rename": "C-i",
    "models_set": "C-i",
    "tools_enable": "C-i",
    "tools_disable": "C-i",
    "mcp_enable": "C-i",
    "mcp_disable": "C-i",
    "profiles_describe": "C-i",
    "kanban_complete": "C-i",
    "cron_edit": "C-i",
    "cron_pause": "C-i",
    "memory_off": "C-i",
    # -- overwrites and aborts: destructive, repeating them is a no-op ------------------
    "config_set": "D-i",            # overwrites a config value (backed up first)
    "config_unset": "D-i",
    "chat_run_stop": "D-i",
    "chat_run_cancel": "D-i",
    "provider_auth_cancel": "D-i",
    "models_fallback_remove": "D-i",
    "profiles_rename": "D-i",
    "secret_delete": "D-i",
    "skills_edit": "D",
    # -- refusals and approvals can authorise a destructive call ------------------------
    "approvals_answer": "D",
    "chat_run_approval": "D",
    # -- own network I/O ----------------------------------------------------------------
    "mcp_test": "C-o",              # launches a stdio server or contacts a remote one
    "provider_auth_start": "C-o",
    "provider_auth_continue": "C-o",
    "plugins_install": "C-o",
    "plugins_update": "C-o",
    "skills_install": "C-o",
}

#: Titles that the mechanical form of the name would get wrong or read badly.
TITLE_OVERRIDES: dict[str, str] = {
    "hermes_call": "Call any tool",
    "hermes_describe_tool": "Describe a tool",
    "hermes_health": "Health overview",
    "hermes_search_tools": "Search tools",
    "hermes_status": "Level-0 status digest",
    "hermes_whoami": "Show this key's identity",
    "inbox_ack": "Acknowledge inbox events",
    "inbox_push": "Push an inbox event",
    "session_card_set": "Register or update a session card",
    "session_cards_list": "List session cards",
    "clients_overview": "Sessions by client",
    "runs_active": "List active runs",
    "agents_live": "List live agents",
    "orchestration_overview": "Orchestration overview",
    "audit_tail": "Tail the audit log",
    "logs_tail": "Tail a log file",
    "keys_list": "List API keys",
    "capture_report": "Report captured client requests",
    "status_version": "Hermes version",
    "status_gateway": "Gateway status",
    "status_usage": "Usage and cost report",
    "status_doctor": "Run doctor diagnostics",
}

#: Words that must not be title-cased by the mechanical rule.
_ACRONYMS = {"mcp": "MCP", "api": "API", "id": "ID", "ids": "IDs", "json": "JSON", "url": "URL"}


def derive_title(name: str) -> str:
    """`cron_list` -> "Cron list": readable, never a rename of the tool itself."""
    words = [_ACRONYMS.get(w, w) for w in name.split("_")]
    s = " ".join(words)
    return s[:1].upper() + s[1:]


def class_for(tool: "Tool") -> str:
    """The class code for a tool: explicit override, else its registry tier."""
    if tool.name in OVERRIDES:
        return OVERRIDES[tool.name]
    if tool.dangerous:
        return "D"
    return TIER_DEFAULT[tool.access]


def annotations_for(tool: "Tool") -> ToolAnnotations:
    code = class_for(tool)
    read_only, destructive, idempotent, open_world = CLASSES[code]
    title = TITLE_OVERRIDES.get(tool.name) or derive_title(tool.name)
    return ToolAnnotations(title=title, read_only=read_only, destructive=destructive,
                           idempotent=idempotent, open_world=open_world, code=code)
