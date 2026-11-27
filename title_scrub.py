"""Maintenance: find and replace session titles that carry a plaintext secret.

Why this exists
---------------
A session title is free text: the title generator takes it from the conversation, ``sessions_rename``
takes it from the caller. Since the Ledger no longer renders session titles at all (it labels a
session from structure -- see :func:`hermcp.ledger_page.session_label`), the page is not the leak. The
store still is: whoever reads a profile's ``state.db`` or a tool that shows titles sees whatever was
written there, and a title like "Passwort im Klartext" is not caught by any pattern.

So this is a **cleanup**, not a boundary. It finds titles that *look like* they carry a credential and
replaces them with the same structural label the Ledger uses. The detectors are the ones the code
base already has plus a credential vocabulary; a title they miss is still not shown by the Ledger.

What it does
------------
``hermes hermcp session-titles scrub`` walks the profiles (or ``--profiles a,b``), reads the session
rows of each ``state.db`` **read-only** and reports how many titles match and why -- never the title
text itself. Without ``--apply`` nothing is written (dry run, the default). With ``--apply`` it copies
``state.db`` (plus its ``-wal``/``-shm``) into ``~/.hermes/backups/hermcp-session-titles-<ts>/``
first, then rewrites the matched titles through Hermes' own session store, and reports what it did.
Running it twice is safe: the replacement label matches no detector, so the second run finds nothing.
"""
from __future__ import annotations

import os
import re
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

from .adapters.common import hermes_home, list_profile_names, profile_home
from .ledger_page import session_label
from .redact import redact_text

BACKUP_DIR_PREFIX = "hermcp-session-titles-"
REASONS = ("secret-value", "credential-word", "random-string", "secret-prefix", "masked-value")

# A title that *names* a credential class is the case this maintenance is for ("Passwort im Klartext",
# "Token für die API"). Word-bounded on purpose: "keyboard" must not match, "API-Key" must.
_CREDENTIAL_WORD = re.compile(
    r"(?i)\b(?:passwor[dt]|passwd|passphrase|kennwort|secret|token|api[ _-]?key|apikey|access[ _-]?key|"
    r"private[ _-]?key|client[ _-]?secret|bearer|otp|2fa|tfa|recovery[ _-]?code)\b")
# A literal credential that starts with a recognisable prefix, even without the word around it.
_SECRET_PREFIX = re.compile(
    r"(?:hmcp_[A-Za-z0-9]{4,}|sk-[A-Za-z0-9-]{8,}|ghp_[A-Za-z0-9]{8,}|github_pat_[A-Za-z0-9_]{8,}|"
    r"xox[abps]-[A-Za-z0-9-]{6,}|AKIA[0-9A-Z]{12,}|AIza[0-9A-Za-z_-]{10,}|whsec_[A-Za-z0-9]{8,}|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----)")
# Redaction that already happened ("…ab12" style masking is fine, a half mask is not). This is only
# here so a title that merely *mentions* a mask is not rewritten twice.
_MASKED = re.compile(r"(?i)\b(?:passwor[dt]|token|secret|key)\b\s*[:=]\s*[^\s]{0,3}[*•]{3,}")


def title_reason(title: Any) -> str | None:
    """Why this title looks like it carries a plaintext secret, or None.

    The reasons are ordered from strongest to weakest: a value the redactor would rewrite, a literal
    credential prefix, a masked leftover, a credential word, or a random-looking string.
    """
    if not isinstance(title, str) or not title.strip():
        return None
    if redact_text(title) != title:
        return "secret-value"
    if _SECRET_PREFIX.search(title):
        return "secret-prefix"
    if _MASKED.search(title):
        return "masked-value"
    if _CREDENTIAL_WORD.search(title):
        return "credential-word"
    from .redact import looks_random
    for chunk in title.split():
        if len(chunk) >= 24 and looks_random(chunk):
            return "random-string"
    return None


def _db_path(profile: str) -> Path | None:
    try:
        path = profile_home(profile) / "state.db"
    except Exception:  # noqa: BLE001 - unknown/ignored profile: nothing to scan
        return None
    return path if path.is_file() else None


def scan(profiles: Iterable[str] | None = None) -> list[dict[str, Any]]:
    """Titles that would be replaced, per profile: id, reason, and the replacement -- never the title."""
    out: list[dict[str, Any]] = []
    for profile in (list(profiles) if profiles else list_profile_names()):
        path = _db_path(profile)
        if path is None:
            continue
        try:
            db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2.0)
        except sqlite3.Error as exc:
            out.append({"profile": profile, "error": f"{type(exc).__name__}: {exc}", "items": []})
            continue
        try:
            db.row_factory = sqlite3.Row
            rows = [dict(r) for r in db.execute("SELECT id, title, source FROM sessions")]
        except sqlite3.Error as exc:
            out.append({"profile": profile, "error": f"{type(exc).__name__}: {exc}", "items": []})
            continue
        finally:
            try:
                db.close()
            except Exception:  # noqa: BLE001 - the scan result matters, not the close
                pass
        items = []
        for row in rows:
            reason = title_reason(row.get("title"))
            if reason:
                items.append({"session_id": str(row.get("id")),
                              "reason": reason,
                              "replacement": session_label(profile, row.get("id"), None, row.get("source"))})
        out.append({"profile": profile, "scanned": len(rows), "items": items})
    return out


def backup_root() -> Path:
    return hermes_home() / "backups" / (BACKUP_DIR_PREFIX + time.strftime("%Y%m%d-%H%M%S"))


def backup_db(profile: str, root: Path | None = None) -> Path | None:
    """Copy one profile's ``state.db`` (with its ``-wal``/``-shm``) before it is written."""
    path = _db_path(profile)
    if path is None:
        return None
    root = root or backup_root()
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"{profile or 'default'}-state.db"
    shutil.copyfile(path, target)
    for suffix in ("-wal", "-shm"):
        side = path.with_name(path.name + suffix)
        if side.is_file():
            shutil.copyfile(side, target.with_name(target.name + suffix))
    os.chmod(target, 0o600)
    return target


def apply_plan(plan: list[dict[str, Any]], backup_dir: Path | None = None) -> list[dict[str, Any]]:
    """Rewrite the planned titles through Hermes' own session store. Returns what was done per profile."""
    from .adapters.hsessions import open_db
    from .adapters.common import call_inproc

    done: list[dict[str, Any]] = []
    root = backup_dir
    for entry in plan:
        profile = str(entry.get("profile") or "default")
        items = entry.get("items") or []
        result: dict[str, Any] = {"profile": profile, "replaced": 0, "failed": [], "backup": None}
        if not items:
            done.append(result)
            continue
        result["backup"] = str(backup_db(profile, root) or "")
        with open_db(profile, read_only=False) as db:
            for item in items:
                try:
                    ok = bool(call_inproc(db.set_session_title, item["session_id"], item["replacement"]))
                except Exception as exc:  # noqa: BLE001 - one row must not stop the rest
                    result["failed"].append({"session_id": item["session_id"], "error": type(exc).__name__})
                    continue
                if ok:
                    result["replaced"] += 1
                else:
                    result["failed"].append({"session_id": item["session_id"], "error": "not applied"})
        done.append(result)
    return done


def render(plan: list[dict[str, Any]]) -> str:
    lines = []
    total = 0
    for entry in plan:
        profile = entry.get("profile")
        if entry.get("error"):
            lines.append(f"  {profile:<16} [skip] {entry['error']}")
            continue
        items = entry.get("items") or []
        total += len(items)
        if not items:
            lines.append(f"  {profile:<16} 0 of {entry.get('scanned', 0)} titles need replacing")
            continue
        lines.append(f"  {profile:<16} {len(items)} of {entry.get('scanned', 0)} titles would be replaced")
        for item in items:
            lines.append(f"      {item['session_id']}  ({item['reason']}) -> {item['replacement']}")
    return "\n".join(lines) + f"\n  total: {total}\n"
