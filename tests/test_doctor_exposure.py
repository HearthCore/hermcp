"""`doctor` names the Ledger exposure the docs call a misconfiguration.

Issue #62: doctor ended with `ALL OK` on an install that serves the Ledger with
`HERMCP_LEDGER_AUTH=none` on a non-loopback bind, so every host that can reach the port reads
`/ledger` without a key. The row is advice, not a hard failure: `serve` keeps starting, because a
LAN address is a documented setup. The other half of #62 — the proxy warning that tested the
deprecated `HERMCP_TRUST_PROXY` — is pinned by `tests/test_doctor_trusted_proxies.py`.
"""
import pytest

from hermcp import doctor
from hermcp.settings import Settings, ledger_warnings


@pytest.fixture()
def env(home, monkeypatch):
    """Nothing real is probed and no exposure setting from the caller's shell leaks in."""
    monkeypatch.setenv("HERMCP_HERMES_AGENT_DIR", str(home))
    monkeypatch.setenv("HERMES_API_URL", "http://127.0.0.1:1")     # instant refusal, no live probe
    for k in ("HERMCP_LEDGER", "HERMCP_LEDGER_AUTH", "HERMCP_HOST", "HERMCP_PORT",
              "HERMCP_PUBLIC_URL", "HERMCP_TRUSTED_PROXIES", "HERMCP_TRUST_PROXY"):
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


# ---------------------------------------------------------------------------
# the rule itself
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.2", "::1", "[::1]", "localhost", "LOCALHOST"])
def test_a_loopback_bind_is_not_an_exposure(host):
    assert ledger_warnings(Settings(ledger=True, ledger_auth="none", host=host)) == []


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "198.51.100.5", "203.0.113.7", "hermes.lan", ""])
def test_a_non_loopback_bind_is_reported_with_the_fix(host):
    msgs = ledger_warnings(Settings(ledger=True, ledger_auth="none", host=host))
    assert len(msgs) == 1
    assert f"non-loopback bind ({host})" in msgs[0]
    assert "HERMCP_LEDGER_AUTH=key" in msgs[0]


def test_a_key_authenticated_ledger_is_not_an_exposure():
    assert ledger_warnings(Settings(ledger=True, ledger_auth="key", host="0.0.0.0")) == []


def test_a_disabled_ledger_is_not_an_exposure():
    assert ledger_warnings(Settings(ledger=False, ledger_auth="none", host="0.0.0.0")) == []


# ---------------------------------------------------------------------------
# doctor's rows
# ---------------------------------------------------------------------------

def test_doctor_warns_about_an_unauthenticated_ledger_on_a_non_loopback_bind(env, capsys):
    env.setenv("HERMCP_LEDGER", "1")
    env.setenv("HERMCP_LEDGER_AUTH", "none")
    env.setenv("HERMCP_HOST", "0.0.0.0")
    env.setenv("HERMCP_PUBLIC_URL", "https://hermes.example.com/mcp")   # the only other FAIL row
    rc = doctor.run()
    out = capsys.readouterr().out
    assert rc == 0                                          # advice: serve still starts
    assert "[warn] Ledger exposure: HERMCP_LEDGER_AUTH=none on a non-loopback bind (0.0.0.0)" in out
    assert "HERMCP_LEDGER_AUTH=key" in out


def test_doctor_is_quiet_about_a_loopback_ledger(env, capsys):
    env.setenv("HERMCP_LEDGER", "1")
    env.setenv("HERMCP_LEDGER_AUTH", "none")
    env.setenv("HERMCP_HOST", "127.0.0.1")
    doctor.run()
    out = capsys.readouterr().out
    assert "[warn] Ledger exposure" not in out
    assert "[info] Ledger: /ledger served, auth=none" in out


def test_doctor_says_nothing_about_the_ledger_when_it_is_off(env, capsys):
    env.setenv("HERMCP_HOST", "0.0.0.0")
    doctor.run()
    assert "Ledger" not in capsys.readouterr().out
