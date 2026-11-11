"""Approval ledger bookkeeping shared by the answer paths (approvals_answer, chat_run_approval).

Kept out of the tool modules so both can use it without importing each other. Everything here is
best effort: an answer that went through must never fail because the ledger could not be updated.
"""
from __future__ import annotations

from .inbox_store import get_inbox


def surface_of(ctx) -> str:
    """Which surface answered, from what hermcp already knows (no names, no guessing).

    `principal.client` is the coarse label derived from request headers (claude-code, cowork, …); a key
    without one is reported as `mcp`. The gateway's own label (telegram, cli, smart) comes from the hook.
    """
    client = getattr(ctx.principal, "client", "") or ""
    return f"mcp:{client}" if client else "mcp"


def settle(ctx, *, session_key=None, run_id=None, approval_id=None, choice, note=None) -> dict | None:
    """Settle the matching waiting approval row. First answer wins; later ones are logged.

    Returns the settled row when this call settled it, else None (already settled / unknown).
    """
    try:
        box = get_inbox(ctx.settings)
        row = box.find_approval(session_key=session_key, run_id=run_id, approval_id=approval_id)
        surface = surface_of(ctx)
        if row is None:
            settled = box.find_approval(session_key=session_key, run_id=run_id, approval_id=approval_id,
                                        waiting_only=False)
            if settled is not None:
                box.record_decision(approval_key=settled["approval_key"], outcome="too_late_already_decided",
                                    surface=surface, decision=choice, actor=ctx.principal.name, note=note,
                                    run_id=run_id)
            return None
        out, applied = box.settle_approval(approval_key=row["approval_key"], decision=choice, surface=surface,
                                           actor=ctx.principal.name, note=note, run_id=run_id)
        return out if applied else None
    except Exception:  # noqa: BLE001 - bookkeeping must never break an answer that went through
        return None


def note_attempt(ctx, *, run_id=None, session_key=None, approval_id=None, outcome, choice=None, note=None) -> None:
    """Record an attempt that did NOT go through (refused, error, no longer pending)."""
    try:
        box = get_inbox(ctx.settings)
        row = box.find_approval(session_key=session_key, run_id=run_id, approval_id=approval_id,
                                waiting_only=False)
        if row is None:
            return
        real = outcome
        if row.get("approval_state") != "waiting" and outcome not in ("too_late_already_decided",):
            real = "too_late_already_decided"
        box.record_decision(approval_key=row["approval_key"], outcome=real, surface=surface_of(ctx),
                            decision=choice, actor=ctx.principal.name, note=note, run_id=run_id)
    except Exception:  # noqa: BLE001
        pass
