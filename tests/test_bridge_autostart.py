"""The approvals bridge starts by itself once this process is the running gateway (no inbound message needed)."""
import json
import os

import hermcp


def _state(home, **kw):
    (home / "gateway_state.json").write_text(json.dumps(kw), encoding="utf-8")


def test_gateway_detection_needs_own_pid_and_running(tmp_path):
    assert hermcp._is_gateway_process(tmp_path) is False                      # no file
    _state(tmp_path, pid=os.getpid() + 1, gateway_state="running")
    assert hermcp._is_gateway_process(tmp_path) is False                      # another process' gateway
    _state(tmp_path, pid=os.getpid(), gateway_state="starting")
    assert hermcp._is_gateway_process(tmp_path) is False                      # not up yet
    _state(tmp_path, pid=os.getpid(), gateway_state="running")
    assert hermcp._is_gateway_process(tmp_path) is True
    (tmp_path / "gateway_state.json").write_text("{not json")
    assert hermcp._is_gateway_process(tmp_path) is False                      # garbage never raises


def test_watcher_starts_bridge_once_gateway_is_running(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(hermcp, "_start_bridge", lambda **kw: calls.append(1))
    _state(tmp_path, pid=os.getpid(), gateway_state="running")
    assert hermcp._watch_for_gateway(tmp_path, interval=0.01, timeout=1) is True
    assert calls == [1]


def test_watcher_gives_up_in_a_cli_process(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(hermcp, "_start_bridge", lambda **kw: calls.append(1))
    assert hermcp._watch_for_gateway(tmp_path, interval=0.01, timeout=0.05) is False
    assert calls == []


def test_ensure_watcher_is_idempotent(tmp_path, monkeypatch):
    import sys
    import types
    mod = types.ModuleType("hermes_constants")
    mod.get_hermes_home = lambda: tmp_path
    monkeypatch.setitem(sys.modules, "hermes_constants", mod)
    monkeypatch.setattr(hermcp, "WATCH_TIMEOUT_S", 0.5)
    monkeypatch.setattr(hermcp, "_watcher", None)
    hermcp._ensure_gateway_watcher()
    first = hermcp._watcher
    hermcp._ensure_gateway_watcher()
    assert first is not None and hermcp._watcher is first
