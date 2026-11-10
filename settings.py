"""Settings: process env > .env next to the package > defaults. Paths resolve via HERMES_HOME."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent


class ConfigError(ValueError):
    """A hermcp setting has an invalid value. Readable message, no traceback needed (ValueError for compatibility)."""


SCOPES = ("system", "user")


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
    trust_proxy: bool = False          # DEPRECATED: does not widen anything; use trusted_proxies
    trusted_proxies: list[str] = field(default_factory=list)  # peers whose forwarding headers are believed
    call_timeout: float = 60.0
    auth_fail_limit: int = 10          # failures per window per IP
    auth_fail_window: float = 60.0
    oauth_redirect_uris: list[str] = field(default_factory=list)
    oauth_dcr: bool = False
    inbox_max_events: int = 2000       # hard cap on stored events (oldest acked go first)
    inbox_retention_days: float = 14.0  # acked events older than this are dropped
    inbox_rate_per_min: int = 60       # pushes per key per minute
    inbox_join_window_min: float = 30.0  # a consumer's first poll delivers only events this young (0 = none)
    inbox_watcher: bool = True         # run watcher: observes runs started via chat_send
    inbox_watch_interval: float = 2.0  # seconds between polls of the Hermes api_server
    inbox_watch_max_per_key: int = 50  # active watched runs per key
    session_max_runs: int = 8          # open runs per registered client session (0 = no limit)
    inbox_run_max_events: int = 20     # watcher events per run
    inbox_watch_ttl_hours: float = 24.0  # a watch that never saw a terminal state expires silently
    inbox_approvals: bool = True       # approval hooks: mirror pending/resolved approvals into the inbox
    debug_capture: bool = False        # discovery aid: record what MCP clients send (capture.py); off by default
    debug_capture_max: int = 300       # newest records kept in capture.jsonl
    debug_capture_hours: float = 12.0  # recording stops by itself this long after start
    systemd_scope: str = "system"      # "system" | "user": manager of the Hermes gateway unit (gateway_restart)
    self_systemd_scope: str = "system"  # "system" | "user": manager of hermcp.service itself (hermcp_restart)
    ledger: bool = False             # serve the read-only situation page at /ledger; off by default
    ledger_auth: str = "key"         # "key" (default) | "none" (only from private/loopback addresses)
    ledger_base_url: str = ""        # where session links point (desktop app); empty = from Hermes config
    ledger_poll_seconds: int = 15    # page polling interval
    ledger_window_hours: int = 24    # cron ledger / failed-run window
    ledger_cache_ttl: float = 8.0    # server-side cache per payload

    invalid_scopes: list[str] = field(default_factory=list)   # set by load_settings(strict=False) only

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


LEDGER_AUTH_MODES = ("key", "none")


def proxy_errors(s: "Settings") -> list[str]:
    """One readable line per trusted-proxy entry that is not an address or CIDR."""
    from .client_ip import invalid_networks
    bad = invalid_networks(s.trusted_proxies)
    if not bad:
        return []
    return [f"HERMCP_TRUSTED_PROXIES: not an address or CIDR: {', '.join(bad)}"]


def ledger_errors(s: "Settings") -> list[str]:
    """One readable line per invalid Ledger setting (empty list = fine)."""
    if s.ledger_auth not in LEDGER_AUTH_MODES:
        return [f"HERMCP_LEDGER_AUTH must be one of {list(LEDGER_AUTH_MODES)}, got {s.ledger_auth!r}"]
    return []


def scope_errors(s: "Settings") -> list[str]:
    """One readable line per invalid systemd scope value (empty list = fine)."""
    out = []
    for env, val in (("HERMCP_SYSTEMD_SCOPE", s.systemd_scope), ("HERMCP_SELF_SYSTEMD_SCOPE", s.self_systemd_scope)):
        if val not in SCOPES:
            out.append(f"{env} must be 'system' or 'user', got {val!r}")
    return out


DEFAULT_REDIRECTS = [
    "https://claude.ai/api/mcp/auth_callback",
    "https://claude.com/api/mcp/auth_callback",
]


def load_settings(env: dict[str, str] | None = None, strict: bool = True) -> Settings:
    """strict=False (doctor, hooks inside the gateway) never raises for a bad scope: it falls back to `system`
    and `scope_errors(settings)` tells what was wrong."""
    merged = _read_dotenv(PKG_DIR / ".env")
    merged.update(os.environ if env is None else env)
    # Deprecated: the page was called "Lagebild" for one day. Old names still work
    # where the new one is unset; the new name always wins.
    for old_name, new_name in (("HERMCP_LAGEBILD", "HERMCP_LEDGER"),
                               ("HERMCP_LAGEBILD_AUTH", "HERMCP_LEDGER_AUTH"),
                               ("HERMCP_LAGEBILD_BASE_URL", "HERMCP_LEDGER_BASE_URL"),
                               ("HERMCP_LAGEBILD_POLL_SECONDS", "HERMCP_LEDGER_POLL_SECONDS"),
                               ("HERMCP_LAGEBILD_WINDOW_HOURS", "HERMCP_LEDGER_WINDOW_HOURS"),
                               ("HERMCP_LAGEBILD_CACHE_TTL", "HERMCP_LEDGER_CACHE_TTL")):
        if old_name in merged and new_name not in merged:
            merged[new_name] = merged[old_name]
    g = merged.get
    s = Settings(
        host=g("HERMCP_HOST", "127.0.0.1"),
        port=int(g("HERMCP_PORT", "9230")),
        public_url=g("HERMCP_PUBLIC_URL", "").rstrip("/"),
        hermes_api_url=g("HERMES_API_URL", "http://127.0.0.1:8642").rstrip("/"),
        hermes_api_key=g("HERMES_API_KEY", ""),
        hermes_bin=g("HERMCP_HERMES_BIN", ""),
        trust_proxy=g("HERMCP_TRUST_PROXY", "").lower() in ("1", "true", "yes"),
        trusted_proxies=_csv(g("HERMCP_TRUSTED_PROXIES", "")),
        call_timeout=float(g("HERMCP_CALL_TIMEOUT", "60")),
        oauth_dcr=g("HERMCP_OAUTH_DCR", "").lower() in ("1", "true", "yes"),
        inbox_max_events=int(g("HERMCP_INBOX_MAX_EVENTS", "2000")),
        inbox_retention_days=float(g("HERMCP_INBOX_RETENTION_DAYS", "14")),
        inbox_rate_per_min=int(g("HERMCP_INBOX_RATE_PER_MIN", "60")),
        inbox_join_window_min=max(0.0, float(g("HERMCP_INBOX_JOIN_WINDOW_MIN", "30"))),
        inbox_watcher=g("HERMCP_INBOX_WATCHER", "1").lower() not in ("0", "false", "no", "off"),
        inbox_watch_interval=max(0.5, float(g("HERMCP_INBOX_WATCH_INTERVAL", "2"))),
        inbox_watch_max_per_key=int(g("HERMCP_INBOX_WATCH_MAX_PER_KEY", "50")),
        session_max_runs=int(g("HERMCP_SESSION_MAX_RUNS", "8")),
        inbox_run_max_events=int(g("HERMCP_INBOX_RUN_MAX_EVENTS", "20")),
        inbox_watch_ttl_hours=float(g("HERMCP_INBOX_WATCH_TTL_HOURS", "24")),
        inbox_approvals=g("HERMCP_INBOX_APPROVALS", "1").lower() not in ("0", "false", "no", "off"),
        debug_capture=g("HERMCP_DEBUG_CAPTURE", "").lower() in ("1", "true", "yes", "on"),
        debug_capture_max=int(g("HERMCP_DEBUG_CAPTURE_MAX", "300")),
        debug_capture_hours=float(g("HERMCP_DEBUG_CAPTURE_HOURS", "12")),
        systemd_scope=g("HERMCP_SYSTEMD_SCOPE", "system").strip().lower() or "system",
        self_systemd_scope=g("HERMCP_SELF_SYSTEMD_SCOPE", "system").strip().lower() or "system",
        ledger=g("HERMCP_LEDGER", "").lower() in ("1", "true", "yes", "on"),
        ledger_auth=(g("HERMCP_LEDGER_AUTH", "key").strip().lower() or "key"),
        ledger_base_url=g("HERMCP_LEDGER_BASE_URL", "").rstrip("/"),
        ledger_poll_seconds=max(5, int(g("HERMCP_LEDGER_POLL_SECONDS", "15"))),
        ledger_window_hours=max(1, int(g("HERMCP_LEDGER_WINDOW_HOURS", "24"))),
        ledger_cache_ttl=max(0.0, float(g("HERMCP_LEDGER_CACHE_TTL", "8"))),
    )
    bad = scope_errors(s) + ledger_errors(s) + proxy_errors(s)
    if bad:
        if strict:
            raise ConfigError("; ".join(bad))
        s.invalid_scopes = bad
        for attr in ("systemd_scope", "self_systemd_scope"):
            if getattr(s, attr) not in SCOPES:
                setattr(s, attr, "system")
        if s.ledger_auth not in LEDGER_AUTH_MODES:
            s.ledger_auth = "key"
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
