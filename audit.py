"""Append-only JSONL audit log with secret redaction. Never records secret values."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from .redact import MARK as REDACTED, redact as _central_redact, redact_text  # noqa: F401   # one masking implementation for everything
MAX_STR = 500


def redact(obj: Any, _depth: int = 0) -> Any:
    """Audit-log style: WRITE masking (no secret characters at all), lists capped at 50 items, strings at 500."""
    def _cap(o: Any, d: int = 0) -> Any:
        if d > 8:
            return "[DEPTH]"
        if isinstance(o, dict):
            return {k: _cap(v, d + 1) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [_cap(v, d + 1) for v in list(o)[:50]]
        if isinstance(o, str) and len(o) > MAX_STR:
            return o[:MAX_STR] + f"...[+{len(o) - MAX_STR}]"
        return o
    return _cap(_central_redact(obj, "", strict=True))


class AuditLog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def write(self, *, key_id: str, key_name: str, tool: str, args: dict, profile: str | None,
              status: str, duration_ms: float, detail: str = "") -> None:
        rec = {
            "ts": round(time.time(), 3), "key_id": key_id, "key_name": key_name, "tool": tool,
            "args": redact(args), "profile": profile, "status": status,
            "duration_ms": round(duration_ms, 1),
        }
        if detail:
            rec["detail"] = redact(detail)
        line = json.dumps(rec, ensure_ascii=False, default=str)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            new = not self.path.exists()
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            if new:
                self.path.chmod(0o600)

    def tail(self, n: int = 50, tool: str | None = None, key: str | None = None) -> list[dict]:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out: list[dict] = []
        for ln in reversed(lines):
            try:
                rec = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if tool and rec.get("tool") != tool:
                continue
            if key and key not in (rec.get("key_id"), rec.get("key_name")):
                continue
            out.append(rec)
            if len(out) >= max(1, min(n, 500)):
                break
        return list(reversed(out))
