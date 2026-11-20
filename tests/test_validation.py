"""The stdlib JSON-Schema subset, held to JSON Schema's `pattern` semantics.

Why this file exists: `pattern` is ECMA-262 in JSON Schema, but `re.search` treats `$` as "end of
string **or** just before a trailing newline". Every anchored tool-schema pattern therefore
accepted its value plus a newline, and `secret_set` wrote a broken `.env` line (#88).
"""
import json

import pytest
from starlette.testclient import TestClient

from hermcp.scopes import Scopes
from hermcp.validation import ValidationError, validate


@pytest.fixture()
def call(happ):
    """call(tool, **args) -> (ok, payload) through the real MCP endpoint."""
    client = TestClient(happ.app)
    _, token = happ.keystore.create("t", Scopes(access="write", secrets_write=True))

    def _call(tool, /, **args):
        r = client.post("/mcp", headers={"Authorization": f"Bearer {token}"},
                        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                              "params": {"name": tool, "arguments": args}})
        res = r.json()["result"]
        body = json.loads(res["content"][0]["text"])
        return (not res["isError"]), body.get("result", body.get("error"))
    return _call


# ---- the end anchor ----------------------------------------------------------------------
@pytest.mark.parametrize("pattern, value", [
    ("^[A-Z][A-Z0-9_]{2,64}$", "ACME_TOKEN\n"),          # secret_set / providers NAME
    ("^[a-z0-9][a-z0-9_-]{0,63}$", "researcher\n"),      # profiles NAME
    ("^[A-Za-z0-9_.:-]{1,80}$", "sess_1\n"),             # chat CLIENT_SESSION
    ("^[0-9a-f]{40}$", "a" * 40 + "\n"),                 # plugins_ts ref
])
def test_anchored_pattern_rejects_a_trailing_newline(pattern, value):
    """#88: `$` is end-of-input in ECMA-262; Python's also matches before a final `\\n`."""
    with pytest.raises(ValidationError, match="does not match the required pattern"):
        validate(value, {"type": "string", "pattern": pattern})


@pytest.mark.parametrize("pattern, value", [
    ("^[A-Z][A-Z0-9_]{2,64}$", "ACME_TOKEN"),
    ("^[a-z0-9][a-z0-9_-]{0,63}$", "researcher"),
    ("^[A-Za-z0-9_.:-]{1,80}$", "sess_1"),
    ("^[0-9a-f]{40}$", "a" * 40),
])
def test_anchored_pattern_still_accepts_the_plain_value(pattern, value):
    validate(value, {"type": "string", "pattern": pattern})


def test_a_dollar_inside_a_character_class_stays_a_literal():
    """`[$]` is a literal dollar sign in both dialects, so it must not become an anchor."""
    schema = {"type": "string", "pattern": "^[a-z$]+$"}
    validate("a$b", schema)
    with pytest.raises(ValidationError):
        validate("a$b\n", schema)


def test_an_escaped_dollar_stays_a_literal():
    schema = {"type": "string", "pattern": r"^a\$b$"}
    validate("a$b", schema)
    with pytest.raises(ValidationError):
        validate("a$b\n", schema)


def test_an_unanchored_alternative_still_matches():
    """Only `$` is rewritten, so a pattern with an unanchored branch keeps ECMA-262's answer.

    "Reject when the match ends one character before the end" would wrongly refuse this: the
    leftmost match is the anchored branch, but the unanchored one accepts the value.
    """
    validate("AB\n", {"type": "string", "pattern": "^[A-Z]+$|^[A-Z]+"})


def test_a_multiline_pattern_keeps_pythons_dollar():
    """`(?m)` means "end of line" in both dialects, so `$` is left alone there."""
    validate("a\nb", {"type": "string", "pattern": "(?m)^b$"})


# ---- the rest of the pattern contract ----------------------------------------------------
def test_pattern_is_a_search_not_a_full_match():
    validate("prefix-abc-suffix", {"type": "string", "pattern": "abc"})


def test_a_broken_pattern_never_blocks_input():
    """A pattern that does not compile must not reject every value."""
    validate("anything", {"type": "string", "pattern": "([unclosed"})


def test_a_tool_rejects_a_trailing_newline_in_an_id(call):
    """The operator-visible half of #88: `secret_set` accepted `ACME_TOKEN\\n` and wrote it."""
    ok, r = call("secret_set", name="ACME_TOKEN\n", value="placeholder", dry_run=True)
    assert not ok and r["code"] == "invalid_arguments"
    assert r["path"] == ["name"] and "pattern" in r["message"]
