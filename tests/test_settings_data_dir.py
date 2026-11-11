"""Settings: `Settings.data` must describe the instance, not the surrounding process.

Why this file exists: a test built its settings with an explicit `HERMES_HOME` and then cleaned
up with `shutil.rmtree(settings.data)`. `Settings.data` used to call a module-level `data_dir()`
that re-read `os.environ` on every access, so the settings object pointed at the *live*
`~/.hermes/plugin-data/hermcp` while its caller believed it pointed at a temp dir. A test run
deleted the running server's key store, and every request answered 401 until it was restored.

The contract held down here: whatever env a Settings instance was built with, `settings.data`
resolves inside that env — never inside `os.environ`.
"""
from __future__ import annotations

from pathlib import Path

from hermcp.settings import Settings, load_settings


def test_data_follows_the_instance_env_not_the_process(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "process-home"))
    other = tmp_path / "instance-home"

    s = load_settings({"HERMES_HOME": str(other), "HERMCP_LEDGER_AUTH": "key"})

    assert s.data == other / "plugin-data" / "hermcp"
    assert str(tmp_path / "process-home") not in str(s.data)


def test_data_falls_back_to_the_process_only_without_an_explicit_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "process-home"))

    s = load_settings({"HERMCP_LEDGER_AUTH": "key"})

    assert s.data == tmp_path / "process-home" / "plugin-data" / "hermcp"


def test_data_is_created_and_stays_inside_its_home(tmp_path):
    home = tmp_path / "fresh-home"

    s = load_settings({"HERMES_HOME": str(home)})

    assert s.data.is_dir()
    assert s.data.parent.parent == Path(home)


def test_two_instances_do_not_share_a_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "process-home"))
    a = load_settings({"HERMES_HOME": str(tmp_path / "a")})
    b = load_settings({"HERMES_HOME": str(tmp_path / "b")})

    assert a.data != b.data


def test_a_plain_settings_object_resolves_the_process_home(monkeypatch, tmp_path):
    """`Settings()` without load_settings has no home of its own: process env is the right answer."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "process-home"))

    assert Settings().data == tmp_path / "process-home" / "plugin-data" / "hermcp"
