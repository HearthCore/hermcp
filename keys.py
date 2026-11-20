"""Key store: hashed API keys (`hmcp_<id>_<secret>`), 0600 JSON file, shown once at creation."""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import hmac
import json
import os
import secrets
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .scopes import Scopes, ScopeError  # noqa: F401

PREFIX = "hmcp_"
LAST_USED_WRITE_INTERVAL = 60.0


class KeyError_(Exception):
    pass


@dataclass
class KeyRecord:
    id: str
    name: str
    owner: str
    created: float
    last_used: float | None
    expires: float | None
    disabled: bool
    scopes: Scopes
    salt: str
    hash: str

    def public(self) -> dict[str, Any]:
        """Everything except salt/hash."""
        return {
            "id": self.id, "name": self.name, "owner": self.owner, "created": self.created,
            "last_used": self.last_used, "expires": self.expires, "disabled": self.disabled,
            "scopes": self.scopes.to_dict(),
        }

    def to_json(self) -> dict[str, Any]:
        d = self.public()
        d.update(salt=self.salt, hash=self.hash)
        return d

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "KeyRecord":
        return cls(
            id=d["id"], name=d["name"], owner=d.get("owner", ""), created=d["created"],
            last_used=d.get("last_used"), expires=d.get("expires"),
            disabled=bool(d.get("disabled", False)), scopes=Scopes.from_dict(d.get("scopes")),
            salt=d["salt"], hash=d["hash"],
        )

    def expired(self, now: float | None = None) -> bool:
        return self.expires is not None and (now or time.time()) >= self.expires


def _digest(salt: str, secret: str) -> str:
    return hmac.new(bytes.fromhex(salt), secret.encode(), hashlib.sha256).hexdigest()


def split_token(token: str) -> tuple[str, str] | None:
    """`hmcp_<id>_<secret>` -> (id, secret); None when malformed."""
    if not token.startswith(PREFIX):
        return None
    rest = token[len(PREFIX):]
    kid, sep, secret = rest.partition("_")
    if not sep or not kid or len(secret) < 16:
        return None
    return kid, secret


class KeyStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._keys: dict[str, KeyRecord] = {}
        self._sig: tuple | None = None
        self._last_flush = 0.0
        self._load()

    # -- persistence --------------------------------------------------------------------
    @staticmethod
    def _signature(st: os.stat_result) -> tuple:
        # mtime alone misses a write that lands in the same timestamp tick; _save always replaces
        # the file (new inode), and size/ns-mtime catch in-place edits.
        return (st.st_mtime_ns, st.st_size, st.st_ino)

    @contextlib.contextmanager
    def _write_lock(self):
        """Serialise writers across processes (server vs. `hermes hermcp keys ...`): an flock on a
        sidecar file, plus the in-process lock. Inside it, `_load(force=True)` sees every earlier
        write, so a read-modify-write can never apply a stale cached view over a newer file."""
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(self.path) + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                self._load(force=True)
                yield
            finally:
                os.close(fd)                       # closing the descriptor releases the flock

    def _load(self, force: bool = False) -> None:
        with self._lock:
            try:
                st = self.path.stat()
            except OSError:
                self._keys, self._sig = {}, None
                return
            if not force and self._signature(st) == self._sig:
                return
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise KeyError_(f"key store unreadable: {exc}") from exc
            self._keys = {r["id"]: KeyRecord.from_json(r) for r in raw.get("keys", [])}
            self._sig = self._signature(st)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".keys-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "keys": [k.to_json() for k in self._keys.values()]}, f, indent=2)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        self._sig = self._signature(self.path.stat())

    # -- operations ---------------------------------------------------------------------
    def create(self, name: str, scopes: Scopes, owner: str = "", expires: float | None = None) -> tuple[KeyRecord, str]:
        if not name.strip():
            raise KeyError_("name required")
        with self._write_lock():
            if any(k.name == name for k in self._keys.values()):
                raise KeyError_(f"a key named {name!r} already exists")
            rec, token = self._new(name, scopes, owner, expires)
            self._keys[rec.id] = rec
            self._save()
            return rec, token

    def _new(self, name, scopes, owner, expires, kid: str | None = None):
        kid = kid or secrets.token_hex(4)
        secret = secrets.token_urlsafe(32)
        salt = secrets.token_hex(16)
        rec = KeyRecord(id=kid, name=name, owner=owner, created=time.time(), last_used=None,
                        expires=expires, disabled=False, scopes=scopes, salt=salt,
                        hash=_digest(salt, secret))
        return rec, f"{PREFIX}{kid}_{secret}"

    def authenticate(self, token: str) -> KeyRecord | None:
        parts = split_token(token)
        if not parts:
            return None
        kid, secret = parts
        with self._lock:
            self._load()
            rec = self._keys.get(kid)
            if rec is None:
                # constant-ish work to avoid id-probing timing differences
                _digest("00" * 16, secret)
                return None
            if not hmac.compare_digest(rec.hash, _digest(rec.salt, secret)):
                return None
            if rec.disabled or rec.expired():
                return None
            now = time.time()
            rec.last_used = now
            if now - self._last_flush >= LAST_USED_WRITE_INTERVAL:
                self._last_flush = now
                self._flush_last_used(kid, now)
            return rec

    def _flush_last_used(self, kid: str, when: float) -> None:
        """Persist `last_used` as a merge: re-read the file under the write lock and touch only that
        field of that key. Everything else (a revoke, rotate, scope change or new key made by the
        CLI) is taken from disk, never from this process's cache."""
        try:
            with self._write_lock():
                fresh = self._keys.get(kid)
                if fresh is None:                      # deleted meanwhile: nothing to record
                    return
                fresh.last_used = max(when, fresh.last_used or 0.0)
                self._save()
        except (OSError, KeyError_):
            pass                                       # best effort, never fail an authenticate

    def get(self, ref: str) -> KeyRecord:
        """Look up by id or name."""
        with self._lock:
            self._load()
            if ref in self._keys:
                return self._keys[ref]
            for k in self._keys.values():
                if k.name == ref:
                    return k
        raise KeyError_(f"no such key: {ref}")

    def list(self) -> list[KeyRecord]:
        with self._lock:
            self._load()
            return sorted(self._keys.values(), key=lambda k: k.created)

    def revoke(self, ref: str, delete: bool = False) -> KeyRecord:
        with self._write_lock():
            rec = self.get(ref)
            if delete:
                del self._keys[rec.id]
            else:
                rec.disabled = True
            self._save()
            return rec

    def rotate(self, ref: str) -> tuple[KeyRecord, str]:
        with self._write_lock():
            old = self.get(ref)
            rec, token = self._new(old.name, old.scopes, old.owner, old.expires, kid=old.id)
            rec.created, rec.disabled = old.created, old.disabled
            self._keys[rec.id] = rec
            self._save()
            return rec, token

    def update_scopes(self, ref: str, scopes: Scopes) -> KeyRecord:
        with self._write_lock():
            rec = self.get(ref)
            rec.scopes = scopes
            self._save()
            return rec

    def rename(self, ref: str, name: str) -> KeyRecord:
        """Change a key's name. Metadata only — salt/hash (and therefore the token) are untouched."""
        new = (name or "").strip()
        if not new:
            raise KeyError_("name required")
        with self._write_lock():
            rec = self.get(ref)
            for k in self._keys.values():
                if k.id != rec.id and k.name == new:
                    raise KeyError_(f"a key named {new!r} already exists")
            rec.name = new
            self._save()
            return rec


def parse_expiry(text: str | None) -> float | None:
    """`30d`, `12h`, `90m` or ISO date -> epoch seconds."""
    if not text:
        return None
    t = text.strip().lower()
    mult = {"m": 60, "h": 3600, "d": 86400}
    if t[-1] in mult and t[:-1].isdigit():
        return time.time() + int(t[:-1]) * mult[t[-1]]
    from datetime import datetime
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError as exc:
        raise KeyError_(f"bad expiry {text!r} (use 30d / 12h / ISO date)") from exc
