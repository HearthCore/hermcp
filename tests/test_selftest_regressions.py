"""Regressions for the selftest failures with a single profile, and the stale-hermcp warning.

- `cron_list` omits the `profile` key for a single requested profile and keeps it for several;
  the selftest's matrix check must cope with both shapes.
- `check_bound_isolation` used to raise IndexError with one profile (no foreign profile to try).
- hermcp started before the Hermes build on disk: the warning must show up in every _meta.
"""
import json

import pytest

from hermcp.registry import Principal
from hermcp.scopes import Scopes
from hermcp.selftest import INFO, Report, check_bound_isolation
from hermcp.settings import load_settings


@pytest.fixture()
def rig(home):
    from hermcp.tools import load_all
    reg = load_all()
    st = load_settings({"HERMES_HOME": str(home)})
    admin = Principal("t", "t", Scopes(access="admin", approvals=True))

    def ok(tool, /, **args):
        r = reg.invoke(tool, args, admin, st, None)
        assert r["ok"], r
        return r["result"]
    return type("R", (), {"ok": staticmethod(ok), "home": home})


def _job(i):
    return {"id": i, "name": f"job-{i}", "prompt": "x", "schedule_display": "every 1h", "enabled": True,
            "state": "scheduled", "last_status": "ok", "deliver": "local"}


def test_cron_list_profile_key_shape_single_vs_multi(rig):
    p = rig.home / "profiles" / "researcher" / "cron" / "jobs.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"jobs": [_job("a1")]}))
    single = rig.ok("cron_list", profiles="researcher", include="all")
    assert single["jobs"], single
    assert all("profile" not in j for j in single["jobs"])          # implied by the one requested profile
    multi = rig.ok("cron_list", profiles="default,researcher", include="all")
    assert any(j.get("profile") == "researcher" for j in multi["jobs"])
    assert all("profile" in j for j in multi["jobs"])


def test_bound_keys_check_with_one_profile_is_info_not_crash():
    rep = Report()
    check_bound_isolation(rep, None, ["default"])                    # must not touch rig or raise IndexError
    rows = [r for r in rep.rows if r["check"] == "bound keys"]
    assert rows and rows[0]["result"] == INFO
    assert rep.failed == 0


def test_stale_hermcp_warning_in_meta(monkeypatch):
    from hermcp import meta
    monkeypatch.setattr(meta, "PROCESS_STARTED", 1_000_000_000.0)    # process started in 2001
    monkeypatch.setattr(meta, "hermes_stamp", lambda: {"version": "x", "commit": "abc", "built_at": "2026-10-30T10:00:00Z"})
    m = meta.build(None, None, 0.0, version="t")
    assert m["hermcp_older_than_hermes_build"] is True
    assert m["warnings"] and "Restart hermcp.service" in m["warnings"][0]


def test_no_warning_when_process_is_newer_than_build(monkeypatch):
    from hermcp import meta
    monkeypatch.setattr(meta, "PROCESS_STARTED", 2_000_000_000.0)
    monkeypatch.setattr(meta, "hermes_stamp", lambda: {"version": "x", "commit": "abc", "built_at": "2026-10-30T10:00:00Z"})
    m = meta.build(None, None, 0.0, version="t")
    assert m["hermcp_older_than_hermes_build"] is False
    assert "warnings" not in m
