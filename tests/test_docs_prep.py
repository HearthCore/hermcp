"""`docs/prep/` holds pre-implementation research drafts, and must not read as shipped documentation.

The drafts predate the implementation (issue #71): `docs/prep/approvals.md` concluded that there is
no out-of-process read path for a gateway session's approvals, `docs/prep/oauth.md` called itself
the authoritative spec, and `docs/prep/cli-parity.md` is the German original of the shipped English
table. Each draft now opens with a banner that names the shipped doc superseding it,
`docs/prep/README.md` maps every draft, and no shipped doc links into the folder — so a reader who
lands there is sent to the file that is kept current.
"""
import re
from pathlib import Path

import pytest

from conftest import tracked_files

PLUGIN = Path(__file__).resolve().parents[1]
PREP = PLUGIN / "docs" / "prep"
BANNER = "> **Historical research draft — superseded.**"
# Committed files only: a live install keeps its own git-excluded notes in docs/prep/ (issue #87),
# and those are neither drafts this repository ships nor docs a reader of it lands on.
DRAFTS = [p for p in tracked_files("docs/prep/*.md") if p.name != "README.md"]
# Shipped docs that a reader may land on. CHANGELOG.md is a historical record and keeps its own
# links to the paths a commit touched, so it is not part of this check.
SHIPPED = [PLUGIN / "README.md", PLUGIN / "AGENTS.md", PLUGIN / "CONTRIBUTING.md",
           *tracked_files("docs/*.md")]


def _banner_block(text: str) -> str:
    """The contiguous `>` block that follows the title."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("> "))
    block = []
    for line in lines[start:]:
        if not line.startswith(">"):
            break
        block.append(line)
    return "\n".join(block)


def test_the_folder_has_drafts_to_check():
    assert DRAFTS, "docs/prep/ is empty: drop the folder and this test instead of leaving it behind"


@pytest.mark.parametrize("draft", DRAFTS, ids=lambda p: p.name)
def test_every_draft_opens_with_the_superseded_banner(draft):
    text = draft.read_text(encoding="utf-8")
    assert text.startswith("# "), f"{draft.name} must start with its title"
    assert _banner_block(text).startswith(BANNER), (
        f"{draft.name}: the first block after the title must be the historical-draft banner"
    )


@pytest.mark.parametrize("draft", DRAFTS, ids=lambda p: p.name)
def test_every_banner_names_a_shipped_doc_that_exists(draft):
    """A banner that points nowhere is no better than no banner."""
    block = _banner_block(draft.read_text(encoding="utf-8"))
    targets = [t for t in re.findall(r"\]\(([^)]+)\)", block) if not t.startswith("http")]
    resolved = [(draft.parent / t).resolve() for t in targets]
    shipped = [p for p in resolved if p.exists() and PREP.resolve() not in p.parents]
    assert shipped, f"{draft.name}: the banner must link the shipped doc that supersedes it"


def test_the_readme_maps_every_draft():
    readme = (PREP / "README.md").read_text(encoding="utf-8")
    for draft in DRAFTS:
        assert f"]({draft.name})" in readme, f"docs/prep/README.md does not map {draft.name}"
        assert f"](../{draft.stem}.md)" in readme, (
            f"docs/prep/README.md does not name the shipped doc for {draft.name}"
        )


def test_no_shipped_doc_links_into_the_drafts():
    """The shipped docs are the reference; pointing at a superseded draft would undo the banner."""
    # a link target (inline `](…)` or reference `[x]: …`) into the folder; naming it in prose is fine
    link = re.compile(r"\]\([^)]*prep/|^\s*\[[^\]]+\]:\s*\S*prep/")
    offenders = []
    for doc in SHIPPED:
        for line in doc.read_text(encoding="utf-8").splitlines():
            if link.search(line):
                offenders.append(f"{doc.relative_to(PLUGIN)}: {line.strip()[:80]}")
    assert not offenders, "shipped docs must not link docs/prep/: " + "; ".join(offenders)


def test_the_superseded_conclusions_are_marked():
    """The two claims the issue names are corrected where they stand, not only in the banner."""
    approvals = (PREP / "approvals.md").read_text(encoding="utf-8")
    assert "**Superseded answer:**" in approvals
    assert "bridge.py" in approvals
    oauth = (PREP / "oauth.md").read_text(encoding="utf-8")
    assert "This was the pre-implementation spec" in oauth
    assert "This is the authoritative implementation spec" not in oauth


def test_the_retired_planning_files_are_not_referenced_any_more():
    """`SPEC.md` and `PROGRESS.md` are gone; a pointer to them sends a reader to nothing."""
    docs = [*SHIPPED, *tracked_files(".claude/agents/*.md")]
    offenders = [f"{doc.relative_to(PLUGIN)}: {name}"
                 for doc in docs
                 for name in ("SPEC.md", "PROGRESS.md")
                 if name in doc.read_text(encoding="utf-8")]
    assert not offenders, "retired planning files are still referenced: " + "; ".join(offenders)
