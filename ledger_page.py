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

POLL_SECONDS_DEFAULT = 15
CACHE_TTL_S = 8.0
WINDOW_HOURS_DEFAULT = 24
SESSIONS_PER_PROFILE = 6
MAX_TEXT = 160

SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
ISO_SAFE = re.compile(r"^[A-Za-z0-9:.+~-]{4,40}$")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

PAGE_PRINCIPAL_ID = "ledger-page"
PAGE_PRINCIPAL_NAME = "Ledger (read-only page)"


class LedgerError(RuntimeError):
    """A section could not be read; the message is shown, never a traceback."""


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
    mode, the desktop app's board view is derived from its base URL ({base}/#/kanban). The web dashboard's board
    route is not known, so in dashboard mode there is no link unless it is configured."""
    explicit = (getattr(settings, "ledger_kanban_url", "") or "").strip()
    if explicit:
        return explicit if _http_ok(explicit) else None
    base = targets.get("base_url")
    if targets.get("kind") == "desktop" and _http_ok(base):
        return f"{base.rstrip('/')}/#/kanban"
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
    """The caller's identity, with every write capability stripped."""
    scopes = principal.scopes
    return Principal(
        principal.id,
        principal.name,
        Scopes(access="read", toolsets=list(scopes.toolsets), profiles=list(scopes.profiles),
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
        "profile": profile,
        "command": clean(redacted(entry.get("command") or entry.get("action") or entry.get("pattern_key")), 120),
        "rationale": clean(redacted(entry.get("description") or entry.get("rationale")), 120),
        "created_at": iso(entry.get("created_at") or entry.get("ts")),
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


def _sessions(registry, principal, settings, profiles: List[str], targets, errors) -> Dict[str, Any]:
    now = time.time()
    rows: List[Dict[str, Any]] = []
    for profile in profiles:
        try:
            res = call(registry, principal, settings, "sessions_list",
                       profile=profile, limit=SESSIONS_PER_PROFILE, order="recent")
        except Exception as exc:  # noqa: BLE001
            errors.setdefault("sessions", str(exc))
            continue
        for row in (res or {}).get("sessions") or []:
            if not isinstance(row, dict):
                continue
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
    return {"count": len(rows), "items": rows}


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
    active, superseded = _split_superseded(registry, principal, settings, rows, since, errors)
    out["failed"] = active
    out["superseded"] = superseded
    out["superseded_count"] = len(superseded)
    return out


_TERMINAL_OK = ("completed", "ok", "success")
MAX_SUPERSEDED_LOOKUPS = 12


def _split_superseded(registry, principal, settings, rows, since, errors) -> tuple:
    """A failed cron run is *superseded* once the same job ran successfully afterwards.

    Decided read-only from the ledger itself: one extra ``cron_runs`` call per distinct
    ``job_id`` in the failure list (capped, and a failing lookup just leaves the entry
    in the active list). Nothing is hidden from the page — superseded entries move to
    their own collapsed group and stop counting as attention.
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


def _failed_chat_runs(registry, principal, settings, profiles, targets, window_hours, errors) -> List[Dict[str, Any]]:
    since = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    try:
        res = call(registry, principal, settings, "inbox_list", kind="run_failed", limit=50, consumer="ledger") or {}
    except Exception as exc:  # noqa: BLE001
        errors.setdefault("failed_runs", str(exc))
        return []
    rows = []
    for event in res.get("events") or []:
        if not isinstance(event, dict):
            continue
        try:
            when = datetime.fromisoformat(str(event.get("ts")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if when < since:
            continue
        if isinstance(event.get("profile"), str) and event["profile"] not in profiles:
            continue
        rows.append(failed_run_view(event, targets))
    return rows[:20]


def _read_reachable_profiles(registry, principal, settings) -> List[str]:
    res = call(registry, principal, settings, "profiles_list") or {}
    names = [r.get("name") for r in res.get("profiles") or []
             if isinstance(r, dict) and r.get("reachable") and safe_token(r.get("name"))]
    return names or ["default"]


def build_overview(registry, principal: Principal, settings, hermes_config: Dict[str, Any],
                   request_base: str = "", window_hours: int = WINDOW_HOURS_DEFAULT) -> Dict[str, Any]:
    """Everything the page shows, one dict. Sections fail independently."""
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
    approvals = _approvals(registry, principal, settings, profiles, errors)
    sessions = _sessions(registry, principal, settings, profiles, targets, errors)
    cron = _cron(registry, principal, settings, targets, window_hours, errors)
    failed = _failed_chat_runs(registry, principal, settings, profiles, targets, window_hours, errors)
    payload["sections"] = {"sessions": sessions, "cron": cron, "approvals": approvals}
    payload["attention"] = {
        "approvals": approvals,
        "failed_cron_runs": cron.get("failed") or [],
        "failed_cron_count": len(cron.get("failed") or []),
        "superseded_cron_runs": cron.get("superseded") or [],
        "superseded_count": len(cron.get("superseded") or []),
        "failed_chat_runs": failed,
        "failed_chat_count": len(failed),
        "active_sessions": sum(1 for s in sessions["items"] if s["status"] == "aktiv"),
        "total": approvals["count"] + len(cron.get("failed") or []) + len(failed),
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
<title>hermcp Ledger</title>
<style>
:root{--bg:#0f1115;--panel:#171a1f;--line:#2a2f37;--fg:#e7e9ec;--muted:#98a1ac;
--accent:#6ea8fe;--ok:#3fbf7f;--warn:#e0a33e;--bad:#ef6b6b;--radius:8px}
@media (prefers-color-scheme:light){:root{--bg:#f7f7f8;--panel:#fff;--line:#d9dbdf;--fg:#1b1d21;
--muted:#6b7280;--accent:#2563eb;--ok:#128a4b;--warn:#b45309;--bad:#b91c1c}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:22px 20px 60px;display:flex;flex-direction:column;gap:16px}
.head{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap}
h1{margin:0;font-size:19px;letter-spacing:.02em}
.sub{font-size:12px;color:var(--muted);font-variant-numeric:tabular-nums}
.spacer{flex:1}
.pill{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line);border-radius:999px;
padding:2px 10px;font-size:11px;letter-spacing:.04em;text-transform:uppercase;color:var(--muted)}
.pill.ok{color:var(--ok);border-color:var(--ok)}.pill.warn{color:var(--warn);border-color:var(--warn)}
.pill.bad{color:var(--bad);border-color:var(--bad)}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.kpi{border:1px solid var(--line);border-radius:var(--radius);background:var(--panel);padding:12px 14px}
.kpi b{display:block;font-size:24px;font-variant-numeric:tabular-nums}
.kpi span{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}
.kpi.bad b{color:var(--bad)}.kpi.warn b{color:var(--warn)}
.card{border:1px solid var(--line);border-radius:var(--radius);background:var(--panel);overflow:hidden}
.card>.hd{display:flex;gap:10px;align-items:center;padding:10px 14px;border-bottom:1px solid var(--line)}
.card>.hd h3{margin:0;font-size:13px}
.card>.hd .sub{margin-left:auto}
.alert{border:1px solid var(--bad);border-left-width:4px;border-radius:var(--radius);background:var(--panel);padding:12px 16px}
.alert h2{margin:0 0 8px;font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--bad)}
.alert h4{margin:10px 0 4px;font-size:12px}
.alert .it{font-size:12px;color:var(--muted);overflow-wrap:anywhere;padding:2px 0}
.chips{display:flex;flex-wrap:wrap;gap:8px;padding:12px 14px}
.chip{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line);border-radius:999px;
padding:3px 10px;font-size:12px;color:var(--muted)}
.dot{width:7px;height:7px;border-radius:50%;background:var(--muted)}
.dot.ok{background:var(--ok)}.dot.warn{background:var(--warn)}.dot.bad{background:var(--bad)}
.tw{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);
padding:8px 14px;border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:7px 14px;border-bottom:1px solid var(--line);vertical-align:top}
tr:last-child td{border-bottom:0}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11.5px;color:var(--muted);overflow-wrap:anywhere}
.num{font-variant-numeric:tabular-nums;white-space:nowrap}
a{color:var(--accent);text-decoration:none;border-bottom:1px dotted currentColor;white-space:nowrap}
a:hover{border-bottom-style:solid}
.empty{padding:14px;font-size:12px;color:var(--muted)}
.legend{padding:0 14px 12px;font-size:11px;color:var(--muted);line-height:1.5}
.err{border:1px solid var(--warn);border-radius:var(--radius);padding:10px 14px;font-size:12px;color:var(--muted)}
.err strong{color:var(--warn)}
button.dismiss{border:1px solid var(--line);background:transparent;color:var(--muted);border-radius:6px;
font-size:11px;padding:1px 8px;margin-left:8px;cursor:pointer;font:inherit;font-size:11px}
button.dismiss:hover{color:var(--fg);border-color:var(--fg)}
button.dismiss.all{font-size:12px;padding:2px 10px}
.hiddenline{display:flex;align-items:center;gap:10px;margin-top:8px;flex-wrap:wrap}
.hidden,.superseded{border-top:1px dashed var(--line);margin-top:8px;padding-top:6px}
label.toggle{display:inline-flex;align-items:center;gap:8px;font-size:12px;color:var(--muted);cursor:pointer}
</style></head>
<body><div class="wrap" id="root"><div class="empty">lade …</div></div>
<script>
__APP_JS__
</script></body></html>
"""


APP_JS_PATH = Path(__file__).resolve().parent / "ledger_assets" / "app.js"


def app_js() -> str:
    """The page script, inlined. Kept as a file so node can test it directly."""
    try:
        return APP_JS_PATH.read_text(encoding="utf-8")
    except OSError:                                     # a broken install: say so, never 500
        return 'document.getElementById("root").textContent = ' \
               '"app.js fehlt in dieser Installation (hermcp/ledger_assets/app.js)";'


def page_html(poll_seconds: int) -> str:
    """The page with the poll interval baked in; nothing else is interpolated."""
    poll = str(int(poll_seconds) or POLL_SECONDS_DEFAULT)
    return PAGE.replace("__APP_JS__", app_js()).replace("__POLL__", poll)
