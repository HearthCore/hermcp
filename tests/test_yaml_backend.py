"""`hermcp._yaml`: one surface over PyYAML and ruamel.yaml.

These tests must pass on a host that has only ruamel.yaml (Hermes 0.23 ships no PyYAML), so
nothing here imports PyYAML at module level — the shim is reloaded with a backend forced instead.
"""
import importlib
import io
import re
import sys
from pathlib import Path

import pytest

import hermcp._yaml as YAML_SHIM

# PyYAML 6 output for PAYLOAD; the ruamel path has to reproduce it byte for byte.
PAYLOAD = {"model": {"default": "test-model"}, "plugins": {"enabled": ["hermcp"]}, "flag": "off"}
PAYLOAD_DUMP = "flag: 'off'\nmodel:\n  default: test-model\nplugins:\n  enabled:\n  - hermcp\n"


@pytest.fixture()
def backend(monkeypatch):
    """Reloads hermcp._yaml with one backend forced, and restores the real one afterwards."""
    def load(name: str):
        monkeypatch.delitem(sys.modules, "yaml", raising=False)
        if name == "ruamel":
            monkeypatch.setitem(sys.modules, "yaml", None)      # `import yaml` raises ImportError
        else:
            pytest.importorskip("yaml")                         # PyYAML absent: nothing to test
        mod = importlib.reload(YAML_SHIM)
        assert mod.BACKEND == name
        return mod

    yield load
    monkeypatch.undo()
    importlib.reload(YAML_SHIM)                                 # back to the installed default


BOTH = pytest.mark.parametrize("name", ["pyyaml", "ruamel"])


@BOTH
def test_load_resolves_yaml_1_1_booleans(backend, name):
    y = backend(name)
    assert y.safe_load("a: yes\nb: off\nc: No\n") == {"a": True, "b": False, "c": False}


@BOTH
def test_load_accepts_str_bytes_and_stream(backend, name):
    y = backend(name)
    assert y.safe_load("model:\n  default: m\n") == {"model": {"default": "m"}}
    assert y.safe_load(b"model:\n  default: m\n") == {"model": {"default": "m"}}
    assert y.safe_load(io.StringIO("model:\n  default: m\n")) == {"model": {"default": "m"}}
    assert y.safe_load(io.BytesIO(b"model:\n  default: m\n")) == {"model": {"default": "m"}}
    assert y.safe_load("") is None


@BOTH
def test_dump_matches_pyyaml_output(backend, name):
    y = backend(name)
    assert y.safe_dump(PAYLOAD) == PAYLOAD_DUMP


@BOTH
def test_dump_honours_sort_keys_and_flow_style(backend, name):
    y = backend(name)
    assert y.safe_dump({"b": 2, "a": 1}, sort_keys=False) == "b: 2\na: 1\n"
    assert y.safe_dump({"b": 2, "a": 1}, default_flow_style=True) == "{a: 1, b: 2}\n"


@BOTH
def test_dump_writes_to_stream_and_returns_none(backend, name):
    y = backend(name)
    out = io.StringIO()
    assert y.safe_dump({"a": 1}, out) is None
    assert out.getvalue() == "a: 1\n"


@BOTH
def test_dumped_values_reload_as_the_same_objects(backend, name):
    """Quoting must not let a string come back as a bool, an int or None."""
    y = backend(name)
    data = {"model": {"default": "off"}, "names": ["yes", "0012", "null", "a: b", ""], "n": 3, "empty": None}
    assert y.safe_load(y.safe_dump(data)) == data


@BOTH
def test_invalid_yaml_raises_the_module_error(backend, name):
    y = backend(name)
    with pytest.raises(y.YAMLError):
        y.safe_load("model: [unclosed\n")


def test_import_prefers_pyyaml_when_it_is_importable():
    """Without a poisoned sys.modules the shim takes PyYAML; ruamel is only the fallback."""
    pytest.importorskip("yaml")
    assert importlib.reload(YAML_SHIM).BACKEND == "pyyaml"


def test_no_module_imports_pyyaml_directly():
    """The shim is only worth anything if every production module goes through it."""
    root = Path(__file__).resolve().parents[1]
    direct = re.compile(r"^\s*(?:import yaml|from yaml import)\s*$", re.MULTILINE)
    offenders = [str(p.relative_to(root)) for p in sorted(root.rglob("*.py"))
                 if "tests" not in p.relative_to(root).parts and direct.search(p.read_text(encoding="utf-8"))]
    assert offenders == []
