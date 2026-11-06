"""hermcp — H.E.R.MCP: remote MCP server for the Hermes control surface.

Hermes loads this package as a plugin (``register``) that only adds the ``hermes hermcp`` CLI
command. The server itself runs as its own process: ``python -m hermcp serve``.
"""
__version__ = "0.1.0"

from pathlib import Path


def _start_bridge(**_kw) -> None:
    """Agent half: publish this gateway's pending approvals on a 0600 AF_UNIX socket (see bridge.py).

    One socket per process, in the process's own home. A multiplex gateway (one process serving every
    profile) therefore publishes its single socket in the default home, and that socket holds every
    profile's approvals — the standalone hermcp server detects that case and filters by the session
    key's profile namespace (see bridge.resolve / bridge.profile_of_session_key). Nothing to do here.
    """
    try:
        from . import bridge
        from hermes_constants import get_hermes_home  # type: ignore[import-not-found]
        bridge.start(get_hermes_home())
    except Exception:  # noqa: BLE001 - a plugin must never break the gateway
        import logging
        logging.getLogger("hermcp").exception("approvals bridge not started")


_watcher_lock = None
_watcher = None
WATCH_INTERVAL_S = 1.0
WATCH_TIMEOUT_S = 600.0


def _is_gateway_process(home) -> bool:
    """True once THIS process is the running gateway: gateway_state.json names our pid and says running.

    A CLI/TUI process that merely loads the plugin never matches, so it never opens the socket."""
    import json
    import os
    try:
        st = json.loads((Path(home) / "gateway_state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(st, dict) and st.get("pid") == os.getpid() and st.get("gateway_state") == "running"


def _watch_for_gateway(home, interval: float = WATCH_INTERVAL_S, timeout: float = WATCH_TIMEOUT_S) -> bool:
    """Wait (bounded) until this process is the running gateway, then start the bridge. Returns True if started.

    Plugins load before the gateway is up (``gateway.run`` is not imported yet) and api_server runs never fire
    ``pre_gateway_dispatch``, so without this the socket only appeared after a Telegram/mail message."""
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _is_gateway_process(home):
            _start_bridge()      # idempotent (bridge.start), never raises
            return True
        time.sleep(interval)
    return False


def _ensure_gateway_watcher() -> None:
    """Start the one-shot watcher thread (idempotent, daemon, fail-open)."""
    global _watcher, _watcher_lock
    import threading
    if _watcher_lock is None:
        _watcher_lock = threading.Lock()
    with _watcher_lock:
        if _watcher is not None and _watcher.is_alive():
            return
        try:
            from hermes_constants import get_hermes_home  # type: ignore[import-not-found]
            home = get_hermes_home()
        except Exception:  # noqa: BLE001 - not inside Hermes: nothing to watch
            return
        _watcher = threading.Thread(target=lambda: _watch_for_gateway(home), name="hermcp-bridge-watch", daemon=True)
        _watcher.start()


def register(ctx) -> None:
    # Approval hooks: observers only (Hermes ignores their return values), they mirror pending/resolved
    # approvals into the inbox so an agent notices from any session. Answering stays with approvals_pending.
    try:
        from . import approvals_hook
        approvals_hook.register_hooks(ctx)
    except Exception:  # noqa: BLE001 - a plugin must never break the agent
        import logging
        logging.getLogger("hermcp").exception("approval inbox hooks not registered")
    # `pre_gateway_dispatch` fires only inside a gateway process, so the CLI never opens the socket.
    # The first inbound message starts the bridge (idempotent, fail-open: returns None = "allow").
    try:
        ctx.register_hook("pre_gateway_dispatch", lambda **kw: _start_bridge() or None)
    except Exception:  # noqa: BLE001
        pass
    import sys
    if "gateway.run" in sys.modules:   # gateway already up when plugins load: start right away
        _start_bridge()
    else:                              # still booting (or a CLI process): the watcher decides by gateway_state.json
        _ensure_gateway_watcher()
    from .cli import dispatch, setup_parser
    ctx.register_cli_command(
        name="hermcp",
        help="H.E.R.MCP remote MCP server: keys, serve, doctor",
        setup_fn=setup_parser,
        handler_fn=dispatch,
        description="Manage and run the hermcp remote MCP server.",
    )
