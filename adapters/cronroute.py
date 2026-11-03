"""Cron model routing: what a job is pinned to, what it actually runs on, and the planned effect of an edit.

Mirrors the scheduler's rules (cron/scheduler.py in Hermes):
  * a job is PINNED when it carries its own provider, model or base_url (any non-empty value);
  * model:    job.model > cron.model > main model (model.default);
  * provider: job.provider > cron.model_provider > main model provider;
  * a pinned job never uses the global fallback_providers chain (only same-provider credential-pool rotation).
Read-only helpers; nothing here writes. base_url is shown without userinfo, query or fragment.
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from ..registry import ToolError

REASONING = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")
BACKUP_PREFIX = "jobs.json.hermcp-"
KEEP_BACKUPS = 20
# fields an edit of the model route must never change (verified after every write)
PROTECTED = ("schedule", "schedule_display", "prompt", "script", "deliver", "failure_deliver", "enabled", "state",
             "skills", "no_agent", "workdir", "name", "repeat", "paused_at", "paused_reason", "enabled_toolsets",
             "context_from", "origin")


def _txt(v: Any) -> str:
    return v.strip() if isinstance(v, str) else ""


def safe_url(u: Any) -> str | None:
    """URL without userinfo, query and fragment (those can carry credentials)."""
    u = _txt(u)
    if not u:
        return None
    try:
        p = urlsplit(u)
        host = p.hostname or ""
        netloc = host + (f":{p.port}" if p.port else "")
        return urlunsplit((p.scheme, netloc, p.path, "", "")) if p.scheme and host else "[invalid url]"
    except ValueError:
        return "[invalid url]"


def is_pinned(job: dict) -> bool:
    return any(_txt(job.get(k)) for k in ("provider", "model", "base_url"))


def read_defaults(profile: str) -> dict[str, Any]:
    """cron.model / cron.model_provider, the main model and the global fallback chain (names only)."""
    from . import hconfig as H
    from .common import call_inproc, hermes_scope
    with hermes_scope(profile):
        c = H.cfg_mod()
        cfg = call_inproc(c.load_config) or {}
        try:
            from hermes_cli.fallback_config import get_fallback_chain
            chain = call_inproc(get_fallback_chain, cfg)
        except ImportError:
            chain = cfg.get("fallback_providers") or []
    cron = cfg.get("cron") if isinstance(cfg.get("cron"), dict) else {}
    m = cfg.get("model")
    main_model = _txt(m if isinstance(m, str) else (m or {}).get("default") or (m or {}).get("model"))
    main_provider = _txt((m or {}).get("provider")) if isinstance(m, dict) else ""
    return {
        "cron_model": _txt(cron.get("model")) or None,
        "cron_model_provider": _txt(cron.get("model_provider")) or None,
        "main_model": main_model or None,
        "main_provider": main_provider or None,
        "fallback_chain": [{"provider": _txt(e.get("provider")) or None, "model": _txt(e.get("model")) or None,
                            "base_url": safe_url(e.get("base_url"))} for e in chain if isinstance(e, dict)],
    }


def route_info(job: dict, d: dict) -> dict[str, Any]:
    """Per-job routing summary. `d` comes from read_defaults()."""
    pinned = is_pinned(job)
    out: dict[str, Any] = {
        "pinned": pinned,
        "provider": _txt(job.get("provider")) or None,
        "model": _txt(job.get("model")) or None,
        "base_url": safe_url(job.get("base_url")),
        "reasoning_effort": _txt(job.get("reasoning_effort")) or None,
    }
    if job.get("no_agent"):
        out["effective"] = {"applies": False, "note": "no_agent script job: no model is used"}
        out["inherits_fallback_chain"] = False
        return out
    if out["model"]:
        model, msrc = out["model"], "job_pin"
    elif d["cron_model"]:
        model, msrc = d["cron_model"], "cron.model"
    else:
        model, msrc = d["main_model"], "main_model"
    if out["provider"]:
        prov, psrc = out["provider"], "job_pin"
    elif d["cron_model_provider"]:
        prov, psrc = d["cron_model_provider"], "cron.model_provider"
    else:
        prov, psrc = d["main_provider"], "main_model"
    out["effective"] = {"applies": True, "model": model, "model_source": msrc, "provider": prov or "auto",
                        "provider_source": psrc, "base_url": out["base_url"],
                        "reasoning_effort": out["reasoning_effort"] or "from config"}
    out["inherits_fallback_chain"] = not pinned
    out["fallback_note"] = ("pinned: never falls back to fallback_providers (only credential-pool rotation of the "
                            "same provider); unpin to inherit the chain" if pinned else
                            f"follows the global fallback chain ({len(d['fallback_chain'])} entries)")
    return out


def snapshot(job: dict) -> dict[str, Any]:
    return {k: job.get(k) for k in PROTECTED}


# -- edit planning --------------------------------------------------------------------------------
def validate_edit(job: dict, *, pin, unpin, model, provider, base_url, reasoning_effort) -> None:
    if pin and unpin:
        raise ToolError("invalid_arguments", "pin and unpin are mutually exclusive")
    if unpin and any(_txt(x) for x in (model, provider, base_url)):
        raise ToolError("invalid_arguments", "unpin releases the whole route; do not combine it with model/provider/base_url")
    if pin and model is not None:
        raise ToolError("invalid_arguments", "pin freezes the CURRENT main model; to pin a specific model pass model (+provider)")
    if reasoning_effort not in (None, "") and reasoning_effort not in REASONING:
        raise ToolError("invalid_arguments", f"reasoning_effort must be one of {list(REASONING)} or '' to clear")
    if base_url:
        sp = urlsplit(base_url)
        if sp.scheme not in ("http", "https") or not sp.hostname:
            raise ToolError("invalid_arguments", "base_url must be an http(s) URL")
        if sp.username or sp.password or sp.query or sp.fragment:
            raise ToolError("invalid_arguments", "base_url must not contain credentials, query or fragment; "
                            "store keys with secret_set")
    if model == "" and (_txt(job.get("provider")) and provider != "" or _txt(job.get("base_url")) and base_url != ""):
        raise ToolError("invalid_arguments", "clearing the model while provider/base_url stay set leaves a half-pinned "
                        "job; use unpin, or clear them too ('')")
    if _txt(provider) and model is None and not _txt(job.get("model")):
        raise ToolError("invalid_arguments", "provider needs a model (pass model too)")
    if not any(x is not None for x in (model, provider, base_url, reasoning_effort)) and not (pin or unpin):
        raise ToolError("invalid_arguments", "nothing to change")


def predict(job: dict, *, pin, unpin, model, provider, base_url, reasoning_effort, main_model, main_provider) -> dict:
    """Job fields as they will be after the edit (only the route fields)."""
    new = {k: job.get(k) for k in ("provider", "model", "base_url", "reasoning_effort")}
    if unpin:
        new["provider"] = new["model"] = new["base_url"] = None      # base_url too: it alone keeps a job pinned
    if pin and not _txt(job.get("model")):
        new["provider"], new["model"] = main_provider, main_model
    for k, v in (("model", model), ("provider", provider), ("base_url", base_url), ("reasoning_effort", reasoning_effort)):
        if v is not None:
            new[k] = v or None
    return new


def cli_argv(job_id: str, *, pin, unpin, model, provider, reasoning_effort) -> list[str]:
    argv = ["cron", "edit", job_id]
    if pin:
        argv.append("--pin")
    if unpin:
        argv.append("--unpin")
    for flag, v in (("--model", model), ("--provider", provider), ("--reasoning-effort", reasoning_effort)):
        if v is not None:
            argv += [flag, v]
    return argv


def backup_jobs(jobs_file: Path) -> str | None:
    """Timestamped copy of jobs.json next to it (0600, newest KEEP_BACKUPS kept)."""
    if not jobs_file.exists():
        return None
    stem = f"{BACKUP_PREFIX}{time.strftime('%Y%m%d-%H%M%S')}-{int(time.time() * 1000) % 1000:03d}"
    dest, n = jobs_file.with_name(stem), 1
    while dest.exists():                                   # never overwrite an earlier backup
        dest, n = jobs_file.with_name(f"{stem}.{n}"), n + 1
    shutil.copy2(jobs_file, dest)
    dest.chmod(0o600)
    for old in sorted(jobs_file.parent.glob(BACKUP_PREFIX + "*"), key=lambda p: p.stat().st_mtime_ns)[:-KEEP_BACKUPS]:
        old.unlink(missing_ok=True)
    return dest.name
