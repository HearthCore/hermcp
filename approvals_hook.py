"""Approval hooks -> inbox (agent half): ONE row per approval, whose state changes.

Hermes fires ``pre_approval_request`` / ``post_approval_response`` on every interactive approval prompt —
in the gateway process (Telegram / gateway-queue approvals) and in the CLI process (terminal approvals).
``plugins_dispatch`` treats both as pure observers: return values are ignored and exceptions are
swallowed, so this module can never approve or deny anything.

What it writes (since the approval-ledger change):

- ``pre``  -> opens the approval row (kind ``approval``, state ``waiting``)
- ``post`` -> settles THAT row (state ``approved`` / ``denied`` / ``expired``) with the decision, the
  surface and the time. No second event, no pair.

The two raw hook signals are still recorded, as rows of the decision protocol
(``approval_decisions``): ``opened`` for the pre signal and the outcome for the post signal. So nothing
about the pair is lost, but the inbox shows one entry per approval.

Correlation (verified against the Hermes sources, not assumed): the hook payload carries
``command``, ``description``, ``pattern_key``/``pattern_keys``, ``session_key``, ``surface`` and
(optionally) ``turn_id``/``tool_call_id``/``session_id`` — but **no ``request_id``**. Hermes' request id
is created in ``tools/approval_gateway_wait.py`` (``_ApprovalEntry.__init__``, ``uuid4().hex``) and only
leaves the gateway through the run status and the approvals bridge; the plugin-transport branch of
``tools/approval_prompt.py`` does pass a ``request_id``, but that is a different prompt path. So the
only stable handle both signals share is a hash of the prompt's own identity
(``session_key`` + ``pattern_key`` + ``command`` + ``description``). ``approval_key`` is exactly that
hash plus a sequence number, so two identical prompts in one session still get two rows. ``approval_id``
is filled only when a payload really carries a ``request_id`` — it is never invented.

User and chat context: Hermes does not hand a user id or a chat/group id to these hooks today. Both
fields (``approval_user_id``, ``approval_chat``) exist for a future OIDC / user / group model and are
filled only from a payload key that is actually present (``user_id``, ``chat_id``/``group_id``). No
names, no lookups, no guessing.

Answering stays where it always was: ``approvals_pending`` reads the live request out of the gateway
process and ``approvals_answer`` takes its ``request_id``. The inbox row is a pointer, never an
instruction.

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
DEDUPE_TTL = 300.0                      # seconds: the same prompt re-fired is not a new approval
_seen: dict[str, tuple[str, float]] = {}   # identity hash -> (approval_key, monotonic time opened)
_seq: dict[str, int] = {}                  # identity hash -> how often it was opened (distinct rows)
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


def approval_key(payload: dict[str, Any]) -> str:
    """The stable handle of THIS prompt: identity hash plus a per-identity sequence number.

    The sequence keeps two identical prompts in one session apart. It is allocated once, when the pre
    signal opens the row, and reused by the post signal of the same prompt.
    """
    ident = _identity(payload)
    with _lock:
        hit = _seen.get(ident)
        if hit is not None:
            return hit[0]
        n = _seq.get(ident, 0) + 1
        _seq[ident] = n
        key = ident if n == 1 else f"{ident}-{n}"
        _seen[ident] = (key, time.monotonic())
        return key


def _user_and_chat(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    """Stable user id and chat/group context — only when Hermes actually supplies them.

    Today's hook payload does not. The fields exist so a future OIDC / user / group model can fill them
    without a schema change; nothing is derived from a name or looked up.
    """
    def pick(*names):
        for n in names:
            v = payload.get(n)
            if isinstance(v, (str, int)) and str(v).strip():
                return str(v).strip()[:120]
        return None
    return pick("user_id", "user"), pick("chat_id", "group_id", "chat")


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
    """Drop expired identities; keeps the dicts bounded on long-lived gateway processes."""
    now = time.monotonic()
    for key, (_akey, seen_at) in list(_seen.items()):
        if now - seen_at > DEDUPE_TTL:
            _seen.pop(key, None)
            _seq.pop(key, None)


def _profile_label(profile: str | None) -> str:
    return f"profile {profile}" if profile else "unknown profile"


def _run_id_from_session_key(session_key: Any) -> str | None:
    """API runs use ``agent:<profile>:api_server:dm:run_<id>`` — the run id is the last segment."""
    if not isinstance(session_key, str):
        return None
    tail = session_key.rsplit(":", 1)[-1]
    return tail if tail.startswith("run_") else None


def handle_pre_approval_request(**payload: Any) -> None:
    """Observer hook: open the approval row. Never raises, never answers."""
    try:
        from .adapters.inbox_store import get_inbox
        from .settings import load_settings
        settings = load_settings(strict=False)
        if not enabled(settings) or payload.get("coalesced"):
            return                       # a follower of a coalesced prompt: the leader already reported it
        with _lock:
            _collect()
        key = approval_key(payload)
        profile = _event_profile(payload)
        surface = str(payload.get("surface") or "unknown")
        session_key = str(payload.get("session_key") or "") or None
        summary = (f"Approval waiting on {surface} ({_profile_label(profile)}). Read the live request with "
                   f"approvals_pending, show the command to the user, then answer with approvals_answer.")
        get_inbox(settings).open_approval(
            approval_key=key, summary=summary, profile=profile, origin="hook", session_key=session_key,
            run_id=_run_id_from_session_key(session_key),
            session_id=str(payload.get("session_id") or "") or None,
            approval_id=str(payload.get("request_id") or "") or None,
            action=str(payload.get("command") or "") or None,
            rationale=str(payload.get("description") or "") or None, surface=surface)
    except Exception:  # noqa: BLE001 - a plugin hook must never disturb the approval prompt
        log.debug("hermcp inbox: approval hook (pre) failed", exc_info=True)


def handle_post_approval_response(**payload: Any) -> None:
    """Observer hook: settle the row the pre signal opened. Never raises, never answers."""
    try:
        from .adapters.inbox_store import get_inbox
        from .settings import load_settings
        settings = load_settings(strict=False)
        if not enabled(settings):
            return
        ident = _identity(payload)
        with _lock:
            hit = _seen.pop(ident, None)
        key = hit[0] if hit else None
        if key is None:
            return                   # never announced (disabled, deduped or a coalesced follower): stay quiet
        choice = str(payload.get("choice") or "unknown")
        surface = str(payload.get("surface") or "unknown")
        user_id, chat = _user_and_chat(payload)
        get_inbox(settings).settle_approval(
            approval_key=key, decision=choice, surface=surface, user_id=user_id, chat=chat,
            actor="hermcp:hook", run_id=_run_id_from_session_key(payload.get("session_key")))
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
