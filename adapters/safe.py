"""Read-only file helpers for the log / profile-file / hook / plugin-config tools.

Rules every caller gets for free:
  * a path is resolved and must stay inside an allowed root (no `..`, no symlink escape),
  * only regular files are read, with a byte cap; the tail is read from the end so huge logs stay cheap,
  * every returned line passes hermcp's text redaction (secret shapes are masked),
  * truncation is always announced (`truncated`, `scanned_*`), never silent.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from .. import meta
from ..redact import redact_text
from ..registry import ToolError

MAX_TAIL_BYTES = 2_000_000
MAX_READ_BYTES = 400_000
TRUNC_MARK = "…[truncated by hermcp]"
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@+-]{0,127}$")


def safe_name(name: str, what: str = "name") -> str:
    if not _SAFE_NAME.match(name or "") or ".." in name:
        raise ToolError("invalid_arguments", f"bad {what} {name!r}")
    return name


def resolve_inside(root: Path, rel: str | Path, *, what: str = "path") -> Path:
    """`root/rel` resolved; refuses anything that leaves `root` (traversal or symlink escape)."""
    root_r = Path(root).resolve()
    p = (root_r / rel).resolve()
    if p != root_r and root_r not in p.parents:
        raise ToolError("forbidden", f"{what} escapes its directory")
    return p


def tail_lines(path: Path, lines: int, *, contains: str | None = None, exclude: str | None = None,
               max_line_chars: int = 600) -> dict[str, Any]:
    """Last `lines` lines (after optional substring filters) of a text file, redacted, with a size report."""
    if not path.is_file():
        raise ToolError("not_found", f"no such file: {path.name}")
    st = path.stat()
    meta.note_file(path)
    size = st.st_size
    with open(path, "rb") as fh:
        start = max(0, size - MAX_TAIL_BYTES)
        fh.seek(start)
        raw = fh.read(MAX_TAIL_BYTES)
    text = raw.decode("utf-8", errors="replace")
    all_lines = text.splitlines()
    if start > 0 and all_lines:
        all_lines = all_lines[1:]            # first line is cut mid-way
    c = contains.lower() if contains else None
    x = exclude.lower() if exclude else None
    sel = [ln for ln in all_lines if (c is None or c in ln.lower()) and (x is None or x not in ln.lower())]
    picked = sel[-lines:]
    out = []
    for ln in picked:
        ln = redact_text(ln.rstrip("\n"), partial=True)
        out.append(ln if len(ln) <= max_line_chars else ln[:max_line_chars] + f"…[+{len(ln) - max_line_chars}]")
    return {"size_bytes": size, "mtime": st.st_mtime, "matched_lines": len(sel), "returned": len(out),
            "truncated": len(sel) > len(picked) or start > 0,
            "scanned_bytes": len(raw), "scanned_from_offset": start, "lines": out}


def read_capped(path: Path, max_chars: int, *, redact: bool = True) -> dict[str, Any]:
    if not path.is_file():
        raise ToolError("not_found", f"no such file: {path.name}")
    st = path.stat()
    meta.note_file(path)
    with open(path, "rb") as fh:
        raw = fh.read(MAX_READ_BYTES + 1)
    text = raw[:MAX_READ_BYTES].decode("utf-8", errors="replace")
    cut = len(raw) > MAX_READ_BYTES or len(text) > max_chars
    text = text[:max_chars]
    if redact:
        text = redact_text(text, partial=True)
    if cut:
        text += "\n" + TRUNC_MARK
    return {"size_bytes": st.st_size, "mtime": st.st_mtime, "truncated": cut, "chars": len(text), "content": text}


def list_dir_names(path: Path, limit: int = 60) -> list[str]:
    try:
        return sorted(e.name for e in os.scandir(path))[:limit]
    except OSError:
        return []
