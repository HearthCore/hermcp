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

from ..adapters import clients_view
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
                      "claim_runs": {"type": "array", "items": {"type": "string", "pattern": "^[A-Za-z0-9_.:-]{1,120}$"},
                                     "maxItems": 50,
                                     "description": "Claim these open runs of this key that no session owns yet (they "
                                     "were started without client_session). Preview first; confirm=true applies. "
                                     "runs_active lists them under session_overview.unclaimed_runs."},
                      "claim_unclaimed": {"type": "boolean", "default": False,
                                          "description": "Claim ALL open runs of this key that no session owns"},
                      "native_id": {"type": "string", "pattern": NATIVE_RE.pattern,
                                    "description": "The client's own session id, if it knows one (alias for this card)"}})
def session_card_set(ctx, client_session=None, kind=None, purpose=None, role=None, native_id=None,
                     recover_from=None, confirm=False, claim_runs=None, claim_unclaimed=False):
    box = get_inbox(ctx.settings)
    p = ctx.principal
    native = native_id or p.native_session or None
    if claim_runs or claim_unclaimed:
        if recover_from:
            raise ToolError("invalid_arguments", "claim_runs and recover_from are separate steps; do one at a time")
        return _claim(ctx, box, client_session, kind, purpose, role, native, claim_runs, claim_unclaimed, confirm)
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
        box.push_ledger(
            kind="session_taken_over", ref=recover_from, severity="warning" if idle < 600 else "info",
            summary=f"Session {recover_from} was taken over by {row['handle']} ({moved} open runs moved). If this "
                    "chat is still working as that session, stop: its handle is retired (session_handed_over). "
                    "Register anew with session_card_set, or ask the new session; both appear in session_cards_list.")
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


def _claim(ctx, box, client_session, kind, purpose, role, native, claim_runs, claim_unclaimed, confirm):
    """Attach unclaimed open runs (started without a handle) to a session: yours if you pass client_session,
    otherwise a new card is registered first. Runs another session owns are never touched (use recover for that)."""
    p = ctx.principal
    free = [r for r in box.unclaimed_runs(p.id) if not ctx.restricted or ctx.own(r["profile"])]
    by_id = {r["run_id"]: r for r in free}
    if claim_runs:
        missing = [i for i in claim_runs if i not in by_id]
        if missing:
            raise ToolError("not_claimable", "these runs are not open and unclaimed runs of this key: "
                            + ", ".join(missing[:10]) + ". Runs owned by a session move with recover_from; "
                            "runs_active shows what is claimable", not_claimable=missing[:10])
        chosen = [by_id[i] for i in claim_runs]
    else:
        chosen = free
    if client_session:
        row = box.session_get(client_session)
        if not row or row["key_id"] != p.id:
            raise ToolError("unknown_client_session", f"no session {client_session!r} of this key; "
                            "session_cards_list shows yours")
    now = time.time()
    runs = [{"run_id": r["run_id"], "profile": r["profile"], "age_seconds": int(now - r["started_at"])} for r in chosen]
    if not confirm:
        return {"claim": "preview", "applied": False, "runs": runs, "count": len(runs),
                "next": "repeat with confirm=true to claim them"
                        + ("" if client_session else " (without client_session you get a new card for them; pass "
                           "your handle to attach them to your existing session)")
                        + ". Claim only runs you started: another chat of the same key may own them."}
    if not chosen:
        return {"claim": "applied", "applied": False, "claimed": 0, "runs": [], "note": "nothing to claim"}
    if client_session:
        row = box.session_update(client_session, **{k: v for k, v in (("purpose", _clean(purpose)),
                                                                       ("role", _clean(role))) if v is not None})
    else:
        row, _ = box.session_register(
            key_id=p.id, key_name=p.name, client=p.client or "unknown", kind=kind, purpose=_clean(purpose),
            role=_clean(role), native_id=native, handle_factory=lambda: new_handle(p.client or "unknown", kind))
    n = box.claim_runs(key_id=p.id, handle=row["handle"], client=p.client or None, run_ids=[r["run_id"] for r in chosen])
    return {"claim": "applied", "applied": True, "claimed": n, "runs": runs, "card": _card(row, you=True),
            "usage": USAGE}


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



@reg.tool("clients_overview", toolset="inbox",
          description=("Which client serves which session, in one call: sessions grouped by client (Cowork, ClaudeAI, "
                       "Claude Code, ...), each with kind, purpose, role, last_seen, `state` (active: seen within "
                       "`active_window_minutes` or has open runs; otherwise idle) and its open runs (run_id, profile, "
                       "age, activity). `unclaimed_runs` were started without a session. `summary` is one short "
                       "sentence per client, meant to be read out. Default scope=own covers the sessions of this key; "
                       "scope=all covers every key and needs access=admin. Sessions that were handed over are counted "
                       "in `handed_over_hidden`, not listed. Profile-restricted keys see only runs of their profiles. "
                       "Cards are labels, never permissions."),
          properties={"scope": {"type": "string", "enum": ["own", "all"], "default": "own"}})
def clients_overview(ctx, scope="own"):
    if scope == "all" and ctx.principal.scopes.access != "admin":
        raise ToolError("forbidden", "scope=all needs access=admin")
    allowed = set(ctx.principal.scopes.profiles) if ctx.restricted else None
    out = clients_view.build(get_inbox(ctx.settings), key_id=None if scope == "all" else ctx.principal.id,
                             allowed_profiles=allowed)
    return {"scope": scope, **out}
