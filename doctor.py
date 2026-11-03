"""`hermes hermcp doctor`: installation checks. Read-only; exit 1 when a hard check fails."""
from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

from .keys import KeyStore
from .settings import hermes_home, load_settings

LOOPBACK = ("127.0.0.1", "localhost", "::1")


def _mode_ok(path: Path) -> bool:
    return path.stat().st_mode & 0o077 == 0


def run() -> int:
    s = load_settings()
    problems = 0

    def row(ok: bool, label: str, detail: str = "") -> None:
        nonlocal problems
        problems += 0 if ok else 1
        print(f"[{'ok' if ok else 'FAIL'}] {label}{(': ' + detail) if detail else ''}")

    def warn(label: str, detail: str = "") -> None:
        print(f"[warn] {label}{(': ' + detail) if detail else ''}")

    row(sys.version_info >= (3, 11), "python", sys.version.split()[0])
    pp = os.environ.get("PYTHONPATH", "")
    if pp and "site-packages" in pp:
        warn("PYTHONPATH points at a site-packages dir", f"{pp[:80]} -- it can shadow the Hermes venv (mcp import)")
    for mod in ("starlette", "uvicorn", "yaml", "mcp.server.auth.provider"):
        try:
            __import__(mod)
            row(True, f"import {mod}")
        except Exception as exc:  # noqa: BLE001 - a broken native ext raises more than ImportError
            row(False, f"import {mod}", f"{type(exc).__name__}: {exc}")
    row(hermes_home().is_dir(), "HERMES_HOME", str(hermes_home()))
    try:
        from .adapters.common import ensure_hermes_importable, hermes_bin, list_profile_names, profile_home
        ensure_hermes_importable()
        import hermes_cli.config  # noqa: F401
        row(True, "hermes_cli importable")
        row(True, "hermes binary", hermes_bin())
    except Exception as exc:  # noqa: BLE001
        row(False, "hermes internals", f"{type(exc).__name__}: {exc}")
        list_profile_names = lambda: ["default"]  # noqa: E731
        profile_home = lambda p: hermes_home()    # noqa: E731

    # -- keys / data files ---------------------------------------------------------------
    ks = KeyStore(s.data / "keys.json")
    keys = ks.list()
    row(True, "key store", f"{len(keys)} key(s) at {ks.path}")
    if not keys:
        warn("no keys yet", "python -m hermcp keys create --name admin --access admin")
    for name in ("keys.json", "audit.jsonl", "oauth-client.json", "oauth-tokens.json"):
        p = s.data / name
        if p.exists():
            row(_mode_ok(p), f"{name} permissions", oct(p.stat().st_mode & 0o777))

    # -- network / public exposure -------------------------------------------------------
    row(bool(s.public_url), "HERMCP_PUBLIC_URL", s.public_url or "unset (needed for OAuth/Claude.ai)")
    if s.public_url:
        from urllib.parse import urlparse
        host = urlparse(s.public_url).netloc
        row(host in s.allowed_hosts or "*" in s.allowed_hosts, "public host in HERMCP_ALLOWED_HOSTS", host)
        if not s.public_url.startswith("https://"):
            warn("HERMCP_PUBLIC_URL is not https", "Claude.ai needs TLS at the proxy")
    if s.host not in LOOPBACK:
        warn(f"bound to {s.host}", "expose only behind the TLS proxy; loopback (127.0.0.1) is the safe default")
    if "*" in s.allowed_hosts:
        warn("HERMCP_ALLOWED_HOSTS contains *", "disables the Host (DNS-rebinding) check")
    if s.public_url and not s.trust_proxy:
        warn("HERMCP_TRUST_PROXY is off", "the failed-auth limiter will see only the proxy's IP")
    try:
        with socket.create_connection((s.host if s.host != "0.0.0.0" else "127.0.0.1", s.port), timeout=1):
            print(f"[info] something is listening on {s.host}:{s.port}")
    except OSError:
        print(f"[info] nothing listening on {s.host}:{s.port}")

    # -- OAuth ---------------------------------------------------------------------------
    from . import oauth
    client = oauth.load_client(s.data)
    if client:
        row(True, "OAuth client", client["client_id"])
        if not s.public_origin:
            warn("OAuth client exists but HERMCP_PUBLIC_URL is unset", "endpoints stay disabled")
    elif s.public_url:
        warn("no OAuth client", "Claude.ai connectors need: python -m hermcp oauth init")
    else:
        print("[info] OAuth not configured (bearer keys only)")

    # -- approvals bridge (agent half) per profile -----------------------------------------
    try:
        import yaml
        cfg = yaml.safe_load((hermes_home() / "config.yaml").read_text(encoding="utf-8")) or {}
        enabled = ((cfg.get("plugins") or {}).get("enabled")) or []
        if "hermcp" in enabled:
            row(True, "plugins.enabled contains hermcp")
        else:
            warn("hermcp not in plugins.enabled", "agent half (cross-session approvals) will not load")
    except Exception as exc:  # noqa: BLE001
        warn("could not read plugins.enabled", f"{type(exc).__name__}")
    from . import bridge
    for prof in list_profile_names():
        try:
            path = bridge.socket_path(profile_home(prof))
        except Exception:  # noqa: BLE001
            continue
        if not path.exists():
            print(f"[info] approvals bridge [{prof}]: not running (needs plugins.enabled + gateway restart)")
            continue
        try:
            r = bridge.call(path, {"op": "ping"}, timeout=1.0)
            row(bool(r.get("ok")), f"approvals bridge [{prof}]", f"gateway pid {r.get('pid')}")
        except OSError as exc:
            warn(f"approvals bridge [{prof}] stale socket", f"{path} ({exc})")

    # -- service / lock --------------------------------------------------------------------
    unit = Path("/etc/systemd/system/hermcp.service")
    print(f"[info] systemd unit: {'installed' if unit.exists() else 'not installed'} ({unit})")
    from .lock import InstanceLock
    hold = InstanceLock.holder(s.data)
    if hold and not hold.get("stale"):
        print(f"[info] instance lock: pid {hold.get('pid')} role={hold.get('role')}")
    else:
        print("[info] instance lock: free")

    try:
        import urllib.request
        urllib.request.urlopen(s.hermes_api_url + "/health", timeout=2)
        row(True, "Hermes api_server", s.hermes_api_url)
    except Exception as exc:  # noqa: BLE001
        warn(f"Hermes api_server {s.hermes_api_url} not reachable ({type(exc).__name__})", "chat tools need it")
    print(f"doctor: {'ALL OK' if not problems else str(problems) + ' problem(s)'}")
    return 1 if problems else 0
