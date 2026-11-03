"""Cron model routing tools: route in cron_list/cron_show, cron_model_set, cron_models_bulk, fallback write gate.
CLI calls are replaced by a stand-in that applies `hermes cron edit` with Hermes' own pin semantics to jobs.json."""
import json

import pytest
import yaml
from starlette.testclient import TestClient

from hermcp.adapters import cronroute as R
from hermcp.scopes import Scopes
from hermcp.tools import cron as cron_tools

S = "SYNTHVALUE0123456789ABCDEF"


def job(i, **kw):
    base = {"id": i, "name": f"job {i}", "prompt": f"prompt {i}", "skills": [], "model": None, "provider": None,
            "base_url": None, "script": None, "no_agent": False, "schedule": {"kind": "cron", "expr": "0 * * * *"},
            "schedule_display": "0 * * * *", "repeat": {"times": None, "completed": 3}, "enabled": True,
            "state": "scheduled", "deliver": "telegram", "enabled_toolsets": ["terminal"], "workdir": None}
    return {**base, **kw}


@pytest.fixture()
def rig(happ, home, monkeypatch):
    c = TestClient(happ.app)
    _, tok = happ.keystore.create("t", Scopes(access="admin", approvals=True))
    _, wtok = happ.keystore.create("w", Scopes(access="write"))
    cfgp = home / "config.yaml"
    cfgp.write_text(yaml.safe_dump({"model": {"default": "main-model", "provider": "mainprov"},
                                    "cron": {"model": "cron-model", "model_provider": "cronprov"},
                                    "fallback_providers": [{"provider": "fb", "model": "fm", "base_url": f"https://u:{S}@fb.invalid/v1?k={S}"}]}))
    (home / "cron").mkdir(exist_ok=True)
    jf = home / "cron" / "jobs.json"
    jobs = [job("p1", model="m1", provider="prov1", reasoning_effort="high"),
            job("p2", model="m2", provider="prov2", base_url=f"https://user:{S}@ep.invalid/v1/?token={S}"),
            job("u1"), job("pa", model="m3", provider="prov3", enabled=False, state="paused", paused_at="2026-01-01T00:00:00+02:00"),
            job("only_url", base_url="https://ep.invalid/v1"),
            job("sc", no_agent=True, script="x.sh")]
    jf.write_text(json.dumps({"jobs": jobs}))
    calls = []

    def fake_cli(argv, profile=None, timeout=30.0, stdin=None):
        calls.append(list(argv))
        import subprocess
        data = json.loads(jf.read_text())
        j = next(x for x in data["jobs"] if x["id"] == argv[2])
        a = argv[3:]
        if "--unpin" in a:
            j["provider"] = j["model"] = None                    # base_url stays: Hermes' --unpin does not clear it
        if "--pin" in a and not j.get("model"):
            j["provider"], j["model"] = "mainprov", "main-model"
        for flag, key in (("--model", "model"), ("--provider", "provider"), ("--reasoning-effort", "reasoning_effort")):
            if flag in a:
                j[key] = a[a.index(flag) + 1] or None
        jf.write_text(json.dumps(data))
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")
    monkeypatch.setattr(cron_tools, "run_hermes", fake_cli)

    class Rig:
        pass
    r = Rig()
    r.home, r.jf, r.calls = home, jf, calls

    def mk(token):
        def call(tool, /, **args):
            resp = c.post("/mcp", headers={"Authorization": f"Bearer {token}"},
                          json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}})
            res = resp.json()["result"]
            raw = res["content"][0]["text"]
            body = json.loads(raw)
            return (not res["isError"]), body.get("result", body.get("error")), raw
        return call
    r.call, r.wcall = mk(tok), mk(wtok)
    r.jobs = lambda: {x["id"]: x for x in json.loads(jf.read_text())["jobs"]}
    r.backups = lambda: sorted(p.name for p in jf.parent.glob(R.BACKUP_PREFIX + "*"))
    return r


# ---- reading ---------------------------------------------------------------------------------
def test_cron_list_shows_route_defaults_and_hides_credentials(rig):
    ok, r, raw = rig.call("cron_list", include="all", detail=True)
    assert ok and S not in raw and r["count"] == 6 and r["pinned_count"] == 4
    d = r["defaults"]
    assert d["cron_model"] == "cron-model" and d["cron_model_provider"] == "cronprov" and d["main_model"] == "main-model"
    assert d["fallback_chain"] == [{"provider": "fb", "model": "fm", "base_url": "https://fb.invalid/v1"}]
    by = {j["id"]: j["route"] for j in r["jobs"]}
    assert by["p1"]["pinned"] and by["p1"]["reasoning_effort"] == "high" and by["p1"]["inherits_fallback_chain"] is False
    assert by["p1"]["effective"]["model"] == "m1" and by["p1"]["effective"]["model_source"] == "job_pin"
    assert "never falls back" in by["p1"]["fallback_note"]
    assert by["p2"]["base_url"] == "https://ep.invalid/v1/"                    # no userinfo, no query
    u = by["u1"]
    assert u["pinned"] is False and u["inherits_fallback_chain"] is True and "1 entries" in u["fallback_note"]
    assert u["effective"] == {"applies": True, "model": "cron-model", "model_source": "cron.model", "provider": "cronprov",
                              "provider_source": "cron.model_provider", "base_url": None, "reasoning_effort": "from config"}
    assert by["only_url"]["pinned"] is True                                    # a base_url alone pins
    assert by["only_url"]["effective"]["model"] == "cron-model"
    assert by["sc"]["effective"]["applies"] is False


def test_route_falls_back_to_main_model_without_cron_defaults(rig):
    cfg = yaml.safe_load((rig.home / "config.yaml").read_text())
    cfg.pop("cron")
    (rig.home / "config.yaml").write_text(yaml.safe_dump(cfg))
    ok, r, _ = rig.call("cron_show", job_id="u1")
    e = r["route"]["effective"]
    assert ok and e["model"] == "main-model" and e["model_source"] == "main_model" and e["provider"] == "mainprov"


def test_cron_show_has_route_and_no_secret(rig):
    ok, r, raw = rig.call("cron_show", job_id="p2")
    assert ok and S not in raw and r["route"]["pinned"] and r["id"] == "p2"


# ---- single edit -----------------------------------------------------------------------------
def test_unpin_dry_run_then_apply(rig):
    before = rig.jf.read_text()
    ok, r, _ = rig.call("cron_model_set", job_id="p1", unpin=True, dry_run=True)
    assert ok and r["dry_run"] and r["status"] == "planned" and rig.jf.read_text() == before and rig.calls == [] and rig.backups() == []
    assert r["before"]["pinned"] and r["after"]["pinned"] is False and r["after"]["runs_on"] == "cronprov / cron-model"
    assert r["after"]["inherits_fallback_chain"] is True and r["would_run"][-3:] == ["p1", "--unpin"][-2:] or "--unpin" in r["would_run"]
    ok, r, _ = rig.call("cron_model_set", job_id="p1", unpin=True)
    assert ok and r["status"] == "applied" and r["other_fields_unchanged"] is True and len(rig.backups()) == 1
    assert r["backup"] == rig.backups()[0] and rig.calls == [["cron", "edit", "p1", "--unpin"]]
    j = rig.jobs()["p1"]
    assert j["model"] is None and j["provider"] is None and j["reasoning_effort"] == "high"      # effort untouched by unpin


def test_unpin_also_clears_base_url_which_would_keep_the_job_pinned(rig):
    ok, r, _ = rig.call("cron_model_set", job_id="p2", unpin=True)
    j = rig.jobs()["p2"]
    assert ok and j["base_url"] is None and j["model"] is None and r["after"]["pinned"] is False
    ok, r, _ = rig.call("cron_model_set", job_id="only_url", unpin=True)
    assert ok and rig.jobs()["only_url"]["base_url"] is None and r["after"]["pinned"] is False


def test_pin_freezes_main_model_and_is_noop_if_pinned(rig):
    ok, r, _ = rig.call("cron_model_set", job_id="u1", pin=True)
    assert ok and r["after"]["pinned"] and r["after"]["model"] == "main-model" and r["after"]["provider"] == "mainprov"
    assert "never falls back" not in json.dumps(r) and r["after"]["inherits_fallback_chain"] is False
    ok, r, _ = rig.call("cron_model_set", job_id="p1", pin=True)
    assert ok and r["status"] == "unchanged" and rig.jobs()["p1"]["model"] == "m1"


def test_set_model_provider_base_url_and_reasoning(rig):
    ok, r, _ = rig.call("cron_model_set", job_id="u1", model="newm", provider="newp",
                        base_url="https://api.invalid/v1", reasoning_effort="low")
    j = rig.jobs()["u1"]
    assert ok and (j["model"], j["provider"], j["base_url"], j["reasoning_effort"]) == ("newm", "newp", "https://api.invalid/v1", "low")
    assert r["after"]["runs_on"] == "newp / newm" and r["after"]["base_url"] == "https://api.invalid/v1"
    ok, r, _ = rig.call("cron_model_set", job_id="u1", reasoning_effort="")
    assert ok and rig.jobs()["u1"]["reasoning_effort"] is None and rig.jobs()["u1"]["model"] == "newm"
    ok, r, _ = rig.call("cron_model_set", job_id="u1", base_url="")
    assert ok and rig.jobs()["u1"]["base_url"] is None


def test_paused_job_stays_paused_and_other_fields_stay(rig):
    before = rig.jobs()["pa"]
    ok, r, _ = rig.call("cron_model_set", job_id="pa", unpin=True)
    after = rig.jobs()["pa"]
    assert ok and after["enabled"] is False and after["state"] == "paused" and r["other_fields_unchanged"]
    for k in R.PROTECTED:
        assert after.get(k) == before.get(k), k


@pytest.mark.parametrize("args,needle", [
    (dict(pin=True, unpin=True), "mutually exclusive"), (dict(unpin=True, model="x"), "whole route"),
    (dict(pin=True, model="x"), "CURRENT main model"), (dict(reasoning_effort="turbo"), "invalid_arguments"),
    (dict(base_url=f"https://u:{S}@h.invalid"), "credentials"), (dict(base_url="ftp://h.invalid"), "http"),
    (dict(base_url="https://h.invalid/?token=x"), "credentials"), (dict(), "nothing to change"),
    (dict(provider="p"), "needs a model")])
def test_invalid_combinations_are_refused_before_any_write(rig, args, needle):
    ok, e, raw = rig.call("cron_model_set", job_id="u1", **args)
    assert not ok and e["code"] == "invalid_arguments" and S not in raw
    assert needle in e["message"] or needle == "invalid_arguments"
    assert rig.calls == [] and rig.backups() == []


def test_half_pinned_clear_and_script_jobs_and_unknown_job(rig):
    ok, e, _ = rig.call("cron_model_set", job_id="p1", model="")
    assert not ok and "half-pinned" in e["message"]
    ok, r, _ = rig.call("cron_model_set", job_id="p1", model="", provider="")
    assert ok and rig.jobs()["p1"]["model"] is None
    assert rig.call("cron_model_set", job_id="sc", unpin=True)[1]["code"] == "rejected"
    assert rig.call("cron_model_set", job_id="nope", unpin=True)[1]["code"] == "not_found"


def test_cli_failure_is_reported_not_swallowed(rig, monkeypatch):
    import subprocess
    monkeypatch.setattr(cron_tools, "run_hermes", lambda *a, **k: subprocess.CompletedProcess(a, 2, stdout="", stderr="bad provider"))
    ok, e, _ = rig.call("cron_model_set", job_id="u1", model="x", provider="y")
    assert not ok and e["code"] == "cli_error" and "bad provider" in e["message"] and e["backup"]


# ---- scopes and audit ----------------------------------------------------------------------------
def test_admin_only_and_audited(rig, happ):
    ok, e, _ = rig.wcall("cron_model_set", job_id="u1", unpin=True)
    assert not ok and e["code"] in ("unknown_tool", "forbidden")
    ok, e, _ = rig.wcall("cron_models_bulk", action="unpin", select="pinned")
    assert not ok
    rig.call("cron_model_set", job_id="p1", unpin=True)
    log = happ.audit.path.read_text()
    assert "cron_model_set" in log and "p1" in log and S not in log


def test_cron_edit_no_longer_carries_the_model_route(rig, happ):
    props = happ.registry.tools["cron_edit"].input_schema()["properties"]
    assert "model" not in props and "provider" not in props
    assert {"model", "provider", "base_url", "reasoning_effort", "pin", "unpin"} <= set(happ.registry.tools["cron_model_set"].input_schema()["properties"])
    assert happ.registry.tools["cron_model_set"].access == "admin" and happ.registry.tools["cron_models_bulk"].access == "admin"


# ---- bulk ------------------------------------------------------------------------------------------
def test_bulk_preview_without_confirm_writes_nothing(rig):
    before = rig.jf.read_text()
    ok, r, _ = rig.call("cron_models_bulk", action="unpin", select="pinned")
    assert ok and r["applied"] is False and r["needed"] == "confirm=true to apply" and rig.jf.read_text() == before
    assert rig.calls == [] and rig.backups() == []
    ids = {j["job_id"]: j for j in r["jobs"]}
    assert set(ids) == {"p1", "p2", "pa", "only_url"} and all(j["status"] == "planned" for j in ids.values())
    assert ids["p1"]["before"]["model"] == "m1" and ids["p1"]["after"]["pinned"] is False
    ok, r2, _ = rig.call("cron_models_bulk", action="unpin", select="pinned", confirm=True, dry_run=True)
    assert r2["applied"] is False and rig.calls == []


def test_bulk_unpin_applies_keeps_pause_and_everything_else(rig):
    before = rig.jobs()
    ok, r, _ = rig.call("cron_models_bulk", action="unpin", select="pinned", confirm=True)
    after = rig.jobs()
    assert ok and r["applied"] and r["summary"] == {"applied": 4} and len(rig.backups()) == 1 and r["backup"] == rig.backups()[0]
    for i in ("p1", "p2", "pa", "only_url"):
        assert not R.is_pinned(after[i]), i
        for k in R.PROTECTED:
            assert after[i].get(k) == before[i].get(k), (i, k)
    assert after["pa"]["enabled"] is False and after["pa"]["state"] == "paused"
    assert after["u1"] == before["u1"] and after["sc"] == before["sc"]
    assert all(j["other_fields_unchanged"] for j in r["jobs"])
    again = rig.call("cron_models_bulk", action="unpin", select="pinned", confirm=True)[1]
    assert again["note"] == "no matching jobs"


def test_bulk_by_ids_reports_skips_unchanged_and_not_found(rig):
    ok, r, _ = rig.call("cron_models_bulk", action="unpin", job_ids=["p1", "u1", "sc", "ghost", "p1"], confirm=True)
    st = {j["job_id"]: j["status"] for j in r["jobs"]}
    assert st == {"p1": "applied", "u1": "unchanged", "sc": "skipped", "ghost": "not_found"}
    assert len(r["jobs"]) == 4                                                    # duplicate id handled once


def test_bulk_set_and_pin_and_argument_rules(rig):
    ok, r, _ = rig.call("cron_models_bulk", action="set", select="all", model="mm", provider="pp", confirm=True)
    after = rig.jobs()
    assert ok and all(after[i]["model"] == "mm" and after[i]["provider"] == "pp" for i in ("p1", "p2", "u1", "pa", "only_url"))
    assert after["sc"]["model"] is None
    ok, r, _ = rig.call("cron_models_bulk", action="unpin", select="all", confirm=True)
    ok, r, _ = rig.call("cron_models_bulk", action="pin", job_ids=["u1"], confirm=True)
    assert rig.jobs()["u1"]["model"] == "main-model"
    for bad in (dict(action="unpin"), dict(action="unpin", select="all", job_ids=["u1"]),
                dict(action="unpin", select="all", model="x"), dict(action="set", select="all")):
        ok, e, _ = rig.call("cron_models_bulk", **bad)
        assert not ok and e["code"] == "invalid_arguments", bad


def test_bulk_one_failure_does_not_stop_the_others(rig, monkeypatch):
    import subprocess
    real = cron_tools.run_hermes

    def flaky(argv, *a, **k):
        if argv[2] == "p1":
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="boom")
        return real(argv, *a, **k)
    monkeypatch.setattr(cron_tools, "run_hermes", flaky)
    ok, r, _ = rig.call("cron_models_bulk", action="unpin", select="pinned", confirm=True)
    st = {j["job_id"]: j["status"] for j in r["jobs"]}
    assert ok and st["p1"] == "error" and st["p2"] == "applied" and r["summary"]["error"] == 1


# ---- backup --------------------------------------------------------------------------------------
def test_backup_is_a_timestamped_0600_copy_and_rotates(tmp_path):
    jf = tmp_path / "jobs.json"
    jf.write_text("{}")
    names = {R.backup_jobs(jf) for _ in range(25)}
    left = sorted(tmp_path.glob(R.BACKUP_PREFIX + "*"))
    assert len(left) == R.KEEP_BACKUPS and all(p.read_text() == "{}" and oct(p.stat().st_mode)[-3:] == "600" for p in left)
    assert all(n.startswith("jobs.json.hermcp-") for n in names) and R.backup_jobs(tmp_path / "missing.json") is None


def test_safe_url():
    assert R.safe_url(f"https://u:{S}@h.invalid:8443/p?x={S}#f") == "https://h.invalid:8443/p"
    assert R.safe_url("") is None and R.safe_url(None) is None


# ---- fallback chain ------------------------------------------------------------------------------
def test_fallback_writes_need_admin_and_approvals_reads_do_not(rig, happ):
    ok, e, _ = rig.wcall("models_fallback_add", provider="x", model="y")
    assert not ok and e["code"] in ("unknown_tool", "forbidden")                    # write key: no admin, no approvals
    reg = happ.registry.tools
    for n in ("models_fallback_add", "models_fallback_remove"):
        assert reg[n].access == "admin" and reg[n].requires_approvals
    assert reg["models_fallbacks"].access == "read" and not reg["models_fallbacks"].requires_approvals
    ok, r, raw = rig.call("models_fallbacks")
    assert ok and S not in raw and r["fallbacks"][0]["provider"] == "fb"
    ok, r, _ = rig.call("models_fallback_remove", clear=True, dry_run=True)
    assert ok and r["applied"] is False and yaml.safe_load((rig.home / "config.yaml").read_text())["fallback_providers"]
