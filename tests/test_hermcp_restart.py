"""hermcp_restart: plan / dry_run / confirm, rate limit, scope, audit. systemd-run is mocked; nothing restarts."""
from __future__ import annotations

import subprocess

import pytest

from hermcp.audit import AuditLog
from hermcp.registry import Principal
from hermcp.scopes import Scopes
from hermcp.tools import ops as O


class Calls:
    def __init__(self, rc=0, stderr="", timer_state="active"):
        self.rc, self.stderr, self.timer_state = rc, stderr, timer_state
        self.argvs = []      # systemd-run invocations
        self.probes = []     # systemctl is-active invocations

    def __call__(self, argv, **kw):
        if "is-active" in argv:
            self.probes.append(list(argv))
            return subprocess.CompletedProcess(argv, 0 if self.timer_state == "active" else 3,
                                               f"{self.timer_state}\n", "")
        self.argvs.append(list(argv))
        return subprocess.CompletedProcess(argv, self.rc, "", self.stderr)


@pytest.fixture()
def mocked(monkeypatch):
    c = Calls()
    monkeypatch.setattr(O.subprocess, "run", c)
    return c


def _call(happ, args, access="admin", audit=None):
    return happ.registry.invoke("hermcp_restart", args, Principal("k", "k", Scopes(access=access)), happ.settings, audit)


def test_plan_without_confirm_applies_nothing(happ, mocked):
    r = _call(happ, {})
    assert r["ok"], r
    res = r["result"]
    assert res["applied"] is False and res["needed"] == "confirm=true"
    assert res["unit"] == "hermcp.service" and res["old_pid"] > 0 and "old_started_at" in res
    assert "hermcp.service" in res["command"] and "gateway" not in res["command"].lower()
    assert mocked.argvs == []


def test_dry_run_with_confirm_is_still_a_plan(happ, mocked):
    r = _call(happ, {"dry_run": True, "confirm": True})
    assert r["ok"] and r["result"]["applied"] is False
    assert mocked.argvs == []


def test_confirm_schedules_only_hermcp_unit(happ, mocked):
    r = _call(happ, {"confirm": True})
    assert r["ok"], r
    assert r["result"]["applied"] is True
    assert r["result"]["timer_state"] == "active"
    assert len(mocked.argvs) == 1
    argv = mocked.argvs[0]
    assert argv[0] == "systemd-run" and "--on-active=3s" in argv
    assert argv[-1] == "hermcp.service" and argv[-2] == "restart"
    joined = " ".join(argv)
    for forbidden in ("hermes-gateway", "gateway", "dashboard", "hermes-dashboard"):
        assert forbidden not in joined


def test_timer_accuracy_is_pinned_so_the_announced_delay_is_true(happ, mocked):
    # A transient timer inherits DefaultTimerAccuracySec=1min: without this flag "--on-active=3s"
    # fired 39-59 s late and the restart looked like it never happened.
    r = _call(happ, {"confirm": True})
    argv = mocked.argvs[0]
    assert "--timer-property=AccuracySec=100ms" in argv
    assert r["result"]["scheduled_in_s"] == 3 and r["result"]["delay_s"] == 3
    assert r["result"]["timer_unit"].endswith(".timer")


def test_success_is_withheld_until_the_timer_is_confirmed_active(happ, mocked):
    _call(happ, {"confirm": True})
    assert len(mocked.probes) == 1
    probe = mocked.probes[0]
    assert probe[-2:] == ["is-active", mocked.argvs[0][3].split("=", 1)[1] + ".timer"]


def test_second_restart_within_60s_is_rate_limited(happ, mocked):
    assert _call(happ, {"confirm": True})["ok"]
    r = _call(happ, {"confirm": True})
    assert r["ok"] is False and r["error"]["code"] == "rate_limited"
    assert len(mocked.argvs) == 1


def test_rate_limit_expires(happ, mocked, monkeypatch):
    assert _call(happ, {"confirm": True})["ok"]
    real = O._restart_last_requested
    monkeypatch.setattr(O, "_restart_last_requested", lambda *a, **kw: real(*a, **kw) - 120)
    assert _call(happ, {"confirm": True})["ok"]
    assert len(mocked.argvs) == 2


def test_failed_systemd_run_is_reported_not_hidden(happ, monkeypatch):
    monkeypatch.setattr(O.subprocess, "run", Calls(rc=1, stderr="Failed to connect to bus"))
    r = _call(happ, {"confirm": True})
    assert r["ok"] is False and r["error"]["code"] == "restart_failed"
    assert "bus" in r["error"]["message"]
    assert r["error"]["applied"] is False and r["error"]["exit_code"] == 1


def test_failed_schedule_does_not_consume_the_rate_limit(happ, monkeypatch):
    c = Calls(rc=1, stderr="Failed to connect to bus")
    monkeypatch.setattr(O.subprocess, "run", c)
    assert _call(happ, {"confirm": True})["ok"] is False
    assert O._restart_last_requested() == 0.0
    c.rc = 0                                                   # the bus is back
    r = _call(happ, {"confirm": True})
    assert r["ok"] is True, r                               # no rate_limited after a failure


def test_timer_that_did_not_survive_scheduling_is_a_failure(happ, monkeypatch):
    # exit code 0 but no active timer: the tool must not answer applied=true
    c = Calls(timer_state="inactive")
    monkeypatch.setattr(O.subprocess, "run", c)
    r = _call(happ, {"confirm": True})
    assert r["ok"] is False and r["error"]["code"] == "restart_failed"
    assert r["error"]["applied"] is False and r["error"]["timer_state"] == "inactive"
    assert "no restart was queued" in r["error"]["message"]
    assert O._restart_last_requested() == 0.0                # rate limit untouched
    assert len(c.probes) == O.RESTART_VERIFY_TRIES           # retried before giving up


def test_unverifiable_timer_state_does_not_block_a_queued_restart(happ, monkeypatch):
    # if systemctl cannot be asked at all we report the unknown state instead of failing the call
    monkeypatch.setattr(O.subprocess, "run", Calls(timer_state="unknown"))
    r = _call(happ, {"confirm": True})
    assert r["ok"] is True and r["result"]["applied"] is True
    assert r["result"]["timer_state"] == "unknown"


def test_non_admin_is_forbidden(happ, mocked):
    for access in ("read", "write"):
        r = _call(happ, {"confirm": True}, access=access)
        assert r["ok"] is False and r["error"]["code"] == "forbidden"
    assert mocked.argvs == []


def test_audit_entry_is_written_and_has_no_secrets(happ, mocked, tmp_path):
    audit = AuditLog(tmp_path / "audit.jsonl")
    assert _call(happ, {"confirm": True}, audit=audit)["ok"]
    recs = audit.tail(5, tool="hermcp_restart")
    assert recs and recs[0]["status"] == "ok" and recs[0]["args"] == {"confirm": True}
    assert "sk-" not in (tmp_path / "audit.jsonl").read_text(encoding="utf-8")


def test_unit_name_is_not_a_parameter(happ, mocked):
    r = _call(happ, {"confirm": True, "unit": "hermes-gateway.service"})
    assert r["ok"] is False and r["error"]["code"] == "invalid_arguments"
    assert mocked.argvs == []


def test_restart_is_registered_as_admin_tool(happ):
    t = happ.registry.tools["hermcp_restart"]
    assert t.access == "admin" and t.toolset == "ops"
