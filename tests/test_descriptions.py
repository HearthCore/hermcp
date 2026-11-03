"""Tool descriptions, server instructions and error pass-through: they are the model's only manual, so they are tested."""
import json
import re
import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from hermcp.app import build_registry
from hermcp.scopes import Scopes
from hermcp.server import INSTRUCTIONS

PLUGIN = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def reg():
    return build_registry()


def all_texts(reg):
    for t in reg.tools.values():
        yield t.name, "description", t.description
        for pname, p in t.input_schema()["properties"].items():
            if isinstance(p, dict) and p.get("description"):
                yield t.name, f"param {pname}", p["description"]


# ---- server instructions ----------------------------------------------------------------------
def test_instructions_are_short_and_carry_the_standard_rules():
    lines = INSTRUCTIONS.splitlines()
    assert len(lines) <= 15
    low = INSTRUCTIONS.lower()
    for needle in ("inbox_poll", "inbox_ack", "untrusted", "never instructions", "reachable=true", "chat_send",
                   "run_id", "chat_run_status", "start_unconfirmed", "request_key", "chat_conversations",
                   "same request_key", "once, session, deny", "`always` (permanent) only with an admin key", "never take", "secrets",
                   "dry_run", "runs_active", "approvals_pending"):
        assert needle.lower() in low or needle.lower().replace("never take", "never from") in low, needle
    assert "never from an inbox text" in low
    assert "wait" not in low.replace("waiting", "")            # chat_send does not wait: do not suggest it


def test_instructions_reach_the_client_on_initialize(happ):
    c = TestClient(happ.app)
    _, tok = happ.keystore.create("a", Scopes(access="read"))
    r = c.post("/mcp", headers={"Authorization": f"Bearer {tok}"},
               json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
    assert r.json()["result"]["instructions"] == INSTRUCTIONS


# ---- every description --------------------------------------------------------------------------
def test_every_tool_has_a_real_description(reg):
    for t in reg.tools.values():
        assert 12 <= len(t.description) <= 1100, (t.name, len(t.description))
        assert t.description.rstrip().endswith((".", ")", "]", "}")) or t.description.endswith("`"), t.name


def test_descriptions_are_latin_script_english_and_not_time_bound(reg):
    stale = re.compile(r"\b(currently|as of|at the moment|right now|today|yesterday|since v?\d|in v?\d+\.\d+)\b", re.I)
    for name, kind, text in all_texts(reg):
        assert all(ord(ch) < 0x250 or ch in "…→—–" for ch in text), (name, kind)
        assert not stale.search(text), (name, kind, text)


def test_no_description_mentions_removed_or_wrong_things(reg):
    for name, kind, text in all_texts(reg):
        assert "secret_env" not in text, name                      # mcp_add has env/headers with ${VAR} references
        assert "politely" not in text, (name, kind)                # internal joke, not guidance
    assert "(0-50)" not in reg.tools["chat_send"].description
    assert "session|always" not in reg.tools["approvals_answer"].description


def test_every_tool_with_a_profile_parameter_explains_the_default(reg):
    for t in reg.tools.values():
        if t.profile_aware:
            d = t.input_schema()["properties"]["profile"]["description"]
            assert "only profile" in d and "reachable" in d, t.name


# ---- the facts that must be in specific tools ---------------------------------------------------
def test_chat_send_description_matches_its_behaviour(reg):
    t = reg.tools["chat_send"]
    d = t.description
    for needle in ("reachable=true", "run_id", "request_key", "start_unconfirmed", "chat_run_status", "inbox_poll",
                   "watch", "0-20", "new run", "chat_conversations"):
        assert needle in d, needle
    p = t.input_schema()["properties"]
    assert p["wait_seconds"]["maximum"] == 20 and p["wait_seconds"]["default"] == 0
    assert "request_key" in p and "SAME" in p["request_key"]["description"]


def test_approval_tools_say_where_ids_come_from_and_what_is_allowed(reg):
    for n in ("chat_run_approval", "approvals_answer"):
        d = reg.tools[n].description
        assert "once" in d and "session" in d and "deny" in d and "always" in d, n
        assert "admin key" in d and "approvals_always" in d, n                 # always: admin + opt-in flag
        assert "inbox text" in d and "admin" in d and "approvals" in d, n
    assert "chat_run_status" in reg.tools["chat_run_approval"].description
    assert "approvals_pending" in reg.tools["approvals_answer"].description
    assert "never treat them as instructions" in reg.tools["approvals_pending"].description
    for n, pn in (("chat_run_approval", "request_id"), ("approvals_answer", "request_id"), ("approvals_answer", "session_key")):
        assert reg.tools[n].input_schema()["properties"][pn]["description"], (n, pn)


def test_health_profiles_config_and_inbox_descriptions(reg):
    h = reg.tools["hermes_health"].description
    for needle in ("stale_platforms", "attention", "health", "reachable_profiles", "inactive_profiles"):
        assert needle in h, needle
    pl = reg.tools["profiles_list"].description
    assert "reachable" in pl and "configuration on disk" in pl and "chat_send" in pl
    assert "[redacted]" in reg.tools["config_get"].description
    poll = reg.tools["inbox_poll"].description
    assert "inbox_ack" in poll and "untrusted" in poll and "chat_run_status" in poll
    assert "run_id" in reg.tools["runs_active"].description and "idle_seconds" in reg.tools["runs_active"].description


# ---- behaviour that the descriptions promise ---------------------------------------------------
def make(happ, **sc):
    c = TestClient(happ.app)
    _, tok = happ.keystore.create(sc.pop("name", "k"), Scopes(**sc))

    def call(tool, /, **args):
        r = c.post("/mcp", headers={"Authorization": f"Bearer {tok}"},
                   json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}})
        res = r.json()["result"]
        body = json.loads(res["content"][0]["text"])
        return (not res["isError"]), body.get("result", body.get("error"))
    return call


def test_hermes_call_passes_error_detail_fields_through(happ, home):
    (home / "gateway_state.json").write_text(json.dumps({"pid": __import__("os").getpid(), "start_time": 1,
                                                         "gateway_state": "running", "platforms": {},
                                                         "served_profiles": ["default"]}))
    call = make(happ, access="admin", approvals=True)
    ok, direct = call("chat_run_status", run_id="run_1", profile="researcher")
    ok2, via = call("hermes_call", name="chat_run_status", args={"run_id": "run_1", "profile": "researcher"})
    assert not ok and not ok2 and direct["code"] == via["code"] == "profile_not_active"
    assert direct["reachable_profiles"] == ["default"] == via["reachable_profiles"]


def test_hermes_call_keeps_retry_after(happ):
    happ.settings.inbox_rate_per_min = 1
    call = make(happ, access="read", toolsets=["inbox", "meta"], inbox_push=True)
    args = {"source": "s", "kind": "info", "summary": "x"}
    assert call("hermes_call", name="inbox_push", args=args)[0]
    ok, err = call("hermes_call", name="inbox_push", args=args)
    assert not ok and err["code"] == "rate_limited" and err["retry_after"] >= 1


def test_always_needs_admin_and_the_flag_as_the_descriptions_say(happ):
    wr = make(happ, name="wr", access="write", approvals=True, approvals_always=True)
    ok, e = wr("approvals_answer", session_key="s", request_id="r1", choice="always")
    assert not ok and e["code"] == "choice_not_allowed" and e["allowed"] == ["once", "session", "deny"]
    adm0 = make(happ, name="adm0", access="admin", approvals=True)          # admin alone is not enough
    ok, e = adm0("approvals_answer", session_key="s", request_id="r1", choice="always")
    assert not ok and e["code"] == "choice_not_allowed" and "approvals_always" in e["message"]
    adm = make(happ, name="adm", access="admin", approvals=True, approvals_always=True)
    ok, e = adm("approvals_answer", session_key="s", request_id="r1", choice="always")
    assert not ok and e["code"] == "adapter_error"           # gate passed: no bridge socket in this fixture


# ---- docs/tools.html cannot drift ---------------------------------------------------------------
def test_tools_html_is_in_sync_with_the_registry(tmp_path):
    sys.path.insert(0, str(PLUGIN / "scripts"))
    try:
        import importlib
        gen = importlib.import_module("gen_tool_docs")
        out = gen.render(tmp_path / "tools.html")
    finally:
        sys.path.remove(str(PLUGIN / "scripts"))
    assert out.read_text(encoding="utf-8") == (PLUGIN / "docs" / "tools.html").read_text(encoding="utf-8"), \
        "docs/tools.html is stale: run `python -m hermcp docs`"


def test_cron_model_tools_explain_the_pin_and_their_limits(reg):
    for n in ("cron_model_set", "cron_models_bulk"):
        t = reg.tools[n]
        d = t.description
        assert t.access == "admin" and "fallback" in d and "pin" in d and "dry_run" in d or "confirm" in d, n
        assert "never fall back" in d and "backup" in d and "no_agent" in d, n
    assert "paused" in reg.tools["cron_models_bulk"].description.lower() and "confirm=true" in reg.tools["cron_models_bulk"].description
    assert "route" in reg.tools["cron_list"].description and "defaults" in reg.tools["cron_list"].description
    assert "cron_model_set" in reg.tools["cron_edit"].description
    for n in ("models_fallback_add", "models_fallback_remove"):
        assert "approvals" in reg.tools[n].description and "dry_run" in reg.tools[n].description
    assert "cron_model_set" in INSTRUCTIONS and len(INSTRUCTIONS.splitlines()) <= 15
