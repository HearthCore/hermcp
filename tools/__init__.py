"""Toolset modules. Importing this package fills the shared registry `reg`."""
from ..registry import Registry

reg = Registry()


def load_all() -> Registry:
    import importlib
    for mod in ("status", "sessions", "models", "config", "tools_cfg", "plugins_ts", "mcp_ts", "skills",
                "profiles", "kanban", "cron", "chat", "approvals", "providers", "memory", "ops", "dashboard", "inbox", "session_cards", "admin", "orch", "files"):
        try:
            importlib.import_module(f"{__name__}.{mod}")
        except ModuleNotFoundError as exc:
            if exc.name != f"{__name__}.{mod}":
                raise
    return reg
