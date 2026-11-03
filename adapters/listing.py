"""Shared pattern of the list tools: relevant-by-default, everything on request, summary view, profile matrix.

Parameters every list tool shares (each tool documents which of them it supports):
  view      list (default) | summary (counters per category and source, no entries)
  include   active (default: only active / relevant entries) | all (every entry, with an explicit status)
  query     case-insensitive text search in name (and description where there is one)
  category, source   exact filters
  limit, offset      paging; the answer always says total / returned / truncated
  profiles  all | comma list: a matrix entry x profile computed LIVE per profile (never from a shared cache)

The totals (active, disabled, total, what the default hid) go into `_meta.list`.
"""
from __future__ import annotations

from typing import Any

from .. import meta
from ..registry import ToolError

DEFAULT_NOTE = ("Default changed: list tools now return only active / relevant entries in compact form. "
                "include=all returns every entry with its status; view=summary returns counters only.")


def props(*, limit_max: int = 500, default_limit: int = 100, category: bool = True, source: bool = True,
          matrix: bool = True, include_doc: str = "active (default) = only active / relevant entries; all = every entry "
                                                  "with an explicit status") -> dict[str, Any]:
    p: dict[str, Any] = {
        "view": {"type": "string", "enum": ["list", "summary"], "default": "list",
                 "description": "summary = counters per category and source, no single entries"},
        "include": {"type": "string", "enum": ["active", "all"], "default": "active", "description": include_doc},
        "query": {"type": "string", "maxLength": 100, "description": "case-insensitive text search"},
        "limit": {"type": "integer", "minimum": 1, "maximum": limit_max, "default": default_limit},
        "offset": {"type": "integer", "minimum": 0, "default": 0}}
    if category:
        p["category"] = {"type": "string"}
    if source:
        p["source"] = {"type": "string"}
    if matrix:
        p["profiles"] = {"type": "string", "maxLength": 300,
                         "description": "all, or a comma list of profiles: entry x profile matrix, computed live per profile"}
    return p


def pick_profiles(ctx, spec: str | None) -> list[str] | None:
    """None = no matrix requested. 'all' = every profile this key may use."""
    if not spec:
        return None
    from ..adapters.common import list_profile_names, profile_home
    allowed = [n for n in list_profile_names() if ctx.principal.scopes.profile_allowed(n)]
    if spec.strip().lower() == "all":
        return allowed
    names = [s.strip() for s in spec.split(",") if s.strip()]
    for n in names:
        if n not in allowed:
            if not ctx.principal.scopes.profile_allowed(n):
                raise ToolError("forbidden", f"profile {n!r} not in key scope")
            profile_home(n)                       # raises not_found for unknown profiles
    return names


def matches(query: str | None, *texts: Any) -> bool:
    if not query:
        return True
    q = query.lower()
    return any(q in str(t).lower() for t in texts if t)


def page(rows: list, limit: int, offset: int) -> tuple[list, dict[str, Any]]:
    out = rows[offset: offset + limit]
    return out, {"total": len(rows), "returned": len(out), "offset": offset, "limit": limit,
                 "truncated": offset + len(out) < len(rows)}


def tally(rows: list[dict], key: str, active_key: str = "active",
          labels: tuple[str, str] = ("active", "disabled")) -> dict[str, dict[str, int]]:
    """{value: {total, <labels[0]>, <labels[1]>}} for rows grouped by `key`."""
    out: dict[str, dict[str, int]] = {}
    for r in rows:
        k = str(r.get(key) if r.get(key) not in (None, "") else "(none)")
        d = out.setdefault(k, {"total": 0, labels[0]: 0, labels[1]: 0})
        d["total"] += 1
        d[labels[0] if r.get(active_key) else labels[1]] += 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]["total"]))


def note(*, tool: str, include: str, view: str, total: int, active: int, disabled: int, shown: int, hidden_by_default: int,
         extra: dict[str, Any] | None = None, labels: tuple[str, str] = ("active", "disabled")) -> None:
    """Totals into _meta.list so a reader sees what the default view left out.
    `labels` names the two groups for tools where 'disabled' would be wrong (kanban: open / closed)."""
    meta.note("list", {"tool": tool, "view": view, "include": include, "total": total, labels[0]: active,
                       labels[1]: disabled, "shown": shown,
                       "hidden_by_default": hidden_by_default if include == "active" else 0,
                       "default_behaviour": "include=active (only active / relevant entries)", "note": DEFAULT_NOTE,
                       **(extra or {})})
