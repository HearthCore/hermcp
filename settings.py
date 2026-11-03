"""Settings: process env > .env next to the package > defaults. Paths resolve via HERMES_HOME."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent


def hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes")).expanduser()


def data_dir() -> Path:
    d = hermes_home() / "plugin-data" / "hermcp"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _read_dotenv(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[k.strip()] = v
    return out


def _csv(v: str) -> list[str]:
    return [p.strip() for p in v.split(",") if p.strip()]


@dataclass
class Settings:
    host: str = "127.0.0.1"
    port: int = 9230
    public_url: str = ""
    allowed_hosts: list[str] = field(default_factory=list)
    hermes_api_url: str = "http://127.0.0.1:8642"
    hermes_api_key: str = ""
    hermes_bin: str = ""
    trust_proxy: bool = False
    call_timeout: float = 60.0
    auth_fail_limit: int = 10          # failures per window per IP
    auth_fail_window: float = 60.0
    oauth_redirect_uris: list[str] = field(default_factory=list)
    oauth_dcr: bool = False
    inbox_max_events: int = 2000       # hard cap on stored events (oldest acked go first)
    inbox_retention_days: float = 14.0  # acked events older than this are dropped
    inbox_rate_per_min: int = 60       # pushes per key per minute
    inbox_watcher: bool = True         # run watcher: observes runs started via chat_send
    inbox_watch_interval: float = 2.0  # seconds between polls of the Hermes api_server
    inbox_watch_max_per_key: int = 50  # active watched runs per key
    inbox_run_max_events: int = 20     # watcher events per run
    inbox_watch_ttl_hours: float = 24.0  # a watch that never saw a terminal state expires silently
    inbox_approvals: bool = True       # approval hooks: mirror pending/resolved approvals into the inbox

    @property
    def public_origin(self) -> str:
        """Scheme+host of `public_url`, without the endpoint path.

        OAuth metadata documents live at the ORIGIN (`/.well-known/...`), never under the
        MCP endpoint path. `public_url` points at the endpoint (e.g. `https://host/mcp`),
        so every well-known URL must be built from this property, not from `public_url`.
        """
        if not self.public_url:
            return ""
        from urllib.parse import urlparse
        u = urlparse(self.public_url)
        if not u.scheme or not u.netloc:
            return ""
        return f"{u.scheme}://{u.netloc}"

    @property
    def data(self) -> Path:
        return data_dir()


DEFAULT_REDIRECTS = [
    "https://claude.ai/api/mcp/auth_callback",
    "https://claude.com/api/mcp/auth_callback",
]


def load_settings(env: dict[str, str] | None = None) -> Settings:
    merged = _read_dotenv(PKG_DIR / ".env")
    merged.update(os.environ if env is None else env)
    g = merged.get
    s = Settings(
        host=g("HERMCP_HOST", "127.0.0.1"),
        port=int(g("HERMCP_PORT", "9230")),
        public_url=g("HERMCP_PUBLIC_URL", "").rstrip("/"),
        hermes_api_url=g("HERMES_API_URL", "http://127.0.0.1:8642").rstrip("/"),
        hermes_api_key=g("HERMES_API_KEY", ""),
        hermes_bin=g("HERMCP_HERMES_BIN", ""),
        trust_proxy=g("HERMCP_TRUST_PROXY", "").lower() in ("1", "true", "yes"),
        call_timeout=float(g("HERMCP_CALL_TIMEOUT", "60")),
        oauth_dcr=g("HERMCP_OAUTH_DCR", "").lower() in ("1", "true", "yes"),
        inbox_max_events=int(g("HERMCP_INBOX_MAX_EVENTS", "2000")),
        inbox_retention_days=float(g("HERMCP_INBOX_RETENTION_DAYS", "14")),
        inbox_rate_per_min=int(g("HERMCP_INBOX_RATE_PER_MIN", "60")),
        inbox_watcher=g("HERMCP_INBOX_WATCHER", "1").lower() not in ("0", "false", "no", "off"),
        inbox_watch_interval=max(0.5, float(g("HERMCP_INBOX_WATCH_INTERVAL", "2"))),
        inbox_watch_max_per_key=int(g("HERMCP_INBOX_WATCH_MAX_PER_KEY", "50")),
        inbox_run_max_events=int(g("HERMCP_INBOX_RUN_MAX_EVENTS", "20")),
        inbox_watch_ttl_hours=float(g("HERMCP_INBOX_WATCH_TTL_HOURS", "24")),
        inbox_approvals=g("HERMCP_INBOX_APPROVALS", "1").lower() not in ("0", "false", "no", "off"),
    )
    hosts = _csv(g("HERMCP_ALLOWED_HOSTS", ""))
    if not hosts:
        hosts = ["127.0.0.1", "localhost", "[::1]"]
        if s.public_url:
            from urllib.parse import urlparse
            h = urlparse(s.public_url).netloc
            if h:
                hosts.append(h)
    s.allowed_hosts = hosts
    s.oauth_redirect_uris = _csv(g("HERMCP_OAUTH_REDIRECT_URIS", "")) or list(DEFAULT_REDIRECTS)
    return s
