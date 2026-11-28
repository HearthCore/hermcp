"""Every tool carries a title and honest MCP ToolAnnotations; the golden table holds the review.

`tests/golden_tool_annotations.json` is the reviewed outcome of the 2026-11-28 hint audit: for
all tools the registry tier (access + dangerous) decides, and `hermcp/annotations.py`
`OVERRIDES` names the tools where that default would be a lie. The golden file freezes that
decision, so a tier change, a new tool or an edited override fails here until the table is
regenerated and reviewed on purpose.

What the hints must guarantee:
  * readOnlyHint=true never pairs with destructiveHint (the spec's own rule), and every tool
    whose hint says read-only really is declared read and cannot mutate;
  * the four tools that are declared read and still write are annotated as writes — that
    mismatch is the point of the audit, and it is named here so it cannot rot;
  * an open-world hint is set exactly for the tools that do their own network I/O;
  * `tools/list` serves title + annotations, and a read-scoped key is offered nothing that
    claims to write — except `hermes_call`, whose hints are the conservative union because
    the same tool dispatches admin work for a write key (see `test_read_key_*`).
"""
import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from hermcp import annotations as A
from hermcp.app import build_registry
from hermcp.scopes import Scopes

TESTS = Path(__file__).resolve().parent
GOLDEN = json.loads((TESTS / "golden_tool_annotations.json").read_text(encoding="utf-8"))

#: tools the registry declares `read` that still mutate (or reach the internet) — annotated
#: against their tier, never re-tiered. Each one is the reason for an entry in OVERRIDES.
READ_DECLARED_MUTATORS = {
    "inbox_ack": "C-i",          # marks this consumer's inbox events read
    "inbox_push": "C",           # appends events to another consumer's inbox
    "session_card_set": "C-i",   # registers/updates a session card
    "hermes_call": "G",          # dispatches any tool in the caller's scope
}

#: admin-gated tools that only read (the audit trail and the key list). readOnlyHint is honest
#: here; the admin access level is about who may look, not about what the call changes.
ADMIN_DECLARED_READERS = {"audit_tail", "keys_list"}

#: the tools whose own I/O leaves our network (R4: openWorldHint=true exactly here).
OPEN_WORLD = {
    "hermes_call",           # can dispatch every open-world tool above
    "models_list",           # live provider model list
    "mcp_test",              # launches a stdio server / contacts a remote one
    "plugins_check_updates",  # asks upstream for new plugin versions
    "plugins_install",       # fetches plugin code
    "plugins_update",        # pulls plugin code
    "provider_auth_continue",  # submits a code to a provider
    "provider_auth_start",   # opens a provider OAuth flow
    "skills_install",        # fetches a skill from a registry
    "skills_search",         # searches public skill registries
    "status_doctor",         # `hermes doctor` connectivity probes
}


@pytest.fixture(scope="module")
def reg():
    return build_registry()


def hints(tool) -> dict:
    return tool.descriptor()["annotations"]


# ---- the table itself -------------------------------------------------------------------------
def test_golden_covers_exactly_the_registry(reg):
    assert set(GOLDEN) == set(reg.tools), (
        "a tool was added or removed: review its annotations in hermcp/annotations.py, then "
        "regenerate tests/golden_tool_annotations.json with scripts/gen_tool_annotations.py")


def test_served_annotations_equal_the_golden_table(reg):
    for name, t in reg.tools.items():
        d = t.descriptor()
        assert d["title"] == GOLDEN[name]["title"], name
        assert d["annotations"] == GOLDEN[name]["annotations"], name


def test_every_tool_has_a_title_and_all_four_hints(reg):
    for name, t in reg.tools.items():
        ann = t.annotations
        assert ann is not None, name
        assert 3 <= len(ann.title) <= 60 and "  " not in ann.title, (name, ann.title)
        assert ann.title == ann.title.strip(), name
        payload = ann.as_dict()
        assert set(payload) == {"readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"}, name
        assert all(isinstance(v, bool) for v in payload.values()), name


def test_the_class_codes_are_valid_and_documented(reg):
    for name, t in reg.tools.items():
        assert t.annotations.code in A.CLASSES, name
        assert A.CLASSES[t.annotations.code] == (t.annotations.read_only, t.annotations.destructive,
                                                 t.annotations.idempotent, t.annotations.open_world), name


def test_no_override_is_dead_or_equal_to_its_default(reg):
    for name, code in A.OVERRIDES.items():
        assert name in reg.tools, f"override for an unknown tool: {name}"
        assert code in A.CLASSES, name
        default = "D" if reg.tools[name].dangerous else A.TIER_DEFAULT[reg.tools[name].access]
        assert code != default, f"{name}: override repeats the tier default {default}"
    for name in A.TITLE_OVERRIDES:
        assert name in reg.tools, f"title override for an unknown tool: {name}"


# ---- the rules that make the hints honest -----------------------------------------------------
def test_read_only_never_pairs_with_destructive(reg):
    for name, t in reg.tools.items():
        if t.annotations.read_only:
            assert not t.annotations.destructive, name


def test_read_only_tools_are_declared_read_and_cannot_mutate(reg):
    read_only = {n for n, t in reg.tools.items() if t.annotations.read_only}
    declared_read = {n for n, t in reg.tools.items() if t.access == "read"}
    assert read_only == (declared_read - set(READ_DECLARED_MUTATORS)) | ADMIN_DECLARED_READERS
    # the two admin-gated readers are the only non-read tier allowed to claim readOnly
    assert {n for n in ADMIN_DECLARED_READERS if reg.tools[n].access != "admin"} == set()


def test_read_declared_mutators_are_annotated_as_writes(reg):
    for name, code in READ_DECLARED_MUTATORS.items():
        t = reg.tools[name]
        assert t.access == "read", f"{name} was re-tiered; update this audit list"
        assert t.annotations.code == code, name
        assert not t.annotations.read_only, f"{name} is declared read but writes: readOnlyHint must be false"
    assert reg.tools["hermes_call"].annotations.destructive, "hermes_call dispatches admin tools: destructive"


def test_dangerous_tools_are_destructive_and_not_read_only(reg):
    for name, t in reg.tools.items():
        if t.dangerous:
            assert t.annotations.destructive and not t.annotations.read_only, name


def test_open_world_is_set_exactly_for_the_tools_with_own_network_io(reg):
    got = {n for n, t in reg.tools.items() if t.annotations.open_world}
    assert got == OPEN_WORLD, (sorted(got - OPEN_WORLD), sorted(OPEN_WORLD - got))


def test_names_and_description_tags_are_unchanged(reg):
    for name, t in reg.tools.items():
        tag = {"read": "", "write": " [write]", "admin": " [admin]"}[t.access] + (" [dangerous]" if t.dangerous else "")
        assert t.descriptor()["name"] == name
        assert t.descriptor()["description"] == f"{t.description}{tag}"


# ---- what a client actually receives ----------------------------------------------------------
def _client(happ, **scope):
    c = TestClient(happ.app)
    _, tok = happ.keystore.create(scope.pop("name", "k"), Scopes(**scope))
    return c, tok


def _tools_list(c, tok):
    r = c.post("/mcp", headers={"Authorization": f"Bearer {tok}"},
               json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    return r.json()["result"]["tools"]


def test_tools_list_serves_title_and_annotations(happ):
    c, tok = _client(happ, access="admin")
    listed = _tools_list(c, tok)
    assert listed
    for row in listed:
        assert row["title"], row["name"]
        assert row["annotations"] == GOLDEN[row["name"]]["annotations"], row["name"]
    assert any(row["annotations"]["readOnlyHint"] is False for row in listed)


def test_a_read_key_is_offered_only_read_only_tools_except_the_documented_two(happ):
    """`tools/list` varies per key, so a read key sees no claim of writing — with two exceptions,
    both annotated truthfully: `hermes_call` dispatches (destructive) and `inbox_ack` marks the
    key's own inbox events read. Neither can mutate anything else: the scope gate decides."""
    c, tok = _client(happ, access="read")
    listed = {row["name"]: row for row in _tools_list(c, tok)}
    writing = sorted(n for n, row in listed.items() if not row["annotations"]["readOnlyHint"])
    assert writing == ["hermes_call", "inbox_ack"], writing
    assert listed["hermes_call"]["annotations"]["destructiveHint"] is True
    assert listed["inbox_ack"]["annotations"]["idempotentHint"] is True
    assert "inbox_push" not in listed            # needs the explicit inbox_push scope
    assert "config_set" not in listed


def test_a_read_key_cannot_reach_a_write_tool_through_hermes_call(happ):
    """The hint that hermes_call is destructive is not the guard — the scope is."""
    c, tok = _client(happ, access="read")
    r = c.post("/mcp", headers={"Authorization": f"Bearer {tok}"},
               json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                     "params": {"name": "hermes_call",
                                "arguments": {"name": "config_set", "args": {"key": "a.b", "value": "x"}}}})
    body = json.loads(r.json()["result"]["content"][0]["text"])
    assert r.json()["result"]["isError"] and body["error"]["code"] == "forbidden"


def test_describe_and_search_carry_the_same_annotations(happ, reg):
    """The 95 non-advertised tools reach a client through describe/search — same data there."""
    c, tok = _client(happ, access="admin", approvals=True, dangerous=True, secrets_write=True, inbox_push=True)
    hidden = [t.name for t in reg.tools.values() if not (t.core or t.meta)]

    def call(tool_name, **args):
        r = c.post("/mcp", headers={"Authorization": f"Bearer {tok}"},
                   json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                         "params": {"name": tool_name, "arguments": args}})
        return json.loads(r.json()["result"]["content"][0]["text"])["result"]

    for name in hidden:
        d = call("hermes_describe_tool", name=name)
        assert d["title"] == GOLDEN[name]["title"], name
        assert d["annotations"] == GOLDEN[name]["annotations"], name
    rows = call("hermes_search_tools", query="", limit=100)["tools"]
    assert rows, "an empty query lists the catalog"
    for row in rows:
        assert row["title"] == GOLDEN[row["name"]]["title"], row["name"]
        assert row["annotations"] == GOLDEN[row["name"]]["annotations"], row["name"]


# ---- the doc table cannot drift ---------------------------------------------------------------
def test_docs_tool_annotations_is_in_sync(tmp_path):
    import importlib
    import sys

    plugin = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(plugin / "scripts"))
    try:
        gen = importlib.import_module("gen_tool_docs")
        out = gen.render_annotations(tmp_path / "tool-annotations.md")
    finally:
        sys.path.remove(str(plugin / "scripts"))
    assert out.read_text(encoding="utf-8") == (plugin / "docs" / "tool-annotations.md").read_text(encoding="utf-8"), \
        "docs/tool-annotations.md is stale: run `python -m hermcp docs`"
