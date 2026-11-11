"""'Ask Claude': a prepared question behind a plain link.

The Ledger stays read-only. This module only *builds a URL* — it never talks to Claude, never
writes, never acknowledges anything. Clicking the button opens Claude with a question that names
the entry's ids and asks Claude to read the details itself through the hermcp tools.

Privacy is the whole point of the prompt design: the question carries ids, kind, state, times,
profile/surface and at most one short masked command *prefix*. Never a message body, never tool
output, never a secret. The URL length is bounded (~1800 chars) because both the web and the
desktop handlers drop long query strings; when the prompt would overflow, the optional detail is
shortened first and only then the tail of the prompt.
"""
from __future__ import annotations

from typing import Any, Dict, Optional
from urllib.parse import quote

from .ledger_page import clean, redacted, safe_token

ASK_URL_MAX = 1800
ASK_PROMPT_MAX = 1200
ASK_COMMAND_CHARS = 120
ALLOWED_SCHEMES = ("https://", "http://", "claude://")
TOOLS_HINT = ("hermes_status, inbox_attention, approval_detail, chat_run_status, sessions_messages")
NO_CONTENT = ("Bitte lies die Details selbst über die hermcp-Tools (" + TOOLS_HINT +
              "), statt sie aus dieser Nachricht abzuleiten.")


# ---------------------------------------------------------------------------
# prompt pieces
# ---------------------------------------------------------------------------

def _age(seconds: Optional[int]) -> str:
    if seconds is None:
        return "unbekannt"
    seconds = max(0, int(seconds))
    if seconds < 90:
        return f"{seconds} Sekunden"
    if seconds < 5400:
        return f"{round(seconds / 60)} Minuten"
    if seconds < 172800:
        return f"{round(seconds / 3600)} Stunden"
    return f"{round(seconds / 86400)} Tagen"


def _line(label: str, value: Any) -> Optional[str]:
    text = clean(value, 160) if not isinstance(value, (int, float)) else str(value)
    return f"{label}: {text}" if text else None


def _compose(head: str, lines, question: str, extra: str = "") -> str:
    parts = [head, "", *[l for l in lines if l], ""]
    if extra:
        parts.append(extra)
        parts.append("")
    parts.append(question)
    parts.append("")
    parts.append(NO_CONTENT)
    return "\n".join(p for p in parts).strip()


def prompt_for(kind: str, facts: Dict[str, Any]) -> str:
    """One short German question for one entry kind. Pure: same facts in, same text out."""
    f = facts or {}
    kind = str(kind or "session")

    if kind in ("approval", "approval_waiting"):
        cmd = clean(redacted(f.get("command")), ASK_COMMAND_CHARS)
        lines = [
            _line("approval_key", safe_token(f.get("approval_key"))),
            _line("Profil", f.get("profile")),
            _line("Oberfläche", f.get("surface")),
            _line("Wartet seit", _age(f.get("age_s"))),
            f"Befehl (gekürzt, maskiert): {cmd}" if cmd else None,
        ]
        return _compose(
            "Ich sehe im hermcp-Ledger eine Freigabe, die auf eine Entscheidung wartet.",
            lines,
            "Bitte ordne das Risiko dieses Befehls ein und empfiehl eine Entscheidung (once, session "
            "oder deny) mit kurzer Begründung. Nicht selbst antworten, nur beraten — ich entscheide "
            "im Ledger.",
        )

    if kind in ("run_failed", "failed"):
        lines = [
            _line("run_id", safe_token(f.get("run_id"))),
            _line("Profil", f.get("profile")),
            _line("Zeitpunkt", f.get("at")),
            _line("Zustand", f.get("state")),
        ]
        return _compose(
            "Ein Lauf in hermcp ist fehlgeschlagen (möglicherweise am Iterationslimit).",
            lines,
            "Bitte kläre die Ursache, ob die Arbeit vollständig ist, was offen geblieben ist und ob "
            "ein Weiterlaufen in derselben Session sinnvoll ist.",
        )

    if kind in ("run_interrupted", "interrupted"):
        lines = [
            _line("run_id", safe_token(f.get("run_id"))),
            _line("Profil", f.get("profile")),
            _line("Zeitpunkt", f.get("at")),
        ]
        return _compose(
            "Ein Lauf in hermcp wurde unterbrochen (Gateway-/Prozess-Neustart), nicht durch einen Fehler.",
            lines,
            "Bitte sag, was dabei unterbrochen wurde und ob der Lauf neu gestartet werden sollte — "
            "und was dabei zu beachten ist.",
        )

    if kind in ("cron", "cron_error"):
        lines = [
            _line("Job", f.get("job_name") or safe_token(f.get("job_id"))),
            _line("Profil", f.get("profile")),
            _line("Letzter Lauf", f.get("at")),
            _line("Ergebnis", f.get("state")),
            _line("run_id", safe_token(f.get("run_id"))),
        ]
        return _compose(
            "Ein Cron-Job in hermcp ist auffällig.",
            lines,
            "Bitte sag, was bei diesem Lauf passiert ist, ob noch etwas offen ist und ob sich über "
            "die letzten Läufe ein Muster zeigt.",
        )

    lines = [
        _line("session_id", safe_token(f.get("session_id"))),
        _line("Profil", f.get("profile")),
        _line("Oberfläche", f.get("surface")),
        _line("letzte Aktivität", f.get("at")),
        _line("Zustand", f.get("state")),
    ]
    return _compose(
        "Diese Session in hermcp interessiert mich.",
        lines,
        "Bitte fasse zusammen, worum es in dieser Session geht, was noch offen ist und welche "
        "nächsten Schritte sinnvoll sind.",
    )


# ---------------------------------------------------------------------------
# URL building
# ---------------------------------------------------------------------------

def _fill(template: str, prompt: str) -> Optional[str]:
    """The template with {prompt} url-encoded, or None for anything unusable."""
    tmpl = (template or "").strip()
    if not tmpl or "{prompt}" not in tmpl:
        return None
    if not tmpl.lower().startswith(ALLOWED_SCHEMES):
        return None
    return tmpl.replace("{prompt}", quote(prompt, safe=""))


def _bounded(template: str, prompt: str) -> tuple:
    """(url, prompt) that fit ASK_URL_MAX: drop the optional extra first, then cut the prompt tail.

    The ids and the question come first in every prompt, so cutting from the end keeps the useful
    part. Never returns an empty question: the template's own length is the hard floor.
    """
    url = _fill(template, prompt)
    if url is None:
        return None, prompt
    if len(url) <= ASK_URL_MAX:
        return url, prompt
    shortened = prompt
    # first: drop the trailing "read the details yourself" hint (recoverable), then cut body text
    for marker in (NO_CONTENT, ""):
        if marker and marker in shortened:
            candidate = shortened.replace("\n\n" + marker, "").strip()
            url = _fill(template, candidate)
            if url and len(url) <= ASK_URL_MAX:
                return url, candidate
            shortened = candidate
    head, _, _tail = prompt.partition("\n\n")
    while len(shortened) > 80:
        shortened = shortened[:-80].rstrip()
        url = _fill(template, shortened + " …")
        if url and len(url) <= ASK_URL_MAX:
            return url, shortened + " …"
    return None, prompt


def ask_links(settings, kind: str, facts: Dict[str, Any]) -> Dict[str, Any]:
    """The button payload for one entry: web/desktop URLs or None, plus the prompt for tests/logs.

    `web` is None when the main template is unset or unusable (button hidden); `desktop` is None
    unless HERMCP_LEDGER_ASK_CLAUDE_DESKTOP_URL is set — the desktop URL scheme differs per install
    and is never guessed here.
    """
    prompt = prompt_for(kind, facts)
    if len(prompt) > ASK_PROMPT_MAX:
        prompt = prompt[:ASK_PROMPT_MAX].rstrip() + " …"
    web, web_prompt = _bounded(getattr(settings, "ledger_ask_claude_url", ""), prompt)
    desktop, _ = _bounded(getattr(settings, "ledger_ask_claude_desktop_url", ""), prompt)
    return {
        "web": web,
        "desktop": desktop,
        "prompt": web_prompt if web else prompt,
        "kind": str(kind or "session"),
        "label": "Ask Claude",
        "tooltip": "öffnet Claude mit vorbereiteter Frage",
    }
