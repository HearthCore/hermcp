"""Discovery aid: record WHAT an MCP client sends (header names, clientInfo, params._meta, tool names) so we can
see whether a client already sends a stable session or conversation identifier.

Off by default (HERMCP_DEBUG_CAPTURE=1) and self-expiring (HERMCP_DEBUG_CAPTURE_HOURS, default 12 h after start).
Only authenticated requests are recorded. Never recorded: the Authorization header and other credential
headers (only a sha256 fingerprint of the bearer token, never the token), tool argument VALUES (only their
names), client IPs (presence only). Everything else is passed through
the central secret masker and capped. File: <data>/capture.jsonl (0600, trimmed to the newest N records).
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any

from .redact import redact_text

MAX_VALUE = 200
MAX_DEPTH = 5
FILE_NAME = "capture.jsonl"

# value is never stored for these (credentials / client addresses); presence is
SECRET_HEADERS = {"authorization", "proxy-authorization", "cookie", "set-cookie", "x-api-key", "x-auth-token"}
ADDRESS_HEADERS = {"x-forwarded-for", "x-real-ip", "cf-connecting-ip", "forwarded", "x-forwarded-host",
                   "x-forwarded-server", "host"}
# headers every HTTP client sends: not interesting when looking for session identifiers
COMMON_HEADERS = {"host", "user-agent", "content-type", "content-length", "accept", "accept-encoding",
                  "accept-language", "connection", "authorization", "mcp-protocol-version", "x-forwarded-for",
                  "x-forwarded-proto", "x-forwarded-host", "x-forwarded-port", "x-forwarded-server", "x-real-ip",
                  "cache-control", "pragma", "origin", "referer", "sec-fetch-mode", "sec-fetch-site",
                  "sec-fetch-dest", "traceparent", "tracestate"}


def _clip(v: Any, depth: int = 0) -> Any:
    """JSON-safe, depth- and size-limited copy with secret-looking strings masked."""
    if depth > MAX_DEPTH:
        return "[DEPTH]"
    if isinstance(v, dict):
        return {str(k)[:80]: _clip(x, depth + 1) for k, x in list(v.items())[:40]}
    if isinstance(v, (list, tuple)):
        return [_clip(x, depth + 1) for x in list(v)[:20]]
    if isinstance(v, str):
        s = redact_text(v)
        if s != v:   # looks like a secret to the masker: keep only a fingerprint (equal values -> equal fp)
            return {"masked": True, "len": len(v), "fp": hashlib.sha256(v.encode()).hexdigest()[:10]}
        return s if len(s) <= MAX_VALUE else s[:MAX_VALUE] + f"...[+{len(s) - MAX_VALUE}]"
    if isinstance(v, (int, float, bool)) or v is None:
        return v
    return f"<{type(v).__name__}>"


def auth_fingerprint(headers: dict[str, str]) -> str | None:
    """Short sha256 fingerprint of the bearer token (never the token): equal token -> equal fp. Lets us see whether
    parallel sessions of one client share a credential (e.g. an OAuth token per connector)."""
    for name, val in headers.items():
        if name.lower() == "authorization":
            token = val.partition(" ")[2].strip()
            return hashlib.sha256(token.encode()).hexdigest()[:10] if token else None
    return None


def clean_headers(headers: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, val in headers.items():
        low = name.lower()
        if low in SECRET_HEADERS:
            out[low] = "[present]"
        elif low in ADDRESS_HEADERS:
            out[low] = "[present]"
        else:
            out[low] = _clip(val)
    return out


def summarize_message(msg: Any) -> dict[str, Any]:
    """Shape of one JSON-RPC message; argument VALUES of tool calls are dropped."""
    if not isinstance(msg, dict):
        return {"kind": type(msg).__name__}
    params = msg.get("params")
    params = params if isinstance(params, dict) else {}
    out: dict[str, Any] = {"rpc": msg.get("method"), "has_id": "id" in msg,
                           "top_level_keys": sorted(str(k) for k in msg),
                           "param_keys": sorted(str(k) for k in params)}
    if "_meta" in params:
        out["params_meta"] = _clip(params["_meta"])
    if msg.get("method") == "initialize":
        out["protocolVersion"] = _clip(params.get("protocolVersion"))
        out["clientInfo"] = _clip(params.get("clientInfo"))
        out["capabilities"] = _clip(params.get("capabilities"))
    if msg.get("method") == "tools/call":
        out["tool"] = _clip(params.get("name"))
        args = params.get("arguments")
        out["argument_names"] = sorted(str(k) for k in args) if isinstance(args, dict) else []
    return out


class Capture:
    def __init__(self, path: Path, *, enabled: bool, max_records: int = 300, hours: float = 12.0):
        self.path = Path(path)
        self.enabled = enabled
        self.max_records = max(20, max_records)
        self.expires_at = time.time() + max(0.0, hours) * 3600
        self._lock = threading.Lock()
        self._n = 0

    @property
    def active(self) -> bool:
        return self.enabled and time.time() < self.expires_at

    def record(self, *, method: str, path: str, headers: dict[str, str], messages: list[Any], key_name: str,
               key_id: str, auth_kind: str | None = None) -> None:
        """Never raises: a debug aid must not break a request."""
        if not self.active:
            return
        try:
            rec = {"ts": round(time.time(), 3), "http": method, "path": path, "key_name": key_name,
                   "key_id": key_id, "auth_kind": auth_kind, "auth_fp": auth_fingerprint(headers),
                   "headers": clean_headers(headers),
                   "messages": [summarize_message(m) for m in messages[:20]]}
            line = json.dumps(rec, ensure_ascii=False, default=str)
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                new = not self.path.exists()
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
                if new:
                    self.path.chmod(0o600)
                self._n += 1
                if self._n % 50 == 0:
                    self._trim()
        except Exception:  # noqa: BLE001
            pass

    def _trim(self) -> None:
        lines = self.path.read_text(encoding="utf-8").splitlines()
        if len(lines) > self.max_records:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text("\n".join(lines[-self.max_records:]) + "\n", encoding="utf-8")
            tmp.chmod(0o600)
            tmp.replace(self.path)


def read_records(path: Path) -> list[dict]:
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out


def _meta_paths(obj: Any, prefix: str = "") -> list[str]:
    if isinstance(obj, dict):
        res: list[str] = []
        for k, v in obj.items():
            res += _meta_paths(v, f"{prefix}.{k}" if prefix else str(k))
        return res
    return [prefix] if prefix else []


def report(records: list[dict], *, last: int = 10) -> dict[str, Any]:
    """Group by client (x-anthropic-client + User-Agent) and surface everything that could carry a session id."""
    clients: dict[str, dict[str, Any]] = {}
    for r in records:
        h = r.get("headers") or {}
        info = None
        for m in r.get("messages") or []:
            if m.get("clientInfo"):
                info = m["clientInfo"]
        ua = h.get("user-agent", "")
        label = " | ".join(x for x in (str(h.get("x-anthropic-client", "")), ua) if x)
        c = clients.setdefault(label or "(no user-agent)", {
            "user_agent": ua, "auth_fps": {}, "auth_kinds": set(), "requests": 0, "keys": set(), "client_info": None, "http_methods": set(),
            "header_names": set(), "non_standard_headers": {}, "mcp_session_id_seen": False,
            "meta_paths": set(), "rpc_methods": set(), "tools_called": set(),
            "first_ts": r.get("ts"), "last_ts": r.get("ts")})
        c["requests"] += 1
        c["last_ts"] = r.get("ts")
        c["keys"].add(r.get("key_name"))
        if r.get("auth_fp"):
            c["auth_fps"][r["auth_fp"]] = c["auth_fps"].get(r["auth_fp"], 0) + 1
        if r.get("auth_kind"):
            c["auth_kinds"].add(r["auth_kind"])
        c["http_methods"].add(r.get("http"))
        if info:
            c["client_info"] = info
        for name, val in h.items():
            c["header_names"].add(name)
            if name == "mcp-session-id":
                c["mcp_session_id_seen"] = True
            if name not in COMMON_HEADERS:
                c["non_standard_headers"].setdefault(name, set()).add(str(val))
        for m in r.get("messages") or []:
            if m.get("rpc"):
                c["rpc_methods"].add(m["rpc"])
            if m.get("tool"):
                c["tools_called"].add(m["tool"])
            c["meta_paths"].update(_meta_paths(m.get("params_meta") or {}))
    out = []
    for c in clients.values():
        out.append({**c, "auth_kinds": sorted(c["auth_kinds"]), "keys": sorted(c["keys"]), "http_methods": sorted(c["http_methods"]),
                    "header_names": sorted(c["header_names"]), "rpc_methods": sorted(c["rpc_methods"]),
                    "tools_called": sorted(c["tools_called"]), "meta_paths": sorted(c["meta_paths"]),
                    "non_standard_headers": {k: sorted(v)[:5] for k, v in sorted(c["non_standard_headers"].items())}})
    return {"records": len(records), "clients": out, "last": records[-max(0, last):] if last else []}
