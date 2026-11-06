"""`hermcp doctor` and the CLI report an invalid systemd scope readably instead of a traceback."""
import pytest

from hermcp import cli, doctor


@pytest.fixture()
def env(home, monkeypatch):
    for k in ("HERMCP_SYSTEMD_SCOPE", "HERMCP_SELF_SYSTEMD_SCOPE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HERMCP_HERMES_AGENT_DIR", str(home))      # nothing real is probed
    return monkeypatch


@pytest.mark.parametrize("var", ["HERMCP_SYSTEMD_SCOPE", "HERMCP_SELF_SYSTEMD_SCOPE"])
def test_doctor_reports_an_invalid_scope_as_a_fail_row_naming_the_variable(env, capsys, var):
    env.setenv(var, "global")
    try:
        rc = doctor.run()                       # must not raise
    except SystemExit:
        rc = 1
    out = capsys.readouterr().out
    assert rc == 1
    assert f"[FAIL] systemd scope: {var} must be 'system' or 'user', got 'global'" in out
    assert "fix:" in out and "Traceback" not in out


def test_doctor_reports_both_invalid_values_in_two_rows(env, capsys):
    env.setenv("HERMCP_SYSTEMD_SCOPE", "a")
    env.setenv("HERMCP_SELF_SYSTEMD_SCOPE", "b")
    doctor.run()
    out = capsys.readouterr().out
    assert out.count("[FAIL] systemd scope:") == 2


def test_doctor_shows_the_effective_scopes_when_valid(env, capsys):
    env.setenv("HERMCP_SYSTEMD_SCOPE", "user")
    doctor.run()
    out = capsys.readouterr().out
    assert "[info] systemd scope: gateway=user, hermcp=system" in out and "[FAIL] systemd scope" not in out


def test_cli_serve_refuses_an_invalid_scope_with_one_readable_error(env, capsys):
    env.setenv("HERMCP_SELF_SYSTEMD_SCOPE", "nope")
    rc = cli.main(["serve"])
    err = capsys.readouterr().err
    assert rc == 1 and "invalid configuration" in err and "HERMCP_SELF_SYSTEMD_SCOPE" in err
    assert "Traceback" not in err and "doctor" in err


def test_approval_hook_keeps_working_with_an_invalid_scope(env):
    """The hook runs inside the gateway: a typo in the scope must not silently switch the approval inbox off."""
    from hermcp import approvals_hook
    env.setenv("HERMCP_SYSTEMD_SCOPE", "bogus")
    assert approvals_hook.enabled() is True
