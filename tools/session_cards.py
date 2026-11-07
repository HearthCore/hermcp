"""Ledger of client sessions: hermcp issues and remembers the `client_session` handles.

A session registers once (`session_card_set`), gets a handle shaped by what kind of client it is
(client_profiles.py), and passes that handle to chat_send and the run-control tools so every run can be attributed
to the session that started it. Cards are free labels (purpose, role); they never restrict what a key may do.

Both tools are access=read in the `inbox` toolset, like inbox_ack: they only touch hermcp's own ledger, never
Hermes state, so a read-only key (a live-voice session) can register and list.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from ..adapters.inbox_store import get_inbox
from ..client_profiles import KINDS, NATIVE_RE, new_handle
from ..redact import redact_text
from ..registry import ToolError
from . import reg

HANDLE = {"type": "string", "pattern": "^[A-Za-z0-9_.:-]{1,80}$"}
PURPOSE = {"type": "string", "maxLength": 160,
           "description": "What this session is working on (free text; a label, not an identifier)"}
ROLE = {"type": "string", "maxLength": 60,
        "description": "Self-chosen role label, e.g. orchestrator, reviewer. Informational only: it never limits access"}
USAGE = ("Pass this handle as client_session to chat_send and to chat_run_steer / chat_run_cancel. hermcp remembers it "
         "for this key; after a new chat or a compacted context, find it again with session_cards_list.")


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if ts else None


def _clean(text):
    return redact_text(" ".join(str(text).split()))[:160] if text else None


def _card(row: dict, *, you: bool = False, show_key: bool = False) -> dict:
    out = {"client_session": row["handle"], "client": row["client"], "kind": row.get("kind"),
           "purpose": row.get("purpose"), "role": row.get("role"),
           "created_at": _iso(row["created_at"]), "last_seen": _iso(row["last_seen"])}
    if row.get("native_id"):
        out["native_id"] = row["native_id"]
    if row.get("predecessor"):
        out["continues"] = row["predecessor"]
    if row.get("successor"):
        out["taken_over_by"] = row["successor"]
    if "open_runs" in row:
        out["open_runs"] = row["open_runs"]
    if show_key:
        out["registered_by"] = row["key_name"]
    if you:
        out["you"] = True
    return out


@reg.tool("session_card_set", toolset="inbox",
          description=("Register this session and get its client_session handle, or update its card. Without "
                       "client_session a new card is created (hermcp shapes the handle by the kind of client; if you "
                       "pass native_id, e.g. Claude Code's session id, registering again returns the same card). "
                       "With client_session the card's purpose, role or kind is updated. The card is a label for "
                       "attribution: it never changes what this key may do. " + USAGE),
          properties={"client_session": {**HANDLE, "description": "Update this existing card instead of creating one"},
                      "kind": {"type": "string", "enum": list(KINDS),
                               "description": "What surface this is (chat and voice are both ClaudeAI): refines the handle"},
                      "purpose": PURPOSE, "role": ROLE,
                      "recover_from": {**HANDLE, "description": "Take over an earlier session of this key: you get a NEW "
                                       "handle that continues it and its open runs move to you. Without confirm=true "
                                       "you only get a preview."},
                      "confirm": {"type": "boolean", "default": False,
                                  "description": "Apply a recover_from (otherwise it is only previewed)"},
                      "native_id": {"type": "string", "pattern": NATIVE_RE.pattern,
                                    "description": "The client's own session id, if it knows one (alias for this card)"}})
def session_card_set(ctx, client_session=None, kind=None, purpose=None, role=None, native_id=None,
                     recover_from=None, confirm=False):
    box = get_inbox(ctx.settings)
    p = ctx.principal
    native = native_id or p.native_session or None
    if recover_from:
        if client_session:
            raise ToolError("invalid_arguments", "recover_from registers a new session; do not pass client_session")
        old = box.session_get(recover_from)
        if not old or old["key_id"] != p.id:
            raise ToolError("unknown_client_session", f"no session {recover_from!r} of this key to recover; "
                            "session_cards_list shows yours")
        if old["successor"]:
            raise ToolError("already_recovered", f"session {recover_from!r} was already taken over by "
                            f"{old['successor']!r}; use that handle", successor=old["successor"])
        idle = int(time.time() - old["last_seen"])
        if not confirm:
            return {"recover": "preview", "applied": False, "from": _card({**old, "open_runs": box.session_open_runs(recover_from)}),
                    "idle_seconds": idle,
                    **({"warning": f"that session was active {idle // 60} min ago: if its chat is still open, two "
                                   "threads would act as one session. Only continue if it is really finished."}
                       if idle < 600 else {}),
                    "next": "repeat the call with confirm=true to take it over: you get a new handle, its open runs "
                            "move to you and the old handle is marked as handed over"}
        row, moved = box.session_recover(
            old=recover_from, key_id=p.id, key_name=p.name, client=p.client or "unknown", kind=kind,
            purpose=_clean(purpose), role=_clean(role), native_id=native,
            handle_factory=lambda: new_handle(p.client or "unknown", kind or old["kind"]))
        return {"created": True, "recovered": {"from": recover_from, "open_runs_moved": moved},
                "card": _card(row, you=True), "usage": USAGE}
    if client_session:
        row = box.session_get(client_session)
        if not row:
            raise ToolError("unknown_client_session", f"no session {client_session!r}: register with session_card_set "
                            "without client_session to get one")
        if row["key_id"] != p.id:
            raise ToolError("forbidden", f"session {client_session!r} belongs to another key")
        fields = {k: v for k, v in (("kind", kind), ("purpose", _clean(purpose)), ("role", _clean(role)),
                                     ("native_id", native)) if v is not None}
        row = box.session_update(client_session, **fields)
        return {"created": False, "card": _card(row, you=True), "usage": USAGE}
    row, created = box.session_register(
        key_id=p.id, key_name=p.name, client=p.client or "unknown", kind=kind, purpose=_clean(purpose),
        role=_clean(role), native_id=native, handle_factory=lambda: new_handle(p.client or "unknown", kind))
    if not created and (purpose or role or kind):
        row = box.session_update(row["handle"], **{k: v for k, v in (("kind", kind), ("purpose", _clean(purpose)),
                                                                      ("role", _clean(role))) if v is not None})
    return {"created": created, "card": _card(row, you=True), "usage": USAGE}


@reg.tool("session_cards_list", toolset="inbox",
          description=("List session cards: handle, client, kind, purpose, role, last_seen and how many runs are open. "
                       "Default scope=own lists the cards this key registered (use it to find your handle again; pass "
                       "your handle as client_session to have it marked `you`). scope=all lists cards of every key "
                       "and needs access=admin. Cards are labels, not permissions."),
          properties={"scope": {"type": "string", "enum": ["own", "all"], "default": "own"},
                      "client_session": {**HANDLE, "description": "Your own handle, to mark your card"},
                      "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50}})
def session_cards_list(ctx, scope="own", client_session=None, limit=50):
    if scope == "all" and ctx.principal.scopes.access != "admin":
        raise ToolError("forbidden", "scope=all needs access=admin")
    rows = get_inbox(ctx.settings).sessions_list(key_id=None if scope == "all" else ctx.principal.id, limit=limit)
    cards = [_card(r, you=(r["handle"] == client_session), show_key=(scope == "all")) for r in rows]
    return {"scope": scope, "count": len(cards), "cards": cards}

