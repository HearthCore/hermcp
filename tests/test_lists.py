"""List tools: relevant-by-default, include=all, view=summary, profile matrix against fresh-process references."""
import json

import pytest
import yaml

from hermcp.registry import Principal
from hermcp.scopes import Scopes
from hermcp.settings import load_settings


@pytest.fixture()
def rig(home):
    from hermcp.tools import load_all
    reg = load_all()
    st = load_settings({"HERMES_HOME": str(home)})
    admin = Principal("t", "t", Scopes(access="admin", approvals=True))

    def call(tool, /, **args):
        return reg.invoke(tool, args, admin, st, None)

    def ok(tool, /, **args):
        r = call(tool, **args)
        assert r["ok"], r
        return r["result"]
    call.ok, call.home, call.call = ok, home, call
    return call


def _skill(base, rel, name, desc="d"):
    d = base / rel
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\n\nbody\n")


def _cfg(path, **kw):
    cfg = yaml.safe_load(path.read_text()) or {}
    cfg.update(kw)
    path.write_text(yaml.safe_dump(cfg))


@pytest.fixture()
def skills_home(rig):
    h = rig.home
    _cfg(h / "config.yaml", skills={"disabled": ["off-one"], "platform_disabled": {"api_server": ["cli-only"]}})
    _cfg(h / "profiles" / "researcher" / "config.yaml", skills={"disabled": ["alpha", "off-one"]})
    for base in (h / "skills", h / "profiles" / "researcher" / "skills"):
        _skill(base, "devops/alpha", "alpha", "the alpha skill with a long description " * 5)
        _skill(base, "devops/cli-only", "cli-only")
        _skill(base, "web/off-one", "off-one")
    _skill(h / "skills", "web/only-default", "only-default")
    return rig


# ---- skills_list ----------------------------------------------------------------------------------------
def test_skills_default_is_compact_and_only_active(skills_home):
    r = skills_home.ok("skills_list")
    names = {s["name"] for s in r["skills"]}
    assert names == {"alpha", "cli-only", "only-default"}                 # off-one is disabled and hidden
    row = r["skills"][0]
    assert set(row) <= {"name", "category", "source", "status", "enabled", "disabled_on", "duplicate_name"}
    assert "description" not in row and "path" not in row
    lst = r["_meta"]["list"]
    assert (lst["total"], lst["active"], lst["disabled"], lst["hidden_by_default"]) == (4, 3, 1, 1)
    assert "Default changed" in lst["note"] and lst["include"] == "active"
    assert r["installed_total"] == 4 and r["total"] == 3 and r["returned"] == 3 and r["truncated"] is False and r["offset"] == 0


def test_skills_include_all_shows_status_for_every_skill(skills_home):
    r = skills_home.ok("skills_list", include="all")
    by = {s["name"]: s for s in r["skills"]}
    assert set(by) == {"alpha", "cli-only", "only-default", "off-one"}
    assert by["off-one"]["status"] == "disabled" and by["alpha"]["status"] == "enabled"
    assert by["cli-only"]["disabled_on"] == ["api_server"] and by["cli-only"]["status"] == "enabled"
    r2 = skills_home.ok("skills_list", include="all", platform="api_server")
    assert {s["name"]: s["status"] for s in r2["skills"]}["cli-only"] == "disabled"
    assert r2["effective_enabled_count"]["api_server"] == 2


def test_skills_summary_has_counters_and_no_entries(skills_home):
    r = skills_home.ok("skills_list", view="summary")
    assert "skills" not in r and r["matching"] == 4 and r["active"] == 3 and r["disabled"] == 1
    assert r["by_category"]["devops"] == {"total": 2, "active": 2, "disabled": 0}
    assert r["by_category"]["web"] == {"total": 2, "active": 1, "disabled": 1}
    assert r["by_source"]["local"]["total"] == 4


def test_skills_filters_and_paging_report_truncation(skills_home):
    assert [s["name"] for s in skills_home.ok("skills_list", category="devops")["skills"]] == ["alpha", "cli-only"]
    assert [s["name"] for s in skills_home.ok("skills_list", query="only-def")["skills"]] == ["only-default"]
    assert skills_home.ok("skills_list", source="hub")["skills"] == []
    p1 = skills_home.ok("skills_list", limit=2)
    p2 = skills_home.ok("skills_list", limit=2, offset=2)
    assert (p1["total"], p1["returned"], p1["truncated"]) == (3, 2, True)
    assert (p2["returned"], p2["truncated"]) == (1, False)
    assert {s["name"] for s in p1["skills"] + p2["skills"]} == {"alpha", "cli-only", "only-default"}
    d = skills_home.ok("skills_list", descriptions=True, query="alpha")["skills"][0]["description"]
    assert 0 < len(d) <= 80


def _reference(profile, home, **env):
    """Fresh-process reference: what an agent session of that profile lists (independent code path)."""
    from hermcp.selftest import _probe
    return _probe(profile, "catalog_visible", **env)


def test_skills_matrix_equals_fresh_process_reference(skills_home):
    r = skills_home.ok("skills_list", profiles="all", include="all")
    rows = {s["name"]: s for s in r["skills"]}
    assert r["profiles"] == ["default", "researcher"]
    for prof in ("default", "researcher"):
        ref = set(_reference(prof, skills_home.home)["names"])
        on = {n for n, row in rows.items() if row["profiles"][prof] == "on"}
        assert on == ref, (prof, on, ref)
        assert r["per_profile"][prof]["global_enabled"] == len(ref)
    assert rows["alpha"]["profiles"] == {"default": "on", "researcher": "off"}
    assert rows["only-default"]["profiles"]["researcher"] == "absent"
    assert rows["cli-only"]["platform_off"] == {"default": ["api_server"]}
    # per platform: the matrix agrees with a session on api_server
    api = skills_home.ok("skills_list", profiles="all", include="all", platform="api_server")
    ref_api = set(_reference("default", skills_home.home, HERMES_PLATFORM="api_server")["names"])
    active_default = {s["name"] for s in api["skills"] if "default" in s["active_in"]}
    assert active_default == ref_api and "cli-only" not in active_default
    assert api["per_profile"]["default"]["effective_by_platform"]["api_server"] == len(ref_api)
    # default view of the matrix: only skills active in at least one profile
    act = skills_home.ok("skills_list", profiles="default,researcher")
    assert "off-one" not in {s["name"] for s in act["skills"]} and act["_meta"]["list"]["hidden_by_default"] == 1


def test_matrix_rejects_unknown_profile(skills_home):
    assert skills_home.call("skills_list", profiles="nope")["error"]["code"] == "not_found"
    scoped = Principal("p", "p", Scopes(access="read", profiles=["default"]))
    from hermcp.tools import reg
    r = reg.invoke("skills_list", {"profiles": "all"}, scoped, load_settings({"HERMES_HOME": str(skills_home.home)}), None)
    assert r["ok"] and r["result"]["profiles"] == ["default"]


# ---- mcp_list --------------------------------------------------------------------------------------------
@pytest.fixture()
def mcp_home(rig):
    _cfg(rig.home / "config.yaml", mcp_servers={
        "a": {"url": "http://a", "headers": {"Authorization": "Bearer ${A}"}},
        "b": {"url": "http://b", "enabled": False}, "c": {"command": "x"}})
    _cfg(rig.home / "profiles" / "researcher" / "config.yaml", mcp_servers={"a": {"url": "http://a", "enabled": False},
                                                                         "d": {"command": "y"}})
    return rig


def test_mcp_default_enabled_only_include_all_summary_detail(mcp_home):
    r = mcp_home.ok("mcp_list")
    assert r["servers"] == {"a": {"enabled": True, "transport": "url"}, "c": {"enabled": True, "transport": "stdio"}}
    assert r["_meta"]["list"]["hidden_by_default"] == 1 and r["total"] == 2 and r["configured"] == 3
    allr = mcp_home.ok("mcp_list", include="all")
    assert allr["servers"]["b"]["enabled"] is False and set(allr["servers"]) == {"a", "b", "c"}
    s = mcp_home.ok("mcp_list", view="summary")
    assert s["by_transport"]["url"] == {"total": 2, "active": 1, "disabled": 1} and "servers" not in s
    d = mcp_home.ok("mcp_list", detail=True)
    assert d["servers"]["a"]["headers"]["Authorization"].startswith("Bearer ") and "********" in d["servers"]["a"]["headers"]["Authorization"]
    assert list(mcp_home.ok("mcp_list", source="stdio")["servers"]) == ["c"]
    assert mcp_home.ok("mcp_list", limit=1)["truncated"] is True


def test_mcp_matrix_equals_fresh_process_reference(mcp_home):
    from hermcp.selftest import _probe
    r = mcp_home.ok("mcp_list", profiles="all", include="all")
    rows = {s["name"]: s["profiles"] for s in r["servers"]}
    for prof in ("default", "researcher"):
        raw = _probe(prof, "config_raw")["raw"].get("mcp_servers") or {}
        for name, c in raw.items():
            assert rows[name][prof] == ("on" if c.get("enabled", True) else "off")
        for name in set(rows) - set(raw):
            assert rows[name][prof] == "absent"
    assert rows["a"] == {"default": "on", "researcher": "off"}


# ---- cron_list -------------------------------------------------------------------------------------------
def _jobs(path, jobs):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"jobs": jobs}))


def _job(i, **kw):
    return {"id": i, "name": f"job-{i}", "prompt": "do it " * 50, "schedule_display": "every 1h", "enabled": True,
            "state": "scheduled", "last_status": "ok", "deliver": "local", **kw}


def test_cron_default_active_plus_failing(rig):
    _jobs(rig.home / "cron" / "jobs.json", [
        _job("s1"), _job("p1", enabled=False, state="paused"), _job("p2", enabled=False, state="paused", last_status="error"),
        _job("c1", enabled=False, state="completed")])
    r = rig.ok("cron_list")
    assert [j["id"] for j in r["jobs"]] == ["s1", "p2"]
    assert "prompt" not in r["jobs"][0] and r["jobs"][0]["schedule"] == "every 1h"
    lst = r["_meta"]["list"]
    assert (lst["total"], lst["active"], lst["inactive"], lst["hidden_by_default"], lst["failing_last_run"]) == (4, 1, 3, 2, 1)
    assert {j["id"] for j in rig.ok("cron_list", include="all")["jobs"]} == {"s1", "p1", "p2", "c1"}
    s = rig.ok("cron_list", view="summary")
    assert s["by_state"]["paused"]["total"] == 2 and s["failing"][0]["id"] == "p2" and "jobs" not in s
    d = rig.ok("cron_list", include="all", detail=True)
    assert d["jobs"][0]["route"]["pinned"] is False and "prompt" in d["jobs"][0]
    assert rig.ok("cron_list", state="paused", include="all")["total"] == 2
    assert rig.ok("cron_list", include="all", limit=3)["truncated"] is True


def test_cron_profiles_read_each_store_live(rig):
    _jobs(rig.home / "cron" / "jobs.json", [_job("d1")])
    _jobs(rig.home / "profiles" / "researcher" / "cron" / "jobs.json", [_job("x1"), _job("x2", enabled=False, state="paused")])
    r = rig.ok("cron_list", profiles="all", include="all")
    assert sorted((j["profile"], j["id"]) for j in r["jobs"]) == [("default", "d1"), ("researcher", "x1"), ("researcher", "x2")]
    _jobs(rig.home / "profiles" / "researcher" / "cron" / "jobs.json", [_job("x9")])          # changed on disk
    r2 = rig.ok("cron_list", profiles="researcher")
    assert [j["id"] for j in r2["jobs"]] == ["x9"]
    from hermcp.selftest import _probe
    ref = {j["id"] for j in _probe("researcher", "cron_jobs")["jobs"]}
    assert ref == {"x9"}


# ---- plugins_list ----------------------------------------------------------------------------------------
PLUG = {"default": [{"name": "a", "status": "enabled", "source": "bundled", "version": "1", "description": "da"},
                    {"name": "b", "status": "disabled", "source": "bundled", "version": "1", "description": "db"},
                    {"name": "c", "status": "enabled", "source": "git", "version": "2", "description": "dc"}],
        "researcher": [{"name": "a", "status": "disabled", "source": "bundled", "version": "1", "description": "da"},
                  {"name": "c", "status": "enabled", "source": "git", "version": "2", "description": "dc"}]}


@pytest.fixture()
def plug(rig, monkeypatch):
    from hermcp.tools import plugins_ts
    calls = []

    def fake(argv, profile=None, timeout=30.0):
        calls.append(profile)
        return PLUG[profile or "default"]
    monkeypatch.setattr(plugins_ts, "cli_json", fake)
    rig.calls = calls
    return rig


def test_plugins_default_enabled_only_and_modes(plug):
    r = plug.ok("plugins_list")
    assert [p["name"] for p in r["plugins"]] == ["a", "c"] and "description" not in r["plugins"][0]
    assert r["_meta"]["list"]["hidden_by_default"] == 1
    assert {p["name"]: p["status"] for p in plug.ok("plugins_list", include="all")["plugins"]}["b"] == "disabled"
    assert plug.ok("plugins_list", view="summary")["by_source"]["bundled"]["total"] == 2
    assert plug.ok("plugins_list", detail=True, query="dc")["plugins"][0]["description"] == "dc"
    assert [p["name"] for p in plug.ok("plugins_list", source="git")["plugins"]] == ["c"]


def test_plugins_matrix_asks_each_profile(plug):
    r = plug.ok("plugins_list", profiles="all", include="all")
    rows = {p["name"]: p["profiles"] for p in r["plugins"]}
    assert rows["a"] == {"default": "on", "researcher": "off"} and rows["b"] == {"default": "off", "researcher": "absent"}
    assert sorted(x or "default" for x in plug.calls) == ["default", "researcher"]            # one live call per profile


# ---- kanban_list -----------------------------------------------------------------------------------------
TASKS = [{"id": "t_1", "title": "open one", "status": "ready", "assignee": "developer", "priority": 1, "body": "x" * 500},
         {"id": "t_2", "title": "running", "status": "running", "assignee": "researcher", "priority": 0},
         {"id": "t_3", "title": "finished", "status": "done", "assignee": "developer", "priority": 0},
         {"id": "t_4", "title": "old", "status": "archived", "assignee": None, "priority": 0}]


@pytest.fixture()
def kb(rig, monkeypatch):
    from hermcp.tools import kanban
    seen = []

    def fake(argv, profile=None, timeout=30.0):
        seen.append(argv)
        out = TASKS if "--archived" in argv else [t for t in TASKS if t["status"] != "archived"]
        if "--status" in argv:
            out = [t for t in out if t["status"] == argv[argv.index("--status") + 1]]
        return out
    monkeypatch.setattr(kanban, "cli_json", fake)
    rig.seen = seen
    return rig


def test_kanban_default_open_only_and_modes(kb):
    r = kb.ok("kanban_list")
    assert [t["id"] for t in r["tasks"]] == ["t_1", "t_2"] and "body" not in r["tasks"][0]
    lst = r["_meta"]["list"]
    assert (lst["total"], lst["open"], lst["closed"], lst["hidden_by_default"]) == (3, 2, 1, 1)
    assert "active" not in lst and "disabled" not in lst
    assert [t["id"] for t in kb.ok("kanban_list", include="all")["tasks"]] == ["t_1", "t_2", "t_3", "t_4"]
    assert [t["id"] for t in kb.ok("kanban_list", status="done")["tasks"]] == ["t_3"]
    s = kb.ok("kanban_list", view="summary", include="all")
    assert s["by_status"]["done"] == {"total": 1, "open": 0, "closed": 1} and s["by_assignee"]["developer"]["total"] == 2 and "tasks" not in s
    assert len(kb.ok("kanban_list", detail=True)["tasks"][0]["body"]) <= 320
    assert [t["id"] for t in kb.ok("kanban_list", query="run")["tasks"]] == ["t_2"]
    p = kb.ok("kanban_list", limit=1)
    assert (p["total"], p["returned"], p["truncated"]) == (2, 1, True)
