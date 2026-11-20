"""The suite reads neither the developer's plugin `.env`, the shell's settings nor the live Hermes home.

Issue #81: `load_settings` merges the plugin's `.env` and the process environment, so on a host
that serves the Ledger (`HERMCP_LEDGER=1`, `HERMCP_LEDGER_AUTH=none`, `HERMCP_HOST=0.0.0.0`) the
default-behaviour tests of `tests/test_doctor_exposure.py` and `tests/test_doctor_trusted_proxies.py`
failed while CI stayed green. `tests/conftest.py` blanks every `HERMCP_*` and the host variables
hermcp reads, points the `.env` at an empty temp dir and gives each test an empty `HERMES_HOME`
(issue #87); these tests hold that down against the source.
"""
import inspect
import os
import re
from pathlib import Path

import conftest
from conftest import tracked_files
from hermcp.settings import PKG_DIR, env_file, load_settings


def _env_names_read_by_the_product() -> set[str]:
    """Every environment name a committed product module reads (`environ.get("X")`, `getenv("X")`,
    `g("X")` in load_settings, `"X"` handed to a reader) — scanned from the source."""
    names = set()
    for path in tracked_files("**/*.py"):
        if "tests" in path.relative_to(PKG_DIR).parts or "scripts" in path.relative_to(PKG_DIR).parts:
            continue
        names |= set(re.findall(r'"((?:HERMCP|HERMES)_[A-Z0-9_]+|INVOCATION_ID)"', path.read_text(encoding="utf-8")))
    return names


def test_every_host_setting_the_product_reads_is_blanked():
    """A new variable read from the environment must be covered by conftest, not silently leak.

    `HERMCP_*` is blanked by prefix; anything else must be named in `conftest.HOST_ENV_VARS` (or be
    `HERMES_HOME`, which conftest replaces). Names hermcp only *sets* for a child process (e.g.
    `HERMES_DISABLE_LAZY_INSTALLS`) are listed in IGNORED so the list stays honest.
    """
    read = set(re.findall(r'"((?:HERMCP|HERMES)_[A-Z0-9_]+)"', inspect.getsource(load_settings)))
    assert {"HERMCP_HOST", "HERMES_API_URL"} <= read, "the source scan found no settings — did load_settings move?"
    ignored = {"HERMES_HOME", "HERMES_DISABLE_LAZY_INSTALLS"}
    foreign = {n for n in _env_names_read_by_the_product() if not n.startswith("HERMCP_")} - ignored
    uncovered = {n for n in foreign
                 if n not in conftest.HOST_ENV_VARS and not n.startswith(conftest.HERMES_ENV_PREFIXES)}
    assert not uncovered, f"add these to conftest.HOST_ENV_VARS (or IGNORED if only set): {sorted(uncovered)}"


def test_the_shell_settings_are_gone():
    leaked = [n for n in os.environ if (n.startswith("HERMCP_")
                                        and n not in (*conftest.RUNNER_ENV_VARS, "HERMCP_ENV_FILE", "HERMCP_PORT"))
              or n in conftest.HOST_ENV_VARS and n != "HERMES_API_URL"
              or n.startswith(conftest.HERMES_ENV_PREFIXES)]
    assert leaked == []
    assert os.environ["HERMES_API_URL"] == conftest.UNREACHABLE_API
    assert os.environ["HERMCP_PORT"] == conftest.UNBOUND_PORT


def test_hermes_home_is_never_the_live_one():
    """Without the `home` fixture a test still gets an empty temp home, never `~/.hermes`."""
    from hermcp.settings import hermes_home
    assert hermes_home() != Path.home() / ".hermes"
    assert list(hermes_home().iterdir()) == []


def test_the_plugin_env_file_is_not_the_real_one():
    assert env_file() != PKG_DIR / ".env"
    assert not env_file().exists()


def test_the_defaults_are_the_documented_ones(home, monkeypatch):
    monkeypatch.delenv("HERMCP_PORT")      # conftest pins it to an unbound port; the default is the point here
    monkeypatch.delenv("HERMES_API_URL")
    s = load_settings()
    assert (s.host, s.port, s.public_url) == ("127.0.0.1", 9230, "")
    assert s.hermes_api_url == "http://127.0.0.1:8642"
    assert (s.ledger, s.ledger_auth) == (False, "key")
    assert (s.trust_proxy, s.trusted_proxies) == (False, [])
    assert s.allowed_hosts == ["127.0.0.1", "localhost", "[::1]"]
    assert s.debug_capture is False


def test_the_env_file_override_is_what_load_settings_reads(tmp_path, monkeypatch):
    """The mechanism itself: `HERMCP_ENV_FILE` decides which file is read, and it is honoured."""
    f = tmp_path / "hostile.env"
    f.write_text("HERMCP_LEDGER=1\nHERMCP_LEDGER_AUTH=none\nHERMCP_HOST=0.0.0.0\n")
    monkeypatch.setenv("HERMCP_ENV_FILE", str(f))
    s = load_settings()
    assert (s.ledger, s.ledger_auth, s.host) == (True, "none", "0.0.0.0")


def test_without_the_override_the_plugin_env_file_is_the_default(monkeypatch):
    monkeypatch.delenv("HERMCP_ENV_FILE")
    assert env_file() == PKG_DIR / ".env"
