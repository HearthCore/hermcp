"""Live-correctness work: provenance block, redaction echo, effective skills, read-only inspection tools.

Synthetic values only. Everything runs against a temporary HERMES_HOME (fixture `home`).
"""
import json
import os
import sqlite3
import time

import pytest
import yaml

from hermcp.redact import MARK, echo_labels, mask_value, redact
from hermcp.registry import Principal
from hermcp.scopes import Scopes
from hermcp.settings import load_settings

S = "SYNTHVALUE0123456789ABCDEF"
META_FIELDS = {"source", "sources", "generated_at", "profile", "gateway_pid", "gateway_alive", "hermes_version", "hermcp"}


@pytest.fixture()
def rig(home):
    from hermcp.tools import load_all
    reg = load_all()
    if "hermes_search_tools" not in reg.tools:
        from hermcp.registry import install_meta_tools
        install_meta_tools(reg)
    st = load_settings({"HERMES_HOME": str(home)})
    admin = Principal("t", "t", Scopes(access="admin", approvals=True))
    reader = Principal("r", "r", Scopes(access="read"))

    def call(tool, /, principal=None, **args):
        return reg.invoke(tool, args, principal or admin, st, None)

    def ok(tool, /, **args):
        r = call(tool, **args)
        assert r["ok"], r
        return r["result"]
    call.ok, call.reg, call.reader, call.home, call.call = ok, reg, reader, home, call
    return call


def _gw(home, pid=None, **extra):
    st = {"pid": pid or os.getpid(), "start_time": 1, "gateway_state": "running", "platforms": {}, **extra}
    (home / "gateway_state.json").write_text(json.dumps(st))


# ---- A3: provenance block ----------------------------------------------------------------------
def test_every_result_carries_meta_and_keeps_old_fields(rig):
    _gw(rig.home)
    r = rig.ok("config_get", key="model.default")
    assert r["value"] == "test-model" and r["exists"] is True          # old fields untouched
    m = r["_meta"]
    assert META_FIELDS <= set(m)
    assert m["source"] == "live-inproc" and m["profile"] == "default"
    assert abs(m["generated_at"] - time.time()) < 5
    assert m["gateway_pid"] == os.getpid() and m["gateway_alive"] is True


def test_meta_reports_the_profile_that_was_really_resolved(rig):
    _gw(rig.home)
    assert rig.ok("config_get", key="model.default", profile="researcher")["_meta"]["profile"] == "researcher"
    assert rig.ok("config_get", key="model.default", profile="researcher")["value"] == "researcher-model"
    assert rig.ok("config_get", key="model.default")["value"] == "test-model"


def test_file_backed_results_list_the_file_with_mtime(rig):
    _gw(rig.home)
    r = rig.ok("hooks_list")
    files = {f["path"]: f for f in r["_meta"]["files"]}
    assert str(rig.home / "config.yaml") in files
    assert files[str(rig.home / "config.yaml")]["age_s"] >= 0
    assert r["_meta"]["source"] == "file"


def test_gateway_state_source_and_platform_fields(rig):
    me = os.getpid()
    _gw(rig.home, platforms={
        "telegram": {"state": "connected", "updated_at": "2026-10-25T10:00:00+00:00", "writer_pid": me, "writer_start_time": 1},
        "buzz": {"state": "retrying", "error_message": "x", "needs_attention": True,
                 "updated_at": "2026-10-11T11:14:40+00:00", "writer_pid": 1922890, "writer_start_time": 5}},
        served_profiles=["default"], parked_profiles={})
    r = rig.ok("status_gateway")
    assert r["platforms"]["telegram"] == {**r["platforms"]["telegram"], "stale": False, "writer_pid": me,
                                          "updated_at": "2026-10-25T10:00:00+00:00"}
    b = r["stale_platforms"]["buzz"]
    assert b["stale"] is True and b["writer_pid"] == 1922890 and b["updated_at"].startswith("2026-10-11")
    assert r["_meta"]["source"] == "gateway-state"
    assert r["served_profiles"] == ["default"]


def test_meta_tools_are_not_wrapped(rig):
    r = rig.ok("hermes_search_tools", query="config")
    assert "_meta" not in r


def test_errors_stay_errors_without_meta(rig):
    r = rig.call("config_get", profile="nope")
    assert r["ok"] is False and r["error"]["code"] == "not_found"


# ---- A4: redaction echo ------------------------------------------------------------------------
def test_config_get_echoes_the_key_argument(rig):
    r = rig.ok("config_get", key="model.default")
    assert r["key"] == "model.default"
    assert rig.ok("config_get", key="kanban")["key"] == "kanban"


def test_secret_value_is_still_masked_and_label_stays(rig):
    cfg = yaml.safe_load((rig.home / "config.yaml").read_text())
    cfg["providers"] = {"x": {"api_key": S}}
    (rig.home / "config.yaml").write_text(yaml.safe_dump(cfg))
    r = rig.ok("config_get", key="providers.x.api_key")
    assert r["key"] == "providers.x.api_key" and r["value"] == mask_value(S, True)      # read style: edge markers
    assert S not in json.dumps(r)


def test_secret_looking_argument_is_not_exempted():
    assert echo_labels({"key": "sk-ant-api03-" + "A" * 40}) == {}
    assert echo_labels({"key": "Zk3x9Qw2Lp7Vb5Nc8Rt1Yh4Gd6Fs0Jm2Kq"}) == {}
    assert echo_labels({"key": "model.default", "limit": 5}) == {"key": "model.default"}


def test_echo_exempts_only_the_exact_argument_value():
    out = redact({"key": "kanban", "other": {"key": S}}, strict=True, echo={"key": "kanban"})
    assert out["key"] == "kanban" and out["other"]["key"] == MARK


@pytest.mark.parametrize("field", ["session_key", "idempotency_key", "key"])
def test_label_fields_survive_in_generic_and_strict_when_echoed(field):
    for strict in (True, False):
        out = redact({field: "some.label", "value": "v"}, strict=strict, echo={field: "some.label"})
        assert out[field] == "some.label"


# ---- A5: skills ----------------------------------------------------------------------------------
def _skill(base, rel, name, desc="d"):
    d = base / rel
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\n\nbody {name}\n")


def test_skills_list_effective_counts_categories_and_paths(rig):
    cfg = yaml.safe_load((rig.home / "config.yaml").read_text())
    cfg["skills"] = {"disabled": ["gone"], "platform_disabled": {"api_server": ["only-cli"], "cron": ["only-cli"]}}
    (rig.home / "config.yaml").write_text(yaml.safe_dump(cfg))
    sk = rig.home / "skills"
    _skill(sk, "devops/alpha", "alpha")
    _skill(sk, "devops/only-cli", "only-cli")
    _skill(sk, "gone", "gone")                       # no category
    _skill(sk, "a/dup", "dup")
    _skill(sk, "b/dup", "dup")                       # same declared name twice in one tier
    r = rig.ok("skills_list", limit=100, include="all", detail=True)
    by = {s["name"]: s for s in r["skills"]}
    assert r["installed_total"] == 5 and r["total"] == 5 and r["disabled_total"] == 1
    assert r["effective_enabled_count"]["global"] == 4
    assert r["effective_enabled_count"]["api_server"] == 3 and r["effective_enabled_count"]["cli"] == 4
    assert r["platform_disabled_counts"] == {"api_server": 1, "cron": 1}
    assert by["gone"]["category"] is None and by["gone"]["category_null"] is True and by["gone"]["enabled"] is False
    assert by["alpha"]["category"] == "devops" and by["alpha"]["source"] == "local" and by["alpha"]["path"] == "devops/alpha/SKILL.md"
    assert by["only-cli"]["disabled_on"] == ["api_server", "cron"]
    assert {"a/dup", "b/dup"} <= set(by) and by["a/dup"]["declared_name"] == "dup" and by["a/dup"]["duplicate_name"] is True
    only = rig.ok("skills_list", platform="api_server")
    assert "only-cli" not in {s["name"] for s in only["skills"]}
    assert rig.ok("skills_list", limit=2, include="all")["truncated"] is True
    assert r["_meta"]["source"] == "subprocess"


def test_skills_list_sees_an_in_place_edit_at_once(rig):
    sk = rig.home / "skills"
    _skill(sk, "c/one", "one", desc="first")
    assert rig.ok("skills_list", detail=True)["skills"][0]["description"] == "first"
    (sk / "c" / "one" / "SKILL.md").write_text("---\nname: one\ndescription: second\n---\n\nbody\n")
    assert rig.ok("skills_list", detail=True)["skills"][0]["description"] == "second"


def test_skills_view_reads_linked_files_and_refuses_traversal(rig):
    sk = rig.home / "skills"
    _skill(sk, "c/one", "one")
    (sk / "c" / "one" / "references").mkdir()
    (sk / "c" / "one" / "references" / "api.md").write_text("api notes " + "x" * 50)
    v = rig.ok("skills_view", name="one")
    assert "body one" in v["content"] and v["source"] == "local" and "references" in v["files"]
    assert "api notes" in rig.ok("skills_view", name="one", file="references/api.md")["content"]
    r = rig.call("skills_view", name="one", file="../../../config.yaml")
    assert r["ok"] is False and r["error"]["code"] == "forbidden"
    assert rig.call("skills_view", name="missing")["ok"] is False


def test_skills_view_reads_external_dirs(rig):
    ext = rig.home.parent / "ext_skills"
    _skill(ext, "ext/zeta", "zeta")
    cfg = yaml.safe_load((rig.home / "config.yaml").read_text())
    cfg["skills"] = {"external_dirs": [str(ext)]}
    (rig.home / "config.yaml").write_text(yaml.safe_dump(cfg))
    v = rig.ok("skills_view", name="zeta")
    assert v["source"] == "external" and "body zeta" in v["content"]


# ---- A6: profile details / overview ----------------------------------------------------------------
def test_profiles_list_details(rig):
    _gw(rig.home, served_profiles=["default", "researcher"], parked_profiles={},
        platforms={"telegram": {"state": "connected", "writer_pid": os.getpid(), "writer_start_time": 1},
                   "researcher:telegram": {"state": "connected", "writer_pid": os.getpid(), "writer_start_time": 1}})
    cfg = yaml.safe_load((rig.home / "config.yaml").read_text())
    cfg.update(fallback_providers=[{"provider": "a", "model": "m1"}], toolsets=["file"],
               platform_toolsets={"cli": ["file"]}, mcp_servers={"x": {"url": "http://x"}, "y": {"url": "http://y", "enabled": False}})
    (rig.home / "config.yaml").write_text(yaml.safe_dump(cfg))
    r = rig.ok("profiles_list")
    rows = {p["name"]: p for p in r["profiles"]}
    d = rows["default"]["details"]
    assert d["model"] == "test-model" and d["fallback_chain"] == [{"provider": "a", "model": "m1"}]
    assert d["toolsets"] == ["file"] and d["platform_toolsets"] == {"cli": ["file"]}
    assert d["served"] is True and d["parked"] is False
    assert d["adapters"]["connected"] == 1 and rows["researcher"]["details"]["adapters"]["connected"] == 1
    assert d["mcp"] == {"enabled": ["x"], "disabled": ["y"]}
    assert d["skills"]["effective_by_platform"]["global"] == 0
    assert "details" not in rig.ok("profiles_list", detail=False)["profiles"][0]


def test_orchestration_overview_is_compact_and_complete(rig):
    _gw(rig.home, served_profiles=["default", "researcher"], parked_profiles={}, platforms={
        "telegram": {"state": "connected", "updated_at": "u", "writer_pid": os.getpid(), "writer_start_time": 1},
        "ghost": {"state": "retrying", "error_message": "e", "needs_attention": True, "writer_pid": 7, "writer_start_time": 1}})
    (rig.home / "cron").mkdir(exist_ok=True)
    (rig.home / "cron" / "jobs.json").write_text(json.dumps({"jobs": [
        {"id": "aaaaaaaaaaaa1", "name": "ok", "enabled": True, "state": "scheduled", "last_status": "ok"},
        {"id": "bbbbbbbbbbbb2", "name": "bad", "enabled": False, "state": "paused", "last_status": "error", "last_error": "boom"}]}))
    r = rig.ok("orchestration_overview", skills=False)
    assert {"profiles", "kanban", "cron", "plugins", "mcp_default", "gateway", "approvals_pending", "runs_active"} <= set(r)
    assert r["cron"]["default"]["active"] == 1 and r["cron"]["default"]["paused"] == 1
    assert r["cron"]["default"]["last_status_error"][0]["name"] == "bad"
    assert r["gateway"]["platforms"]["ghost"]["stale"] is True and r["gateway"]["platforms"]["telegram"]["stale"] is False
    assert r["profiles"]["researcher"]["served"] is True
    assert len(json.dumps(r)) < 20000
    assert "section_errors" not in r or "kanban" not in r["section_errors"]
    assert "not visible" in rig.call("orchestration_overview", principal=rig.reader, skills=False)["result"]["approvals_pending"]


# ---- B: read-only inspection tools -------------------------------------------------------------------
def test_logs_tail_filters_redacts_and_marks_truncation(rig):
    logs = rig.home / "logs"
    logs.mkdir()
    lines = [f"line {i} ok" for i in range(300)] + [f"leak Authorization: Bearer {S}abc ERROR boom"]
    (logs / "gateway.log").write_text("\n".join(lines) + "\n")
    r = rig.ok("logs_tail", lines=5)
    assert r["returned"] == 5 and r["truncated"] is True and S not in json.dumps(r)
    r2 = rig.ok("logs_tail", contains="error")
    assert r2["matched_lines"] == 1 and ("********" in r2["lines"][0] or MARK in r2["lines"][0])
    assert r["_meta"]["files"][0]["path"].endswith("gateway.log")


@pytest.mark.parametrize("bad", ["../config.yaml", "/etc/passwd", "gateway.log/../../x", "a b.log", "worker:../x", "worker:nothex"])
def test_logs_tail_refuses_unlisted_paths(rig, bad):
    r = rig.call("logs_tail", log=bad)
    assert r["ok"] is False and r["error"]["code"] in ("invalid_arguments", "forbidden", "not_found")


def test_logs_tail_symlink_escape_and_audit_scope(rig, tmp_path):
    logs = rig.home / "logs"
    logs.mkdir()
    secret = tmp_path / "outside.log"
    secret.write_text("outside\n")
    (logs / "evil.log").symlink_to(secret)
    r = rig.call("logs_tail", log="evil.log")
    assert r["ok"] is False and r["error"]["code"] == "forbidden"
    assert rig.call("logs_tail", principal=rig.reader, log="hermcp-audit")["error"]["code"] == "forbidden"


def test_logs_tail_worker_log(rig):
    d = rig.home / "kanban" / "logs"
    d.mkdir(parents=True)
    (d / "t_abcd1234.log").write_text("started\nfinished token=" + S + "\n")
    r = rig.ok("logs_tail", log="worker:t_abcd1234")
    assert r["returned"] == 2 and S not in json.dumps(r)


def test_profile_files_read(rig):
    (rig.home / "SOUL.md").write_text("# soul\napi_key=" + S + "\n")
    (rig.home / "profiles" / "researcher" / "SOUL.md").write_text("researcher soul")
    listing = rig.ok("profile_files_read")
    assert [f["file"] for f in listing["files"]] == ["SOUL.md"]
    assert rig.ok("profile_files_read", file="SOUL.md", profile="researcher")["content"] == "researcher soul"
    soul = rig.ok("profile_files_read", file="SOUL.md")
    assert "# soul" in soul["content"] and S not in soul["content"]
    assert rig.call("profile_files_read", file="../config.yaml")["ok"] is False
    assert rig.call("profile_files_read", file="config.yaml")["ok"] is False
    big = rig.home / "MEMORY.md"
    big.write_text("x" * 5000)
    cut = rig.ok("profile_files_read", file="MEMORY.md", max_chars=500)
    assert cut["truncated"] is True and "truncated by hermcp" in cut["content"]


def test_plugin_config_get_redacts(rig):
    pd = rig.home / "plugins" / "demo"
    pd.mkdir(parents=True)
    (pd / "plugin.yaml").write_text("name: demo\nversion: 1.2.3\nhooks: [pre_x]\n")
    (pd / "config.json").write_text(json.dumps({"endpoint": "http://x", "api_key": S, "nested": {"password": S}}))
    cfg = yaml.safe_load((rig.home / "config.yaml").read_text())
    cfg["plugins"] = {"enabled": ["demo"]}
    cfg["demo"] = {"token": S, "mode": "fast"}
    (rig.home / "config.yaml").write_text(yaml.safe_dump(cfg))
    r = rig.ok("plugin_config_get", plugin="demo")
    assert r["status"] == "enabled" and r["manifest_summary"]["version"] == "1.2.3"
    assert r["files"]["config.json"]["data"]["api_key"] == mask_value(S, True) and r["profile_config_section"]["token"] == mask_value(S, True)
    assert r["profile_config_section"]["mode"] == "fast" and S not in json.dumps(r)
    assert rig.call("plugin_config_get", plugin="../x")["ok"] is False


def test_hooks_list(rig):
    h = rig.home / "hooks" / "ping"
    h.mkdir(parents=True)
    (h / "HOOK.yaml").write_text("name: ping\ndescription: d\nevents: [agent:end]\n")
    (h / "handler.py").write_text("x=1\n")
    pd = rig.home / "plugins" / "demo"
    pd.mkdir(parents=True)
    (pd / "plugin.yaml").write_text("name: demo\nhooks: [pre_gateway_dispatch]\n")
    cfg = yaml.safe_load((rig.home / "config.yaml").read_text())
    cfg["plugins"] = {"enabled": ["demo"]}
    (rig.home / "config.yaml").write_text(yaml.safe_dump(cfg))
    r = rig.ok("hooks_list")
    assert r["profile_hook_dirs"][0]["name"] == "ping" and r["profile_hook_dirs"][0]["events"] == ["agent:end"]
    assert r["profile_hook_dirs"][0]["handler_py"] is True
    assert r["plugin_declared_hooks"] == [{"plugin": "demo", "hooks": ["pre_gateway_dispatch"]}]


def test_secrets_status_never_returns_values(rig, monkeypatch):
    cfg = yaml.safe_load((rig.home / "config.yaml").read_text())
    cfg["mcp_servers"] = {"a": {"url": "http://x", "headers": {"Authorization": "Bearer ${A_TOKEN}"}},
                          "b": {"command": "x", "env": {"K": "${B_KEY}"}}}
    (rig.home / "config.yaml").write_text(yaml.safe_dump(cfg))
    (rig.home / ".env").write_text(f"A_TOKEN={S}\nB_KEY=\nOTHER=1\n")
    monkeypatch.delenv("B_KEY", raising=False)
    r = rig.ok("secrets_status", names=["OTHER"])
    rows = {v["name"]: v for v in r["variables"]}
    assert rows["A_TOKEN"]["set"] is True and rows["B_KEY"]["set"] is False and rows["OTHER"]["set"] is True
    assert S not in json.dumps(r) and r["unset_count"] == 1
    assert rig.ok("secrets_status", only_unset=True)["count"] == 1


def test_cron_runs_structured_and_outputs(rig):
    (rig.home / "cron" / "output" / "abcdef012345").mkdir(parents=True)
    (rig.home / "cron" / "output" / "abcdef012345" / "2026-10-25_10-00-00.md").write_text("answer token=" + S)
    db = sqlite3.connect(rig.home / "cron" / "executions.db")
    db.execute("CREATE TABLE executions (id TEXT, job_id TEXT, source TEXT, process_id TEXT, pid INT, process_started_at INT,"
               " status TEXT, claimed_at TEXT, started_at TEXT, finished_at TEXT, error TEXT, handoff_pending INT,"
               " handoff_started_at REAL, delivery_outcome TEXT, scheduled_instant TEXT, progress_at TEXT)")
    db.execute("INSERT INTO executions VALUES ('e1','abcdef012345aaaa','s','p',1,1,'failed','2026-10-25T10:00:00','2026-10-25T10:00:01',"
               "'2026-10-25T10:00:09','HTTP 429',0,NULL,'sent','2026-10-25T10:00:00',NULL)")
    db.commit()
    db.close()
    r = rig.ok("cron_runs", job_id="abcdef012345", include_output=True)
    assert r["executions"][0]["status"] == "failed" and r["executions"][0]["error"] == "HTTP 429"
    assert "answer" in r["outputs"][0]["content"] and S not in json.dumps(r)
    assert rig.call("cron_runs", include_output=True)["ok"] is False


def test_kanban_show_adds_worker_log_and_kanban_log(rig):
    d = rig.home / "kanban" / "logs"
    d.mkdir(parents=True)
    (d / "t_abcd1234.log").write_text("one\ntwo\nthree\n")
    r = rig.ok("kanban_show", task_id="t_abcd1234", log_lines=2)
    assert r["worker_log"]["lines"] == ["two", "three"]
    assert "worker_log" not in rig.ok("kanban_show", task_id="t_abcd1234", log_lines=0)
    assert rig.ok("kanban_log", task_id="t_abcd1234", contains="t")["lines"] == ["two", "three"]
    assert rig.call("kanban_log", task_id="t_00000000")["error"]["code"] == "not_found"


def test_sessions_of_another_profile_are_readable(rig):
    from hermes_state import SessionDB
    db = SessionDB(db_path=rig.home / "profiles" / "researcher" / "state.db")
    db.create_session("s_researcher", source="cli", model="m")
    db.append_message("s_researcher", "user", "hello from researcher")
    db.close()
    r = rig.ok("sessions_list", profile="researcher")
    assert r["count"] == 1 and r["profile"] == "researcher" and r["_meta"]["profile"] == "researcher"
    assert rig.call("sessions_list", profile="default")["error"]["code"] == "not_found"
    assert rig.ok("sessions_search", profile="researcher", query="hello")["count"] >= 1
    scoped = Principal("p", "p", Scopes(access="read", profiles=["default"]))
    assert rig.call("sessions_list", principal=scoped, profile="researcher")["error"]["code"] == "forbidden"
