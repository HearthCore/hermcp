"""Single-instance coordination for the hermcp server.

Why: the same server can be started from three places — a terminal, the gateway plugin
surface, or a systemd unit. Only ONE may hold the port and the key store's write path at a
time. An flock on `plugin-data/hermcp/serve.lock` is the arbiter (survives crashes
automatically: the kernel drops the lock when the process dies).

The lock file also carries metadata (pid, role, port, started, argv) so a second starter can
say exactly *who* is already running and what to do about it — and so `hermes hermcp status`
can answer "plugin instance or systemd instance?" without guessing.

Exit code contract for `serve`:
  0   clean shutdown
  75  EX_TEMPFAIL — another instance holds the lock (systemd: Restart=on-failure +
      RestartPreventExitStatus=75 keeps it from flapping)
"""
from __future__ import annotations

import errno
import fcntl
import json
import os
import socket
import sys
import time
from pathlib import Path

EX_TEMPFAIL = 75


def lock_path(data_dir: Path) -> Path:
    return data_dir / "serve.lock"


def detect_role() -> str:
    """`systemd` when we were started by a unit, else `standalone`."""
    return "systemd" if os.environ.get("INVOCATION_ID") else "standalone"


class InstanceLock:
    """Advisory exclusive lock around one serving process."""

    def __init__(self, data_dir: Path, host: str, port: int) -> None:
        self.path = lock_path(data_dir)
        self.host, self.port = host, port
        self._fh = None

    # -- introspection (read-only, no lock needed) --------------------------------------
    @staticmethod
    def holder(data_dir: Path) -> dict | None:
        """Metadata of the process holding the lock, or None when it is free."""
        path = lock_path(data_dir)
        try:
            raw = path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return None
        if not raw:
            return None
        try:
            meta = json.loads(raw)
        except json.JSONDecodeError:
            return {"pid": None, "raw": raw[:200], "note": "unparseable lock metadata"}
        pid = meta.get("pid")
        if isinstance(pid, int) and not _alive(pid):
            meta["stale"] = True
        return meta

    # -- acquire -----------------------------------------------------------------------
    def acquire(self) -> dict:
        """Take the lock. Raises BlockingIOError (errno EWOULDBLOCK) when someone else holds it."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+", encoding="utf-8")
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            fh.seek(0)
            holder = fh.read().strip()
            fh.close()
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                raise BlockingIOError(errno.EWOULDBLOCK, "another hermcp instance holds the lock",
                                      holder) from exc
            raise
        meta = {"pid": os.getpid(), "role": detect_role(), "host": self.host, "port": self.port,
                "started": int(time.time()), "argv": " ".join(sys.argv)[:400]}
        fh.seek(0)
        fh.truncate()
        fh.write(json.dumps(meta) + "\n")
        fh.flush()
        self._fh = fh
        return meta

    def release(self) -> None:
        fh, self._fh = self._fh, None
        if not fh:
            return
        try:
            fh.seek(0)
            fh.truncate()
            fh.flush()
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()

    def __enter__(self) -> "InstanceLock":
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def port_owner(host: str, port: int) -> dict:
    """Best-effort: is the port already bound, and can we reach a healthy server there?"""
    out: dict = {"host": host, "port": port, "bound": False, "healthy": False}
    probe_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        out["bound"] = s.connect_ex((probe_host, port)) == 0
    if out["bound"]:
        try:
            import urllib.request
            with urllib.request.urlopen(f"http://{probe_host}:{port}/health", timeout=2) as r:
                out["healthy"] = r.status == 200
                out["body"] = r.read(200).decode("utf-8", "replace")
        except Exception as exc:
            out["health_error"] = str(exc)[:160]
    return out


def busy_report(data_dir: Path, host: str, port: int) -> dict:
    """Everything a second starter needs to explain itself (no secrets)."""
    holder = InstanceLock.holder(data_dir) or {}
    owner = port_owner(host, port)
    role = holder.get("role") or "unknown"
    if role == "systemd":
        next_step = ("systemd owns it — use `systemctl restart hermcp` (or `hermes hermcp "
                     "restart-service`); do not start a second process")
    elif role == "standalone":
        next_step = ("a standalone/hermes-spawned instance owns it — `hermes hermcp stop` "
                     "ends it, then start the systemd unit if you want it managed")
    else:
        next_step = "lock metadata missing — inspect `hermes hermcp status`, then free the port"
    return {"lock": str(lock_path(data_dir)), "holder": holder, "port": owner,
            "next_step": next_step}
