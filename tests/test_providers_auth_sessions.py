"""Provider auth sessions are bounded in time and in number (issue #68).

`_sessions_gc` used to close a session only once its child process had exited. A hung
`hermes auth add` waits on stdin forever, so its process never exits and the rule never matched:
the PTY fd and the child stayed for the lifetime of the server. The tests drive the real tool
handlers with a fake long-running child, so nothing here starts a real `hermes` process.
"""
import dataclasses
import logging
import os
import pty
import time

import pytest

from hermcp.registry import Principal
from hermcp.scopes import Scopes
from hermcp.settings import load_settings
from hermcp.tools import providers


class FakeProc:
    """A child process: alive until terminated, or already exited with `returncode`."""

    def __init__(self, returncode=None):
        self.returncode = returncode
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


@pytest.fixture()
def rig(home, monkeypatch):
    """The app plus a fake PTY spawner; `_SESSIONS` is replaced so tests never share state."""
    from hermcp.app import create_app

    monkeypatch.setattr(providers, "_SESSIONS", {})
    monkeypatch.setattr(providers, "_read_available", lambda s, wait=1.0: "")
    procs = []

    def fake_spawn(argv):
        master, slave = pty.openpty()
        os.close(slave)
        proc = FakeProc()
        procs.append(proc)
        return {"proc": proc, "fd": master, "transcript": "", "created": time.time(), "last": time.time()}

    monkeypatch.setattr(providers, "_spawn", fake_spawn)
    app = create_app(load_settings({"HERMES_HOME": str(home), "HERMCP_ALLOWED_HOSTS": "testserver,127.0.0.1"}))
    return app, procs


def call(app, name, args, settings=None, access="write"):
    principal = Principal("k", "k", Scopes(access=access))
    return app.registry.invoke(name, args, principal, settings or app.settings, None)


def start(app, provider="acme", settings=None):
    res = call(app, "provider_auth_start", {"provider": provider}, settings=settings)
    assert res["ok"], res
    return res["result"]["auth_id"]


# ---- the reported repro: a child that never exits -------------------------------------------
def test_hard_cap_closes_a_session_whose_process_is_still_running(rig):
    app, procs = rig
    aid = start(app)
    sess = providers._SESSIONS[aid]
    fd = sess["fd"]
    # Old, but touched just now: the idle rule alone would keep it forever.
    sess["created"] = time.time() - (app.settings.auth_session_hard_cap + 60)
    sess["last"] = time.time()
    assert sess["proc"].poll() is None

    status = call(app, "provider_auth_status", {}, access="read")

    assert status["ok"], status
    assert status["result"]["count"] == 0
    assert aid not in providers._SESSIONS
    assert procs[0].terminated and procs[0].returncode == -15
    with pytest.raises(OSError):
        os.fstat(fd)          # the PTY fd is released, not leaked


def test_a_running_session_inside_the_hard_cap_survives(rig):
    app, _ = rig
    aid = start(app)

    status = call(app, "provider_auth_status", {}, access="read")

    assert status["result"]["count"] == 1
    row = status["result"]["sessions"][0]
    assert row["auth_id"] == aid and row["running"] is True
    assert row["age_seconds"] < app.settings.auth_session_hard_cap


def test_the_hard_cap_is_configurable(rig):
    app, _ = rig
    settings = dataclasses.replace(app.settings, auth_session_hard_cap=60.0)
    aid = start(app, settings=settings)
    providers._SESSIONS[aid]["created"] = time.time() - 120

    assert call(app, "provider_auth_status", {}, settings=settings, access="read")["result"]["count"] == 0


# ---- the idle rule keeps working, and only for finished processes ---------------------------
def test_idle_gc_drops_a_finished_session_and_keeps_a_running_one(rig):
    app, procs = rig
    finished, running = start(app, "one"), start(app, "two")
    providers._SESSIONS[finished]["proc"].returncode = 0        # exited on its own
    for aid in (finished, running):
        providers._SESSIONS[aid]["last"] = time.time() - (app.settings.auth_session_max_age + 60)

    status = call(app, "provider_auth_status", {}, access="read")

    assert [r["auth_id"] for r in status["result"]["sessions"]] == [running]
    assert procs[1].terminated is False


# ---- a hard bound on how many sessions exist at once ---------------------------------------
def test_starting_one_more_session_closes_the_oldest(rig):
    app, procs = rig
    settings = dataclasses.replace(app.settings, auth_session_max=2)
    first = start(app, "one", settings=settings)
    second = start(app, "two", settings=settings)
    third = start(app, "three", settings=settings)

    assert list(providers._SESSIONS) == [second, third]
    assert first not in providers._SESSIONS
    assert procs[0].terminated and procs[1].terminated is False


def test_a_dry_run_start_closes_nothing(rig):
    app, _ = rig
    settings = dataclasses.replace(app.settings, auth_session_max=1)
    aid = start(app, settings=settings)

    plan = call(app, "provider_auth_start", {"provider": "other", "dry_run": True}, settings=settings)

    assert plan["ok"] and plan["result"]["dry_run"] is True
    assert list(providers._SESSIONS) == [aid]


# ---- the operator can see the limits -------------------------------------------------------
def test_status_reports_the_limits(rig):
    app, _ = rig

    limits = call(app, "provider_auth_status", {}, access="read")["result"]["limits"]

    assert limits == {"idle_seconds": app.settings.auth_session_max_age,
                      "hard_cap_seconds": app.settings.auth_session_hard_cap,
                      "max_sessions": app.settings.auth_session_max}


# ---- settings ------------------------------------------------------------------------------
def test_the_limits_come_from_the_environment(home):
    s = load_settings({"HERMES_HOME": str(home), "HERMCP_AUTH_SESSION_MAX_AGE": "60",
                       "HERMCP_AUTH_SESSION_HARD_CAP": "120", "HERMCP_AUTH_SESSION_MAX": "3"})
    assert (s.auth_session_max_age, s.auth_session_hard_cap, s.auth_session_max) == (60.0, 120.0, 3)


def test_an_invalid_limit_falls_back_to_the_default_and_says_so(home, caplog):
    with caplog.at_level(logging.WARNING, logger="hermcp.settings"):
        s = load_settings({"HERMES_HOME": str(home), "HERMCP_AUTH_SESSION_MAX_AGE": "viele",
                           "HERMCP_AUTH_SESSION_HARD_CAP": "0", "HERMCP_AUTH_SESSION_MAX": "-2"})
    assert (s.auth_session_max_age, s.auth_session_hard_cap, s.auth_session_max) == (1800.0, 3600.0, 8)
    for name in ("HERMCP_AUTH_SESSION_MAX_AGE", "HERMCP_AUTH_SESSION_HARD_CAP", "HERMCP_AUTH_SESSION_MAX"):
        assert any(name in r.getMessage() for r in caplog.records), name
