"""`doctor` names the yaml shim as the host mounted this package, not as a top-level `hermcp`.

A directory plugin is imported as `hermes_plugins.<slug>` (hermes_cli.plugins_loader) and no top-level
`hermcp` is importable then — so an absolute `import hermcp._yaml` check reported FAIL on a plugin
install while `python -m hermcp doctor` from the plugin dir was fine. The row now resolves the shim
through `__package__`, which is the same module in both contexts.

`plugin_context` reproduces the plugin install: the package is loaded under the loader's name, and a
bare `hermcp` is taken off both sys.modules and sys.path.
"""
import importlib
import importlib.util
import re
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NS_PARENT = "hermes_plugins"
CONTEXT = f"{NS_PARENT}.hermcp"


@pytest.fixture()
def isolated_env(home, monkeypatch):
    """Nothing real is probed and no scope value from the caller's shell leaks in."""
    monkeypatch.setenv("HERMCP_HERMES_AGENT_DIR", str(home))
    for k in ("HERMCP_SYSTEMD_SCOPE", "HERMCP_SELF_SYSTEMD_SCOPE"):
        monkeypatch.delenv(k, raising=False)
    return home


@pytest.fixture()
def plugin_context(monkeypatch):
    """Load the package the way the plugin loader does, with a bare `hermcp` unreachable."""
    created = []
    if NS_PARENT not in sys.modules:
        ns = types.ModuleType(NS_PARENT)
        ns.__path__ = []                       # type: ignore[attr-defined]
        ns.__package__ = NS_PARENT
        sys.modules[NS_PARENT] = ns
        created.append(NS_PARENT)
    for name in [n for n in sys.modules if n == "hermcp" or n.startswith("hermcp.")]:
        monkeypatch.delitem(sys.modules, name)
    # conftest puts the checkout's parent on sys.path so tests can `import hermcp`; a plugin install
    # does not have that, and the whole point here is the name it lacks.
    monkeypatch.setattr(sys, "path", [p for p in sys.path if Path(p or ".").resolve() != ROOT.parent])

    spec = importlib.util.spec_from_file_location(CONTEXT, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    module.__package__ = CONTEXT
    module.__path__ = [str(ROOT)]              # type: ignore[attr-defined]
    sys.modules[CONTEXT] = module
    try:
        spec.loader.exec_module(module)
        yield CONTEXT
    finally:
        for name in [n for n in sys.modules if n == CONTEXT or n.startswith(CONTEXT + ".")]:
            del sys.modules[name]
        for name in created:
            sys.modules.pop(name, None)


def test_doctor_checks_the_shim_under_the_mounted_name(plugin_context, isolated_env, capsys):
    with pytest.raises(ImportError):                    # the premise: no top-level hermcp here
        importlib.import_module("hermcp._yaml")

    doctor = importlib.import_module(f"{plugin_context}.doctor")
    doctor.run()

    out = capsys.readouterr().out
    assert f"[ok] import {CONTEXT}._yaml" in out
    assert "yaml backend:" in out
    assert not re.search(r"^\[FAIL\] import .*_yaml", out, re.MULTILINE)


def test_doctor_keeps_the_top_level_name_in_the_plugin_dir_context(isolated_env, capsys):
    from hermcp import doctor

    doctor.run()

    out = capsys.readouterr().out
    assert "[ok] import hermcp._yaml" in out
    assert not re.search(r"^\[FAIL\] import .*_yaml", out, re.MULTILINE)
