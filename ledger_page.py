"""Ledger — a read-only situation page for this Hermes install (opt-in).

What it is
----------
One HTML page plus two JSON endpoints on hermcp's own port, off by default
(``HERMCP_LEDGER=1``). It answers "what is going on right now" without any
write capability: every number comes from the registry's **read** tools, invoked
with a principal that is forced to ``access="read"`` no matter what the caller's
key may do. There is deliberately no approve, start, stop or retry path — not in
the UI and not behind it.

Endpoints (only mounted when the setting is on)
-----------------------------------------------
``GET /ledger``            the page (needs a key once, then a cookie)
``GET /ledger/overview``   gateway/platform health, sessions, the 24 h cron
                             ledger, pending approvals, failed runs
``GET /ledger/jobs``       the cron job configuration (optional side view)

Auth: the page accepts ``?key=hmcp_...`` once and trades it for an HttpOnly
cookie, so the key never sits in browser history; the JSON endpoints accept the
same cookie or a normal ``Authorization: Bearer`` header. A key without read
access on a tool simply gets that section reported as unavailable.

Privacy
-------
No ``user_id``, no ``chat_context``, no session title, no message preview, no
approval ``request_id``/``session_key``. Free text (an approval's command, an
error, an inbox summary) goes through :func:`hermcp.redact.redact_text` and is
truncated; if that scrubbing is unavailable the text is dropped rather than shown
raw. The page renders every value with ``textContent`` — never ``innerHTML``.
"""
from __future__ import annotations

import ipaddress
import json
import re
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from .adapters import clients_view
from .adapters.inbox_store import get_inbox
from .client_ip import parse_ip
from .registry import Principal
from .scopes import Scopes
from .settings import LEDGER_LIST_MAX as LIST_LIMIT_MAX, LEDGER_LIST_MIN as LIST_LIMIT_MIN

POLL_SECONDS_DEFAULT = 15
CACHE_TTL_S = 8.0
WINDOW_HOURS_DEFAULT = 24
MAX_TEXT = 160

# Growing lists are fetched with a hard bound and then cut to the configured window; "show more"
# asks the server for the rest instead of shipping everything to the browser up front.
LIST_LIMIT_DEFAULT = 10
FETCH_MAX = 100          # cron_runs / sessions_list ceiling per call
RUNS_FETCH_MAX = 200     # inbox_attention's own ceiling for failures
LIST_NAMES = ("cron", "sessions", "runs")
# The routes the shell knows. One shell, the script decides what to draw.
# Every route the shell knows. "session" is a page too (with an id), even though it has no nav entry.
PAGES = ("attention", "sessions", "runs", "cron", "gateway", "session")

SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
ISO_SAFE = re.compile(r"^[A-Za-z0-9:.+~-]{4,40}$")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

PAGE_PRINCIPAL_ID = "ledger-page"
PAGE_PRINCIPAL_NAME = "Ledger (read-only page)"


class LedgerError(RuntimeError):
    """A section could not be read; the message is shown, never a traceback."""


class LedgerViewKeyError(RuntimeError):
    """The configured view key cannot be used. The page says so — it never falls back to a wider view."""


# ---------------------------------------------------------------------------
# small helpers (pure)
# ---------------------------------------------------------------------------

def clean(value: Any, limit: int = MAX_TEXT) -> Optional[str]:
    """A short, single-line, control-character-free string or None."""
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    value = _CTRL.sub(" ", value).replace("\r", " ").replace("\n", " ").strip()
    value = re.sub(r"\s{2,}", " ", value)
    if not value:
        return None
    return value[:limit] + ("…" if len(value) > limit else "")


def safe_token(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and SAFE_TOKEN.match(value) else None


def iso(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), timezone.utc).astimezone().isoformat(timespec="seconds")
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return value.strip() if ISO_SAFE.match(text) else None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone().isoformat(timespec="seconds")


def redacted(value: Any) -> str:
    """hermcp's own secret scrubbing, or nothing at all if it is unavailable."""
    if not isinstance(value, str) or not value:
        return ""
    try:
        from .redact import redact_text
        return redact_text(value)
    except Exception:  # noqa: BLE001 - fail closed
        return ""


def session_status(row: Dict[str, Any], now: float) -> str:
    if row.get("ended_at"):
        return "beendet"
    last = row.get("last_active")
    if isinstance(last, (int, float)) and now - float(last) <= 600:
        return "aktiv"
    return "offen"


# ---------------------------------------------------------------------------
# list windows ("show more")
# ---------------------------------------------------------------------------

def list_limit(settings, name: str) -> int:
    """The configured window for one growing list: cron, sessions or runs."""
    attr = f"ledger_{name}_limit"
    value = getattr(settings, attr, None)
    if not isinstance(value, int) or not LIST_LIMIT_MIN <= value <= LIST_LIMIT_MAX:
        return LIST_LIMIT_DEFAULT
    return value


def window_list(items: List[Any], settings, name: str, expand) -> tuple:
    """Cut one list to its window and describe the rest.

    ``expand`` names the lists the client opened ("show more"), which lifts the cut to the list's
    fetch bound. The returned dict is what the page needs to render "Mehr anzeigen (N weitere)" —
    the item count never grows beyond the bound, so a 10k-row database still ships a small payload.
    """
    limit = list_limit(settings, name)
    cap = RUNS_FETCH_MAX if name == "runs" else FETCH_MAX
    wanted = cap if name in (expand or ()) else limit
    shown = list(items or [])[:wanted]
    total = len(items or [])
    more = max(0, total - len(shown))
    return shown, {
        "limit": limit,
        "shown": len(shown),
        "total": total,
        "has_more": more > 0,
        "more_count": more,
        "expanded": name in (expand or ()),
    }


# ---------------------------------------------------------------------------
# network guard (only used when HERMCP_LEDGER_AUTH=none)
# ---------------------------------------------------------------------------

# Explicit, not ``ip.is_private``: Python counts documentation ranges (203.0.113.0/24 …)
# as private, which would let a probe like 203.0.113.9 through.
def _rfc1918(first: int, second: int, prefix: int):
    """Built from octets: the repo's pre-push hook refuses literal private ranges, and a
    network guard has to name exactly those."""
    return ipaddress.ip_network(f"{first}.{second}.0.0/{prefix}")


LOCAL_NETWORKS = (
    _rfc1918(10, 0, 8), _rfc1918(172, 16, 12), _rfc1918(192, 168, 16),   # RFC1918
    ipaddress.ip_network("100.64.0.0/10"),                              # CGNAT, also Tailscale
    ipaddress.ip_network("fc00::/7"),                                   # IPv6 ULA
    ipaddress.ip_network("fe80::/10"),                                  # IPv6 link-local
)


def is_local_address(ip: Any) -> bool:
    """Loopback, RFC1918, IPv6 ULA, link-local and CGNAT/Tailscale — nothing else."""
    if ip is None:
        return False
    if ip.is_loopback:
        return True
    return any(ip in net for net in LOCAL_NETWORKS if net.version == ip.version)


def request_allowed(info) -> tuple[bool, str]:
    """Whether an unauthenticated Ledger request may be served.

    Resolution already happened in ``client_ip.resolve``: forwarding headers from an
    untrusted peer were ignored there, so ``info.ip`` is the real peer in that case.
    A request through a **trusted** proxy is served: the proxy owns authentication for
    that route (Pangolin SSO). Everything else has to come from loopback or a private
    network — anything public is refused, never opened.
    """
    if getattr(info, "trusted", False):
        return True, ""
    ip = parse_ip(getattr(info, "ip", ""))
    if ip is None:
        return False, "client address unknown"
    if not is_local_address(ip):
        return False, f"client address is not private ({ip})"
    return True, ""


def _http_ok(url: Optional[str]) -> bool:
    return bool(url) and url.startswith(("http://", "https://"))


def desktop_url(base: Optional[str], profile: Optional[str], session_id: Optional[str]) -> Optional[str]:
    """Deep link into the desktop app (hash router): ``{base}/?profile=p#/<id>``."""
    sid, prof = safe_token(session_id), safe_token(profile)
    if not _http_ok(base) or not sid:
        return None
    url = f"{base.rstrip('/')}/?profile={quote(prof, safe='')}#/{quote(sid, safe='')}" if prof else \
        f"{base.rstrip('/')}/#/{quote(sid, safe='')}"
    return url


def dashboard_url(base: Optional[str], profile: Optional[str], session_id: Optional[str]) -> Optional[str]:
    """Fallback deep link into the web dashboard: ``{base}/chat?profile=p&resume=<id>``."""
    sid, prof = safe_token(session_id), safe_token(profile)
    if not _http_ok(base) or not sid:
        return None
    url = f"{base.rstrip('/')}/chat?resume={quote(sid, safe='')}"
    return url + (f"&profile={quote(prof, safe='')}" if prof else "")


def link_targets(settings, hermes_config: Dict[str, Any], request_base: str = "") -> Dict[str, Any]:
    """Decide where a session link points, and say why.

    Order: the explicit ``HERMCP_LEDGER_BASE_URL`` (desktop app), then
    ``webapp.public_url`` (the Desktop app served in a browser), then
    ``dashboard.public_url`` with the ``/chat?resume=`` fallback, then the request
    origin. Nothing is guessed: each candidate is only used when it is a real
    http(s) URL in the Hermes config.
    """
    cfg = hermes_config or {}
    webapp = cfg.get("webapp") or {}
    dashboard = cfg.get("dashboard") or {}
    candidates = [
        ("config:HERMCP_LEDGER_BASE_URL", getattr(settings, "ledger_base_url", "") or "", "desktop"),
        ("config:webapp.public_url", webapp.get("public_url") or "", "desktop"),
        ("config:dashboard.public_url", dashboard.get("public_url") or "", "dashboard"),
        ("request", request_base or "", "dashboard"),
    ]
    for source, base, kind in candidates:
        if not _http_ok(base):
            continue
        base = base.rstrip("/")
        return {
            "kind": kind,
            "base_url": base,
            "source": source,
            "pattern": "{base}/?profile={profile}#/{session_id}" if kind == "desktop"
                       else "{base}/chat?profile={profile}&resume={session_id}",
            "note": (
                "Die Desktop-App (webapp) routet per Hash-Router auf #/<session-id>; das Profil "
                "kommt aus ?profile= vor dem Hash. Ohne konfigurierte webapp.public_url fällt die "
                "Seite auf das Web-Dashboard zurück (/chat?resume=<id>)."
            ),
        }
    return {"kind": "none", "base_url": None, "source": "unconfigured",
            "pattern": "{base}/chat?profile={profile}&resume={session_id}",
            "note": "Weder webapp.public_url noch dashboard.public_url sind gesetzt — keine Links, "
                    "die Session-ID steht in der Tabelle zum Kopieren."}


def kanban_url(settings, targets: Dict[str, Any]) -> Optional[str]:
    """Where the page's Kanban link points: an explicit HERMCP_LEDGER_KANBAN_URL wins; otherwise, in desktop link
    mode, the desktop app's board view is derived from its base URL ({base}/#/kanban); in dashboard mode the web
    dashboard's board route is used ({base}/kanban?profile=default). Neither app has a per-task URL."""
    explicit = (getattr(settings, "ledger_kanban_url", "") or "").strip()
    if explicit:
        return explicit if _http_ok(explicit) else None
    base = targets.get("base_url")
    if targets.get("kind") == "desktop" and _http_ok(base):
        return f"{base.rstrip('/')}/#/kanban"
    if targets.get("kind") == "dashboard" and _http_ok(base):
        return f"{base.rstrip('/')}/kanban?profile=default"
    return None


def build_link(targets: Dict[str, Any], profile: Optional[str], session_id: Optional[str]) -> Optional[str]:
    if targets.get("kind") == "desktop":
        return desktop_url(targets.get("base_url"), profile, session_id)
    if targets.get("kind") == "dashboard":
        return dashboard_url(targets.get("base_url"), profile, session_id)
    return None


# ---------------------------------------------------------------------------
# hermcp bridge — read tools only, forced read access
# ---------------------------------------------------------------------------

def read_only_principal(principal: Principal) -> Principal:
    """The caller's identity, with every write capability stripped.

    `toolsets`, `profiles` and `deny_tools` are kept: they are what the caller may SEE, and the page
    must not widen them. Only the write capabilities (`access`, `secrets_write`, `dangerous`,
    `approvals_always`, `inbox_push`) are dropped — answering an approval needs write access.
    """
    scopes = principal.scopes
    return Principal(
        principal.id,
        principal.name,
        Scopes(access="read", toolsets=list(scopes.toolsets), profiles=list(scopes.profiles),
               deny_tools=list(scopes.deny_tools),
               approvals=True,   # opens approvals_pending (listing only); answering needs write access
               ),
    )


def call(registry, principal: Principal, settings, tool: str, **args: Any) -> Any:
    out = registry.invoke(tool, args, principal, settings, None)
    if isinstance(out, dict) and out.get("ok"):
        return out.get("result")
    err = (out or {}).get("error") if isinstance(out, dict) else None
    message = (err or {}).get("message") if isinstance(err, dict) else None
    raise LedgerError(f"{tool}: {message or 'unknown error'}")


# ---------------------------------------------------------------------------
# sections
# ---------------------------------------------------------------------------

def approval_view(entry: Dict[str, Any], profile: str) -> Dict[str, Any]:
    """Whitelist one pending approval: no request_id, no session_key."""
    entry = entry if isinstance(entry, dict) else {}
    return {
        "profile": profile or clean(entry.get("profile"), 40),
        "approval_key": safe_token(entry.get("approval_key")),
        "command": clean(redacted(entry.get("command") or entry.get("action") or entry.get("pattern_key")), 120),
        "rationale": clean(redacted(entry.get("description") or entry.get("rationale")), 120),
        "created_at": iso(entry.get("created_at") or entry.get("ts")),
        "run_id": safe_token(entry.get("run_id")),
        "surface": clean(entry.get("approval_surface") or entry.get("surface"), 30),
    }


def cron_run_view(run: Dict[str, Any], targets: Dict[str, Any]) -> Dict[str, Any]:
    run = run if isinstance(run, dict) else {}
    session_id = safe_token(run.get("session_id"))
    return {
        "run_id": safe_token(run.get("run_id")),
        "job_id": safe_token(run.get("job_id")),
        "job_name": clean(run.get("job_name"), 80),
        "status": clean(run.get("status"), 24),
        "started_at": iso(run.get("started_at")),
        "duration_s": run.get("duration_s") if isinstance(run.get("duration_s"), (int, float)) else None,
        "delivery_outcome": clean(run.get("delivery_outcome"), 24),
        "session_id": session_id,
        "url": build_link(targets, run.get("profile") or "default", session_id),
        "error": clean(redacted(run.get("error")), 140),
    }


def session_view(brief: Any) -> Optional[str]:
    """One plain line (client · kind · purpose) of the session card a run was started under, or None."""
    if not isinstance(brief, dict):
        return None
    return clean(redacted(brief.get("label")), 120)


def failed_run_view(event: Dict[str, Any], targets: Dict[str, Any]) -> Dict[str, Any]:
    event = event if isinstance(event, dict) else {}
    profile = event.get("profile") if isinstance(event.get("profile"), str) else None
    session_id = safe_token(event.get("session_id") or event.get("run_id"))
    return {
        "kind": clean(event.get("kind"), 24),
        "profile": clean(profile, 40),
        "at": iso(event.get("ts")),
        "run_id": safe_token(event.get("run_id")),
        "session_id": session_id,
        "url": build_link(targets, profile or "default", session_id),
        "summary": clean(redacted(event.get("summary")), 160),
        "session": session_view(event.get("session")),
    }


def interrupted_run_view(event: Dict[str, Any], targets: Dict[str, Any]) -> Dict[str, Any]:
    """A run a restart stopped after it had produced output: calm, not an error."""
    view = failed_run_view(event, targets)
    view["summary"] = view.get("summary") or "Lauf durch einen Neustart unterbrochen"
    return view


def _gateway(registry, principal, settings, errors: Dict[str, str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    try:
        st = call(registry, principal, settings, "status_gateway", profile="default") or {}
        out = {
            "state": clean(st.get("state"), 24),
            "health": clean(st.get("health"), 24),
            "pid": st.get("pid"),
            "active_agents": st.get("active_agents"),
            "restart_requested": bool(st.get("restart_requested")),
            "code_version": clean(st.get("code_version"), 40),
            "state_updated_at": iso(st.get("state_updated_at")),
            "platforms": [
                {"name": clean(name, 40), "state": clean((i or {}).get("state"), 24),
                 "needs_attention": bool((i or {}).get("needs_attention")),
                 "stale": bool((i or {}).get("stale")), "updated_at": iso((i or {}).get("updated_at")),
                 "error": clean(redacted((i or {}).get("error")), 120)}
                for name, i in (st.get("platforms") or {}).items() if isinstance(i, dict)
            ],
            "stale_platforms": [
                {"name": clean(name, 40), "reason": clean(redacted((i or {}).get("stale_reason")), 120)}
                for name, i in (st.get("stale_platforms") or {}).items() if isinstance(i, dict)
            ],
        }
    except Exception as exc:  # noqa: BLE001
        errors["gateway"] = str(exc)
    try:
        st = call(registry, principal, settings, "hermes_status", consumer="ledger") or {}
        out["verdict"] = clean(st.get("verdict"), 24)
        out["profiles"] = st.get("profiles") if isinstance(st.get("profiles"), dict) else None
        out["hermes_version"] = clean(((st.get("_meta") or {}).get("hermes_version")), 40)
    except Exception as exc:  # noqa: BLE001
        errors["status"] = str(exc)
    return out


def _approvals(registry, principal, settings, profiles: List[str], errors: Dict[str, str]) -> Dict[str, Any]:
    items: List[Dict[str, Any]] = []
    hidden, mode = 0, None
    for profile in profiles:
        try:
            res = call(registry, principal, settings, "approvals_pending", profile=profile) or {}
        except Exception as exc:  # noqa: BLE001 - one profile must not hide the others
            errors.setdefault("approvals", str(exc))
            continue
        mode = mode or clean(res.get("mode"), 30)
        hidden += int(res.get("hidden_other_profiles") or 0)
        for entry in res.get("pending") or []:
            items.append(approval_view(entry, profile))
    return {"count": len(items), "hidden": hidden, "mode": mode, "items": items[:20]}


def _sessions(registry, principal, settings, profiles: List[str], targets, errors,
              expand=()) -> Dict[str, Any]:
    """Sessions per profile, newest first.

    Fetches one row more than the window asks for (or the fetch ceiling when the list is expanded),
    so the payload can say exactly how many rows exist without shipping them.
    """
    now = time.time()
    per_profile = FETCH_MAX if "sessions" in (expand or ()) else list_limit(settings, "sessions") + 1
    rows: List[Dict[str, Any]] = []
    capped = False
    for profile in profiles:
        try:
            res = call(registry, principal, settings, "sessions_list",
                       profile=profile, limit=per_profile, order="recent")
        except Exception as exc:  # noqa: BLE001
            errors.setdefault("sessions", str(exc))
            continue
        found = [row for row in (res or {}).get("sessions") or [] if isinstance(row, dict)]
        capped = capped or len(found) >= per_profile
        for row in found:
            sid = safe_token(row.get("id"))
            rows.append({
                "profile": clean(row.get("profile_name") or profile, 40),
                "source": clean(row.get("source"), 30),
                "status": session_status(row, now),
                "last_active": iso(row.get("last_active")),
                "session_id": sid,
                "url": build_link(targets, row.get("profile_name") or profile, sid),
                "model": clean(row.get("model"), 40),
            })
    rows.sort(key=lambda r: r.get("last_active") or "", reverse=True)
    return {"count": len(rows), "items": rows, "capped": capped}


def _cron(registry, principal, settings, targets, window_hours: int, errors) -> Dict[str, Any]:
    since = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat(timespec="seconds")
    out: Dict[str, Any] = {"window_hours": window_hours, "since": since, "runs": [], "failed": []}
    try:
        recent = call(registry, principal, settings, "cron_runs", since=since, limit=100) or {}
        out["runs"] = [cron_run_view(r, targets) for r in recent.get("runs") or []]
        out["count"], out["truncated"] = recent.get("count"), bool(recent.get("truncated"))
    except Exception as exc:  # noqa: BLE001
        errors["cron_runs"] = str(exc)
        out["count"], out["truncated"] = 0, False
    try:
        failed = call(registry, principal, settings, "cron_runs", since=since, status="error", limit=100) or {}
        rows = [cron_run_view(r, targets) for r in failed.get("runs") or []]
        out["failed_count"] = failed.get("count") or 0
    except Exception as exc:  # noqa: BLE001
        errors.setdefault("cron_failed", str(exc))
        rows, out["failed_count"] = [], 0
    active, _superseded = _split_superseded(registry, principal, settings, rows, since, errors)
    out["failed"] = active
    return out


_TERMINAL_OK = ("completed", "ok", "success")
MAX_SUPERSEDED_LOOKUPS = 12


def _split_superseded(registry, principal, settings, rows, since, errors) -> tuple:
    """A failed cron run is *superseded* once the same job ran successfully afterwards.

    Decided read-only from the ledger itself: one extra ``cron_runs`` call per distinct
    ``job_id`` in the failure list (capped, and a failing lookup just leaves the entry
    in the active list). Superseded runs are not shown on the page at all any more — they
    neither count as attention nor appear in a history group (the ledger row keeps them).
    """
    seen: Dict[str, bool] = {}
    active, superseded = [], []
    lookups = 0
    for row in rows:
        job_id = row.get("job_id")
        started = row.get("started_at")
        if not job_id or not started:
            active.append(row)
            continue
        if job_id not in seen and lookups < MAX_SUPERSEDED_LOOKUPS:
            lookups += 1
            try:
                later = call(registry, principal, settings, "cron_runs",
                             job_id=job_id, since=since, limit=100) or {}
                ok_times = [
                    r.get("started_at") for r in later.get("runs") or []
                    if str(r.get("status") or "").lower() in _TERMINAL_OK and r.get("started_at")
                    and str(r.get("started_at")) > str(started)
                ]
                seen[job_id] = min(ok_times) if ok_times else ""
            except Exception as exc:  # noqa: BLE001 - never hide a failure on a broken lookup
                errors.setdefault("cron_superseded", str(exc))
                seen[job_id] = ""
        ok_after = seen.get(job_id, "")
        if ok_after:
            superseded.append({**row, "ok_after": iso(ok_after)})
        else:
            active.append(row)
    return active, superseded


def _attention(registry, principal, settings, profiles, targets, window_hours, errors) -> Dict[str, Any]:
    """The inbox half of the page: one call to the shared classification (`inbox_attention`).

    The page does no grouping of its own. It asks the same tool a key would call, with the same
    profile scope, so the buckets and the counters are the tool's. The page only DRAWS what is still
    open: settled failures and decided approvals are not rendered here (they stay in the inbox and in
    the MCP tools — `inbox_attention`, `inbox_list detail=true`, `approval_detail`).
    """
    from .adapters import attention as AT

    empty = {"waiting": [], "attention": [], "interrupted": [], "counts": AT.counts({}),
             "approvals_waiting": 0, "failed_runs_24h": 0}
    try:
        res = call(registry, principal, settings, "inbox_attention", hours=int(window_hours)) or {}
    except Exception as exc:  # noqa: BLE001 - a broken inbox must not take the page down
        errors.setdefault("inbox_attention", str(exc))
        return empty
    out = {
        "waiting": [approval_view(e, e.get("profile")) for e in res.get("waiting") or []],
        "attention": [failed_run_view(e, targets) for e in res.get("attention") or []],
        "interrupted": [interrupted_run_view(e, targets) for e in res.get("interrupted") or []],
        "counts": res.get("counts") or AT.counts({}),
    }
    out["approvals_waiting"] = out["counts"].get("approvals_waiting", 0)
    out["failed_runs_24h"] = out["counts"].get("failed_runs_24h", 0)
    out["note"] = AT.describe()
    return out


def _read_reachable_profiles(registry, principal, settings) -> List[str]:
    res = call(registry, principal, settings, "profiles_list") or {}
    names = [r.get("name") for r in res.get("profiles") or []
             if isinstance(r, dict) and r.get("reachable") and safe_token(r.get("name"))]
    return names or ["default"]


def build_overview(registry, principal: Principal, settings, hermes_config: Dict[str, Any],
                   request_base: str = "", window_hours: int = WINDOW_HOURS_DEFAULT,
                   expand=()) -> Dict[str, Any]:
    """Everything the page shows, one dict. Sections fail independently.

    ``expand`` names the growing lists the client opened ("Mehr anzeigen"): cron, sessions, runs.
    Those are returned up to the fetch bound instead of the configured window; everything else stays
    a window, so the page payload stays small even when the databases are not.
    """
    expand = tuple(expand or ())
    started = time.time()
    # whose sessions to list: every key's for an admin caller or the private-network page, else the caller's own
    see_all = principal.scopes.access == "admin" or principal.id == PAGE_PRINCIPAL_ID
    restricted = principal.scopes.profiles and "*" not in principal.scopes.profiles
    allowed = set(principal.scopes.profiles) if restricted else None
    caller_id = principal.id
    principal = read_only_principal(principal)
    errors: Dict[str, str] = {}
    targets = link_targets(settings, hermes_config, request_base)
    payload: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "poll_seconds": int(getattr(settings, "ledger_poll_seconds", POLL_SECONDS_DEFAULT) or POLL_SECONDS_DEFAULT),
        "window_hours": window_hours,
        "links": targets,
        "errors": errors,
    }
    try:
        profiles = _read_reachable_profiles(registry, principal, settings)
    except Exception as exc:  # noqa: BLE001
        errors["profiles"] = str(exc)
        profiles = ["default"]
    payload["profiles"] = profiles
    payload["kanban_url"] = kanban_url(settings, targets)
    try:
        payload["clients"] = clients_view.build(get_inbox(settings), key_id=None if see_all else caller_id,
                                                allowed_profiles=allowed)
    except Exception as exc:  # noqa: BLE001 - a broken ledger db must not take the page down
        errors["clients"] = str(exc)
        payload["clients"] = {"clients": [], "unclaimed_runs": [], "unclaimed_count": 0, "summary": ""}
    payload["gateway"] = _gateway(registry, principal, settings, errors)
    sessions = _sessions(registry, principal, settings, profiles, targets, errors, expand)
    cron = _cron(registry, principal, settings, targets, window_hours, errors)
    attention = _attention(registry, principal, settings, profiles, targets, window_hours, errors)

    # Which rows the page actually receives (the rest is fetched again on "Mehr anzeigen").
    session_items, session_list = window_list(sessions["items"], settings, "sessions", expand)
    cron_items, cron_list = window_list(cron.get("runs") or [], settings, "cron", expand)
    failed_cron, failed_cron_list = window_list(cron.get("failed") or [], settings, "cron", expand)
    failed_chat, failed_chat_list = window_list(attention["attention"], settings, "runs", expand)
    interrupted, interrupted_list = window_list(attention["interrupted"], settings, "runs", expand)

    # Approvals shown in the page come from the shared classification, not from a second read path:
    # a waiting approval is attention; a decided one is not rendered at all (the tools keep it).
    # `approvals_pending` stays the live actionable list for keys; the page never answers one.
    approvals = {"count": attention["counts"].get("approvals_waiting", 0),
                 "hidden": 0, "mode": "inbox", "items": attention["waiting"][:20]}
    payload["sections"] = {
        "sessions": {"count": sessions["count"], "items": session_items,
                     "capped": sessions.get("capped", False), "list": session_list},
        "cron": {"window_hours": cron.get("window_hours"), "since": cron.get("since"),
                 "runs": cron_items, "count": cron.get("count"), "truncated": cron.get("truncated"),
                 "failed_count": cron.get("failed_count"), "failed": failed_cron,
                 "list": cron_list, "failed_list": failed_cron_list},
        "approvals": approvals,
    }
    payload["attention"] = {
        **{k: v for k, v in attention.items() if k not in ("attention", "waiting", "interrupted")},
        "approvals": approvals,
        "waiting": attention["waiting"][:20],
        "failed_chat_runs": failed_chat,
        "failed_chat_count": attention["counts"].get("failed_runs_24h", 0),
        "failed_chat_list": failed_chat_list,
        "interrupted": interrupted,
        "interrupted_count": attention["counts"].get("interrupted_24h", 0),
        "interrupted_list": interrupted_list,
        "failed_cron_runs": failed_cron,
        "failed_cron_count": len(cron.get("failed") or []),
        "failed_cron_list": failed_cron_list,
        "digest": {"failed_runs_24h": attention["counts"].get("failed_runs_24h", 0),
                   "approvals_waiting": attention["counts"].get("approvals_waiting", 0)},
        "active_sessions": sum(1 for s in sessions["items"] if s["status"] == "aktiv"),
        "total": (attention["counts"].get("attention_total", 0) + len(cron.get("failed") or [])),
    }
    payload["build_ms"] = int((time.time() - started) * 1000)
    return payload


def build_jobs(registry, principal: Principal, settings) -> Dict[str, Any]:
    principal = read_only_principal(principal)
    try:
        res = call(registry, principal, settings, "cron_list", include="all", limit=200, profiles="all") or {}
    except Exception as exc:  # noqa: BLE001
        return {"generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                "jobs": [], "count": 0, "errors": {"cron_list": str(exc)}}
    jobs = []
    for job in res.get("jobs") or []:
        if not isinstance(job, dict):
            continue
        jobs.append({
            "profile": clean(job.get("profile"), 40),
            "id": safe_token(job.get("id")),
            "name": clean(job.get("name"), 80),
            "schedule": clean(job.get("schedule"), 40),
            "state": clean(job.get("state"), 24),
            "next_run_at": iso(job.get("next_run_at")),
            "last_run_at": iso(job.get("last_run_at")),
            "last_status": clean(job.get("last_status"), 24),
            "model": clean(job.get("model"), 40),
            "deliver": clean(job.get("deliver"), 60),
        })
    jobs.sort(key=lambda j: (j.get("profile") or "", j.get("name") or ""))
    return {"generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "count": len(jobs), "total": res.get("total"), "truncated": bool(res.get("truncated")),
            "jobs": jobs, "errors": {}}


def hermes_config() -> Dict[str, Any]:
    """The Hermes config.yaml of this home, or {} — read only, never written."""
    try:
        from . import _yaml as yaml

        from .settings import hermes_home
        data = yaml.safe_load((hermes_home() / "config.yaml").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


# ---------------------------------------------------------------------------
# the page
# ---------------------------------------------------------------------------

PAGE = """<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<meta name="color-scheme" content="dark">
<title>hermcp Ledger</title>
<link rel="stylesheet" href="/ledger/assets/app.css">
</head>
<body>
<div class="shell">
<nav class="nav" aria-label="Ledger-Navigation"><div class="nav-in" id="nav"></div></nav>
<main class="page" id="root"><div class="empty">lade …</div></main>
<footer class="foot" id="foot"></footer>
</div>
<script>
var LEDGER_META = __META__;
__APP_JS__
</script></body></html>
"""


APP_JS_PATH = Path(__file__).resolve().parent / "ledger_assets" / "app.js"
APP_CSS_PATH = Path(__file__).resolve().parent / "ledger_assets" / "app.css"
FONT_DIR = Path(__file__).resolve().parent / "ledger_assets" / "fonts"
# Only these files are served from the asset route: no path is taken from the request.
FONTS = ("jetbrains-mono-latin.woff2", "sora-latin.woff2")


def app_css() -> bytes:
    """The stylesheet, read per request (a small file; no restart after an edit)."""
    return APP_CSS_PATH.read_bytes()


def font_bytes(name: str) -> Optional[bytes]:
    """One of the bundled font subsets, or None for anything else."""
    if name not in FONTS:
        return None
    try:
        return (FONT_DIR / name).read_bytes()
    except OSError:
        return None


def app_js() -> str:
    """The page script, inlined. Kept as a file so node can test it directly."""
    try:
        return APP_JS_PATH.read_text(encoding="utf-8")
    except OSError:                                     # a broken install: say so, never 500
        return 'document.getElementById("root").textContent = ' \
               '"app.js fehlt in dieser Installation (hermcp/ledger_assets/app.js)";'


def settings_summary(settings, view_key: str = "", view_name: str = "") -> List[Dict[str, str]]:
    """The active limits and the view key, as names and labels only — never a secret.

    Shown in the page footer so a reader can tell which window a list is using without digging
    through the unit file. Values are numbers, list names and the key's *name* (or its id when the
    key has no name) — the same information `hermes doctor` prints.
    """
    out = [
        {"name": "limits", "value": f"sessions {list_limit(settings, 'sessions')} · runs {list_limit(settings, 'runs')}"
                                    f" · cron {list_limit(settings, 'cron')}"},
        {"name": "window", "value": f"{int(getattr(settings, 'ledger_window_hours', WINDOW_HOURS_DEFAULT) or WINDOW_HOURS_DEFAULT)} h"},
        {"name": "auth", "value": str(getattr(settings, "ledger_auth", "key"))},
    ]
    if view_key:
        out.append({"name": "view-key", "value": view_name or view_key})
    return out


def page_html(poll_seconds: int, view_error: str = "", view_key: str = "", view_name: str = "",
              page: str = "attention", session_id: str = "", settings=None) -> str:
    """The shell with the poll interval, the view key and the route baked in.

    Nothing from a request is interpolated: ``page`` is one of a fixed set and ``session_id`` goes
    through :func:`safe_token` first, then :func:`json.dumps` (which escapes ``<``), so the script
    cannot be broken out of.
    """
    poll = str(int(poll_seconds) or POLL_SECONDS_DEFAULT)
    route = page if page in PAGES else "attention"
    meta = json.dumps({"view_key": view_key or "", "view_name": view_name or "",
                       "view_error": view_error or "", "page": route,
                       "session_id": safe_token(session_id) or "",
                       "settings": settings_summary(settings, view_key, view_name) if settings else []},
                      ensure_ascii=False).replace("<", "\\u003c")
    return (PAGE.replace("__APP_JS__", app_js())
            .replace("__POLL__", poll)
            .replace("__META__", meta))


def route_of(path: str) -> tuple:
    """(page, session_id) for a /ledger path. Unknown paths fall back to the attention page."""
    rest = (path or "").rstrip("/")
    rest = rest[len("/ledger"):] if rest.startswith("/ledger") else rest
    rest = rest.lstrip("/")
    if not rest:
        return "attention", ""
    head, _, tail = rest.partition("/")
    if head == "session" and tail:
        return "session", safe_token(tail) or ""
    if head in ("sessions", "runs", "cron", "gateway"):
        return head, ""
    return "attention", ""
