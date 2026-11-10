"""One YAML surface for hermcp: PyYAML when importable, `ruamel.yaml` otherwise.

Hermes ships `ruamel.yaml` (it parses every config itself) but not always PyYAML: a 0.23 install
came without it, and hermcp imported `yaml` directly in eleven modules, so it only ran there
through a PYTHONPATH shim that any Hermes update can break — and a broken shim surfaces as an
ImportError inside a tool call, not at start-up. Those modules now do
`from . import _yaml as yaml`; their call sites (`yaml.safe_load`, `yaml.safe_dump`,
`except yaml.YAMLError`) stay unchanged.

Both backends are made to behave the same for those calls:

- loading resolves through YAML 1.1, so `yes`/`no`/`on`/`off` are booleans in either backend
  (PyYAML's own default, and how Hermes reads its configs);
- dumping is block style, key-sorted, with PyYAML's mapping/sequence indentation;
- the ruamel emitter quotes a string exactly when PyYAML would, so a value written here reloads as
  the same string (`default: 'off'` stays the string "off" instead of becoming False).

Known cosmetic difference: ruamel's YAML 1.1 resolver also treats the single letters `y`/`n` as
booleans, so such a key is quoted (and loads as a bool) under ruamel where PyYAML keeps it a
string. Hermes' own parser (`hermes_yaml`) has the same behaviour.
"""
from __future__ import annotations

import io
from typing import Any, IO

__all__ = ["BACKEND", "YAMLError", "safe_dump", "safe_load"]

_STR_TAG = "tag:yaml.org,2002:str"


def _document(stream: str | bytes | IO[str] | IO[bytes]) -> str | bytes:
    """Read text/bytes streams so both backends accept the same arguments."""
    return stream if isinstance(stream, (str, bytes)) else stream.read()


try:                                        # PyYAML: preferred when the install provides it
    import yaml as _pyyaml
except ImportError:                         # Hermes 0.23: only ruamel.yaml is there
    _pyyaml = None


if _pyyaml is not None:
    BACKEND = "pyyaml"
    YAMLError = _pyyaml.YAMLError

    def safe_load(stream: str | bytes | IO[str] | IO[bytes]) -> Any:
        return _pyyaml.safe_load(_document(stream))

    def safe_dump(
        data: Any,
        stream: IO[str] | None = None,
        *,
        default_flow_style: bool = False,
        sort_keys: bool = True,
        allow_unicode: bool | None = None,
        width: int | None = None,
    ) -> str | None:
        return _pyyaml.safe_dump(
            data, stream, default_flow_style=default_flow_style, sort_keys=sort_keys,
            allow_unicode=allow_unicode, width=width,
        )

else:
    from ruamel.yaml import YAML
    from ruamel.yaml.error import YAMLError as _RuamelYAMLError
    from ruamel.yaml.nodes import ScalarNode
    from ruamel.yaml.representer import SafeRepresenter
    from ruamel.yaml.resolver import VersionedResolver

    BACKEND = "ruamel"
    YAMLError = _RuamelYAMLError

    class _Yaml11Resolver(VersionedResolver):
        """YAML 1.1 resolution without writing a `%YAML 1.1` directive into the output."""

        @property
        def processing_version(self) -> tuple[int, int]:
            return (1, 1)

    class _QuotingRepresenter(SafeRepresenter):
        """Quote strings that would reload as something else (bool, int, null, ...)."""

        def represent_str(self, data: str) -> ScalarNode:
            node = SafeRepresenter.represent_str(self, data)
            if node.style is None and self.dumper.resolver.resolve(ScalarNode, data, (True, False)) != _STR_TAG:
                node.style = "'"
            return node

    _QuotingRepresenter.add_representer(str, _QuotingRepresenter.represent_str)

    def _instance(
        *, default_flow_style: bool = False, sort_keys: bool = True,
        allow_unicode: bool | None = None, width: int | None = None,
    ) -> YAML:
        # A YAML instance is not thread-safe, so every call builds its own (cheap: the parser and
        # representer tables are class-level).
        yaml = YAML(typ="safe")
        yaml.Resolver = _Yaml11Resolver
        yaml.Representer = _QuotingRepresenter
        yaml.default_flow_style = default_flow_style
        yaml.sort_base_mapping_type_on_output = sort_keys
        yaml.allow_unicode = bool(allow_unicode)
        if width is not None:
            yaml.width = width
        yaml.indent(mapping=2, sequence=2, offset=0)   # PyYAML's layout: "- " under its key
        return yaml

    def safe_load(stream: str | bytes | IO[str] | IO[bytes]) -> Any:
        return _instance().load(_document(stream))

    def safe_dump(
        data: Any,
        stream: IO[str] | None = None,
        *,
        default_flow_style: bool = False,
        sort_keys: bool = True,
        allow_unicode: bool | None = None,
        width: int | None = None,
    ) -> str | None:
        yaml = _instance(default_flow_style=default_flow_style, sort_keys=sort_keys,
                         allow_unicode=allow_unicode, width=width)
        if stream is not None:
            yaml.dump(data, stream)
            return None
        out = io.StringIO()
        yaml.dump(data, out)
        return out.getvalue()
