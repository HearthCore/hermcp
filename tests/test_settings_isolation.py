"""The suite reads neither the developer's plugin `.env` nor the shell's `HERMCP_*` settings.

Issue #81: `load_settings` merges the plugin's `.env` and the process environment, so on a host
that serves the Ledger (`HERMCP_LEDGER=1`, `HERMCP_LEDGER_AUTH=none`, `HERMCP_HOST=0.0.0.0`) the
default-behaviour tests of `tests/test_doctor_exposure.py` and `tests/test_doctor_trusted_proxies.py`
failed while CI stayed green. `tests/conftest.py` blanks every setting `load_settings` reads and
points the `.env` it reads at an empty temp dir; these tests hold that down.
"""
import inspect
import os
import re

import conftest
from hermcp.settings import PKG_DIR, env_file, load_settings


def test_every_setting_load_settings_reads_is_blanked():
    """A new `HERMCP_*` in load_settings must be added to conftest's list, not silently leak."""
    read = set(re.findall(r'"(HERMCP_[A-Z0-9_]+)"', inspect.getsource(load_settings)))
    assert read, "the source scan found no settings — did load_settings move?"
    assert read <= set(conftest.SETTINGS_ENV_VARS), sorted(read - set(conftest.SETTINGS_ENV_VARS))


def test_the_shell_settings_are_gone():
    assert [n for n in conftest.SETTINGS_ENV_VARS if n in os.environ] == []


def test_the_plugin_env_file_is_not_the_real_one():
    assert env_file() != PKG_DIR / ".env"
    assert not env_file().exists()


def test_the_defaults_are_the_documented_ones(home):
    s = load_settings()
    assert (s.host, s.port, s.public_url) == ("127.0.0.1", 9230, "")
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
