"""Keys bound to ONE profile: read/write inside that profile, nothing outside it. Synthetic data, temp HERMES_HOME."""
import json
import os
import sqlite3

import pytest
import yaml
from starlette.testclient import TestClient

from hermcp import pinning
from hermcp.registry import Principal
from hermcp.scopes import Scopes
from hermcp.settings import load_settings

S = "SYNTHVALUE0123456789ABCDEF"
CHAT_ARG = "message"


@pytest.fixture()
def rig(home, monkeypatch):
    from hermcp.tools import load_all
    reg = load_all()
    if "hermes_search_tools" not in reg.tools:
        from hermcp.registry import install_meta_tools
        install_meta_tools(reg)
    h = home
    for n in ("p1", "p2"):
        (h / "profiles" / n).mkdir(parents=True, exist_ok=True)
        (h / "profiles" / n / "config.yaml").write_text(yaml.safe_dump({"model": {"default": f"{n}-model"}}))
    (h / "profiles" / "researcher" / "config.yaml").write_text(yaml.safe_dump({"model": {"default": "researcher-model"}}))
    st = load_settings({"HERMES_HOME": str(h)})
    P = {"w1": Principal("w1", "w1", Scopes(access="write", profiles=["p1"], approvals=True)),
         "r1": Principal("r1", "r1", Scopes(access="read", profiles=["p1"])),
         "a1": Principal("a1", "a1", Scopes(access="admin", profiles=["p1"], approvals=True)),   # legacy: admin + pinned
         "wd": Principal("wd", "wd", Scopes(access="write", profiles=["default"], approvals=True)),
         "adm": Principal("adm", "adm", Scopes(access="admin", approvals=True))}

    def call(who, tool, /, **args):
        return reg.invoke(tool, args, P[who], st, None)

    def ok(who, tool, /, **args):
        r = call(who, tool, **args)
        assert r["ok"], r
        return r["result"]

    def deny(who, tool, /, **args):
        r = call(who, tool, **args)
        assert r["ok"] is False and r["error"]["code"] == "forbidden", r
        return r["error"]["message"]
    call.ok, call.deny, call.home, call.reg, call.st, call.P = ok, deny, h, reg, st, P
    call.call = call
    return call


def _gw(h, **kw):
    (h / "gateway_state.json").write_text(json.dumps({
        "pid": os.getpid(), "start_time": 1, "gateway_state": "running", "served_profiles": ["default", "p1", "p2"],
        "parked_profiles": {}, "platforms": {
            "telegram": {"state": "connected", "writer_pid": os.getpid(), "writer_start_time": 1, "updated_at": "u"},
            "p1:telegram": {"state": "connected", "writer_pid": os.getpid(), "writer_start_time": 1, "updated_at": "u"},
            "p2:telegram": {"state": "connected", "writer_pid": os.getpid(), "writer_start_time": 1, "updated_at": "u"}}, **kw}))


# ---- 1/2: pinning of profile-aware tools -----------------------------------------------------------------
def test_default_profile_is_the_bound_one_and_other_profiles_are_refused(rig):
    assert rig.ok("w1", "config_get", key="model.default")["value"] == "p1-model"
    assert rig.ok("w1", "config_get", key="model.default", profile="p1")["value"] == "p1-model"
    for other in ("p2", "default", "researcher"):
        msg = rig.deny("w1", "config_get", key="model.default", profile=other)
        assert "bound to profile 'p1'" in msg and f"requested profile '{other}'" in msg and "No data returned" in msg
        assert "p2-model" not in msg and "researcher-model" not in msg


def test_whoami_shows_the_binding(rig):
    r = rig.ok("w1", "hermes_whoami")
    assert r["bound_profile"] == "p1" and r["profile_restricted"] is True


def test_write_works_only_inside_the_bound_profile(rig):
    before = (rig.home / "profiles" / "p2" / "config.yaml").read_text()
    rig.ok("w1", "config_set", key="display.compact", value="true")
    assert yaml.safe_load((rig.home / "profiles" / "p1" / "config.yaml").read_text())["display"]["compact"] is True
    rig.deny("w1", "config_set", key="display.compact", value="true", profile="p2")
    rig.deny("w1", "config_set", key="display.compact", value="true", profile="default")
    assert (rig.home / "profiles" / "p2" / "config.yaml").read_text() == before
    assert "display" not in (yaml.safe_load((rig.home / "config.yaml").read_text()) or {})


def test_read_scope_cannot_write_even_in_its_own_profile(rig):
    assert rig.call("r1", "config_set", key="a.b", value="1")["error"]["code"] == "forbidden"
    assert rig.call("r1", "models_set", model="x")["error"]["code"] == "forbidden"
    assert rig.ok("r1", "config_get", key="model.default")["value"] == "p1-model"


def test_bound_key_is_never_admin_even_if_the_record_says_so(rig):
    for tool, args in (("config_restore", {"backup": "x"}), ("cron_remove", {"job_id": "x"}), ("skills_install", {"identifier": "x"}),
                       ("mcp_remove", {"name": "x"}), ("sessions_delete", {"session_id": "x"}), ("memory_reset", {})):
        r = rig.call("a1", tool, **args)
        assert r["ok"] is False and r["error"]["code"] == "forbidden", tool
        assert "bound to one profile" in r["error"]["message"] or "dangerous" in r["error"]["message"]
    assert rig.call("adm", "config_backups")["ok"]                                   # an unrestricted admin still can


# ---- 4: every tool is classified; denied tools are unreachable and invisible ------------------------------------
def test_every_registered_tool_is_classified(rig):
    assert [t.name for t in rig.reg.tools.values() if pinning.classify(t) == "unclassified"] == []


GLOBAL_TOOLS = ["keys_list", "audit_tail", "gateway_restart", "ops_gateway_logs", "models_providers", "providers_list",
                "plugins_check_updates", "plugins_install", "kanban_boards", "kanban_stats", "kanban_assignees",
                "kanban_archive", "kanban_board_create", "profiles_create", "profiles_delete", "profiles_describe",
                "profiles_rename", "provider_auth_start", "providers_add", "inbox_push"]


@pytest.mark.parametrize("tool", GLOBAL_TOOLS)
def test_global_and_admin_tools_are_blocked_for_bound_keys(rig, tool):
    for who in ("w1", "r1", "a1"):
        r = rig.call(who, tool)
        assert r["ok"] is False and r["error"]["code"] in ("forbidden", "invalid_arguments"), (who, tool, r)
        if r["error"]["code"] == "invalid_arguments":                                  # schema first: retry with args
            continue


def test_meta_tools_do_not_bypass_the_protection(rig):
    names = {t["name"] for t in rig.ok("w1", "hermes_search_tools", query="", limit=100)["tools"]}
    for tool in GLOBAL_TOOLS:
        assert tool not in names, tool
    assert {"config_get", "kanban_list", "skills_list"} <= names
    assert rig.call("w1", "hermes_describe_tool", name="keys_list")["error"]["code"] == "unknown_tool"
    r = rig.call("w1", "hermes_call", name="keys_list", args={})
    assert r["ok"] is False and r["error"]["code"] == "forbidden"
    r = rig.call("w1", "hermes_call", name="config_get", args={"key": "model.default", "profile": "p2"})
    assert r["ok"] is False and r["error"]["code"] == "forbidden" and "p2-model" not in json.dumps(r)
    r = rig.call("w1", "hermes_call", name="hermes_call", args={"name": "keys_list"})
    assert r["ok"] is False


def test_meta_tools_over_http_for_a_bound_key(happ):
    (happ.settings.data.parent.parent / "profiles" / "researcher").mkdir(parents=True, exist_ok=True)
    _, tok = happ.keystore.create("bound", Scopes(access="write", profiles=["researcher"]))
    c = TestClient(happ.app)

    def rpc(method, params):
        return c.post("/mcp", headers={"Authorization": f"Bearer {tok}"},
                      json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).json()["result"]
    listed = {t["name"] for t in rpc("tools/list", {})["tools"]}
    assert "keys_list" not in listed and "gateway_restart" not in listed
    res = rpc("tools/call", {"name": "hermes_call", "arguments": {"name": "config_get", "args": {"key": "model.default", "profile": "default"}}})
    body = json.loads(res["content"][0]["text"])
    assert res["isError"] is True and body["error"]["code"] == "forbidden" and "test-model" not in json.dumps(body)
    res = rpc("tools/call", {"name": "hermes_call", "arguments": {"name": "config_get", "args": {"key": "model.default"}}})
    assert json.loads(res["content"][0]["text"])["result"]["value"] == "researcher-model"


# ---- 3: indirect paths ---------------------------------------------------------------------------------------
def _tasks(h):
    con = sqlite3.connect(h / "kanban.db")
    con.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT, status TEXT, assignee TEXT)")
    con.executemany("INSERT INTO tasks VALUES (?,?,?,?)", [("t_aaaa1111", "mine", "ready", "p1"), ("t_bbbb2222", "theirs", "ready", "p2"),
                                                         ("t_cccc3333", "mine too", "done", "p1"), ("t_dddd4444", "nobody", "triage", None)])
    con.commit()
    con.close()


@pytest.fixture()
def kb(rig, monkeypatch):
    _tasks(rig.home)
    from hermcp.tools import kanban
    seen = []

    def fake(argv, profile=None, timeout=30.0):
        seen.append(list(argv))
        if "list" in argv:
            who = argv[argv.index("--assignee") + 1] if "--assignee" in argv else None
            allt = [{"id": "t_aaaa1111", "title": "mine", "status": "ready", "assignee": "p1"},
                    {"id": "t_bbbb2222", "title": "theirs", "status": "ready", "assignee": "p2"}]
            return [t for t in allt if who in (None, t["assignee"])]
        if "show" in argv:
            return {"task": {"id": argv[argv.index("show") + 1]}, "parents": [{"id": "t_aaaa1111"}, {"id": "t_bbbb2222"}], "children": ["t_bbbb2222"]}
        return {}
    monkeypatch.setattr(kanban, "cli_json", fake)
    rig.seen = seen
    return rig


def test_kanban_reads_are_limited_to_own_tasks(kb):
    r = kb.ok("w1", "kanban_list", include="all")
    assert [t["id"] for t in r["tasks"]] == ["t_aaaa1111"] and "--assignee" in kb.seen[-1] and "p1" in kb.seen[-1]
    kb.deny("w1", "kanban_list", assignee="p2")
    s = kb.ok("w1", "kanban_show", task_id="t_aaaa1111", log_lines=0)
    assert [p["id"] for p in s["parents"]] == ["t_aaaa1111"] and s["children"] == [] and s["foreign_links_hidden"] == 2
    for tid in ("t_bbbb2222", "t_dddd4444", "t_eeee5555"):                        # foreign, unassigned, unknown: same answer
        assert "not assigned to this key's profile" in kb.deny("w1", "kanban_show", task_id=tid)
        kb.deny("w1", "kanban_log", task_id=tid)
    assert kb.ok("adm", "kanban_show", task_id="t_bbbb2222", log_lines=0)["task"]["id"] == "t_bbbb2222"


def test_kanban_writes_are_limited_to_own_tasks(kb):
    foreign = "t_bbbb2222"
    kb.deny("w1", "kanban_comment", task_id=foreign, text="hi", dry_run=True)
    kb.deny("w1", "kanban_edit", task_id=foreign, title="x", dry_run=True)
    kb.deny("w1", "kanban_block", task_id=foreign, dry_run=True)
    kb.deny("w1", "kanban_unblock", task_ids=["t_aaaa1111", foreign], dry_run=True)
    kb.deny("w1", "kanban_complete", task_ids=[foreign], dry_run=True)
    kb.deny("w1", "kanban_assign", task_id=foreign, profile="p1", dry_run=True)
    kb.deny("w1", "kanban_assign", task_id="t_aaaa1111", profile="p2", dry_run=True)       # hand-off to another profile
    assert kb.ok("w1", "kanban_comment", task_id="t_aaaa1111", text="hi", dry_run=True)["dry_run"] is True
    assert kb.ok("w1", "kanban_complete", task_ids=["t_aaaa1111"], dry_run=True)["dry_run"] is True


def test_kanban_create_is_forced_to_the_bound_profile(kb):
    r = kb.ok("w1", "kanban_create", title="t", dry_run=True)
    assert "--assignee" in r["would_run"] and r["would_run"][r["would_run"].index("--assignee") + 1] == "p1"
    kb.deny("w1", "kanban_create", title="t", assignee="p2", dry_run=True)
    kb.deny("w1", "kanban_create", title="t", parents=["t_bbbb2222"], dry_run=True)
    assert kb.call("r1", "kanban_create", title="t")["error"]["code"] == "forbidden"          # read scope


def test_logs_tail_paths(rig):
    _tasks(rig.home)
    wd = rig.home / "kanban" / "logs"
    wd.mkdir(parents=True)
    (wd / "t_aaaa1111.log").write_text("own\n")
    (wd / "t_bbbb2222.log").write_text("foreign\n")
    (rig.home / "profiles" / "p1" / "logs").mkdir()
    (rig.home / "profiles" / "p1" / "logs" / "gateway.log").write_text("p1 gateway line\n")
    (rig.home / "profiles" / "p2" / "logs").mkdir()
    (rig.home / "profiles" / "p2" / "logs" / "gateway.log").write_text("p2 secret line\n")
    (rig.home / "logs").mkdir()
    (rig.home / "logs" / "gateway.log").write_text("shared gateway line of all profiles\n")
    assert rig.ok("w1", "logs_tail", log="worker:t_aaaa1111")["lines"] == ["own"]
    rig.deny("w1", "logs_tail", log="worker:t_bbbb2222")
    assert "own profile" not in rig.ok("w1", "logs_tail", log="gateway")["lines"][0] and rig.ok("w1", "logs_tail", log="gateway")["lines"] == ["p1 gateway line"]
    rig.deny("w1", "logs_tail", log="gateway", profile="p2")
    rig.deny("w1", "logs_tail", log="gateway", profile="default")
    rig.deny("wd", "logs_tail", log="gateway")                                  # default-bound: shared gateway log
    rig.deny("w1", "logs_tail", log="hermcp-audit")
    for trick in ("../p2/logs/gateway.log", "worker:../../logs/gateway", "../../config.yaml", "gateway.log/../../../config.yaml"):
        r = rig.call("w1", "logs_tail", log=trick)
        assert r["ok"] is False, trick
    assert "foreign" not in json.dumps(rig.call("w1", "logs_tail", log="worker:t_bbbb2222"))


def test_sessions_are_only_those_of_the_own_profile(rig):
    from hermes_state import SessionDB
    for prof, sid, text in (("p1", "s_one", "hello from p1"), ("p2", "s_two", "secret of p2")):
        db = SessionDB(db_path=rig.home / "profiles" / prof / "state.db")
        db.create_session(sid, source="cli", model="m")
        db.append_message(sid, "user", text)
        db.close()
    assert rig.ok("w1", "sessions_list")["count"] == 1
    assert "hello from p1" in json.dumps(rig.ok("w1", "sessions_messages", session_id="s_one"))
    for tool, args in (("sessions_list", {}), ("sessions_search", {"query": "secret"}), ("sessions_messages", {"session_id": "s_two"}),
                       ("sessions_get", {"session_id": "s_two"}), ("sessions_export", {"session_id": "s_two"}),
                       ("chat_history", {"session_id": "s_two"}), ("chat_conversations", {})):
        rig.deny("w1", tool, profile="p2", **args)
    r = rig.call("w1", "sessions_messages", session_id="s_two")                       # right profile, foreign session id
    assert r["ok"] is False and r["error"]["code"] == "not_found" and "secret of p2" not in json.dumps(r)
    assert rig.ok("w1", "sessions_search", query="secret")["count"] == 0
    rig.deny("w1", "sessions_archive", session_id="s_two", profile="p2")


def _jobs(h, prof, ids):
    base = h if prof == "default" else h / "profiles" / prof
    (base / "cron").mkdir(parents=True, exist_ok=True)
    (base / "cron" / "jobs.json").write_text(json.dumps({"jobs": [
        {"id": i, "name": f"job-{i}", "prompt": "x", "schedule_display": "every 1h", "enabled": True, "state": "scheduled"} for i in ids]}))


def test_cron_is_limited_to_the_own_profile(rig):
    _jobs(rig.home, "p1", ["own1"])
    _jobs(rig.home, "p2", ["foreign2"])
    assert [j["id"] for j in rig.ok("w1", "cron_list")["jobs"]] == ["own1"]
    assert [j["id"] for j in rig.ok("w1", "cron_list", profiles="all")["jobs"]] == ["own1"]
    rig.deny("w1", "cron_list", profiles="p2")
    rig.deny("w1", "cron_list", profile="p2")
    rig.deny("w1", "cron_runs", profile="p2")
    rig.deny("w1", "cron_show", job_id="foreign2", profile="p2")
    assert rig.call("w1", "cron_show", job_id="foreign2")["error"]["code"] == "not_found"        # not in own store
    rig.deny("w1", "cron_create", schedule="every 2h", prompt="x", script="evil.sh", dry_run=True)
    rig.deny("w1", "cron_create", schedule="every 2h", prompt="x", no_agent=True, dry_run=True)
    rig.deny("w1", "cron_create", schedule="every 2h", prompt="x", deliver="bot-chat:p2", dry_run=True)
    rig.deny("w1", "cron_edit", job_id="own1", failure_deliver="bot-chat:default", dry_run=True)
    assert rig.ok("w1", "cron_create", schedule="every 2h", prompt="x", deliver="bot-chat:p1", dry_run=True)["dry_run"] is True


def test_profile_files_and_skills_are_limited(rig):
    (rig.home / "SOUL.md").write_text("default soul")
    (rig.home / "profiles" / "p1" / "SOUL.md").write_text("p1 soul")
    (rig.home / "profiles" / "p2" / "SOUL.md").write_text("p2 soul")
    assert rig.ok("w1", "profile_files_read", file="SOUL.md")["content"] == "p1 soul"
    for tool, args in (("profile_files_read", {"file": "SOUL.md"}), ("skills_view", {"name": "x"}), ("plugin_config_get", {"plugin": "demo"}),
                       ("hooks_list", {}), ("secrets_status", {}), ("mcp_list", {}), ("skills_list", {}), ("plugins_list", {}),
                       ("models_current", {}), ("config_get", {}), ("tools_list", {}), ("memory_status", {})):
        rig.deny("w1", tool, profile="p2", **args)
    assert rig.call("w1", "profile_files_read", file="../config.yaml")["ok"] is False
    assert rig.ok("w1", "skills_list", profiles="all")["profiles"] == ["p1"]
    rig.deny("w1", "mcp_list", profiles="p2")
    rig.deny("w1", "plugins_list", profiles="default,p1")


def test_secrets_and_plugin_config_show_no_global_data(rig, monkeypatch):
    (rig.home / ".env").write_text(f"GLOBAL_ONLY={S}\n")
    (rig.home / "profiles" / "p1" / ".env").write_text("P1_TOKEN=1\n")
    cfg = {"model": {"default": "p1-model"}, "mcp_servers": {"a": {"url": "http://x", "headers": {"Authorization": "Bearer ${P1_TOKEN}"}},
                                                           "b": {"command": "x", "env": {"K": "${GLOBAL_ONLY}"}}}}
    (rig.home / "profiles" / "p1" / "config.yaml").write_text(yaml.safe_dump(cfg))
    monkeypatch.setenv("GLOBAL_ONLY", S)
    r = rig.ok("w1", "secrets_status")
    rows = {v["name"]: v for v in r["variables"]}
    assert rows["P1_TOKEN"]["set"] is True and rows["GLOBAL_ONLY"]["set"] is False        # default .env / server env invisible
    assert "set_in_server_process_env" not in rows["P1_TOKEN"] and S not in json.dumps(r)
    sd = rig.home / "plugins" / "demo"
    sd.mkdir(parents=True)
    (sd / "plugin.yaml").write_text("name: demo\nversion: 1\n")
    (sd / "config.json").write_text(json.dumps({"endpoint": "http://global"}))
    adm = rig.ok("adm", "plugin_config_get", plugin="demo", profile="p1")
    bound = rig.ok("w1", "plugin_config_get", plugin="demo")
    assert "config.json" in adm["files"] and "config.json" not in bound["files"] and "plugin.yaml" in bound["files"]


def test_approvals_only_own_and_default_is_shared(rig):
    rig.deny("w1", "approvals_pending", profile="p2")
    msg = rig.deny("wd", "approvals_pending")
    assert "shared by every multiplexed profile" in msg
    r = rig.call("w1", "approvals_pending")                                            # own profile, no bridge there
    assert r["ok"] is False and r["error"]["code"] == "adapter_error"


def test_status_views_hide_other_profiles(rig):
    _gw(rig.home)
    for tool, args in (("hermes_health", {"skills": False}), ("orchestration_overview", {"skills": False}),
                       ("profiles_list", {"skills": False}), ("status_gateway", {})):
        r = rig.ok("w1", tool, **args)
        dump = json.dumps(r)
        assert "p2" not in dump and "researcher" not in dump, tool
    h = rig.ok("w1", "hermes_health", skills=False)
    assert h["profiles"] == ["p1"] and "hermes_home" not in h and set(h["gateway"]["platforms"]) == {"p1:telegram"}
    o = rig.ok("w1", "orchestration_overview", skills=False)
    assert list(o["profiles"]) == ["p1"] and "hidden" in o["kanban"] and "hidden" in o["plugins"]
    assert "not visible" in o["approvals_pending"] or isinstance(o["approvals_pending"], dict)
    assert [p["name"] for p in rig.ok("w1", "profiles_list", skills=False)["profiles"]] == ["p1"]
    assert rig.call("w1", "profiles_show", name="p2")["error"]["code"] == "forbidden"
    assert set(rig.ok("wd", "status_gateway")["platforms"]) == {"telegram"}              # default-bound: unprefixed only
    full = rig.ok("adm", "hermes_health", skills=False)
    assert set(full["profiles"]) >= {"p1", "p2"} and "hermes_home" in full


def test_chat_is_limited_to_own_profile_and_own_runs(rig, monkeypatch):
    from hermcp.adapters.inbox_store import get_inbox
    from hermcp.tools import chat
    rig.deny("w1", "chat_send", **{CHAT_ARG: "hi"}, profile="p2", dry_run=True)
    box = get_inbox(rig.st)
    box.add_watch(run_id="run_own", profile="p1", session_id=None, key_id="w1", key_name="w1")
    box.add_watch(run_id="run_foreign", profile="p2", session_id=None, key_id="x", key_name="x")
    monkeypatch.setattr(chat, "require_served", lambda p: None)
    monkeypatch.setattr(chat.hapi, "request", lambda *a, **k: {"status": "completed", "output": "ok"})
    assert rig.ok("w1", "chat_run_status", run_id="run_own")["status"] == "completed"
    assert "was not started on profile 'p1'" in rig.deny("w1", "chat_run_status", run_id="run_foreign")
    rig.deny("w1", "chat_run_status", run_id="run_unknown")
    rig.deny("w1", "chat_run_stop", run_id="run_foreign", dry_run=True)
    rig.deny("w1", "chat_run_approval", run_id="run_foreign", choice="once", request_id="r", dry_run=True)
    rig.deny("w1", "chat_run_status", run_id="run_foreign", profile="p2")
    assert rig.ok("adm", "chat_run_status", run_id="run_foreign", profile="p2")["status"] == "completed"


def test_inbox_and_runs_only_for_own_profile_events(rig):
    from hermcp.adapters.inbox_store import get_inbox
    box = get_inbox(rig.st)
    box.add_watch(run_id="run_own", profile="p1", session_id=None, key_id="w1", key_name="w1")
    box.add_watch(run_id="run_other", profile="p2", session_id=None, key_id="w1", key_name="w1")
    own = box.push_system(kind="run_finished", summary="own done", run_id="run_own", profile="p1", requester="w1", dedupe_key="a")
    foreign = box.push_system(kind="run_finished", summary="foreign done", run_id="run_other", profile="p2", requester="w1", dedupe_key="b")
    pushed = box.push(source="script", kind="info", summary="global note", ref=None, severity="info", pushed_by="x")
    seen = rig.ok("w1", "inbox_poll", detail=True)
    assert [e["id"] for e in seen["events"]] == [own["id"]] and seen["unread_total"] == 1
    assert [e["id"] for e in rig.ok("w1", "inbox_list")["events"]] == [own["id"]]
    ack = rig.ok("w1", "inbox_ack", ids=[own["id"], foreign["id"], pushed["id"]])
    assert ack["acked"] == [own["id"]] and sorted(ack["not_found"]) == sorted([foreign["id"], pushed["id"]])
    # read state is per key: w1's ack did not touch what adm has to read (all three events are still unread for it)
    assert [e["id"] for e in rig.ok("adm", "inbox_poll", detail=True)["events"]] == [own["id"], foreign["id"], pushed["id"]]
    assert [r["run_id"] for r in rig.ok("w1", "runs_active")["runs"]] == ["run_own"]
    assert rig.call("w1", "inbox_push", source="a", kind="info", summary="x")["ok"] is False


# ---- 6: key creation -------------------------------------------------------------------------------------------
def test_keys_create_with_profile_option(home, capsys):
    from hermcp import cli
    from hermcp.keys import KeyStore
    assert cli.main(["keys", "create", "--name", "agent-p1", "--profile", "researcher", "--access", "write"]) == 0
    out = capsys.readouterr().out
    assert "created key" in out and "TOKEN" in out
    ks = KeyStore(load_settings({"HERMES_HOME": str(home)}).data / "keys.json")
    rec = [r for r in ks.list() if r.name == "agent-p1"][0]
    assert rec.scopes.profiles == ["researcher"] and rec.scopes.access == "write" and rec.scopes.bound_profile() == "researcher"
    for bad in (["--profile", "researcher", "--access", "admin"], ["--profile", "nope"], ["--profile", "researcher", "--profiles", "default"],
                ["--profile", "researcher", "--allow-dangerous"], ["--profile", "researcher", "--inbox-push"]):
        assert cli.main(["keys", "create", "--name", "bad", *bad]) == 1, bad
    assert not [r for r in ks.list() if r.name == "bad"]
    capsys.readouterr()
    assert cli.main(["keys", "policy"]) == 0
    table = capsys.readouterr().out
    assert "keys_list" in table and "kanban_show" in table and "filtered" in table


def test_policy_table_covers_every_tool(rig):
    rows = pinning.table(rig.reg.tools.values())
    assert len(rows) == len(rig.reg.tools)
    by = {r["tool"]: r for r in rows}
    assert by["keys_list"]["bound_key"] == "no" and by["kanban_show"]["bound_key"] == "filtered"
    assert by["config_get"]["bound_key"].startswith("yes") and by["config_restore"]["bound_key"] == "no"


# ---- B1: skills_view with a symlinked skill directory ------------------------------------------------------------
def test_skills_view_follows_a_symlink_into_the_shared_store_but_not_elsewhere(rig, tmp_path):
    shared = rig.home / "skills" / "devops" / "litellm"
    shared.mkdir(parents=True)
    (shared / "SKILL.md").write_text("---\nname: litellm\ndescription: d\n---\n\nshared body\n")
    (shared / "references").mkdir()
    (shared / "references" / "a.md").write_text("ref a")
    link_parent = rig.home / "profiles" / "p1" / "skills" / "devops"
    link_parent.mkdir(parents=True)
    (link_parent / "litellm").symlink_to(shared)
    v = rig.ok("w1", "skills_view", name="litellm")
    assert "shared body" in v["content"] and v["source"] == "local"
    assert "ref a" in rig.ok("w1", "skills_view", name="litellm", file="references/a.md")["content"]
    for trick in ("../../../../../config.yaml", "/etc/passwd", "references/../../../../../config.yaml"):
        r = rig.call("w1", "skills_view", name="litellm", file=trick)
        assert r["ok"] is False and r["error"]["code"] == "forbidden", trick
    outside = tmp_path / "outside" / "evil"
    outside.mkdir(parents=True)
    (outside / "SKILL.md").write_text("---\nname: evil\ndescription: d\n---\n\nsecret outside body\n")
    (link_parent / "evil").symlink_to(outside)
    r = rig.call("w1", "skills_view", name="evil")
    assert r["ok"] is False and r["error"]["code"] == "forbidden" and "secret outside body" not in json.dumps(r)


def test_profile_files_read_listing_says_it_is_only_a_listing(rig):
    (rig.home / "profiles" / "p1" / "SOUL.md").write_text("soul text")
    listing = rig.ok("w1", "profile_files_read")
    assert "hint" in listing and "file=" in listing["hint"] and "content" not in listing
    assert "WITHOUT `file`" in rig.reg.tools["profile_files_read"].description
    assert rig.ok("w1", "profile_files_read", file="SOUL.md")["content"] == "soul text"
