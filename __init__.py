"""hermcp — H.E.R.MCP: remote MCP server for the Hermes control surface.

Hermes loads this package as a plugin (``register``) that only adds the ``hermes hermcp`` CLI
command. The server itself runs as its own process: ``python -m hermcp serve``.
"""
__version__ = "0.1.0"


def _start_bridge(**_kw) -> None:
    """Agent half: publish this gateway's pending approvals on a 0600 AF_UNIX socket (see bridge.py)."""
    try:
        from . import bridge
        from hermes_constants import get_hermes_home  # type: ignore[import-not-found]
        bridge.start(get_hermes_home())
    except Exception:  # noqa: BLE001 - a plugin must never break the gateway
        import logging
        logging.getLogger("hermcp").exception("approvals bridge not started")


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
    from .cli import dispatch, setup_parser
    ctx.register_cli_command(
        name="hermcp",
        help="H.E.R.MCP remote MCP server: keys, serve, doctor",
        setup_fn=setup_parser,
        handler_fn=dispatch,
        description="Manage and run the hermcp remote MCP server.",
    )
