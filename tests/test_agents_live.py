"""agents_live + restart preview: the gateway control socket (`active-work`) is faked with a real unix socket.

Nothing talks to a real gateway. Socket paths are limited to ~108 bytes, so the fake HERMES_HOME lives in a short
temp dir instead of pytest's long tmp_path.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
from pathlib import Path

import pytest

from hermcp.adapters import active_work as AW
from hermcp.registry import Principal
from hermcp.scopes import Scopes
from hermcp.tools import ops as O

KEY_MAIN = "agent:main:telegram:dm:123456789"
KEY_RES = "agent:researcher:email:dm:someone@example.org"
KEY_API = "agent:developer:api_server:dm:run_c391350472d5494b83e55e09674fd638"
KEY_PROFILE_MAIN = "agent:main~:telegram:dm:42"

ANSWER = {
    "active_agents": 5,
    "pid": 4242,
    "active_work": [
        {"kind": "chat", "session": KEY_MAIN, "pid": 4242, "elapsed_s": 42.74, "model": "m" * 200,
         "current_tool": "terminal", "idle_s": 1.26},
        {"kind": "chat", "session": KEY_RES, "pid": 4242, "elapsed_s": 5.0, "model": "x", "current_tool": None, "idle_s": 0.2},
        {"kind": "chat", "session": KEY_API, "pid": 4242, "elapsed_s": 600.0, "model": "x", "current_tool": "read", "idle_s": 3.0},
        {"kind": "cron", "job_id": "33a05d408777", "elapsed_s": 9.0, "pid": 4243, "external": False, "wedged": False,
         "restart_safe": True},
        {"kind": "api", "pid": 4242},
    ],
}


class FakeGateway:
    """One-request-per-connection JSON-line server, like gateway/control_socket.py."""

    def __init__(self, home: Path, answer=None, mode="ok"):
        self.path = home / "gateway.sock"
        self.answer, self.mode = answer if answer is not None else ANSWER, mode
        self.requests: list[dict] = []
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(str(self.path))
        os.chmod(self.path, 0o600)
        self.srv.listen(4)
        self.srv.settimeout(0.2)
        self._stop = False
        self.t = threading.Thread(target=self._loop, daemon=True)
        self.t.start()

    def _loop(self):
        while not self._stop:
            try:
                conn, _ = self.srv.accept()
            except (socket.timeout, OSError):
                continue
            with conn:
                conn.settimeout(2)
                data = b""
                while b"\n" not in data:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                req = json.loads(data.decode())
                self.requests.append(req)
                if self.mode == "silent":
                    threading.Event().wait(1.5)
                    continue
                if self.mode == "error":
                    out = {"ok": False, "error": "RuntimeError: active-work: the gateway loop did not answer", "protocol": 1}
                elif self.mode == "garbage":
                    conn.sendall(b"not json\n")
                    continue
                else:
                    result = dict(self.answer)
                    scope = (req.get("params") or {}).get("profile")
                    if scope:
                        result = {**result, "profile": scope,
                                  "active_work": [e for e in result["active_work"]
                                                  if (e.get("session") or "").startswith(f"agent:{scope}:")],
                                  }
                        result["active_agents"] = len(result["active_work"])
                    out = {"ok": True, "protocol": 1, "result": result, "id": req.get("id")}
                conn.sendall(json.dumps(out).encode() + b"\n")

    def close(self):
        self._stop = True
        self.t.join(2)
        self.srv.close()


@pytest.fixture()
def short_home(monkeypatch):
    h = Path(tempfile.mkdtemp(prefix="hm"))
    monkeypatch.setenv("HERMES_HOME", str(h))
    yield h
    shutil.rmtree(h, ignore_errors=True)


@pytest.fixture()
def gw(short_home):
    g = FakeGateway(short_home)
    yield g
    g.close()


def _call(happ, tool, args=None, scopes=None):
    p = Principal("k", "k", scopes or Scopes(access="read"))
    return happ.registry.invoke(tool, args or {}, p, happ.settings, None)


# -- adapter -------------------------------------------------------------------------------------------
def test_request_follows_the_control_socket_protocol(gw):
    assert AW.query() is not None
    assert gw.requests == [{"verb": "active-work", "id": 1, "protocol": 1}]
    AW.query(profile="researcher")
    assert gw.requests[-1]["params"] == {"profile": "researcher"}


def test_no_socket_means_unavailable_not_an_error(short_home):
    snap = AW.snapshot()
    assert snap["available"] is False and snap["source"] == "none" and snap["agents"] == []
    assert "control socket" in snap["reason"]


def test_a_regular_file_or_symlink_is_not_talked_to(short_home):
    (short_home / "gateway.sock").write_text("x")
    assert AW.query() is None
    (short_home / "gateway.sock").unlink()
    (short_home / "real.sock").write_text("x")
    (short_home / "gateway.sock").symlink_to(short_home / "real.sock")
    assert AW.query() is None


@pytest.mark.parametrize("mode", ["error", "garbage"])
def test_failed_or_malformed_answers_degrade_to_none(short_home, mode):
    g = FakeGateway(short_home, mode=mode)
    try:
        assert AW.query() is None
        assert AW.snapshot()["available"] is False
    finally:
        g.close()


def test_a_busy_gateway_times_out_quickly(short_home):
    g = FakeGateway(short_home, mode="silent")
    try:
        assert AW.query(timeout=0.3) is None
    finally:
        g.close()


def test_session_keys_never_leave_the_adapter(gw):
    snap = AW.snapshot()
    blob = json.dumps(snap)
    for secret in (KEY_MAIN, KEY_RES, "123456789", "someone@example.org", "agent:"):
        assert secret not in blob
    chat = snap["agents"][0]
    assert chat["profile"] == "default" and chat["source"] == "telegram" and len(chat["session_ref"]) == 8
    assert chat["elapsed_s"] == 42.7 and chat["idle_s"] == 1.3 and chat["current_tool"] == "terminal"
    assert len(chat["model"]) == AW.TEXT_CAP


def test_profile_naming_of_session_keys():
    assert AW.parse_session_key(KEY_MAIN)["profile"] == "default"
    assert AW.parse_session_key(KEY_PROFILE_MAIN)["profile"] == "main"
    assert AW.parse_session_key(KEY_RES)["profile"] == "researcher"
    assert AW.parse_session_key(KEY_API)["run_id"] == "run_c391350472d5494b83e55e09674fd638"
    assert AW.parse_session_key("garbage")["profile"] is None and AW.parse_session_key(None)["ref"] is None


def test_totals_and_unlisted(gw):
    snap = AW.snapshot()
    assert snap["available"] and snap["source"] == "gateway-control-socket"
    assert snap["active_agents"] == 5 and snap["count"] == 5
    assert snap["by_kind"] == {"chat": 3, "cron": 1, "api": 1}
    assert snap["by_profile"] == {"default": 1, "researcher": 1, "developer": 1}
    assert snap["unlisted"] == 0 and snap["complete"] is True
    cron = next(r for r in snap["agents"] if r["kind"] == "cron")
    assert cron["job_id"] == "33a05d408777" and cron["restart_safe"] is True and cron["source"] == "cron"


def test_count_larger_than_list_is_reported_not_hidden(short_home):
    g = FakeGateway(short_home, answer={**ANSWER, "active_agents": 8})
    try:
        snap = AW.snapshot()
        assert snap["unlisted"] == 3 and snap["complete"] is False
    finally:
        g.close()


def test_restricted_view_keeps_only_attributable_rows(gw):
    snap = AW.snapshot(allowed_profiles={"researcher"})
    assert [r["profile"] for r in snap["agents"]] == ["researcher"]
    assert snap["count"] == 1 and snap["active_agents"] == 1
    assert snap["hidden"] == 4 and snap["scoped_to"] == ["researcher"] and snap["complete"] is False


def test_restart_preview_counts_what_would_be_cut_off(gw):
    prev = AW.restart_preview()
    assert prev["available"] and prev["active_agents"] == 5
    assert prev["would_interrupt"] == 4 and prev["restart_safe"] == 1     # only the cron job is restart-safe


def test_restart_preview_when_unreachable(short_home):
    prev = AW.restart_preview()
    assert prev["available"] is False and "reason" in prev


# -- tools ---------------------------------------------------------------------------------------------
def test_agents_live_tool(happ, gw):
    r = _call(happ, "agents_live")
    assert r["ok"], r
    assert r["result"]["count"] == 5 and r["result"]["by_kind"]["chat"] == 3


def test_agents_live_profile_filter_is_passed_to_the_gateway(happ, gw):
    r = _call(happ, "agents_live", {"profile": "researcher"})
    assert r["ok"], r
    assert gw.requests[-1]["params"] == {"profile": "researcher"}
    assert [a["profile"] for a in r["result"]["agents"]] == ["researcher"]


def test_agents_live_for_a_restricted_key(happ, gw):
    sc = Scopes(access="read", profiles=["researcher"])
    r = _call(happ, "agents_live", scopes=sc)
    assert r["ok"] and [a["profile"] for a in r["result"]["agents"]] == ["researcher"]
    r2 = _call(happ, "agents_live", {"profile": "developer"}, scopes=sc)
    assert not r2["ok"] and r2["error"]["code"] == "forbidden"


def test_agents_live_is_visible_to_read_keys_and_core(happ):
    tool = happ.registry.tools["agents_live"]
    assert tool.access == "read" and tool.toolset == "status" and tool.core is True


def test_gateway_restart_plan_lists_running_agents(happ, gw, monkeypatch):
    calls = []
    monkeypatch.setattr(O.subprocess, "run", lambda *a, **k: calls.append(a) or subprocess.CompletedProcess(a, 0, "", ""))
    r = _call(happ, "gateway_restart", {}, Scopes(access="admin"))
    assert r["ok"], r
    res = r["result"]
    assert res["applied"] is False and res["needed"] == "confirm=true"
    assert res["running_agents"]["active_agents"] == 5 and res["running_agents"]["would_interrupt"] == 4
    assert "drain" in res["warning"] and calls == []


def test_gateway_restart_plan_without_socket_says_so(happ, short_home, monkeypatch):
    monkeypatch.setattr(O.subprocess, "run", lambda *a, **k: pytest.fail("must not run"))
    r = _call(happ, "gateway_restart", {}, Scopes(access="admin"))
    assert r["result"]["running_agents"]["available"] is False


def test_gateway_restart_result_keeps_the_snapshot(happ, gw, monkeypatch):
    monkeypatch.setattr(O.subprocess, "run", lambda a, **k: subprocess.CompletedProcess(a, 0, "", ""))
    r = _call(happ, "gateway_restart", {"confirm": True}, Scopes(access="admin"))
    assert r["ok"] and r["result"]["applied"] is True
    assert r["result"]["running_agents_at_request"]["active_agents"] == 5


def test_hermcp_restart_plan_says_agents_are_untouched(happ, gw, monkeypatch):
    monkeypatch.setattr(O.subprocess, "run", lambda *a, **k: pytest.fail("must not run"))
    r = _call(happ, "hermcp_restart", {}, Scopes(access="admin"))
    ra = r["result"]["running_agents"]
    assert ra["available"] and ra["active_agents"] == 5 and ra["interrupted_by_this_restart"] == 0
    assert "agents" not in ra            # compact: counts only
