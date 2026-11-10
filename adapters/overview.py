"""Compact, evidence-based views of profiles, cron, kanban config and gateway platforms.

Everything here is read-only and reports its origin through `meta` (raw config.yaml files, jobs.json,
gateway_state.json, fresh-process probes). It reads the profile's own config.yaml (what was written), not
a merged-with-defaults object, and says so in `config_source`.
"""
from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .. import _yaml as yaml

from .. import meta
from ..settings import hermes_home
from .common import list_profile_names, profile_home, run_probe
from .served import served_info

SKILL_PLATFORMS = ["cli", "telegram", "api_server", "cron"]


def raw_config(profile: str) -> dict[str, Any]:
    p = profile_home(profile) / "config.yaml"
    try:
        return yaml.safe_load(meta.read_text(p)) or {}
    except (OSError, yaml.YAMLError):
        return {}


def _model(cfg: dict) -> tuple[str | None, str | None]:
    m = cfg.get("model")
    if isinstance(m, dict):
        return m.get("default") or m.get("model"), m.get("provider")
    return (m if isinstance(m, str) else None), None


def fallback_chain(cfg: dict) -> list[dict[str, Any]]:
    chain = cfg.get("fallback_providers")
    if not isinstance(chain, list):
        legacy = cfg.get("fallback_model")
        chain = [legacy] if isinstance(legacy, dict) else []
    return [{"provider": e.get("provider"), "model": e.get("model")} for e in chain if isinstance(e, dict)]


def mcp_sets(cfg: dict) -> dict[str, list[str]]:
    servers = cfg.get("mcp_servers") if isinstance(cfg.get("mcp_servers"), dict) else {}
    on = sorted(k for k, v in servers.items() if not isinstance(v, dict) or v.get("enabled", True))
    off = sorted(k for k in servers if k not in on)
    return {"enabled": on, "disabled": off}


def adapters_of(profile: str, gw_platforms: dict[str, Any], stale: dict[str, Any]) -> dict[str, Any]:
    """Live adapters of one profile from gateway_state.json: unprefixed keys belong to default, `p:name` to p."""
    mine, stale_mine = [], []
    for key, v in gw_platforms.items():
        owner, _, plat = key.rpartition(":")
        if (owner or "default") == profile:
            mine.append({"platform": plat or key, "state": v.get("state"), "updated_at": v.get("updated_at"),
                         "writer_pid": v.get("writer_pid")})
    for key in stale:
        owner, _, plat = key.rpartition(":")
        if (owner or "default") == profile:
            stale_mine.append(plat or key)
    return {"connected": sum(1 for a in mine if a["state"] == "connected"), "live": mine, "stale_entries": stale_mine}


def skill_counts(profile: str) -> dict[str, Any]:
    try:
        d = run_probe(profile, "skills", {"platforms": SKILL_PLATFORMS}, timeout=60)
    except Exception as exc:  # noqa: BLE001 - one broken profile must not hide the others
        return {"error": str(exc)[:200]}
    eff = d["effective_enabled_count"]
    return {"total": d["total"], "global_enabled": eff["global"], "effective_by_platform": eff,
            "platform_disabled_counts": d["platform_disabled_counts"]}


def profile_details(names: list[str], gw: dict[str, Any], *, with_skills: bool = True) -> dict[str, dict[str, Any]]:
    """name -> compact details (model, fallbacks, toolsets, mcp, adapters, served/parked, skills)."""
    info = served_info()
    served = set(info["served"]) if info["status"] == "known" else None
    parked = gw.get("parked_profiles") if isinstance(gw.get("parked_profiles"), dict) else {}
    skills: dict[str, Any] = {}
    if with_skills and names:
        with ThreadPoolExecutor(max_workers=min(4, len(names))) as ex:
            futs = [(n, meta.submit(ex, skill_counts, n)) for n in names]
            for n, f in futs:
                skills[n] = f.result()
    out: dict[str, dict[str, Any]] = {}
    for n in names:
        cfg = raw_config(n)
        model, provider = _model(cfg)
        pt = cfg.get("platform_toolsets") if isinstance(cfg.get("platform_toolsets"), dict) else {}
        out[n] = {
            "model": model, "provider": provider, "fallback_chain": fallback_chain(cfg),
            "toolsets": cfg.get("toolsets"),
            "platform_toolsets": {k: v for k, v in pt.items()},
            "served": None if served is None else n in served, "parked": n in parked,
            "adapters": adapters_of(n, gw.get("platforms") or {}, gw.get("stale_platforms") or {}),
            "mcp": mcp_sets(cfg),
            "skills": skills.get(n) if with_skills else None,
            "config_source": str(profile_home(n) / "config.yaml"),
        }
    return out


# -- cron -----------------------------------------------------------------------------------------------
def cron_summary(profile: str) -> dict[str, Any]:
    p = profile_home(profile) / "cron" / "jobs.json"
    if not p.is_file():
        return {"jobs": 0}
    try:
        data = json.loads(meta.read_text(p))
    except (OSError, ValueError):
        return {"jobs": None, "error": "jobs.json unreadable"}
    jobs = data["jobs"] if isinstance(data, dict) else data
    by_state: dict[str, int] = {}
    errors = []
    for j in jobs:
        st = j.get("state") or ("scheduled" if j.get("enabled") else "disabled")
        by_state[st] = by_state.get(st, 0) + 1
        if j.get("last_status") == "error":
            errors.append({"id": (j.get("id") or "")[:12], "name": (j.get("name") or "")[:60], "state": st,
                           "last_run_at": j.get("last_run_at"), "last_error": (j.get("last_error") or "")[:140] or None})
    return {"jobs": len(jobs), "by_state": by_state, "active": by_state.get("scheduled", 0),
            "paused": by_state.get("paused", 0), "last_status_error": errors}


def kanban_config(profile: str = "default") -> dict[str, Any]:
    k = raw_config(profile).get("kanban")
    return dict(k) if isinstance(k, dict) else {}


def all_profile_names() -> list[str]:
    return list_profile_names()


def gateway_home() -> Path:
    return hermes_home()


def _kanban_counts(db: Path) -> dict[str, int]:
    """Task count per status from one board database (read-only); {"error": -1} if it cannot be read."""
    try:
        meta.note_file(db)
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        try:
            return {st: n for st, n in con.execute("SELECT status, COUNT(*) FROM tasks GROUP BY status")}
        finally:
            con.close()
    except sqlite3.Error:
        return {"error": -1}


def kanban_boards() -> list[dict[str, Any]]:
    """Boards with task counts, read straight from the board databases (read-only; the CLI needs ~3 s).

    The `default` board always exists in Hermes: its database is <home>/kanban.db and its board.json is optional
    metadata (a fresh home has none), so it is listed from the database when no board.json produced it."""
    root = hermes_home() / "kanban" / "boards"
    rows = []
    for slug_dir in sorted(root.iterdir()) if root.is_dir() else []:
        bj = slug_dir / "board.json"
        if slug_dir.name.startswith("_") or not bj.is_file():
            continue
        try:
            b = json.loads(meta.read_text(bj))
        except (OSError, ValueError):
            continue
        db = hermes_home() / "kanban.db" if b.get("slug") == "default" else slug_dir / "kanban.db"
        counts = _kanban_counts(db)
        rows.append({"slug": b.get("slug"), "archived": bool(b.get("archived")), "counts": counts,
                     "total": sum(v for v in counts.values() if v > 0), "board_created_at": b.get("created_at")})
    default_db = hermes_home() / "kanban.db"
    if default_db.is_file() and not any(r["slug"] == "default" for r in rows):
        counts = _kanban_counts(default_db)
        rows.insert(0, {"slug": "default", "archived": False, "counts": counts,
                        "total": sum(v for v in counts.values() if v > 0), "board_created_at": None})
    return rows
