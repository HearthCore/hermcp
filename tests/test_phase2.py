"""Phase 2 toolsets against a temporary HERMES_HOME (never the real one)."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
import yaml
from starlette.testclient import TestClient

from hermcp.scopes import Scopes


@pytest.fixture()
def call(happ):
    """call(tool, key_scopes=…)(args) -> (ok, payload)"""
    c = TestClient(happ.app)
    _, token = happ.keystore.create("t", Scopes(access="admin", approvals=True, secrets_write=True, dangerous=True))

    def _call(tool_name, /, **args):
        r = c.post("/mcp", headers={"Authorization": f"Bearer {token}"},
                   json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool_name, "arguments": args}})
        res = r.json()["result"]
        body = json.loads(res["content"][0]["text"])
        return (not res["isError"]), body.get("result", body.get("error"))
    _call.home = happ.keystore.path.parent.parent.parent
    return _call


def cfg(call):
    return yaml.safe_load((call.home / "config.yaml").read_text())


# ---- config ------------------------------------------------------------------------------
def test_config_get_and_missing(call):
    ok, r = call("config_get", key="model.default")
    assert ok and r["value"] == "test-model"
    ok, r = call("config_get", key="nope.nothing")
    assert ok and r["exists"] is False


def test_config_set_dry_run_changes_nothing(call):
    before = (call.home / "config.yaml").read_text()
    ok, r = call("config_set", key="display.skin", value="mono", dry_run=True)
    assert ok and r["dry_run"] and r["applied"] is False and r["changes"] == [{"path": "display.skin", "change": "changed", "before": r["changes"][0].get("before"), "after": "mono"}]
    assert (call.home / "config.yaml").read_text() == before


def test_config_set_applies_with_backup_and_coercion(call):
    ok, r = call("config_set", key="agent.max_turns", value="42")
    assert ok and r["applied"] and r["backup"]
    assert cfg(call)["agent"]["max_turns"] == 42            # coerced to int
    ok, b = call("config_backups")
    assert ok and any(x["name"] == r["backup"] for x in b["backups"])
    # restore returns to the previous file
    ok, rr = call("config_restore", backup=r["backup"])
    assert ok and "max_turns" not in (cfg(call).get("agent") or {})


def test_config_set_refuses_env_style_names(call):
    ok, r = call("config_set", key="OPENAI_API_KEY", value="sk-should-not-be-written")
    assert not ok and r["code"] == "rejected"
    assert "sk-should" not in (call.home / "config.yaml").read_text()
    assert not (call.home / ".env").exists()


def test_config_get_masks_secrets(call):
    (call.home / "config.yaml").write_text("model:\n  default: m\nauxiliary:\n  vision:\n    api_key: sk-abcdefghijklmnop1234\n")
    ok, r = call("config_get", key="auxiliary")
    assert ok and "abcdefghijklmnop" not in json.dumps(r)


def test_config_unset(call):
    call("config_set", key="agent.max_turns", value="7")
    ok, r = call("config_unset", key="agent.max_turns", dry_run=True)
    assert ok and r["dry_run"]
    assert cfg(call)["agent"]["max_turns"] == 7
    ok, r = call("config_unset", key="agent.max_turns")
    assert ok and "max_turns" not in (cfg(call).get("agent") or {})


def test_config_check(call):
    ok, r = call("config_check", key="model.default")
    assert ok and r["ok"] and r["key"]["known"]


def test_profile_scoped_config(call):
    ok, r = call("config_set", key="model.default", value="researcher-2", profile="researcher")
    assert ok
    assert yaml.safe_load((call.home / "profiles/researcher/config.yaml").read_text())["model"]["default"] == "researcher-2"
    assert cfg(call)["model"]["default"] == "test-model"
    ok, r = call("config_get", key="model.default", profile="nonexistent")
    assert not ok and r["code"] == "not_found"


# ---- models ------------------------------------------------------------------------------
def test_models_fallback_roundtrip(call):
    ok, r = call("models_fallback_add", provider="litellm", model="m1", dry_run=True)
    assert ok and not r["applied"] and {c["path"]: c.get("after") for c in r["changes"]} == {
        "fallback_providers[0].provider": "litellm", "fallback_providers[0].model": "m1"}
    assert "fallback_providers" not in cfg(call)
    ok, r = call("models_fallback_add", provider="litellm", model="m1")
    assert ok and r["applied"]
    ok, r = call("models_fallback_add", provider="anthropic", model="m2", position=0)
    ok, r = call("models_fallbacks")
    assert [e["model"] for e in r["fallbacks"]] == ["m2", "m1"]
    ok, r = call("models_fallback_remove", provider="litellm", model="m1")
    assert ok and [e["model"] for e in cfg(call)["fallback_providers"]] == ["m2"]
    ok, r = call("models_fallback_remove", clear=True)
    assert ok and not cfg(call).get("fallback_providers")


def test_models_set_and_alias(call):
    ok, r = call("models_set", model="new-model", dry_run=True)
    assert ok and r["dry_run"] and cfg(call)["model"]["default"] == "test-model"
    ok, r = call("models_set", model="new-model")
    assert ok and cfg(call)["model"]["default"] == "new-model"
    ok, r = call("models_alias_set", alias="fast", target="litellm/x")
    assert ok and cfg(call)["model"]["aliases"]["fast"] == "litellm/x"
    ok, r = call("models_current")
    assert r["aliases"]["fast"] == "litellm/x"
    ok, r = call("models_alias_remove", alias="fast")
    assert ok and "fast" not in cfg(call)["model"].get("aliases", {})


def test_aux_api_key_must_be_env_ref(call):
    ok, r = call("models_auxiliary_set", task="vision", field="api_key", value="sk-literal-secret-1234")
    assert not ok and r["code"] == "rejected"


# ---- sessions ----------------------------------------------------------------------------
@pytest.fixture()
def sess(call):
    from hermcp.adapters.common import ensure_hermes_importable
    ensure_hermes_importable()
    from hermes_state import SessionDB
    db = SessionDB(db_path=call.home / "state.db")
    db.create_session("20260101_000000_abcd1234", "telegram", model="m")
    db.append_message("20260101_000000_abcd1234", "user", "hello hermcp world")
    db.append_message("20260101_000000_abcd1234", "assistant", "hi there")
    db.close()
    return "20260101_000000_abcd1234"


def test_sessions_read_tools(call, sess):
    ok, r = call("sessions_list")
    assert ok and any(s["id"] == sess for s in r["sessions"])
    ok, r = call("sessions_messages", session_id=sess[:12])
    assert ok and [m["role"] for m in r["messages"]] == ["user", "assistant"]
    ok, r = call("sessions_search", query="hermcp")
    assert ok and r["count"] >= 1
    ok, r = call("sessions_export", session_id=sess)
    assert ok and r["message_count"] == 2
    ok, r = call("sessions_get", session_id="does-not-exist")
    assert not ok and r["code"] == "not_found"


def test_sessions_write_tools(call, sess):
    ok, r = call("sessions_rename", session_id=sess, title="My title", dry_run=True)
    assert ok and r["dry_run"] and r["after"] == "My title"
    assert call("sessions_get", session_id=sess)[1].get("title") in (None, "")
    ok, r = call("sessions_rename", session_id=sess, title="My title")
    assert ok and call("sessions_get", session_id=sess)[1]["title"] == "My title"
    ok, r = call("sessions_pin", session_id=sess, pinned=True)
    assert ok
    ok, r = call("sessions_archive", session_id=sess, archived=True)
    assert ok
    ok, r = call("sessions_list")
    assert all(s["id"] != sess for s in r["sessions"])                # archived hidden by default
    ok, r = call("sessions_list", archived="include")
    assert any(s["id"] == sess for s in r["sessions"])
    ok, r = call("sessions_delete", session_id=sess, dry_run=True)
    assert ok and sess in r["would_delete"]
    assert call("sessions_get", session_id=sess)[0]
    ok, r = call("sessions_delete", session_id=sess)
    assert ok and r["deleted"]
    assert not call("sessions_get", session_id=sess)[0]


# ---- mcp ---------------------------------------------------------------------------------
def test_mcp_add_enable_remove(call):
    ok, r = call("mcp_add", name="demo", url="https://example.invalid/mcp", headers={"Authorization": "literal-secret"})
    assert not ok and r["code"] == "rejected"
    ok, r = call("mcp_add", name="demo", url="https://example.invalid/mcp", headers={"Authorization": "${DEMO_TOKEN}"}, dry_run=True)
    assert ok and not r["applied"] and "mcp_servers" not in cfg(call)
    ok, r = call("mcp_add", name="demo", url="https://example.invalid/mcp", headers={"Authorization": "${DEMO_TOKEN}"})
    assert ok and cfg(call)["mcp_servers"]["demo"]["url"] == "https://example.invalid/mcp"
    ok, r = call("mcp_list", detail=True)
    assert r["servers"]["demo"]["headers"] == {"Authorization": "${DEMO_TOKEN}"}      # references are not secrets
    assert call("mcp_add", name="demo", url="https://x.invalid")[1]["code"] == "conflict"
    ok, r = call("mcp_disable", name="demo")
    assert ok and cfg(call)["mcp_servers"]["demo"]["enabled"] is False
    ok, r = call("mcp_remove", name="demo")
    assert ok and "demo" not in (cfg(call).get("mcp_servers") or {})


# ---- skills ------------------------------------------------------------------------------
def test_skills_toggle(call):
    d = call.home / "skills" / "testcat" / "hermcp-demo"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: hermcp-demo\ndescription: demo skill for tests\n---\n# Demo\nbody\n")
    ok, r = call("skills_list", query="hermcp-demo")
    assert ok and r["skills"][0]["enabled"] is True
    ok, r = call("skills_view", name="hermcp-demo")
    assert ok and "# Demo" in r["content"]
    ok, r = call("skills_disable", names=["hermcp-demo"], dry_run=True)
    assert ok and not r["applied"]
    assert "hermcp-demo" not in str(cfg(call).get("skills", {}).get("disabled", []))
    ok, r = call("skills_disable", names=["hermcp-demo"])
    assert ok and "hermcp-demo" in cfg(call)["skills"]["disabled"]
    assert call("skills_list", query="hermcp-demo", include="all")[1]["skills"][0]["enabled"] is False
    ok, r = call("skills_enable", names=["hermcp-demo"])
    assert ok and "hermcp-demo" not in cfg(call)["skills"]["disabled"]
    assert call("skills_disable", names=["nonexistent-skill"])[1]["code"] == "not_found"


# ---- CLI-backed toolsets (subprocess against the temp home) --------------------------------
def test_dry_run_shows_command_without_running(call):
    ok, r = call("cron_create", schedule="every 2h", prompt="p", name="x", dry_run=True)
    assert ok and r["would_run"][:3] == ["hermes", "cron", "create"] and not (call.home / "cron" / "jobs.json").exists()
    ok, r = call("tools_disable", names=["web"], platform="cli", dry_run=True)
    assert ok and r["would_run"][-4:] == ["disable", "web", "--platform", "cli"] or "web" in r["would_run"]
    ok, r = call("plugins_install", identifier="owner/repo", dry_run=True)
    assert ok and "--no-enable" in r["would_run"] and "--enable" not in r["would_run"]
    assert not call("plugins_install", identifier="--evil")[0]


def test_cron_lifecycle(call, cli_log):
    # reads import cron.jobs against the temp store; writes go through the (fake) CLI
    cron = call.home / "cron"
    cron.mkdir(exist_ok=True)
    (cron / "jobs.json").write_text(json.dumps({"jobs": [
        {"id": "abc123", "name": "hermcp-test", "prompt": "say hi", "schedule": {"kind": "interval", "minutes": 120},
         "schedule_display": "every 2h", "enabled": True, "state": "scheduled"}]}))
    ok, r = call("cron_list")
    assert ok and r["count"] == 1 and r["jobs"][0]["name"] == "hermcp-test"
    ok, r = call("cron_show", job_id="abc123")
    assert ok and r["id"] == "abc123"
    assert call("cron_show", job_id="nope")[1]["code"] == "not_found"
    ok, r = call("cron_create", schedule="every 2h", prompt="say hi", name="n", deliver="local", skills=["a", "b"], paused=True)
    assert ok and r["applied"]
    assert cli_log()[-1]["argv"] == ["cron", "create", "every 2h", "say hi", "--name", "n", "--deliver", "local",
                                     "--skill", "a", "--skill", "b", "--paused"]
    call("cron_edit", job_id="abc123", name="renamed", no_agent=False, script="")
    assert cli_log()[-1]["argv"] == ["cron", "edit", "abc123", "--agent", "--name", "renamed", "--script", ""]
    assert call("cron_edit", job_id="abc123")[1]["code"] == "invalid_arguments"
    call("cron_pause", job_id="abc123")
    call("cron_resume", job_id="abc123", run_now=True)
    assert cli_log()[-1]["argv"] == ["cron", "resume", "abc123", "--run-now"]
    call("cron_remove", job_id="abc123")
    assert cli_log()[-1]["argv"] == ["cron", "remove", "abc123"]
    ok, r = call("cron_run", job_id="abc123", profile="researcher")
    assert cli_log()[-1]["argv"] == ["-p", "researcher", "cron", "run", "abc123"]


def test_kanban_argv_and_stdin(call, cli_log):
    ok, r = call("kanban_create", title="hermcp test task", body="multi\nline body", priority=1, board="myboard",
                 assignee="researcher", parents=["t_1", "t_2"])
    assert ok, r
    last = cli_log()[-1]
    assert last["argv"][:4] == ["kanban", "--board", "myboard", "create"]
    assert "--body-file" in last["argv"] and last["argv"][last["argv"].index("--body-file") + 1] == "-"
    assert last["stdin"] == "multi\nline body"
    assert last["argv"].count("--parent") == 2 and "--json" in last["argv"]
    assert "hermcp:t" in last["argv"]                       # author attribution = key name
    call("kanban_comment", task_id="t_1", text="- starts with dash")
    assert cli_log()[-1]["argv"][-2:] == ["--", "- starts with dash"]
    call("kanban_block", task_id="t_1", reason="waiting", kind="needs_input")
    assert cli_log()[-1]["argv"] == ["kanban", "block", "t_1", "--kind", "needs_input", "--", "waiting"]
    call("kanban_unblock", task_ids=["t_1", "t_2"])
    assert cli_log()[-1]["argv"] == ["kanban", "unblock", "t_1", "t_2"]
    call("kanban_complete", task_ids=["t_1"], result="done")
    assert cli_log()[-1]["argv"] == ["kanban", "complete", "t_1", "--result", "done"]
    n = len(cli_log())
    ok, r = call("kanban_assign", task_id="t_1", profile="researcher", dry_run=True)
    assert ok and r["dry_run"] and len(cli_log()) == n        # dry run never invokes the CLI
    assert not call("kanban_show", task_id="t_1; rm -rf /")[0]  # id pattern rejects shell-ish input
    assert call("kanban_list", status="bogus")[1]["code"] == "invalid_arguments"


def test_cli_failure_is_reported(call, monkeypatch):
    monkeypatch.setenv("FAKE_HERMES_FAIL", "boom: no such task")
    ok, r = call("kanban_complete", task_ids=["t_9"])
    assert not ok and r["code"] == "cli_error" and "boom" in r["message"]


def test_profiles_tools(call, cli_log):
    ok, r = call("profiles_list")
    assert {p["name"] for p in r["profiles"]} >= {"default", "researcher"}
    assert call("profiles_delete", name="default", dry_run=True)[1]["code"] == "rejected"
    ok, r = call("profiles_create", name="scratchy", clone_from="researcher", description="temp", no_skills=True)
    assert ok and cli_log()[-1]["argv"] == ["profile", "create", "scratchy", "--no-alias", "--clone-from", "researcher",
                                            "--description", "temp", "--no-skills"]
    ok, r = call("profiles_delete", name="researcher", dry_run=True)
    assert ok and r["dry_run"] and (call.home / "profiles" / "researcher").exists()
    call("profiles_delete", name="researcher")
    assert cli_log()[-1]["argv"] == ["profile", "delete", "researcher", "--yes"]
    call("profiles_rename", old_name="a", new_name="b")
    assert cli_log()[-1]["argv"] == ["profile", "rename", "a", "b"]
    assert not call("profiles_create", name="Bad Name!")[0]


def test_status_tools(call):
    ok, r = call("status_gateway")
    assert ok and r["state"] == "unknown"
    ok, r = call("hermes_health")
    assert ok and r["hermes_version"] == "Hermes Agent v0.0.0-fake" and "researcher" in r["profiles"]


# ---- chat (fake api_server) ----------------------------------------------------------------
class _Fake(BaseHTTPRequestHandler):
    seen = []

    def _send(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        _Fake.seen.append((self.path, self.headers.get("Authorization"), body))
        self._send({"run_id": "run_1", "status": "started"})

    def do_GET(self):
        _Fake.seen.append((self.path, self.headers.get("Authorization"), None))
        self._send({"run_id": "run_1", "status": "completed", "output": "pong"})

    def log_message(self, *a):
        pass


def test_chat_send_against_fake_api(call, monkeypatch, happ):
    srv = HTTPServer(("127.0.0.1", 0), _Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    happ.settings.hermes_api_url = f"http://127.0.0.1:{srv.server_port}"
    happ.settings.hermes_api_key = "apikey-xyz"
    try:
        ok, r = call("chat_send", message="ping", wait_seconds=5, profile="researcher")
        assert ok and r["output"] == "pong" and r["status"] == "completed" and r["run_id"] == "run_1" and r["request_key"]
        assert _Fake.seen[0][0] == "/p/researcher/v1/runs" and _Fake.seen[0][1] == "Bearer apikey-xyz"
        assert _Fake.seen[0][2]["input"] == "ping"
        assert "apikey-xyz" not in json.dumps(r)
        ok, r = call("chat_send", message="ping", dry_run=True)
        assert ok and r["dry_run"]
        assert call("chat_run_status", run_id="run_1")[1]["status"] == "completed"
    finally:
        srv.shutdown()


def test_chat_api_unreachable_is_adapter_error(call, happ):
    happ.settings.hermes_api_url = "http://127.0.0.1:1"
    ok, r = call("chat_run_status", run_id="r1")
    assert not ok and r["code"] == "adapter_error"


# ---- registry-wide invariants ------------------------------------------------------------
def test_every_write_tool_supports_dry_run_and_has_docs(happ):
    for t in happ.registry.tools.values():
        assert t.description.strip(), t.name
        props = t.input_schema()["properties"]
        if t.access != "read":
            assert props["dry_run"]["type"] == "boolean", t.name
        else:
            assert "dry_run" not in props, t.name
        if t.profile_aware:
            assert "profile" in props, t.name


def test_chat_approval_needs_approvals_scope(happ):
    t = happ.registry.tools["chat_run_approval"]
    assert not Scopes(access="admin").tool_allowed(t)[0]
    assert Scopes(access="admin", approvals=True).tool_allowed(t)[0]
    assert not Scopes(access="write", approvals=True, toolsets=["status"]).tool_allowed(t)[0]


def test_chat_approval_posts_choice(call, happ):
    class Pending(_Fake):
        def do_GET(self):
            _Fake.seen.append((self.path, self.headers.get("Authorization"), None))
            self._send({"run_id": "run_1", "status": "waiting_for_approval",
                        "approval": {"request_id": "rq1", "command": "rm -rf x"}})
    srv = HTTPServer(("127.0.0.1", 0), Pending)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    happ.settings.hermes_api_url = f"http://127.0.0.1:{srv.server_port}"
    try:
        _Fake.seen.clear()
        ok, r = call("chat_run_approval", run_id="run_1", choice="deny", request_id="rq1")
        assert ok and _Fake.seen[-1][0] == "/v1/runs/run_1/approval" and _Fake.seen[-1][2] == {"choice": "deny", "request_id": "rq1"}
    finally:
        srv.shutdown()


def test_secret_list_never_leaks_values(call):
    val = "sup3r-s3cret-VALUE-xyz"
    (call.home / ".env").write_text(f"# token for the work github\nGITHUB_TOKEN={val}\nEMPTY_KEY=\nTERMINAL_TIMEOUT=60\n")
    ok, out = call("secret_list")
    assert ok, out
    blob = json.dumps(out)
    assert val not in blob and "length" not in blob
    by = {s["name"]: s for s in out["secrets"]}
    assert by["GITHUB_TOKEN"]["set"] and by["GITHUB_TOKEN"]["secret"]
    assert by["GITHUB_TOKEN"]["description"]
    assert by["EMPTY_KEY"]["set"] is False
    _, only = call("secret_list", secrets_only=True)
    assert "TERMINAL_TIMEOUT" not in {s["name"] for s in only["secrets"]}


def test_vault_list_empty_profile(call):
    ok, out = call("vault_list")
    assert ok, out
    assert out["count"] == 0 and out["items"] == []
