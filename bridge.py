"""Cross-session approvals bridge.

Pending Hermes approvals live only in the gateway process memory (tools.approval). The plugin's
agent half (``register(ctx)``) runs INSIDE that process, so it can read and resolve them. The
standalone hermcp server reaches it over a loopback-only AF_UNIX socket:

    $HERMES_HOME/plugin-data/hermcp/approvals.sock      mode 0600, peer uid must match

Two gateway layouts answer on that socket:

* **separate gateway** — one gateway process per profile home, so one socket per profile. Profile
  pinning is structural: a key pinned to ``researcher`` can only ever reach researcher's socket.
* **multiplex gateway** (``gateway.multiplex_profiles``) — ONE process launched from the default
  home that serves every profile; it publishes exactly one socket, and that socket lists the
  pending approvals of ALL profiles. ``resolve()`` detects this (the launch home's
  ``gateway_state.json`` records ``served_profiles``) and the approvals tools then filter by the
  profile namespace of the session key (``profile_of_session_key``), so a profile-restricted key
  still sees and answers only its own profile's approvals.

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
from dataclasses import dataclass
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


# -- which socket serves which profile (multiplex vs separate gateway) ---------------------
MULTIPLEX = "multiplex-shared"     # one socket serves every profile: callers MUST filter
PER_HOME = "per-home"              # one gateway process and one socket per profile home


def profile_of_session_key(session_key: Any) -> str | None:
    """Profile a gateway session key belongs to, or None when it cannot be attributed.

    ``gateway/session.py::_session_key_namespace`` namespaces every key with ``agent:<ns>``:
    the default profile is ``agent:main:…``, a profile literally named ``main`` is the marked
    ``agent:main~:…`` (``~`` is outside the profile-id alphabet, so it can never be another
    profile's id), a named profile is ``agent:<name>:…``. This is the dependency-free mirror of
    ``hermes_state_keys.profile_from_session_key`` — the standalone hermcp server has no Hermes on
    its import path. Anything that is not a well formed ``agent:<ns>:<platform>…`` key returns
    None: fail closed, an odd key is never attributed to a profile. That is stricter than the
    upstream inverse, which reads a missing slot as ``main`` (so ``agent:`` -> "default"); here an
    unattributable key would otherwise be shown to a default-bound key.
    """
    if not isinstance(session_key, str) or not session_key:
        return None
    parts = session_key.split(":")
    if len(parts) < 3 or parts[0] != "agent":
        return None
    ns = parts[1]
    if ns == "main":
        return "default"
    if ns == "main~":
        return "main"
    return None if (not ns or "~" in ns) else ns


def entries_for_profile(entries: list[dict[str, Any]], profile: str) -> list[dict[str, Any]]:
    """The entries of a shared socket's queue that belong to *profile* (the namespace rule above).

    The single place the namespace rule is applied to a whole queue, so a listing
    (`approvals_pending`) and a count (`orchestration_overview`) can never disagree.
    """
    return [e for e in entries if profile_of_session_key(e.get("session_key")) == profile]


def runtime_status(home: Path) -> dict[str, Any] | None:
    """``<home>/gateway_state.json`` as a dict, or None when it is absent or unreadable."""
    try:
        data = json.loads((Path(home) / "gateway_state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def recorded_served_profiles(home: Path) -> list[str] | None:
    """``served_profiles`` the gateway at *home* recorded, or None when the key is absent.

    The multiplexer writes every profile it serves into the launch home's (the default home's)
    ``gateway_state.json``. An empty list is an authoritative "serves nobody else"; None means the
    record predates the field (or there is no gateway), which is what the config hint covers.
    """
    served = (runtime_status(home) or {}).get("served_profiles")
    return [str(p) for p in served if p] if isinstance(served, list) else None


def config_multiplex_hint(home: Path) -> bool | None:
    """``gateway.multiplex_profiles`` from *home*'s config.yaml, or None when it cannot be read."""
    try:
        import yaml
        cfg = yaml.safe_load((Path(home) / "config.yaml").read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 - yaml missing or an unreadable config is "no hint"
        return None
    gw = cfg.get("gateway")
    return bool(gw.get("multiplex_profiles")) if isinstance(gw, dict) and "multiplex_profiles" in gw else None


def served_profiles(home: Path, known_profiles: list[str] | None = None) -> list[str] | None:
    """Profiles the gateway at *home* serves, or None when it is not a multiplexer.

    The runtime record is the truth about the running process; the config flag is only a hint for
    a gateway that predates it, and then it means every known profile.
    """
    recorded = recorded_served_profiles(home)
    if recorded is not None:
        return recorded
    if config_multiplex_hint(home) and known_profiles:
        return [str(p) for p in known_profiles]
    return None


@dataclass(frozen=True)
class Target:
    """Which bridge socket serves a profile, and whether that socket is shared across profiles."""
    profile: str
    mode: str
    socket: Path
    shared: bool                                  # the socket lists every profile's approvals
    served_profiles: list[str] | None = None
    gateway_pid: int | None = None


def resolve(profile: str, *, own_socket: Path, root_home: Path,
            known_profiles: list[str] | None = None) -> Target:
    """Decide which socket answers for *profile*.

    1. the profile's own socket answers -> per-home (a separate gateway keeps working unchanged,
       and a live per-profile socket wins even while a multiplexer runs)
    2. the launch home records a multiplexer that serves *profile* and its socket answers ->
       multiplex-shared, which is the ONLY case where a socket holds several profiles' approvals
    3. otherwise the own socket is used ("per-home"): it is the socket the fix hint talks about
    """
    own = Path(own_socket)
    root = socket_path(root_home)
    served = served_profiles(root_home, known_profiles)
    if own != root:
        hit = ping(own)
        if hit is not None:
            return Target(profile, PER_HOME, own, False, served, hit.get("pid"))
    hit = ping(root)
    if hit is not None and served is not None and profile in served:
        return Target(profile, MULTIPLEX, root, True, served, hit.get("pid"))
    return Target(profile, PER_HOME, own, False, served, None)


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
    return ping(path) is not None


# -- client (standalone hermcp server) ----------------------------------------------------
def ping(path: Path, timeout: float = 1.0) -> dict[str, Any] | None:
    """The ping answer of a LIVE bridge at *path*, or None when nothing answers there.

    Never raises: a missing socket file, a refused connection, a stale socket whose owner is gone
    and a garbage answer all mean "not reachable" for mode detection.
    """
    try:
        resp = call(Path(path), {"op": "ping"}, timeout=timeout)
    except (OSError, ValueError):
        return None
    return resp if isinstance(resp, dict) and resp.get("ok") is True else None


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
