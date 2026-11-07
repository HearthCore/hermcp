"""Who is calling: a coarse `client` label derived from request headers, and the `client_session` handle a client
chooses for itself. Neither is authentication (the key is); both only attribute work, so they are sanitized and
never trusted for access decisions."""
from __future__ import annotations

import re

LABEL_RE = re.compile(r"[^A-Za-z0-9._-]+")
HANDLE_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
MAX_LABEL = 40


def _label(raw: str) -> str:
    return LABEL_RE.sub("", raw.strip())[:MAX_LABEL]


def derive_client(headers) -> str:
    """`x-anthropic-client` (Cowork, ClaudeAI, ClaudeCode, ...) wins; else the first product token of the
    User-Agent; else "unknown". Case is kept so the label reads like the client wrote it."""
    h = {str(k).lower(): str(v) for k, v in dict(headers).items()}
    named = _label(h.get("x-anthropic-client", ""))
    if named:
        return named
    ua = _label(re.split(r"[/\s]", h.get("user-agent", "").strip(), maxsplit=1)[0])
    return ua or "unknown"


def clean_handle(value) -> str | None:
    """A client-chosen session handle, or None when absent or not in the allowed shape."""
    return value if isinstance(value, str) and HANDLE_RE.match(value) else None
