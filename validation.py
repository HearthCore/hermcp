"""Minimal stdlib-only JSON-Schema validator for hermcp tool arguments.

Why this exists: the Hermes venv's ``jsonschema`` import chain is broken in this
environment (``rpds`` native extension missing), and docs/DESIGN.md forbids new heavy
dependencies. MCP tool schemas only use a small, well-defined subset:
``type``, ``properties``, ``required``, ``additionalProperties``, ``items``,
``enum``, ``minimum``, ``maximum``, ``minLength``, ``maxLength``, ``minItems``, ``maxItems``, ``default``.

Semantics are intentionally strict on the things that matter for security
(``additionalProperties: False`` rejects unknown keys, required keys must be
present, types must match) and permissive on the rest.

``pattern`` follows ECMA-262, not Python: see :func:`_compiled_pattern`.
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "number": (int, float),
    "integer": int,
}


class ValidationError(ValueError):
    """Mirrors ``jsonschema.ValidationError`` for the attributes we use."""

    def __init__(self, message: str, path: list[str] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.absolute_path = list(path or [])

    def __str__(self) -> str:  # pragma: no cover - convenience
        return self.message


def _type_ok(value: Any, expected: str) -> bool:
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    py = _TYPES.get(expected)
    return py is None or isinstance(value, py)


def _end_anchored(pattern: str) -> str:
    """Rewrite ECMA-262 ``$`` into Python's ``\\Z``.

    JSON Schema patterns are ECMA-262, where ``$`` (without the ``m`` flag) matches only at the
    very end of the input. Python's ``$`` also matches just before a trailing newline, so
    ``^[A-Z]+$`` accepted ``"ABC\\n"`` — a value that then lands in a ``.env`` line or a shell
    argument with the newline intact. ``\\Z`` is Python's end-of-input anchor.

    Only an unescaped ``$`` outside a character class is rewritten: ``\\$`` and ``[$]`` are a
    literal dollar sign in both dialects.
    """
    out: list[str] = []
    in_class = escaped = False
    for char in pattern:
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif in_class:
            in_class = char != "]"
        elif char == "[":
            in_class = True
        elif char == "$":
            out.append(r"\Z")
            continue
        out.append(char)
    return "".join(out)


@lru_cache(maxsize=256)
def _compiled_pattern(pattern: str) -> re.Pattern[str] | None:
    """Compile *pattern* with JSON-Schema semantics, or ``None`` when it does not compile.

    A pattern that sets the multiline flag keeps Python's ``$``: there it already means "end of
    line", which is what ECMA-262's ``m`` flag means too.
    """
    try:
        original = re.compile(pattern)
    except re.error:
        return None
    if original.flags & re.MULTILINE:
        return original
    try:
        return re.compile(_end_anchored(pattern))
    except re.error:   # pragma: no cover - the rewrite only ever adds a valid escape
        return original


def validate(instance: Any, schema: dict[str, Any], path: list[str] | None = None) -> None:
    """Raise :class:`ValidationError` when *instance* does not satisfy *schema*."""
    path = list(path or [])
    if not isinstance(schema, dict):
        return

    expected = schema.get("type")
    if isinstance(expected, str) and not _type_ok(instance, expected):
        raise ValidationError(f"{_join(path) or 'value'} must be {expected}, got {type(instance).__name__}", path)

    if "enum" in schema and instance not in schema["enum"]:
        allowed = ", ".join(repr(v) for v in schema["enum"])
        raise ValidationError(f"{_join(path) or 'value'} must be one of {allowed}", path)

    pattern = schema.get("pattern")
    if isinstance(pattern, str) and isinstance(instance, str):
        # Security-relevant: tool schemas use `pattern` to reject shell-ish ids.
        compiled = _compiled_pattern(pattern)
        if compiled is not None and compiled.search(instance) is None:
            raise ValidationError(
                f"{_join(path) or 'value'} does not match the required pattern {pattern!r}", path)

    if isinstance(instance, str):
        lo, hi = schema.get("minLength"), schema.get("maxLength")
        if isinstance(lo, int) and len(instance) < lo:
            raise ValidationError(f"{_join(path) or 'value'} must be at least {lo} characters", path)
        if isinstance(hi, int) and len(instance) > hi:
            raise ValidationError(f"{_join(path) or 'value'} must be at most {hi} characters (got {len(instance)})", path)

    if isinstance(instance, list):
        lo, hi = schema.get("minItems"), schema.get("maxItems")
        if isinstance(lo, int) and len(instance) < lo:
            raise ValidationError(f"{_join(path) or 'value'} needs at least {lo} item(s)", path)
        if isinstance(hi, int) and len(instance) > hi:
            raise ValidationError(f"{_join(path) or 'value'} allows at most {hi} item(s)", path)

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        minimum, maximum = schema.get("minimum"), schema.get("maximum")
        if minimum is not None and instance < minimum:
            raise ValidationError(f"{_join(path) or 'value'} must be >= {minimum}", path)
        if maximum is not None and instance > maximum:
            raise ValidationError(f"{_join(path) or 'value'} must be <= {maximum}", path)

    if isinstance(instance, dict):
        props = schema.get("properties") or {}
        for key in schema.get("required") or []:
            if key not in instance:
                raise ValidationError(f"{_join(path + [key]) or 'value'} is required", path + [key])
        if schema.get("additionalProperties") is False:
            extra = sorted(set(instance) - set(props))
            if extra:
                allowed = ", ".join(sorted(props)) or "none"
                raise ValidationError(
                    f"unexpected argument(s) {', '.join(extra)}; allowed: {allowed}", path)
        for key, sub in props.items():
            if key in instance and isinstance(sub, dict):
                validate(instance[key], sub, path + [key])
        return

    if isinstance(instance, list):
        items = schema.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(instance):
                validate(item, items, path + [str(index)])


def _join(path: list[str]) -> str:
    return ".".join(path)
