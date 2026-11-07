"""systemd managers of the ops tools.

HERMCP_SYSTEMD_SCOPE       manager of the Hermes gateway unit   (gateway_restart, ops_gateway_logs)
HERMCP_SELF_SYSTEMD_SCOPE  manager of hermcp.service itself     (hermcp_restart payload)

The timer of the self-restart lives in the user manager as soon as either scope is `user`.
Nothing is executed: subprocess.run is mocked and every call (argv + env) is recorded.
"""
import os
import subprocess

import pytest

from hermcp.registry import Principal
from hermcp.scopes import Scopes
from hermcp.settings import ConfigError, load_settings
from hermcp.tools import ops as O

SYSTEMCTL = O.shutil.which("systemctl") or "/usr/bin/systemctl"


def _legacy_argv(stamp):
    """The self-restart argv of origin/main before any scope setting existed (ee27296), spelled out literally."""
    return ["systemd-run", "--collect", "--no-ask-password", f"--unit=hermcp-selfrestart-{stamp}",
            "--on-active=3s", "--timer-property=AccuracySec=100ms", SYSTEMCTL, "restart", "hermcp.service"]


class Calls:
    def __init__(self):
        self.calls = []        # (argv, env)

    def __call__(self, argv, **kw):
        self.calls.append((list(argv), kw.get("env")))
        out = "active\n" if "is-active" in argv else ""
        return subprocess.CompletedProcess(argv, 0, out, "")


@pytest.fixture()
def mocked(monkeypatch):
    c = Calls()
    monkeypatch.setattr(O.subprocess, "run", c)
    return c


def _app(home, gateway=None, own=None):
    from hermcp.app import create_app
    env = {"HERMES_HOME": str(home), "HERMCP_ALLOWED_HOSTS": "testserver,127.0.0.1"}
    if gateway is not None:
        env["HERMCP_SYSTEMD_SCOPE"] = gateway
    if own is not None:
        env["HERMCP_SELF_SYSTEMD_SCOPE"] = own
    return create_app(load_settings(env))


def _call(app, tool, args):
    return app.registry.invoke(tool, args, Principal("k", "k", Scopes(access="admin")), app.settings, None)


# -- settings --------------------------------------------------------------------------------------
def test_defaults_are_system_for_both():
    s = load_settings({})
    assert (s.systemd_scope, s.self_systemd_scope) == ("system", "system")


def test_scopes_are_read_case_insensitively_and_independently():
    s = load_settings({"HERMCP_SYSTEMD_SCOPE": " User ", "HERMCP_SELF_SYSTEMD_SCOPE": "SYSTEM"})
    assert (s.systemd_scope, s.self_systemd_scope) == ("user", "system")


@pytest.mark.parametrize("var", ["HERMCP_SYSTEMD_SCOPE", "HERMCP_SELF_SYSTEMD_SCOPE"])
def test_invalid_scope_is_rejected_naming_the_variable(var):
    with pytest.raises(ConfigError, match=var):
        load_settings({var: "global"})
    assert issubclass(ConfigError, ValueError)


def test_lenient_load_never_raises_and_falls_back_to_system():
    s = load_settings({"HERMCP_SYSTEMD_SCOPE": "x", "HERMCP_SELF_SYSTEMD_SCOPE": "y"}, strict=False)
    assert (s.systemd_scope, s.self_systemd_scope) == ("system", "system")
    assert len(s.invalid_scopes) == 2


# -- gateway_restart: the gateway's manager --------------------------------------------------------
def test_gateway_restart_system(home, mocked):
    r = _call(_app(home), "gateway_restart", {"confirm": True})
    assert r["ok"], r
    argv, env = mocked.calls[0]
    assert argv == ["systemctl", "restart", "hermes-gateway.service"] and env is None


def test_gateway_restart_user_derives_the_runtime_dir(home, mocked, monkeypatch):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    r = _call(_app(home, "user"), "gateway_restart", {"confirm": True})
    assert r["ok"], r
    argv, env = mocked.calls[0]
    assert argv == ["systemctl", "--user", "restart", "hermes-gateway.service"]
    assert env["XDG_RUNTIME_DIR"] == f"/run/user/{os.getuid()}"


def test_gateway_restart_ignores_the_self_scope(home, mocked):
    _call(_app(home, "system", "user"), "gateway_restart", {"confirm": True})
    assert mocked.calls[0][0] == ["systemctl", "restart", "hermes-gateway.service"]


def test_user_scope_keeps_an_existing_xdg_runtime_dir(home, mocked, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/custom")
    _call(_app(home, "user"), "gateway_restart", {"confirm": True})
    assert mocked.calls[0][1]["XDG_RUNTIME_DIR"] == "/run/custom"


@pytest.mark.parametrize("scope,flag", [("system", ""), ("user", " --user")])
def test_gateway_restart_plan_names_the_real_command(home, mocked, scope, flag):
    res = _call(_app(home, scope), "gateway_restart", {})["result"]
    assert res["applied"] is False and res["command"] == f"systemctl{flag} restart hermes-gateway.service"
    assert res["systemd_scope"] == scope and "running_agents" in res     # agents_live preview is kept
    assert mocked.calls == []


# -- ops_gateway_logs: the gateway's journal ----------------------------------------------------------
def test_gateway_logs_system_scope_reads_the_system_journal(home, mocked):
    r = _call(_app(home), "ops_gateway_logs", {"lines": 5})
    assert r["ok"], r
    argv, env = mocked.calls[0]
    assert argv[:3] == ["journalctl", "-u", "hermes-gateway.service"] and "--user" not in argv and env is None


def test_gateway_logs_user_scope_reads_the_user_journal(home, mocked, monkeypatch):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    r = _call(_app(home, "user"), "ops_gateway_logs", {"lines": 5, "since": "1 hour ago"})
    assert r["ok"], r
    argv, env = mocked.calls[0]
    assert argv[:4] == ["journalctl", "--user", "-u", "hermes-gateway.service"]
    assert argv[-2:] == ["--since", "1 hour ago"]
    assert env["XDG_RUNTIME_DIR"] == f"/run/user/{os.getuid()}"


# -- hermcp_restart: the supported combinations ---------------------------------------------------------
def _restart(home, mocked, gateway, own):
    r = _call(_app(home, gateway, own), "hermcp_restart", {"confirm": True})
    assert r["ok"], r
    return r["result"], mocked.calls


def test_system_system_is_identical_to_the_old_behaviour(home, mocked):
    res, calls = _restart(home, mocked, None, None)
    argv, env = calls[0]
    assert argv == _legacy_argv(argv[3].split("-")[-1])          # same argv as ee27296, nothing added
    assert env is None and calls[1][0][1:3] == ["is-active", argv[3].split("=")[1] + ".timer"] and calls[1][1] is None
    assert (res["systemd_scope"], res["timer_scope"]) == ("system", "system")


def test_user_gateway_system_hermcp_timer_in_user_manager_payload_in_system_manager(home, mocked, monkeypatch):
    """Gateway is a user unit, hermcp.service a system unit (restart authorised by e.g. a polkit rule)."""
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    res, calls = _restart(home, mocked, "user", "system")
    argv, env = calls[0]
    assert argv[:2] == ["systemd-run", "--user"]                 # timer: user manager, needs no rights
    assert argv[-3:] == [SYSTEMCTL, "restart", "hermcp.service"] and "--user" not in argv[-3:]   # payload: system manager
    assert env["XDG_RUNTIME_DIR"] == f"/run/user/{os.getuid()}"
    probe, penv = calls[1]
    assert probe[1:3] == ["--user", "is-active"] and penv["XDG_RUNTIME_DIR"] == env["XDG_RUNTIME_DIR"]
    assert (res["systemd_scope"], res["timer_scope"]) == ("system", "user")


def test_user_user_everything_in_the_user_manager(home, mocked, monkeypatch):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    res, calls = _restart(home, mocked, "user", "user")
    argv, env = calls[0]
    assert argv[:2] == ["systemd-run", "--user"] and argv[-4:] == [SYSTEMCTL, "--user", "restart", "hermcp.service"]
    assert env["XDG_RUNTIME_DIR"] == f"/run/user/{os.getuid()}" and calls[1][0][1:3] == ["--user", "is-active"]
    assert (res["systemd_scope"], res["timer_scope"]) == ("user", "user")


def test_system_gateway_user_hermcp_also_works(home, mocked):
    res, calls = _restart(home, mocked, "system", "user")
    argv = calls[0][0]
    assert argv[:2] == ["systemd-run", "--user"] and argv[-4:] == [SYSTEMCTL, "--user", "restart", "hermcp.service"]
    assert (res["systemd_scope"], res["timer_scope"]) == ("user", "user")


def test_the_plan_names_the_exact_command_and_both_scopes(home, mocked):
    res = _call(_app(home, "user", "system"), "hermcp_restart", {})["result"]
    assert res["applied"] is False and mocked.calls == []
    assert res["command"].startswith("systemd-run --user ") and res["command"].endswith("restart hermcp.service")
    assert "--user restart" not in res["command"]
    assert (res["systemd_scope"], res["timer_scope"]) == ("system", "user")


# -- deploy files carry no uid -------------------------------------------------------------------------
def test_no_uid_is_hardcoded_in_the_deploy_files():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "deploy"
    for f in (root / "hermcp.service", root / "hermcp.service.template"):
        assert "/run/user/" not in f.read_text(encoding="utf-8"), f.name


# -- follow-ups from the review of the scope settings (issue #12) ----------------------------------
def test_invalid_configuration_exits_78(monkeypatch):
    """A bad scope must not exit 1: 78 (EX_CONFIG) is what the unit excludes from restarts."""
    from hermcp import cli
    from hermcp.lock import EX_CONFIG
    monkeypatch.setenv("HERMCP_SYSTEMD_SCOPE", "global")
    assert cli.main(["status"]) == EX_CONFIG


def test_units_park_on_config_errors_too():
    """Both shipped units must list 78 next to 75, or systemd flaps every 5 s on a typo."""
    import pathlib
    deploy = pathlib.Path(__file__).parent.parent / "deploy"
    for name in ("hermcp.service", "hermcp.service.template"):
        assert "RestartPreventExitStatus=75 78" in (deploy / name).read_text(), name


def test_hermes_bin_tolerates_an_invalid_scope(monkeypatch):
    """doctor reports the scope itself; the binary lookup must not add a second FAIL row."""
    from hermcp.adapters import common as C
    monkeypatch.setenv("HERMCP_SYSTEMD_SCOPE", "global")
    assert isinstance(C.hermes_bin(), str)


def test_restart_unit_candidates_tolerate_an_invalid_scope(monkeypatch):
    from hermcp import restart_units
    monkeypatch.setenv("HERMCP_SELF_SYSTEMD_SCOPE", "global")
    assert isinstance(restart_units._candidate_bins(), list)
