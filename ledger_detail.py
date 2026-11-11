"""Ledger detail layer: the pop-up (peek), the session full view and the sub-pages.

Everything here builds on the same seam as ``ledger_page``: the registry's **read** tools with a
principal forced to ``access="read"``. The classification is never re-implemented — attention rows
come through :func:`hermcp.adapters.attention.classify` from ``inbox_list`` rows, exactly the
function ``inbox_attention`` uses, and approvals come from ``approval_detail``.

Two rules hold for every value that leaves this module:

* free text (summaries, commands, rationale, message bodies, errors) goes through
  :func:`hermcp.redact.redact_text` and is then truncated — if the scrubbing is unavailable the text
  is dropped, never shown raw;
* every name, id and timestamp is whitelisted (:func:`ledger_page.safe_token`, ``clean``, ``iso``),
  so nothing arbitrary reaches a URL or the page.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from .adapters import attention as AT
from .adapters.inbox_store import get_inbox
from .ledger_page import (WINDOW_HOURS_DEFAULT, call, clean, dashboard_url, desktop_url,
                          iso, link_targets, build_link, read_only_principal, redacted, safe_token,
                          session_status, _read_reachable_profiles, _gateway)
from .registry import Principal

PEEK_ACTIONS = 3
PEEK_TEXT = 160
MSG_TEXT = 400
MSG_TOOL_TEXT = 220
MAX_MESSAGES = 200
LIST_PAGE_DEFAULT = 25
LIST_PAGE_MAX = 100
SESSIONS_DEEP_MAX = 200      # rows fetched per profile for one page of the sessions list
RUNS_FETCH = 200             # inbox_list ceiling per call
CRON_FETCH = 100             # cron_runs ceiling per call
KIND_LABEL = {
    "session": "SESSION", "run": "RUN", "cron": "CRON",
    "approval": "FREIGABE WARTET", "approval_done": "FREIGABE ENTSCHIEDEN",
    "cron_error": "CRON-FEHLER",
}

SURFACE_LABEL = {"api_server": "API", "telegram": "Telegram", "cli": "CLI", "cron": "Cron",
                 "desktop": "Desktop", "webapp": "Web-App", "email": "E-Mail"}


class DetailError(RuntimeError):
    """One detail could not be read; the dialog shows the sentence, never a traceback."""


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def page_args(query: Dict[str, Any]) -> tuple[int, int]:
    """(offset, limit) from a query dict, bounded — a bad value falls back, never raises."""
    def _int(name: str, default: int, low: int, high: int) -> int:
        try:
            value = int(str((query or {}).get(name) or "").strip())
        except (TypeError, ValueError):
            return default
        return min(high, max(low, value))
    return _int("offset", 0, 0, 100_000), _int("limit", LIST_PAGE_DEFAULT, 1, LIST_PAGE_MAX)


def window_hours(query: Dict[str, Any], default: int = WINDOW_HOURS_DEFAULT) -> int:
    try:
        value = int(str((query or {}).get("hours") or "").strip())
    except (TypeError, ValueError):
        return default
    return min(168, max(1, value))


def surface(value: Any) -> Optional[str]:
    """A platform/source label, short and printable."""
    return clean(value, 30)


def surface_label(value: Any) -> Optional[str]:
    name = surface(value)
    return SURFACE_LABEL.get(name, name) if name else None


def sym(tone: str) -> str:
    return {"ok": "●", "bad": "✕", "run": "◌", "warn": "▲", "muted": "○"}.get(tone, "·")


def tone_for(state: Any) -> str:
    text = str(state or "").lower()
    if text in ("aktiv", "running", "completed", "ok", "success", "connected", "approved", "applied"):
        return "ok"
    if text in ("queued", "claimed", "started", "waiting", "waiting_for_approval", "offen", "expired"):
        return "warn"
    if text in ("error", "failed", "lost", "interrupted", "denied", "disconnected", "beendet"):
        return "bad" if text not in ("beendet",) else "muted"
    return "muted"


def _now() -> float:
    return time.time()


def _runtime_s(started: Any, ended: Any) -> Optional[int]:
    """Seconds between two timestamps (or started and now when the session is still open)."""
    def _ts(value):
        if isinstance(value, (int, float)):
            try:
                return datetime.fromtimestamp(float(value), timezone.utc)
            except (OverflowError, OSError, ValueError):
                return None
        if not isinstance(value, str) or not value:
            return None
        text = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    first, last = _ts(started), _ts(ended)
    if first is None:
        return None
    last = last or datetime.now(timezone.utc)
    return max(0, int((last - first).total_seconds()))


def _message_rows(messages: List[Dict[str, Any]], limit_text: int = MSG_TEXT) -> List[Dict[str, Any]]:
    """Whitelist message rows: time, role, text, tool name; text redacted and cut server-side."""
    out: List[Dict[str, Any]] = []
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        role = clean(msg.get("role"), 16) or "?"
        text = msg.get("content") if isinstance(msg.get("content"), str) else ""
        tool = clean(msg.get("tool_name"), 40)
        cut = min(limit_text, MSG_TOOL_TEXT) if role == "tool" else limit_text
        body = clean(redacted(text), cut)
        entry = {
            "at": iso(msg.get("timestamp")),
            "role": role,
            "text": body or "",
            "truncated": bool(body) and body.endswith("…"),
        }
        if tool:
            entry["tool"] = tool
        calls = msg.get("tool_calls")
        if isinstance(calls, list) and calls:
            names = []
            for item in calls[:8]:
                name = None
                if isinstance(item, dict):
                    fn = item.get("function") if isinstance(item.get("function"), dict) else item
                    name = fn.get("name") if isinstance(fn, dict) else None
                if isinstance(name, str) and name:
                    names.append(clean(name, 40))
            if names:
                entry["calls"] = [n for n in names if n]
        out.append(entry)
    return out


def _scoped(registry, principal: Principal, settings) -> tuple[Principal, List[str], Dict[str, str]]:
    """The read-only principal, the profiles to read, and the (mutable) error map."""
    errors: Dict[str, str] = {}
    allowed = set(principal.scopes.profiles) if (principal.scopes.profiles and "*" not in principal.scopes.profiles) else None
    ro = read_only_principal(principal)
    try:
        profiles = _read_reachable_profiles(registry, ro, settings)
    except Exception as exc:  # noqa: BLE001
        errors["profiles"] = str(exc)
        profiles = ["default"]
    if allowed is not None:
        profiles = [p for p in profiles if p in allowed] or list(allowed)
    return ro, profiles, errors


def _links(settings, hermes_config: Dict[str, Any], request_base: str) -> Dict[str, Any]:
    return link_targets(settings, hermes_config, request_base)


def _template_url(template: Optional[str], profile: Optional[str], session_id: Optional[str]) -> Optional[str]:
    """A configured '{session_id}' template, or None. Anything but http(s) is refused."""
    tmpl = (template or "").strip()
    sid = safe_token(session_id)
    if not tmpl or "{session_id}" not in tmpl or not sid:
        return None
    url = tmpl.replace("{session_id}", quote(sid, safe=""))
    url = url.replace("{profile}", quote(safe_token(profile) or "default", safe=""))
    return url if url.startswith(("http://", "https://")) else None


def app_links(settings, targets: Dict[str, Any], profile: Optional[str],
              session_id: Optional[str]) -> Dict[str, Optional[str]]:
    """Desktop and web app deep links. A button is only rendered when its URL exists."""
    desktop = _template_url(getattr(settings, "ledger_desktop_url", ""), profile, session_id)
    web = _template_url(getattr(settings, "ledger_web_url", ""), profile, session_id)
    if desktop is None and targets.get("kind") == "desktop":
        desktop = desktop_url(targets.get("base_url"), profile, session_id)
    if web is None:
        cfg_web = getattr(settings, "ledger_web_url", "")
        if not cfg_web and targets.get("kind") == "dashboard":
            web = dashboard_url(targets.get("base_url"), profile, session_id)
    return {"desktop": desktop, "web": web}


def _session_meta(registry, principal, settings, profile: str, session_id: str) -> Dict[str, Any]:
    try:
        row = call(registry, principal, settings, "sessions_get", session_id=session_id, profile=profile) or {}
    except Exception as exc:  # noqa: BLE001 - `call` already prefixes the tool name
        raise DetailError(str(exc)) from exc
    return row if isinstance(row, dict) else {}


def _session_messages(registry, principal, settings, profile: str, session_id: str,
                      limit: int = MAX_MESSAGES) -> List[Dict[str, Any]]:
    try:
        res = call(registry, principal, settings, "sessions_messages", session_id=session_id, profile=profile,
                   limit=max(1, min(MAX_MESSAGES, int(limit))), latest=True, max_chars=2000) or {}
    except Exception as exc:  # noqa: BLE001
        raise DetailError(str(exc)) from exc
    rows = res.get("messages") if isinstance(res, dict) else None
    return [r for r in rows or [] if isinstance(r, dict)]


def _runs_of(settings, profile: str, session_id: str, allowed: Optional[set]) -> List[Dict[str, Any]]:
    """Runs hermcp watched in this session (its own inbox database, read-only)."""
    if allowed is not None and profile not in allowed:
        return []
    try:
        box = get_inbox(settings)
        rows = box.session_watches(profile, session_id)
    except Exception:  # noqa: BLE001 - a missing inbox must not break the view
        return []
    out = []
    for row in rows or []:
        out.append({"run_id": safe_token(row.get("run_id")),
                    "status": clean(row.get("last_status"), 24),
                    "started_at": iso(row.get("started_at")),
                    "activity": clean(row.get("activity"), 40)})
    out.sort(key=lambda r: r.get("started_at") or "", reverse=True)
    return out


def _open_approvals(settings, profile: str, session_id: str) -> int:
    try:
        rows = get_inbox(settings).open_approvals([profile])
    except Exception:  # noqa: BLE001
        return 0
    return sum(1 for r in rows if r.get("session_id") == session_id)


# ---------------------------------------------------------------------------
# the pop-up (peek)
# ---------------------------------------------------------------------------

def build_peek(registry, principal: Principal, settings, hermes_config: Dict[str, Any], kind: str,
               ident: str, profile: Optional[str] = None, job: Optional[str] = None,
               request_base: str = "") -> Dict[str, Any]:
    """One small dialog payload: head, four facts, the last three actions, links. Nothing more."""
    ro, profiles, errors = _scoped(registry, principal, settings)
    targets = _links(settings, hermes_config, request_base)
    prof = safe_token(profile) or (profiles[0] if profiles else "default")
    ident = safe_token(ident) or ""
    kind = kind if kind in ("session", "run", "cron", "approval") else "session"
    out: Dict[str, Any] = {
        "ok": True, "kind": kind, "id": ident, "read_only": True,
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "title": "", "type_label": KIND_LABEL[kind], "symbol": sym("muted"), "tone": "muted",
        "facts": {"model": None, "profile": prof, "surface": None, "last_active": None},
        "actions": [], "live": False, "session_id": None,
        "links": {"session": None, "desktop": None, "web": None},
        "errors": errors,
    }
    if not ident:
        return {**out, "ok": False, "error": "keine ID übergeben"}

    if kind in ("session", "run"):
        sid = ident
        if kind == "run":
            event = _find_run_event(settings, ident, prof)
            sid = safe_token((event or {}).get("session_id")) or ident
            if event:
                out["facts"]["profile"] = clean(event.get("profile"), 40) or prof
                out["title"] = clean(redacted(event.get("summary")), PEEK_TEXT) or ""
        detail = _peek_session(registry, ro, settings, out["facts"]["profile"] or prof, sid, targets, out)
        return {**out, **detail}

    if kind == "cron":
        row = _find_cron_run(registry, ro, settings, ident, prof, job)
        if row is None:
            return {**out, "ok": False, "error": "Cron-Lauf nicht gefunden (oder außerhalb des Fensters)"}
        status = clean(row.get("status"), 24) or "?"
        tone = "bad" if status in ("error", "failed") else "ok" if status in ("completed", "ok") else "warn"
        out.update(title=clean(row.get("job_name") or row.get("job_id"), 80) or ident,
                   tone=tone, symbol=sym(tone),
                   type_label="CRON-FEHLER" if tone == "bad" else KIND_LABEL["cron"])
        info = {k: v for k, v in row.items() if not str(k).startswith("_")}
        out["cron"] = {k: clean(info.get(k), 120) if isinstance(info.get(k), str) else info.get(k)
                       for k in ("run_id", "job_name", "status", "started_at", "duration_s",
                                 "delivery_outcome", "session_id")}
        out["cron"]["error"] = clean(redacted(row.get("error")), 200)
        out["facts"].update(surface="Cron", last_active=iso(row.get("started_at")))
        sid = safe_token(row.get("session_id"))
        if sid:
            _peek_session(registry, ro, settings, prof, sid, targets, out, title_fallback=out["title"])
        return out

    # approval
    key = ident
    try:
        res = call(registry, ro, settings, "approval_detail", approval_key=key) or {}
    except Exception as exc:  # noqa: BLE001
        return {**out, "ok": False, "error": f"Freigabe nicht lesbar: {clean(str(exc), 160)}"}
    approval = res.get("approval") if isinstance(res, dict) else None
    if not isinstance(approval, dict):
        return {**out, "ok": False, "error": "Freigabe nicht gefunden"}
    state = clean(approval.get("approval_state"), 20) or "?"
    tone = {"waiting": "warn", "approved": "ok", "denied": "bad", "expired": "muted"}.get(state, "muted")
    action = clean(redacted(approval.get("action")), 200)
    out.update(tone=tone, symbol=sym(tone), title=action or "Freigabe",
               type_label="FREIGABE WARTET" if state == "waiting" else "FREIGABE ENTSCHIEDEN")
    out["facts"].update(profile=clean(approval.get("profile"), 40) or prof,
                        surface=surface_label(approval.get("approval_surface")),
                        last_active=iso(approval.get("approval_closed_at") or approval.get("ts")))
    out["approval"] = {
        "approval_key": safe_token(approval.get("approval_key")),
        "state": state,
        "decision": clean(approval.get("approval_decision"), 20),
        "surface": surface_label(approval.get("approval_surface")),
        "rationale": clean(redacted(approval.get("rationale")), 200),
        "protocol": _protocol(res.get("protocol")),
    }
    out["actions"] = [{"at": p["at"], "role": "user" if p.get("outcome") == "applied" else "system",
                       "text": p["text"]} for p in out["approval"]["protocol"][:PEEK_ACTIONS]]
    sid = safe_token(approval.get("session_id"))
    out["session_id"] = sid
    if sid:
        _peek_session(registry, ro, settings, out["facts"]["profile"], sid, targets, out,
                      title_fallback=out["title"], silent=True)
    out["links"].update(session=f"/ledger/session/{quote(sid, safe='')}" if sid else None,
                        **app_links(settings, targets, out["facts"]["profile"], sid))
    return out


def _protocol(rows: Any) -> List[Dict[str, Any]]:
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        parts = [clean(row.get(f), 60) for f in ("surface", "outcome")]
        parts = [p for p in parts if p]
        decision = clean(row.get("decision"), 20)
        text = " · ".join(parts + ([decision] if decision else []))
        out.append({"at": iso(row.get("ts")), "text": clean(redacted(text), PEEK_TEXT) or "—",
                    "outcome": clean(row.get("outcome"), 30)})
    return out


def _peek_session(registry, principal, settings, profile: str, session_id: str,
                  targets: Dict[str, Any], out: Dict[str, Any], title_fallback: str = "",
                  silent: bool = False) -> Dict[str, Any]:
    """Fill ``out`` from one session: four facts, the last three actions, links. Read-only."""
    sid = safe_token(session_id)
    if not sid:
        return {}
    try:
        meta = _session_meta(registry, principal, settings, profile, sid)
        messages = _session_messages(registry, principal, settings, profile, sid, limit=PEEK_ACTIONS)
    except DetailError as exc:
        if silent:
            out["errors"]["peek"] = str(exc)
            return {}
        return {"ok": False, "error": clean(str(exc), 200)}
    title = clean(redacted(meta.get("title")), PEEK_TEXT) or title_fallback
    state = session_status(meta, _now())
    out.update(session_id=sid, title=title or out.get("title") or sid,
               live=state == "aktiv")
    if out["tone"] == "muted":
        out.update(tone=tone_for(state), symbol=sym(tone_for(state)))
    out["facts"].update(
        model=clean(meta.get("model"), 40),
        profile=clean(meta.get("profile_name"), 40) or profile,
        surface=surface_label(meta.get("source")),
        last_active=iso(meta.get("last_active")),
    )
    out["actions"] = [{"at": m["at"], "role": m["role"], "text": m["text"] or "—",
                       "tool": m.get("tool")} for m in _message_rows(messages, PEEK_TEXT)[:PEEK_ACTIONS]]
    out["links"].update(session=f"/ledger/session/{quote(sid, safe='')}",
                        **app_links(settings, targets, out["facts"]["profile"], sid))
    return {}


def _find_run_event(settings, run_id: str, profile: str) -> Optional[Dict[str, Any]]:
    """The inbox row of one run (hermcp's own store, read-only) — for the session id and the summary."""
    try:
        box = get_inbox(settings)
        res = box.list(run_id=run_id, limit=5)
    except Exception:  # noqa: BLE001
        return None
    for row in res.get("events") or []:
        if row.get("run_id") == run_id and (row.get("profile") in (None, profile) or not profile):
            return row
    rows = res.get("events") or []
    return rows[0] if rows else None


def _find_cron_run(registry, principal, settings, run_id: str, profile: str,
                   job: Optional[str]) -> Optional[Dict[str, Any]]:
    args: Dict[str, Any] = {"limit": CRON_FETCH, "profile": profile}
    if safe_token(job):
        args["job_id"] = job
    else:
        args["since"] = (datetime.now(timezone.utc) - timedelta(hours=168)).isoformat(timespec="seconds")
    try:
        res = call(registry, principal, settings, "cron_runs", **args) or {}
    except Exception:  # noqa: BLE001
        return None
    for row in res.get("runs") or []:
        if isinstance(row, dict) and row.get("run_id") == run_id:
            return row
    return None


# ---------------------------------------------------------------------------
# the session full view
# ---------------------------------------------------------------------------

def build_session(registry, principal: Principal, settings, hermes_config: Dict[str, Any],
                  session_id: str, profile: Optional[str] = None, request_base: str = "",
                  message_limit: int = MAX_MESSAGES) -> Dict[str, Any]:
    """One session, wide: history, metadata, tool usage, signals. Works for runs and cron sessions."""
    ro, profiles, errors = _scoped(registry, principal, settings)
    sid = safe_token(session_id)
    targets = _links(settings, hermes_config, request_base)
    if not sid:
        return {"ok": False, "error": "keine gültige Session-ID", "errors": errors}
    candidates = [p for p in [safe_token(profile)] if p] or profiles
    meta: Dict[str, Any] = {}
    used_profile = candidates[0] if candidates else "default"
    last_exc: Optional[str] = None
    for prof in candidates:
        try:
            meta = _session_meta(registry, ro, settings, prof, sid)
            used_profile = prof
            break
        except DetailError as exc:
            last_exc = str(exc)
    if not meta:
        return {"ok": False, "error": clean(last_exc, 200) or "Session nicht gefunden", "errors": errors}
    try:
        messages = _session_messages(registry, ro, settings, used_profile, sid, limit=message_limit)
    except DetailError as exc:
        errors["messages"] = str(exc)
        messages = []
    rows = _message_rows(messages)
    state = session_status(meta, _now())
    runs = _runs_of(settings, used_profile, sid, None)
    open_approvals = _open_approvals(settings, used_profile, sid)

    usage: Dict[str, int] = {}
    for row in messages:
        name = clean(row.get("tool_name"), 40)
        if name:
            usage[name] = usage.get(name, 0) + 1
        calls = row.get("tool_calls")
        if isinstance(calls, list):
            for item in calls:
                fn = item.get("function") if isinstance(item, dict) else None
                name = clean((fn or {}).get("name") if isinstance(fn, dict) else None, 40)
                if name:
                    usage[name] = usage.get(name, 0) + 1
    total_calls = max(1, sum(usage.values()))
    tool_usage = sorted(({"name": n, "count": c, "pct": int(round(100 * c / total_calls))}
                         for n, c in usage.items()), key=lambda x: (-int(x["count"]), str(x["name"])))[:12]

    failed = [r for r in rows if r["role"] == "system" and "error" in (r["text"] or "").lower()]
    signals = []
    if open_approvals:
        signals.append({"tone": "warn", "symbol": sym("warn"),
                        "text": f"{open_approvals} offene Freigabe(n) in dieser Session"})
    if failed:
        signals.append({"tone": "bad", "symbol": sym("bad"), "text": f"{len(failed)} Fehlermeldung(en) im Verlauf"})
    if state == "aktiv":
        signals.append({"tone": "run", "symbol": sym("run"), "text": "Session ist aktiv"})
    if not signals:
        signals.append({"tone": "muted", "symbol": sym("muted"), "text": "Keine Auffälligkeiten"})

    return {
        "ok": True,
        "session": {
            "id": safe_token(meta.get("id")) or sid,
            "title": clean(redacted(meta.get("title")), 200) or sid,
            "profile": clean(meta.get("profile_name"), 40) or used_profile,
            "surface": surface_label(meta.get("source")),
            "model": clean(meta.get("model"), 60),
            "started_at": iso(meta.get("started_at")),
            "last_active": iso(meta.get("last_active")),
            "ended_at": iso(meta.get("ended_at")),
            "state": state,
            "source": clean(meta.get("source"), 30),
        },
        "stats": {
            "state": state,
            "messages": meta.get("message_count") if isinstance(meta.get("message_count"), int) else len(rows),
            "tool_calls": meta.get("tool_call_count") if isinstance(meta.get("tool_call_count"), int)
            else sum(usage.values()),
            "runtime_s": _runtime_s(meta.get("started_at"), meta.get("ended_at")),
            "open_approvals": open_approvals,
        },
        "messages": rows,
        "tool_usage": tool_usage,
        "runs": runs,
        "signals": signals,
        "links": {"session": f"/ledger/session/{quote(sid, safe='')}",
                  **app_links(settings, targets, used_profile, sid)},
        "errors": errors,
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    }


# ---------------------------------------------------------------------------
# sub-page lists
# ---------------------------------------------------------------------------

def _fetch_sessions(registry, principal, settings, profiles: List[str], need: int) -> tuple:
    """Sessions of every profile, newest first, at least ``need`` per profile (bounded)."""
    rows: List[Dict[str, Any]] = []
    errors: Dict[str, str] = {}
    capped = False
    per_profile = min(max(need, LIST_PAGE_DEFAULT), SESSIONS_DEEP_MAX)

    def one(prof: str) -> tuple:
        """One profile's newest rows, in chunks of 100 (the tool's own ceiling)."""
        got: List[Dict[str, Any]] = []
        offset = 0
        while len(got) < per_profile:
            want = min(100, per_profile - len(got))
            try:
                res = call(registry, principal, settings, "sessions_list", profile=prof, limit=want,
                           offset=offset, order="recent") or {}
            except Exception as exc:  # noqa: BLE001
                return got, str(exc)
            batch = [r for r in res.get("sessions") or [] if isinstance(r, dict)]
            got.extend(batch)
            if len(batch) < want:
                break
            offset += want
        return got, None

    # One read per profile, in parallel: 8 profiles are ~8 × 0.5 s when done in series, which is
    # the whole page load. The calls are independent and read-only, so they can overlap.
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(profiles)))) as pool:
        results = list(pool.map(one, profiles))
    for prof, (got, err) in zip(profiles, results):
        if err:
            errors.setdefault("sessions", err)
        if len(got) >= per_profile:
            capped = True
        for row in got:
            rows.append({
                "session_id": safe_token(row.get("id")),
                "title": clean(redacted(row.get("title")), 120),
                "profile": clean(row.get("profile_name"), 40) or prof,
                "surface": surface_label(row.get("source")),
                "source": clean(row.get("source"), 30),
                "status": session_status(row, _now()),
                "last_active": iso(row.get("last_active")),
                "started_at": iso(row.get("started_at")),
                "message_count": row.get("message_count") if isinstance(row.get("message_count"), int) else None,
                "tool_call_count": row.get("tool_call_count") if isinstance(row.get("tool_call_count"), int) else None,
                "model": clean(row.get("model"), 60),
            })
    return rows, errors, capped


def _matches(row: Dict[str, Any], filters: Dict[str, Any]) -> bool:
    q = (filters.get("q") or "").strip().lower()
    if q:
        hay = " ".join(str(row.get(k) or "") for k in ("title", "profile", "surface", "session_id",
                                                       "model", "summary", "job_name", "error", "action",
                                                       "rationale", "state", "decision")).lower()
        if q not in hay:
            return False
    return True


def _since_iso(hours: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")


def list_sessions(registry, principal: Principal, settings, hermes_config: Dict[str, Any],
                  query: Dict[str, Any], request_base: str = "") -> Dict[str, Any]:
    offset, limit = page_args(query)
    hours = window_hours(query, default=0)
    ro, profiles, errors = _scoped(registry, principal, settings)
    rows, fetch_errors, capped = _fetch_sessions(registry, ro, settings, profiles, offset + limit)
    errors.update(fetch_errors)
    targets = _links(settings, hermes_config, request_base)
    want_status = clean(query.get("status"), 20)
    want_profile = clean(query.get("profile"), 40)
    want_surface = clean(query.get("surface"), 30)
    since = _since_iso(hours) if hours else None
    out = []
    for row in rows:
        if want_status and row["status"] != want_status:
            continue
        if want_profile and row["profile"] != want_profile:
            continue
        if want_surface and (row.get("source") or "") != want_surface and row["surface"] != want_surface:
            continue
        if since and (row.get("last_active") or "") < since:
            continue
        if not _matches(row, query):
            continue
        out.append({**row, "peek": "session",
                    "url": build_link(targets, row["profile"], row["session_id"])})
    out.sort(key=lambda r: r.get("last_active") or "", reverse=True)
    page = out[offset:offset + limit]
    return {"kind": "sessions", "items": page, "total": len(out), "offset": offset, "limit": limit,
            "has_more": offset + len(page) < len(out), "capped": capped,
            "filters": _filters_echo(query, ("status", "profile", "surface", "hours", "q")),
            "profiles": profiles, "errors": errors,
            "links": targets,
            "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")}


def _events(settings, profiles: Optional[List[str]], limit: int = RUNS_FETCH) -> tuple:
    """Inbox rows of every profile, classified with the shared function."""
    try:
        res = get_inbox(settings).list(limit=limit, profiles=profiles, status="all")
    except Exception as exc:  # noqa: BLE001
        return [], {"inbox": str(exc)}
    rows = []
    for row in res.get("events") or []:
        if not isinstance(row, dict):
            continue
        rows.append({**row, "_bucket": AT.classify(row)})
    return rows, {}


def _run_row(event: Dict[str, Any], targets: Dict[str, Any]) -> Dict[str, Any]:
    bucket = str(event.get("_bucket") or "")
    settled = event.get("settled_reason")
    state = {"waiting": "Freigabe wartet", "attention": "offen", "interrupted": "unterbrochen",
             "settled": "erledigt", "history": "entschieden"}.get(bucket, "—")
    tone = {"waiting": "warn", "attention": "bad", "interrupted": "warn",
            "settled": "muted", "history": "ok"}.get(bucket, "muted")
    profile = clean(event.get("profile"), 40)
    sid = safe_token(event.get("session_id"))
    return {
        "id": safe_token(event.get("run_id")) or safe_token(event.get("id")),
        "peek": "run", "kind": "run", "bucket": bucket, "state": state, "tone": tone,
        "symbol": sym(tone),
        "profile": profile,
        "surface": surface_label(event.get("source")),
        "event_kind": clean(event.get("kind"), 30),
        "summary": clean(redacted(event.get("summary")), 160),
        "at": iso(event.get("ts")),
        "session_id": sid,
        "url": build_link(targets, profile or "default", sid),
        "settled_reason": clean(settled, 60),
        "settled_text": AT.settled_text(event) if settled else None,
    }


def _approval_row(event: Dict[str, Any], targets: Dict[str, Any]) -> Dict[str, Any]:
    state = clean(event.get("approval_state"), 20) or "?"
    tone = {"waiting": "warn", "approved": "ok", "denied": "bad", "expired": "muted"}.get(state, "muted")
    return {
        "id": safe_token(event.get("approval_key")), "peek": "approval", "kind": "approval",
        "bucket": "waiting" if state == "waiting" else "history", "state": state,
        "tone": tone, "symbol": sym(tone),
        "profile": clean(event.get("profile"), 40),
        "surface": surface_label(event.get("approval_surface")),
        "action": clean(redacted(event.get("action")), 160),
        "rationale": clean(redacted(event.get("rationale")), 160),
        "decision": clean(event.get("approval_decision"), 20),
        "at": iso(event.get("ts")),
        "closed_at": iso(event.get("approval_closed_at")),
        "session_id": safe_token(event.get("session_id")),
    }


def _filters_echo(query: Dict[str, Any], names) -> Dict[str, Any]:
    return {name: clean((query or {}).get(name), 80) for name in names}


def list_runs(registry, principal: Principal, settings, hermes_config: Dict[str, Any],
              query: Dict[str, Any], request_base: str = "") -> Dict[str, Any]:
    """Run history, paged: open failures, restart interruptions and settled ones (with the reason)."""
    offset, limit = page_args(query)
    ro, profiles, errors = _scoped(registry, principal, settings)
    targets = _links(settings, hermes_config, request_base)
    events, ev_errors = _events(settings, profiles if profiles != ["*"] else None)
    errors.update(ev_errors)
    want_status = clean(query.get("status"), 20)
    want_profile = clean(query.get("profile"), 40)
    want_surface = clean(query.get("surface"), 30)
    since = _since_iso(window_hours(query, default=168))
    out = []
    for event in events:
        if event.get("_bucket") not in ("attention", "interrupted", "settled"):
            continue
        row = _run_row(event, targets)
        if since and (row.get("at") or "") < since:
            continue
        if want_status and row["bucket"] != want_status:
            continue
        if want_profile and row["profile"] != want_profile:
            continue
        if want_surface and (row.get("surface") or "") != surface_label(want_surface):
            continue
        if not _matches(row, query):
            continue
        out.append(row)
    out.sort(key=lambda r: r.get("at") or "", reverse=True)
    page = out[offset:offset + limit]
    return {"kind": "runs", "items": page, "total": len(out), "offset": offset, "limit": limit,
            "has_more": offset + len(page) < len(out), "capped": len(events) >= RUNS_FETCH,
            "filters": _filters_echo(query, ("status", "profile", "surface", "hours", "q")),
            "counts": {b: sum(1 for r in out if r["bucket"] == b)
                       for b in ("attention", "interrupted", "settled")},
            "profiles": profiles, "errors": errors, "links": targets,
            "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")}


def list_approvals(registry, principal: Principal, settings, hermes_config: Dict[str, Any],
                   query: Dict[str, Any], request_base: str = "") -> Dict[str, Any]:
    """Approval history, paged, with the decision protocol (who decided, where, what lost)."""
    offset, limit = page_args(query)
    ro, profiles, errors = _scoped(registry, principal, settings)
    targets = _links(settings, hermes_config, request_base)
    events, ev_errors = _events(settings, profiles if profiles != ["*"] else None)
    errors.update(ev_errors)
    want_state = clean(query.get("state"), 20) or clean(query.get("status"), 20)
    want_profile = clean(query.get("profile"), 40)
    since = _since_iso(window_hours(query, default=168))
    out = []
    for event in events:
        if event.get("_bucket") not in ("waiting", "history"):
            continue
        row = _approval_row(event, targets)
        if since and (row.get("at") or "") < since:
            continue
        if want_state and row["state"] != want_state:
            continue
        if want_profile and row["profile"] != want_profile:
            continue
        if not _matches(row, query):
            continue
        out.append(row)
    out.sort(key=lambda r: r.get("at") or "", reverse=True)
    page = out[offset:offset + limit]
    for row in page:            # the protocol only for the rows on this page (one small read each)
        try:
            res = call(registry, ro, settings, "approval_detail", approval_key=row["id"]) or {}
            row["protocol"] = _protocol(res.get("protocol"))
        except Exception as exc:  # noqa: BLE001
            row["protocol"] = []
            errors.setdefault("approval_detail", str(exc))
    return {"kind": "approvals", "items": page, "total": len(out), "offset": offset, "limit": limit,
            "has_more": offset + len(page) < len(out), "capped": len(events) >= RUNS_FETCH,
            "counts": {"waiting": sum(1 for r in out if r["bucket"] == "waiting"),
                       "history": sum(1 for r in out if r["bucket"] == "history")},
            "filters": _filters_echo(query, ("state", "profile", "hours", "q")),
            "profiles": profiles, "errors": errors, "links": targets,
            "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")}


def list_cron(registry, principal: Principal, settings, hermes_config: Dict[str, Any],
              query: Dict[str, Any], request_base: str = "") -> Dict[str, Any]:
    """Cron runs, paged, from the same read tool the start page and the tools use."""
    offset, limit = page_args(query)
    hours = window_hours(query, default=168)
    ro, profiles, errors = _scoped(registry, principal, settings)
    targets = _links(settings, hermes_config, request_base)
    want_status = clean(query.get("status"), 20)
    want_profile = clean(query.get("profile"), 40)
    want_job = clean(query.get("job"), 80)
    since = _since_iso(hours)
    rows: List[Dict[str, Any]] = []
    capped = False

    def one_cron(prof: str) -> tuple:
        args: Dict[str, Any] = {"profile": prof, "since": since, "limit": CRON_FETCH}
        if want_job:
            args["job_id"] = want_job
        try:
            return call(registry, ro, settings, "cron_runs", **args) or {}, None
        except Exception as exc:  # noqa: BLE001
            return {}, str(exc)

    with ThreadPoolExecutor(max_workers=min(8, max(1, len(profiles)))) as pool:
        cron_results = list(pool.map(one_cron, profiles))
    for prof, (res, err) in zip(profiles, cron_results):
        if err:
            errors.setdefault("cron_runs", err)
            continue
        capped = capped or bool(res.get("truncated"))
        for run in res.get("runs") or []:
            if not isinstance(run, dict):
                continue
            status = clean(run.get("status"), 24) or "?"
            tone = "bad" if status in ("error", "failed") else "ok" if status in ("completed", "ok") else "warn"
            sid = safe_token(run.get("session_id"))
            row = {
                "id": safe_token(run.get("run_id")), "peek": "cron", "kind": "cron",
                "job_id": safe_token(run.get("job_id")),
                "job_name": clean(run.get("job_name"), 80),
                "profile": prof, "state": status, "status": status, "tone": tone, "symbol": sym(tone),
                "started_at": iso(run.get("started_at")),
                "at": iso(run.get("started_at")),
                "duration_s": run.get("duration_s") if isinstance(run.get("duration_s"), (int, float)) else None,
                "delivery_outcome": clean(run.get("delivery_outcome"), 24),
                "session_id": sid,
                "error": clean(redacted(run.get("error")), 140),
                "url": build_link(targets, prof, sid),
            }
            if want_status and status != want_status:
                continue
            if want_profile and prof != want_profile:
                continue
            if not _matches(row, query):
                continue
            rows.append(row)
    rows.sort(key=lambda r: r.get("started_at") or "", reverse=True)
    page = rows[offset:offset + limit]
    return {"kind": "cron", "items": page, "total": len(rows), "offset": offset, "limit": limit,
            "has_more": offset + len(page) < len(rows), "capped": capped,
            "filters": _filters_echo(query, ("status", "profile", "job", "hours", "q")),
            "profiles": profiles, "errors": errors, "links": targets,
            "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")}


def build_gateway(registry, principal: Principal, settings, hermes_config: Dict[str, Any],
                  request_base: str = "") -> Dict[str, Any]:
    """The /ledger/gateway page: platform state, versions, profiles — no secrets anywhere."""
    ro, profiles, errors = _scoped(registry, principal, settings)
    gw = _gateway(registry, ro, settings, errors)
    detail_profiles: List[Dict[str, Any]] = []
    try:
        res = call(registry, ro, settings, "profiles_list") or {}
        for row in res.get("profiles") or []:
            if not isinstance(row, dict):
                continue
            detail_profiles.append({"name": clean(row.get("name"), 60),
                                    "reachable": bool(row.get("reachable")),
                                    "kind": clean(row.get("kind"), 30)})
    except Exception as exc:  # noqa: BLE001
        errors.setdefault("profiles_list", str(exc))
    keys: List[Dict[str, Any]] = []
    try:
        res = call(registry, ro, settings, "keys_list") or {}
        for row in res.get("keys") or []:
            if not isinstance(row, dict):
                continue
            keys.append({"id": clean(row.get("id"), 40), "name": clean(row.get("name"), 60),
                         "disabled": bool(row.get("disabled")),
                         "last_used": iso(row.get("last_used")),
                         "scopes": _scopes_brief(row.get("scopes"))})
    except Exception as exc:  # noqa: BLE001 - keys_list needs admin; a read view simply has no Key-Liste
        errors["keys"] = clean(str(exc), 160)
    return {"kind": "gateway", "gateway": gw, "profiles": detail_profiles or
            [{"name": p, "reachable": True, "kind": None} for p in profiles],
            "keys": keys, "errors": errors,
            "links": _links(settings, hermes_config, request_base),
            "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")}


def _scopes_brief(scopes: Any) -> Dict[str, Any]:
    if not isinstance(scopes, dict):
        return {}
    out: Dict[str, Any] = {"access": clean(scopes.get("access"), 20)}
    for name in ("toolsets", "profiles"):
        value = scopes.get(name)
        if isinstance(value, list):
            out[name] = [c for c in (clean(v, 40) for v in value[:12]) if c]
    return out
