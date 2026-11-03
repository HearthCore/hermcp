"""Cross-session approvals bridge.

Pending Hermes approvals live only in the gateway process memory (tools.approval). The plugin's
agent half (``register(ctx)``) runs INSIDE that process, so it can read and resolve them. The
standalone hermcp server reaches it over a loopback-only AF_UNIX socket:

    $HERMES_HOME/plugin-data/hermcp/approvals.sock      mode 0600, peer uid must match

One socket per profile home (every profile gateway has its own HERMES_HOME), which makes
profile pinning structural: a key pinned to ``researcher`` can only ever reach researcher's socket.

Wire format: one JSON object per line in, one out. Ops: ``ping``, ``list``, ``answer``.
Nothing here imports Hermes at module import time; Hermes modules are imported lazily inside the
gateway process only.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import socketserver
import struct
import threading
from pathlib import Path
from typing import Any

from .audit import AuditLog
from .redact import redact_text as redact

log = logging.getLogger("hermcp.bridge")

SOCK_NAME = "approvals.sock"
MAX_LINE = 64 * 1024
CHOICES = ("once", "session", "always", "deny")


def socket_path(home: Path) -> Path:
    return Path(home) / "plugin-data" / "hermcp" / SOCK_NAME


# -- server (gateway process) ------------------------------------------------------------
def _approval_module():
    from tools import approval  # type: ignore[import-not-found]  # present inside the gateway only
    return approval


def snapshot_pending() -> list[dict[str, Any]]:
    """Every unresolved gateway approval across all sessions of this gateway process."""
    ap = _approval_module()
    with ap._lock:
        keys = list(ap._gateway_queues)
    out: list[dict[str, Any]] = []
    for key in keys:
        for d in ap.list_gateway_approvals(key):
            out.append({
                "session_key": key,
                "request_id": d.get("request_id"),
                "command": redact(str(d.get("command", "")))[:800],
                "description": redact(str(d.get("description", "")))[:400],
                "pattern_key": d.get("pattern_key"),
                "surface": d.get("surface"),
                "allowed_choices": d.get("allowed_choices") or list(CHOICES),
                "timeout_seconds": d.get("timeout_seconds"),
            })
    return out


def answer_pending(session_key: str, request_id: str, choice: str, reason: str | None) -> dict[str, Any]:
    if choice not in CHOICES:
        return {"ok": False, "error": f"choice must be one of {list(CHOICES)}"}
    match = [a for a in snapshot_pending() if a["session_key"] == session_key and a["request_id"] == request_id]
    if not match:
        return {"ok": False, "error": "approval_not_pending"}
    if choice not in match[0]["allowed_choices"]:
        return {"ok": False, "error": f"choice {choice!r} not allowed for this request",
                "allowed_choices": match[0]["allowed_choices"]}
    n = _approval_module().resolve_gateway_approval(session_key, choice, False, reason, request_id)
    return {"ok": n > 0, "resolved": n, **({} if n else {"error": "approval_not_pending"})}


def handle_request(req: dict[str, Any]) -> dict[str, Any]:
    op = req.get("op")
    try:
        if op == "ping":
            return {"ok": True, "pid": os.getpid()}
        if op == "list":
            return {"ok": True, "pending": snapshot_pending()}
        if op == "answer":
            sk, rid = req.get("session_key"), req.get("request_id")
            if not (isinstance(sk, str) and isinstance(rid, str) and sk and rid):
                return {"ok": False, "error": "session_key and request_id are required"}
            reason = req.get("reason")
            return answer_pending(sk, rid, str(req.get("choice", "")),
                                  reason[:300] if isinstance(reason, str) else None)
    except ImportError as exc:
        return {"ok": False, "error": f"gateway approval module unavailable: {exc}"}
    except Exception as exc:  # noqa: BLE001 - never kill the gateway thread
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": False, "error": f"unknown op {op!r}"}


class _Handler(socketserver.StreamRequestHandler):
    audit: AuditLog | None = None

    def handle(self) -> None:
        try:
            cred = self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
            _pid, uid, _gid = struct.unpack("3i", cred)
        except (OSError, AttributeError):
            return
        if uid != os.geteuid():
            log.warning("approvals bridge: rejected peer uid %s", uid)
            return
        line = self.rfile.readline(MAX_LINE + 1)
        if not line or len(line) > MAX_LINE:
            return
        try:
            req = json.loads(line)
            if not isinstance(req, dict):
                raise ValueError
        except ValueError:
            resp: dict[str, Any] = {"ok": False, "error": "bad request"}
        else:
            resp = handle_request(req)
            if req.get("op") in ("list", "answer") and self.audit is not None:
                try:
                    self.audit.write(key_id=str(req.get("key_id", "?"))[:40], key_name="bridge",
                                     tool=f"bridge.{req['op']}",
                                     args={k: req.get(k) for k in ("session_key", "request_id", "choice")},
                                     profile=None, status="ok" if resp.get("ok") else "error",
                                     duration_ms=0.0, detail=str(resp.get("error", ""))[:200])
                except OSError:
                    pass
        self.wfile.write((json.dumps(resp, default=str) + "\n").encode())


class _Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


_started: _Server | None = None
_start_lock = threading.Lock()


def start(home: Path) -> Path | None:
    """Start the listener once per process. Idempotent; returns the socket path (None on failure)."""
    global _started
    with _start_lock:
        path = socket_path(home)
        if _started is not None:
            return path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            os.chmod(path.parent, 0o700)
            if path.exists() or path.is_symlink():
                if _alive(path):
                    log.warning("approvals bridge already served at %s", path)
                    return None
                path.unlink()
            old = os.umask(0o177)
            try:
                srv = _Server(str(path), _Handler)
            finally:
                os.umask(old)
            os.chmod(path, 0o600)
            _Handler.audit = AuditLog(path.parent / "audit.jsonl")
        except OSError as exc:
            log.error("approvals bridge failed to start: %s", exc)
            return None
        threading.Thread(target=srv.serve_forever, name="hermcp-approvals-bridge", daemon=True).start()
        _started = srv
        log.info("approvals bridge listening on %s", path)
        return path


def stop() -> None:
    global _started
    with _start_lock:
        if _started is not None:
            _started.shutdown()
            _started.server_close()
            _started = None


def _alive(path: Path) -> bool:
    try:
        return call(path, {"op": "ping"}, timeout=1.0).get("ok") is True
    except OSError:
        return False


# -- client (standalone hermcp server) ----------------------------------------------------
def call(path: Path, req: dict[str, Any], timeout: float = 5.0) -> dict[str, Any]:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect(str(path))
        s.sendall((json.dumps(req) + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n") and len(buf) < 4 * MAX_LINE:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    return json.loads(buf or b"{}")
