"""HERMCP_PROFILES_IGNORE: a host-wide ignore list for profiles. It is NOT a key scope."""
import json
import sqlite3

import pytest
from starlette.testclient import TestClient

from hermcp import doctor
from hermcp import profile_keys as pk
from hermcp.adapters.common import list_profile_names, profile_home
from hermcp.keys import Scopes
from hermcp.registry import ToolError

IGNORED = ["worker-a", "worker-b"]


@pytest.fixture()
def workers(home):
    for n in IGNORED:
        (home / "profiles" / n).mkdir()
        (home / "profiles" / n / "config.yaml").write_text("model:\n  default: m\n")
    return IGNORED


@pytest.fixture()
def ignore(workers, monkeypatch):
    monkeypatch.setenv("HERMCP_PROFILES_IGNORE", ",".join(workers))
    return workers


def test_unset_changes_nothing(workers, monkeypatch):
    monkeypatch.delenv("HERMCP_PROFILES_IGNORE", raising=False)
    assert pk.known_profiles() == ["default", "researcher", "worker-a", "worker-b"]
    assert list_profile_names() == ["default", "researcher", "worker-a", "worker-b"]


def test_names_are_left_out_of_both_listings(ignore):
    assert pk.known_profiles() == ["default", "researcher"]
    assert list_profile_names() == ["default", "researcher"]


def test_whitespace_unknown_names_and_default_are_harmless(workers, monkeypatch):
    monkeypatch.setenv("HERMCP_PROFILES_IGNORE", " worker-a , nope ,, default ")
    assert pk.known_profiles() == ["default", "researcher", "worker-b"]
    assert list_profile_names() == ["default", "researcher", "worker-b"]


def test_profile_home_rejects_an_ignored_profile(ignore):
    with pytest.raises(ToolError) as e:
        profile_home("worker-a")
    assert e.value.code == "profile_ignored"
    assert profile_home("researcher").name == "researcher"


def test_doctor_prints_no_key_line_for_ignored_profiles(ignore, home, monkeypatch, capsys):
    monkeypatch.setenv("HERMCP_HERMES_AGENT_DIR", str(home))
    doctor.run()
    out = capsys.readouterr().out
    assert "api_server key [researcher]" in out
    assert "worker-a" not in out and "worker-b" not in out


def test_doctor_still_lists_them_without_the_variable(workers, home, monkeypatch, capsys):
    monkeypatch.delenv("HERMCP_PROFILES_IGNORE", raising=False)
    monkeypatch.setenv("HERMCP_HERMES_AGENT_DIR", str(home))
    doctor.run()
    out = capsys.readouterr().out
    assert "api_server key [worker-a]" in out and "api_server key [worker-b]" in out


@pytest.fixture()
def call(happ, home, monkeypatch):
    from hermcp.tools import profiles as profiles_tools
    # profile metadata comes from Hermes itself, which is not importable in a plain checkout
    monkeypatch.setattr(profiles_tools, "_meta", lambda name: {})
    c = TestClient(happ.app)
    _, token = happ.keystore.create("t", Scopes(access="admin", approvals=True, secrets_write=True, dangerous=True))

    def _call(tool, /, **args):
        r = c.post("/mcp", headers={"Authorization": f"Bearer {token}"},
                   json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}})
        res = r.json()["result"]
        body = json.loads(res["content"][0]["text"])
        return (not res["isError"]), body.get("result", body.get("error"))
    return _call


def test_profiles_list_returns_only_the_others(ignore, call):
    ok, r = call("profiles_list", detail=False)
    assert ok, r
    assert [p["name"] for p in r["profiles"]] == ["default", "researcher"] and r["count"] == 2


def test_status_tools_do_not_count_them(ignore, call):
    ok, r = call("hermes_status")
    assert ok
    blob = json.dumps(r)
    assert "worker-a" not in blob and "worker-b" not in blob


def test_profile_targeted_tools_reject_with_profile_ignored(ignore, call):
    for tool, args in [("chat_send", {"message": "hi"}), ("chat_send", {"message": "hi", "dry_run": True}),
                       ("chat_conversations", {}), ("config_get", {}), ("sessions_list", {})]:
        ok, r = call(tool, profile="worker-a", **args)
        assert not ok and r["code"] == "profile_ignored", tool
    for tool, args in [("profiles_show", {"name": "worker-b"}), ("profiles_describe", {"name": "worker-b", "text": "x"})]:
        ok, r = call(tool, **args)
        assert not ok and r["code"] == "profile_ignored", tool


def test_other_profiles_still_work(ignore, call):
    ok, r = call("chat_send", message="hi", dry_run=True, profile="researcher")
    assert ok


def test_ignore_is_not_a_scope_kanban_stays_visible_to_an_unrestricted_key(ignore, call, home):
    con = sqlite3.connect(home / "kanban.db")
    con.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY, status TEXT)")
    con.execute("INSERT INTO tasks (status) VALUES ('todo')")
    con.commit()
    con.close()
    ok, r = call("orchestration_overview")
    assert ok
    assert "hidden" not in r["kanban"] and [b["slug"] for b in r["kanban"]["boards"]] == ["default"]
    blob = json.dumps(r)
    assert "worker-a" not in blob and "worker-b" not in blob
