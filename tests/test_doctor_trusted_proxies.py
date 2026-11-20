"""`hermcp doctor` and `AGENTS.md` name the variable that works: `HERMCP_TRUSTED_PROXIES`.

`HERMCP_TRUST_PROXY` is the old boolean and widens nothing (`client_ip.py`), so advice that tells an
operator to set it produces a server that never believes forwarding headers: the failed-auth limiter
then sees only the proxy's address and every client shares one bucket (issue #63).
"""
from pathlib import Path

import pytest

from hermcp import doctor
from hermcp.settings import load_settings

AGENTS_MD = Path(__file__).resolve().parents[1] / "AGENTS.md"


@pytest.fixture()
def out(home, monkeypatch, capsys):
    """Run doctor against an explicit settings dict, so a developer's own `.env` cannot leak in."""
    def run(**env):
        base = {"HERMES_HOME": str(home), "HERMCP_ALLOWED_HOSTS": "hermes.example.com,127.0.0.1"}
        base.update(env)
        monkeypatch.setattr(doctor, "load_settings", lambda strict=True: load_settings(base, strict=False))
        doctor.run()
        return capsys.readouterr().out
    return run


def test_public_url_without_trusted_proxies_warns_about_the_working_variable(out):
    text = out(HERMCP_PUBLIC_URL="https://hermes.example.com/mcp")
    assert "[warn] HERMCP_TRUSTED_PROXIES is empty" in text
    assert "HERMCP_TRUST_PROXY" not in text


def test_a_configured_trusted_proxy_is_not_warned_about(out):
    text = out(HERMCP_PUBLIC_URL="https://hermes.example.com/mcp", HERMCP_TRUSTED_PROXIES="10.1.0.9")
    assert "HERMCP_TRUSTED_PROXIES is empty" not in text
    assert "HERMCP_TRUST_PROXY" not in text


def test_the_deprecated_boolean_is_reported_as_doing_nothing(out):
    text = out(HERMCP_PUBLIC_URL="https://hermes.example.com/mcp",
               HERMCP_TRUSTED_PROXIES="10.1.0.9", HERMCP_TRUST_PROXY="1")
    assert "[warn] HERMCP_TRUST_PROXY is deprecated" in text
    assert "HERMCP_TRUSTED_PROXIES=<proxy address>" in text
    assert "HERMCP_TRUSTED_PROXIES is empty" not in text


def test_agents_md_only_names_the_deprecated_boolean_as_deprecated():
    """The install doc is followed literally, so every mention of the old flag must say it does nothing."""
    lines = [ln for ln in AGENTS_MD.read_text(encoding="utf-8").splitlines() if "HERMCP_TRUST_PROXY" in ln]
    assert lines, "AGENTS.md should still explain why the old flag is not used"
    for line in lines:
        assert "deprecated" in line, line
    assert "HERMCP_TRUSTED_PROXIES=" in AGENTS_MD.read_text(encoding="utf-8")
