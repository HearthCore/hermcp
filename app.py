"""Wiring: settings + key store + audit + registry -> ASGI app."""
from __future__ import annotations

from .audit import AuditLog
from .keys import KeyStore
from .registry import install_meta_tools
from .server import HermcpApp
from .settings import Settings, load_settings


def build_registry():
    from . import tools
    reg = tools.load_all()
    if "hermes_call" not in reg.tools:
        install_meta_tools(reg)
    return reg


def create_app(settings: Settings | None = None) -> HermcpApp:
    s = settings or load_settings()
    audit = AuditLog(s.data / "audit.jsonl")
    keystore = KeyStore(s.data / "keys.json")
    reg = build_registry()
    extra_auth, extra_routes = None, None
    try:  # phase 3: OAuth 2.1 provider
        from .oauth import build_oauth
        extra_auth, extra_routes = build_oauth(s, keystore)
    except ModuleNotFoundError as exc:
        if exc.name != f"{__package__}.oauth":
            raise
    return HermcpApp(s, keystore, reg, audit, extra_auth=extra_auth, extra_routes=extra_routes)
