"""restart_units.py: locating the Hermes checkout and installing the auto-restart units.

Everything runs against temp directories. systemctl is never called: `shutil.which` is faked where the
install-service command would otherwise reach for it.
"""
import os
import shutil
import stat
from pathlib import Path

import pytest

from hermcp import cli, restart_units as ru
from hermcp.adapters import common


@pytest.fixture(autouse=True)
def _fresh_detection():
    ru.detect_agent_dir.cache_clear()
    yield
    ru.detect_agent_dir.cache_clear()


def _checkout(tmp_path, name="agent", stamp=True):
    root = tmp_path / name
    (root / "hermes_cli").mkdir(parents=True)
    (root / "hermes_cli" / "__init__.py").write_text("", encoding="utf-8")
    (root / "venv" / "bin").mkdir(parents=True)
    launcher = root / "venv" / "bin" / "hermes"
    launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR)
    if stamp:
        (root / "install-stamp.json").write_text('{"builtAt": "2026-01-01T00:00:00+00:00"}', encoding="utf-8")
    return root


# -- resolve_agent_dir ------------------------------------------------------------------------------------
def test_env_override_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMCP_HERMES_AGENT_DIR", str(tmp_path / "custom"))
    monkeypatch.setattr(ru, "detect_agent_dir", lambda: pytest.fail("must not detect when the env is set"))
    assert ru.resolve_agent_dir() == tmp_path / "custom"
    assert common.agent_dir() == tmp_path / "custom"


def test_existing_reference_path_is_kept_without_detecting(tmp_path, monkeypatch):
    monkeypatch.delenv("HERMCP_HERMES_AGENT_DIR", raising=False)
    monkeypatch.setattr(ru, "LEGACY_AGENT_DIR", tmp_path)
    monkeypatch.setattr(ru, "detect_agent_dir", lambda: pytest.fail("reference install must not trigger detection"))
    assert ru.resolve_agent_dir() == tmp_path


def test_detected_when_reference_path_is_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("HERMCP_HERMES_AGENT_DIR", raising=False)
    monkeypatch.setattr(ru, "LEGACY_AGENT_DIR", tmp_path / "nope")
    monkeypatch.setattr(ru, "detect_agent_dir", lambda: tmp_path / "found")
    assert ru.resolve_agent_dir() == tmp_path / "found"


def test_falls_back_to_reference_path_when_nothing_is_found(tmp_path, monkeypatch):
    monkeypatch.delenv("HERMCP_HERMES_AGENT_DIR", raising=False)
    monkeypatch.setattr(ru, "LEGACY_AGENT_DIR", tmp_path / "nope")
    monkeypatch.setattr(ru, "detect_agent_dir", lambda: None)
    assert ru.resolve_agent_dir() == tmp_path / "nope"


# -- detect_agent_dir -------------------------------------------------------------------------------------
def test_detect_via_venv_symlink(tmp_path, monkeypatch):
    root = _checkout(tmp_path)
    link = tmp_path / "bin" / "hermes"
    link.parent.mkdir()
    link.symlink_to(root / "venv" / "bin" / "hermes")
    monkeypatch.setattr(ru, "_candidate_bins", lambda: [link])
    monkeypatch.setattr(ru, "_from_import", lambda b: None)
    assert ru.detect_agent_dir() == root.resolve()


def test_import_answer_wins_over_symlink_layout(tmp_path, monkeypatch):
    root = _checkout(tmp_path)
    other = _checkout(tmp_path, "other")
    monkeypatch.setattr(ru, "_candidate_bins", lambda: [root / "venv" / "bin" / "hermes"])
    monkeypatch.setattr(ru, "_from_import", lambda b: other)
    assert ru.detect_agent_dir() == other


def test_detect_returns_none_without_any_hermes(monkeypatch):
    monkeypatch.setattr(ru, "_candidate_bins", lambda: [])
    assert ru.detect_agent_dir() is None


def test_symlink_layout_needs_a_hermes_cli_package(tmp_path):
    root = _checkout(tmp_path)
    shutil.rmtree(root / "hermes_cli")
    assert ru._from_symlink(root / "venv" / "bin" / "hermes") is None


def _fake_interpreter(tmp_path, prints):
    py = tmp_path / "fakepy"
    py.write_text(f"#!/bin/sh\necho '{prints}'\n", encoding="utf-8")
    py.chmod(0o755)
    binary = tmp_path / "hermes-launcher"
    binary.write_text(f"#!{py}\n", encoding="utf-8")
    binary.chmod(0o755)
    return binary


def test_from_import_reads_where_the_interpreter_finds_hermes_cli(tmp_path):
    root = _checkout(tmp_path)
    binary = _fake_interpreter(tmp_path, f"{root}/hermes_cli/__init__.py")
    assert ru._from_import(binary) == root.resolve()


def test_from_import_is_none_when_the_interpreter_finds_nothing(tmp_path):
    binary = _fake_interpreter(tmp_path, "")
    assert ru._from_import(binary) is None


# -- rendering and installing the units -------------------------------------------------------------------
def test_render_fills_every_placeholder(tmp_path):
    units = ru.render_units(tmp_path / "agent" / "install-stamp.json", tmp_path / "x.sh", "/venv/bin/python", "/bin/systemctl")
    assert set(units) == set(ru.UNIT_NAMES)
    for text in units.values():
        for token in ("@STAMP@", "@SCRIPT@", "@PYTHON@", "@SYSTEMCTL@"):
            assert token not in text
    assert f"PathChanged={tmp_path}/agent/install-stamp.json" in units["hermcp-restart.path"]
    svc = units["hermcp-restart.service"]
    assert f"ExecStart={tmp_path}/x.sh" in svc
    assert 'Environment="HERMCP_PYTHON=/venv/bin/python"' in svc
    assert 'Environment="HERMCP_SYSTEMCTL=/bin/systemctl"' in svc
    assert f'Environment="HERMCP_STAMP={tmp_path}/agent/install-stamp.json"' in svc


def test_render_escapes_percent_for_systemd(tmp_path):
    units = ru.render_units(Path("/odd%dir/install-stamp.json"), tmp_path / "x.sh", "/py", "/sc")
    assert "PathChanged=/odd%%dir/install-stamp.json" in units["hermcp-restart.path"]


def test_plan_skips_with_a_hint_when_there_is_no_stamp(tmp_path):
    root = _checkout(tmp_path, stamp=False)
    units, msgs = ru.plan_restart_units(agent_dir=root)
    assert units == {}
    assert "HERMCP_HERMES_AGENT_DIR" in msgs[0] and "--no-auto-restart" in msgs[0]


def test_install_writes_both_units_and_is_idempotent(tmp_path):
    root = _checkout(tmp_path)
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    written, msgs = ru.install_restart_units(unit_dir, agent_dir=root, python="/py", systemctl="/sc")
    assert sorted(written) == sorted(ru.UNIT_NAMES)
    assert f"PathChanged={root}/install-stamp.json" in (unit_dir / "hermcp-restart.path").read_text(encoding="utf-8")
    written2, msgs2 = ru.install_restart_units(unit_dir, agent_dir=root, python="/py", systemctl="/sc")
    assert written2 == [] and any("up to date" in m for m in msgs2)


def test_install_never_writes_through_a_symlink(tmp_path):
    root = _checkout(tmp_path)
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    tracked = tmp_path / "tracked.path"
    tracked.write_text("KEEP", encoding="utf-8")
    (unit_dir / "hermcp-restart.path").symlink_to(tracked)
    written, msgs = ru.install_restart_units(unit_dir, agent_dir=root, python="/py", systemctl="/sc")
    assert tracked.read_text(encoding="utf-8") == "KEEP"
    assert written == ["hermcp-restart.service"]
    assert any("symlink" in m for m in msgs)


# -- the install-service command --------------------------------------------------------------------------
def test_parser_knows_the_new_flags():
    import argparse
    p = argparse.ArgumentParser()
    cli.setup_parser(p)
    ns = p.parse_args(["install-service", "--no-auto-restart", "--dry-run"])
    assert ns.no_auto_restart is True and ns.dry_run is True
    assert p.parse_args(["install-service"]).no_auto_restart is False


def test_dry_run_prints_the_units_and_writes_nothing(home, tmp_path, monkeypatch, capsys):
    root = _checkout(tmp_path)
    monkeypatch.setenv("HERMCP_HERMES_AGENT_DIR", str(root))
    out = tmp_path / "etc" / "hermcp.service"
    assert cli.main(["install-service", "--dry-run", "--out", str(out)]) == 0
    text = capsys.readouterr().out
    assert f"PathChanged={root}/install-stamp.json" in text
    assert not out.parent.exists()


def test_dry_run_with_no_auto_restart_prints_no_units(home, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HERMCP_HERMES_AGENT_DIR", str(_checkout(tmp_path)))
    assert cli.main(["install-service", "--dry-run", "--no-auto-restart", "--out", str(tmp_path / "hermcp.service")]) == 0
    text = capsys.readouterr().out
    assert "disabled by --no-auto-restart" in text and "PathChanged" not in text


def _install(tmp_path, monkeypatch, *extra):
    monkeypatch.setattr(shutil, "which", lambda name: None)   # no real systemctl, ever
    out = tmp_path / "etc" / "hermcp.service"
    rc = cli.main(["install-service", "--out", str(out), *extra])
    return rc, out


def test_install_service_adds_the_restart_units_by_default(home, tmp_path, monkeypatch):
    monkeypatch.setenv("HERMCP_HERMES_AGENT_DIR", str(_checkout(tmp_path)))
    rc, out = _install(tmp_path, monkeypatch)
    assert rc == 1   # systemctl is faked away: written but not registered
    names = sorted(p.name for p in out.parent.iterdir())
    assert names == ["hermcp-restart.path", "hermcp-restart.service", "hermcp.service"]


def test_install_service_without_auto_restart_writes_only_the_main_unit(home, tmp_path, monkeypatch):
    monkeypatch.setenv("HERMCP_HERMES_AGENT_DIR", str(_checkout(tmp_path)))
    _, out = _install(tmp_path, monkeypatch, "--no-auto-restart")
    assert [p.name for p in out.parent.iterdir()] == ["hermcp.service"]


def test_install_service_skips_the_restart_units_without_a_stamp(home, tmp_path, monkeypatch):
    monkeypatch.setenv("HERMCP_HERMES_AGENT_DIR", str(_checkout(tmp_path, stamp=False)))
    _, out = _install(tmp_path, monkeypatch)
    assert [p.name for p in out.parent.iterdir()] == ["hermcp.service"]


def test_restart_script_is_executable_in_the_repo():
    script = Path(ru.__file__).parent / "deploy" / "systemd" / "hermcp-restart.sh"
    assert os.access(script, os.X_OK)
