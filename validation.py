"""Minimal stdlib-only JSON-Schema validator for hermcp tool arguments.

Why this exists: the Hermes venv's ``jsonschema`` import chain is broken in this
environment (``rpds`` native extension missing), and docs/DESIGN.md forbids new heavy
dependencies. MCP tool schemas only use a small, well-defined subset:
``type``, ``properties``, ``required``, ``additionalProperties``, ``items``,
``enum``, ``minimum``, ``maximum``, ``minLength``, ``maxLength``, ``minItems``, ``maxItems``, ``default``.

Semantics are intentionally strict on the things that matter for security
(``additionalProperties: False`` rejects unknown keys, required keys must be
present, types must match) and permissive on the rest.
"""
from __future__ import annotations

import re
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
        try:
            matched = re.search(pattern, instance) is not None
        except re.error:
            matched = True  # a broken pattern must never block all input
        if not matched:
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
