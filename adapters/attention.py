"""One truth for "what needs attention" — shared by the MCP tools and the Ledger page.

Before this module the inbox tools and the Ledger each decided for themselves what counts:
the tools used ``Inbox._attention_sql`` (settled failures excluded), the Ledger asked for
``inbox_list kind=run_failed`` without ``attention_only`` and therefore rendered failures a
later run had already settled. Two readers, two answers.

Everything that classifies an inbox row lives here:

* :func:`classify` — one event into ``waiting`` / ``attention`` / ``interrupted`` / ``settled``
  / ``history`` / ``noise``.
* :func:`settled_text` — one plain sentence for a settled row ("ersetzt durch run_…").
* :func:`group` — the buckets the page draws and the tools can hand out.
* :func:`counts` — the numbers in ``hermes_status.inbox`` (``failed_runs_24h``,
  ``approvals_waiting``) computed from the same buckets, so a badge cannot disagree with the list.

The database side (``Inbox.attention``) uses the same predicate as ``digest``/``poll``; this
module holds no SQL and no state, so it is cheap to unit-test.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List

from .inbox_store import APPROVAL_KIND, FAILURE_KINDS

# Bucket names. The page renders one group per bucket; the tools use them for counts.
WAITING = "waiting"          # an approval a human still has to answer
ATTENTION = "attention"      # an open failure: run_failed / run_lost that nothing settled
INTERRUPTED = "interrupted"  # a run a restart stopped after it produced output — not an error
SETTLED = "settled"          # a failure a later run or an operator already took care of
HISTORY = "history"          # a decided approval (approved / denied / expired)
NOISE = "noise"              # finished/cancelled runs, the raw hook bookkeeping
BUCKETS = (WAITING, ATTENTION, INTERRUPTED, SETTLED, HISTORY, NOISE)

_SETTLED_PREFIXES = (
    ("superseded_by_completed", "ersetzt durch einen Lauf, der abgeschlossen wurde"),
    ("superseded_by_failure", "ersetzt durch einen neueren Fehlschlag derselben Session"),
    ("operator_backfill", "vom Operator als erledigt eingetragen"),
)


def classify(event: Dict[str, Any]) -> str:
    """Which group one inbox row belongs to. Pure; no database, no clock."""
    event = event if isinstance(event, dict) else {}
    kind = str(event.get("kind") or "")
    if event.get("approval_key"):
        state = str(event.get("approval_state") or "")
        if state == "waiting":
            return WAITING
        if state in ("approved", "denied", "expired"):
            return HISTORY
        return NOISE
    if kind in FAILURE_KINDS:
        return SETTLED if event.get("settled_reason") else ATTENTION
    if kind == "run_interrupted":
        return INTERRUPTED
    if kind in ("run_finished", "run_cancelled", "approval_pending", "approval_resolved", APPROVAL_KIND):
        return NOISE
    if kind == "needs_approval":        # legacy row without an approval_key
        return NOISE
    return ATTENTION                    # a producer's own notice: visible unless it says otherwise


def settled_text(event: Dict[str, Any]) -> str:
    """One sentence saying what settled a failure — shown in the collapsed group, never hidden."""
    reason = str((event or {}).get("settled_reason") or "")
    for prefix, label in _SETTLED_PREFIXES:
        if reason.startswith(prefix):
            if prefix == "operator_backfill":
                note = reason.split(":", 1)[1].strip() if ":" in reason else ""
                return label + (f": {note}" if note else "")
            return label
    return "erledigt" + (f": {reason}" if reason else "")


def group(rows: Iterable[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Split rows into the buckets the page draws; unknown rows stay visible."""
    out: Dict[str, List[Dict[str, Any]]] = {name: [] for name in BUCKETS}
    for row in rows or []:
        out[classify(row)].append(row)
    return out


def counts(buckets: Dict[str, List[Dict[str, Any]]]) -> Dict[str, int]:
    """The headline numbers. Same source as the groups, so they cannot drift from them."""
    wait = len(buckets.get(WAITING) or [])
    failed = len(buckets.get(ATTENTION) or [])
    return {
        "failed_runs_24h": failed,
        "approvals_waiting": wait,
        "interrupted_24h": len(buckets.get(INTERRUPTED) or []),
        "settled_24h": len(buckets.get(SETTLED) or []),
        "attention_total": wait + failed,
    }


def describe() -> str:
    """The wording the page and the tools both use for the default view."""
    return ("open approvals, failed or lost runs that nothing settled, and runs waiting for an approval. "
            "Settled failures and decided approvals are not shown on the page; the tools keep them "
            "(`inbox_attention`, `inbox_list detail=true`, `approval_detail`).")
