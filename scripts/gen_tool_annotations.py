#!/usr/bin/env python3
"""Write tests/golden_tool_annotations.json — the reviewed title + hints of every tool.

Run this only after reviewing a change in `hermcp/annotations.py` (a new tool, a tier change,
an override): the golden file is the audit trail that `tests/test_annotations.py` holds the
served descriptors against, so regenerating it blindly defeats the test.

Usage: python scripts/gen_tool_annotations.py [--out PATH]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))  # allow `import hermcp` when running from plugins/

from hermcp.app import build_registry  # noqa: E402


def build() -> dict[str, dict]:
    reg = build_registry()
    return {name: {"title": t.annotations.title, "annotations": t.annotations.as_dict()}
            for name, t in sorted(reg.tools.items())}


def write(out: Path | None = None) -> Path:
    target = out or ROOT / "tests" / "golden_tool_annotations.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(build(), indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return target


if __name__ == "__main__":
    path = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else None
    written = write(path)
    print(f"wrote {written} ({len(build())} tools)")
