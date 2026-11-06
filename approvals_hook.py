"""Approval hooks -> inbox (agent half): make every approval request VISIBLE in the hermcp inbox.

Hermes fires ``pre_approval_request`` / ``post_approval_response`` on every interactive approval prompt —
in the gateway process (Telegram / gateway-queue approvals) and in the CLI process (terminal approvals).
``plugins_dispatch`` treats both as pure observers: return values are ignored and exceptions are
swallowed, so this module can never approve or deny anything. It only writes two events:

- ``approval_pending``   -> a request is waiting for a human (kind, session_key, action=command, rationale=description)
- ``approval_resolved``  -> the prompt settled with once / session / always / deny / timeout / cancelled

That is the useful half of the Telegram/CLI problem: the agent sees *that* something waits and *where*,
from any session and any platform. ANSWERING stays where it always was — ``approvals_pending`` reads the
live request out of the gateway process and ``approvals_answer`` takes its ``request_id``. A hook event
deliberately never stores a request_id, so nothing in the inbox can become an approval instruction.

Payload notes: Hermes redacts ``command``/``description`` before the hook fires (force=True); this module
redacts again (``clean_text``) and single-lines everything. Disable with ``HERMCP_INBOX_APPROVALS=0``.

Profile attribution: the event's ``profile`` decides which profile-restricted key sees it. In a
multiplex gateway (one process, all profiles, launched from the default home) the home says
``default`` for every event, so the profile is derived from the session key's namespace instead
(``_event_profile``) — otherwise every profile's approvals would land in the default profile's events.
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from typing import Any

log = logging.getLogger("hermcp.inbox")

PENDING, RESOLVED = "approval_pending", "approval_resolved"
DEDUPE_TTL = 300.0                      # seconds: the same prompt re-fired is not a new event
_seen: dict[str, float] = {}            # identity hash -> monotonic time of the pending event
_lock = threading.Lock()


def enabled(settings=None) -> bool:
    try:
        from .settings import load_settings
        return bool((settings or load_settings(strict=False)).inbox_approvals)
    except Exception:
        return False


def _identity(payload: dict[str, Any]) -> str:
    """Correlate pre/post: same session_key + pattern_key + command + description => same prompt."""
    parts = [str(payload.get(k) or "") for k in ("session_key", "pattern_key", "command", "description")]
    return hashlib.sha256("\x1f".join(parts).encode("utf-8", "replace")).hexdigest()[:16]


def _profile() -> str | None:
    try:
        from hermes_constants import get_hermes_home, profile_name_for_home  # type: ignore[import-not-found]
        name = profile_name_for_home(get_hermes_home())
        if name:
            return name
    except Exception:
        pass
    import os
    return os.environ.get("HERMES_PROFILE") or None


def _event_profile(payload: dict[str, Any]) -> str | None:
    """Which profile an approval event belongs to.

    Separate gateways: one process per profile home, so the home (``_profile()``) is authoritative
    and the session key of such a gateway is namespaced ``agent:main:…`` — it must NOT be read as
    "default" here.

    Multiplex gateway: ONE process launched from the DEFAULT home serves every profile, so the home
    reports ``default`` for all of them (that is the original bug: every approval landed in the
    default profile's events). The session key does carry the profile namespace
    (``agent:<profile>:…``, see ``gateway/session.py::_session_key_namespace``), so when the home is
    the default home, derive the profile from the session key. Falling back to the home keeps the
    fail-closed behaviour: an unattributable key stays NULL rather than being guessed.
    """
    home = _profile()
    if home and home != "default":
        return home
    try:
        from . import bridge
        return bridge.profile_of_session_key(payload.get("session_key")) or home
    except Exception:  # noqa: BLE001 - a hook must never raise
        return home


def _collect(settings=None) -> None:
    """Drop expired identities; keeps the dict bounded on long-lived gateway processes."""
    now = time.monotonic()
    for key, seen_at in list(_seen.items()):
        if now - seen_at > DEDUPE_TTL:
            _seen.pop(key, None)


def _profile_label(profile: str | None) -> str:
    return f"profile {profile}" if profile else "unknown profile"


def handle_pre_approval_request(**payload: Any) -> None:
    """Observer hook: record that an approval is waiting. Never raises, never answers."""
    try:
        from .adapters.inbox_store import get_inbox
        from .settings import load_settings
        settings = load_settings(strict=False)
        if not enabled(settings) or payload.get("coalesced"):
            return                       # a follower of a coalesced prompt: the leader already reported it
        key, profile = _identity(payload), _event_profile(payload)
        with _lock:
            _collect()
            if key in _seen:
                return
            _seen[key] = time.monotonic()
        surface = str(payload.get("surface") or "unknown")
        summary = (f"Approval waiting on {surface} ({_profile_label(profile)}). Read the live request with "
                   f"approvals_pending, show the command to the user, then answer with approvals_answer.")
        get_inbox(settings).push_hook(
            kind=PENDING, summary=summary, profile=profile,
            session_key=str(payload.get("session_key") or "") or None,
            action=str(payload.get("command") or "") or None,
            rationale=str(payload.get("description") or "") or None,
            severity="warning", dedupe_key=f"approval:{key}:pending")
    except Exception:  # noqa: BLE001 - a plugin hook must never disturb the approval prompt
        log.debug("hermcp inbox: approval hook (pre) failed", exc_info=True)


def handle_post_approval_response(**payload: Any) -> None:
    """Observer hook: record how the prompt settled. Only for prompts announced here."""
    try:
        from .adapters.inbox_store import get_inbox
        from .settings import load_settings
        settings = load_settings(strict=False)
        if not enabled(settings):
            return
        key, profile = _identity(payload), _event_profile(payload)
        with _lock:
            if _seen.pop(key, None) is None:
                return                   # never announced (disabled, deduped or a coalesced follower): stay quiet
        choice = str(payload.get("choice") or "unknown")
        surface = str(payload.get("surface") or "unknown")
        summary = f"Approval settled on {surface} ({_profile_label(profile)}): {choice}."
        get_inbox(settings).push_hook(
            kind=RESOLVED, summary=summary, profile=profile,
            session_key=str(payload.get("session_key") or "") or None,
            dedupe_key=f"approval:{key}:resolved")
    except Exception:  # noqa: BLE001
        log.debug("hermcp inbox: approval hook (post) failed", exc_info=True)


def register_hooks(ctx) -> bool:
    """Attach both observer hooks to the plugin context. Returns True when registered."""
    try:
        ctx.register_hook("pre_approval_request", handle_pre_approval_request)
        ctx.register_hook("post_approval_response", handle_post_approval_response)
        return True
    except Exception:  # noqa: BLE001
        log.exception("hermcp: approval hooks not registered")
        return False
