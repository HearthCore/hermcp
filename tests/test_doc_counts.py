"""The catalog numbers in README.md and docs/tool-surface.md are generated from the live registry.

They used to be hand-maintained and drifted (see issue #69), so the marked regions are rewritten by
`scripts/gen_tool_docs.py` (`python -m hermcp docs`) and this module fails when a file is stale.
"""
import importlib
import shutil
import sys
from pathlib import Path

import pytest

from hermcp.app import build_registry

PLUGIN = Path(__file__).resolve().parents[1]
SOURCES = ("README.md", "docs/tool-surface.md")
REGIONS = {
    "README.md": ("readme-status", "readme-features-tools", "readme-features-advertised",
                  "readme-surface", "readme-roadmap"),
    "docs/tool-surface.md": ("tool-surface",),
}


def _gen():
    sys.path.insert(0, str(PLUGIN / "scripts"))
    try:
        return importlib.import_module("gen_tool_docs")
    finally:
        sys.path.remove(str(PLUGIN / "scripts"))


@pytest.fixture(scope="module")
def gen():
    return _gen()


def _copy_sources(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    for rel in SOURCES:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PLUGIN / rel, root / rel)
    return root


@pytest.fixture(scope="module")
def stats():
    return _gen().catalog_stats()


def test_counts_match_the_live_registry(stats):
    reg = build_registry()
    tools = list(reg.tools.values())
    assert stats["total"] == len(tools)
    assert stats["catalog"] == sum(1 for t in tools if not t.meta)
    assert stats["meta"] == sum(1 for t in tools if t.meta)
    assert stats["catalog"] + stats["meta"] == stats["total"]
    assert stats["toolsets"] == len({t.toolset for t in tools if not t.meta})
    assert stats["core"] == sum(1 for t in tools if t.core)
    assert stats["advertised"] == sum(1 for t in tools if t.core or t.meta)
    assert stats["advertised"] == stats["core"] + stats["meta"]


def test_every_documented_region_exists(gen):
    for rel, names in REGIONS.items():
        text = (PLUGIN / rel).read_text(encoding="utf-8")
        for name in names:
            assert text.count(gen.MARK_START % name) == 1, f"{rel}: missing `{gen.MARK_START % name}`"
            assert text.count(gen.MARK_END % name) == 1, f"{rel}: missing `{gen.MARK_END % name}`"


def test_docs_are_in_sync_with_the_registry(gen, tmp_path):
    root = _copy_sources(tmp_path)
    gen.sync_counts(root)
    for rel in SOURCES:
        assert (root / rel).read_text(encoding="utf-8") == (PLUGIN / rel).read_text(encoding="utf-8"), \
            f"{rel}: catalog counts are stale — run `python -m hermcp docs`"


def test_sync_repairs_a_stale_claim(gen, tmp_path):
    root = _copy_sources(tmp_path)
    readme = root / "README.md"
    stale = readme.read_text(encoding="utf-8").replace(
        f"Today's catalog is **{gen.catalog_stats()['total']} tools**", "Today's catalog is **410 tools**")
    readme.write_text(stale, encoding="utf-8")
    changed = gen.sync_counts(root)
    assert readme in changed
    assert "410 tools" not in readme.read_text(encoding="utf-8")
    assert gen.sync_counts(root) == []          # idempotent
