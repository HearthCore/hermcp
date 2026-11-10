"""Hermes config adapter: reads/writes through hermes_cli.config (never raw YAML edits)."""
from __future__ import annotations

import re
import shutil
import time
from pathlib import Path
from typing import Any

from .. import _yaml as yaml

from ..redact import Label, _nest, diff_changes, redact, scrub
from ..registry import AdapterError, ToolError
from .common import call_inproc, captured, ensure_hermes_importable, hermes_scope

_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
BACKUP_PREFIX = "config.yaml.hermcp-"
KEEP_BACKUPS = 20
_MISSING = object()


def cfg_mod():
    ensure_hermes_importable()
    try:
        import hermes_cli.config as c
        return c
    except ImportError as exc:
        raise AdapterError(f"cannot import hermes_cli.config: {exc}") from exc


def is_env_style(key: str) -> bool:
    """UPPER_SNAKE names are routed to .env by Hermes (secrets/env). Not editable via config tools."""
    return bool(_ENV_NAME.match(key))


def mask(key: str, value: Any, partial: bool = True) -> Any:
    """Output-only redaction of a config value under dotted `key` (hermcp.redact, strict profile, READ style).
    The whole key path is used as context (an `env` or `headers` ancestor makes every leaf a credential).
    The upstream masker (head/tail characters, last key segment only, no lists/URLs) is not used."""
    parts = key.split(".")
    out = redact(_nest(key, value), strict=True, partial=partial)
    for p in parts:
        out = out[p] if isinstance(out, dict) and p in out else None
    return out


def public(plan: dict[str, Any]) -> dict[str, Any]:
    """A plan without its private bookkeeping (keys starting with `_`, e.g. raw secrets for scrubbing)."""
    return {k: v for k, v in plan.items() if not k.startswith("_")}


def _diff_plan(key: str, before: Any, after: Any) -> dict[str, Any]:
    d = diff_changes(key, before, after, strict=True)
    plan: dict[str, Any] = {"key": Label(key), "changes": d["changes"], "_secrets": d["secrets"]}
    if d.get("truncated"):
        plan["truncated"] = d["truncated"]
    return plan


def get_value(profile: str, key: str | None) -> dict[str, Any]:
    with hermes_scope(profile):
        c = cfg_mod()
        cfg = call_inproc(c.load_config)
        if not key:
            return {"key": None, "exists": True, "value": redact(cfg, strict=True, partial=True)}
        if is_env_style(key):
            return {"key": key, "exists": c.get_env_value(key) is not None, "value": "[set]" if c.get_env_value(key) else None,
                    "note": "env/secret names are never returned"}
        val = c._get_nested(cfg, key)
        if val is c._MISSING:
            return {"key": key, "exists": False, "value": None}
        return {"key": key, "exists": True, "value": mask(key, val)}


def _config_path(profile: str) -> Path:
    c = cfg_mod()
    return Path(c.get_config_path())


def make_backup(profile: str) -> str | None:
    with hermes_scope(profile):
        p = _config_path(profile)
        if not p.exists():
            return None
        dest = p.with_name(f"{BACKUP_PREFIX}{time.strftime('%Y%m%d-%H%M%S')}-{int(time.time() * 1000) % 1000:03d}")
        shutil.copy2(p, dest)
        olds = sorted(p.parent.glob(BACKUP_PREFIX + "*"))
        for old in olds[:-KEEP_BACKUPS]:
            old.unlink(missing_ok=True)
        return dest.name


def list_backups(profile: str) -> list[dict[str, Any]]:
    with hermes_scope(profile):
        p = _config_path(profile)
        return [{"name": b.name, "size": b.stat().st_size, "mtime": b.stat().st_mtime}
                for b in sorted(p.parent.glob(BACKUP_PREFIX + "*"), reverse=True)]


def validate_file(profile: str) -> list[dict[str, str]]:
    """YAML parse + Hermes structural validation of the profile's config.yaml."""
    with hermes_scope(profile):
        p = _config_path(profile)
        try:
            yaml.safe_load(p.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            return [{"severity": "error", "message": f"config.yaml does not parse: {exc}", "hint": ""}]
        c = cfg_mod()
        issues = call_inproc(c.validate_config_structure)
        return [{"severity": i.severity, "message": i.message, "hint": i.hint} for i in issues]


def check_key(profile: str, key: str) -> tuple[bool, str | None]:
    with hermes_scope(profile):
        return cfg_mod()._validate_config_key(key)


def plan_set(profile: str, key: str, value: str) -> dict[str, Any]:
    with hermes_scope(profile):
        c = cfg_mod()
        before = c._get_nested(call_inproc(c.load_config), key)
        after = call_inproc(c._coerce_config_set_value, key, value)
        known, suggestion = c._validate_config_key(key)
        return {**_diff_plan(key, None if before is c._MISSING else before, after),
                "key_known": known, "suggestion": suggestion, "created": before is c._MISSING}


def apply_set(profile: str, key: str, value: str, force: bool) -> dict[str, Any]:
    plan = plan_set(profile, key, value)
    backup = make_backup(profile)
    with hermes_scope(profile):
        c = cfg_mod()
        with captured() as buf:
            call_inproc(c.set_config_value, key, value, force)
        out = buf.getvalue()
    return _finish(profile, backup, out, plan)


def plan_unset(profile: str, key: str) -> dict[str, Any]:
    with hermes_scope(profile):
        c = cfg_mod()
        before = c._get_nested(call_inproc(c.load_config), key)
        return _diff_plan(key, None if before is c._MISSING else before, None)


def apply_unset(profile: str, key: str) -> dict[str, Any]:
    plan = plan_unset(profile, key)
    backup = make_backup(profile)
    with hermes_scope(profile):
        c = cfg_mod()
        with captured() as buf:
            call_inproc(c.unset_config_value, key)
        out = buf.getvalue()
    return _finish(profile, backup, out, plan)


def _finish(profile: str, backup: str | None, out: str, plan: dict) -> dict[str, Any]:
    secrets = plan.get("_secrets", [])
    plan = public(plan)
    out = scrub(out, secrets)
    issues = [{**i, "message": scrub(i["message"], secrets), "hint": scrub(i["hint"] or "", secrets)}
              for i in validate_file(profile)]
    errors = [i for i in issues if i["severity"] == "error"]
    if errors and backup:
        with hermes_scope(profile):
            p = _config_path(profile)
            shutil.copy2(p.parent / backup, p)
        raise ToolError("validation_failed", "config invalid after change; rolled back from backup",
                        issues=errors, backup=backup)
    return {**plan, "applied": True, "backup": backup, "output": out.strip()[-800:], "warnings": issues}


def restore_backup(profile: str, name: str) -> dict[str, Any]:
    if "/" in name or not name.startswith(BACKUP_PREFIX):
        raise ToolError("invalid_arguments", "not a hermcp backup name")
    with hermes_scope(profile):
        p = _config_path(profile)
        src = p.parent / name
        if not src.is_file():
            raise ToolError("not_found", f"no such backup {name}")
        try:
            yaml.safe_load(src.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ToolError("validation_failed", f"backup does not parse: {exc}") from None
    pre = make_backup(profile)
    with hermes_scope(profile):
        shutil.copy2(src, _config_path(profile))
    return {"restored": name, "previous_saved_as": pre}


# -- structured edits (lists / dicts) ---------------------------------------------------
def get_path(d: Any, path: list[str]) -> Any:
    for p in path:
        if not isinstance(d, dict) or p not in d:
            return _MISSING
        d = d[p]
    return d


def raw_get(profile: str, dotted: str) -> Any:
    """Value of `dotted` in the user's raw config.yaml (no defaults merged); None if absent."""
    with hermes_scope(profile):
        c = cfg_mod()
        raw = call_inproc(c.read_user_config_raw, _config_path(profile))
        v = get_path(raw, dotted.split("."))
        return None if v is _MISSING else v


def modify(profile: str, dotted: str, mutate, dry_run: bool) -> dict[str, Any]:
    """Read-modify-write one raw config path through Hermes' save_config. `mutate(old) -> new`.
    Returns before/after; applies (with backup, validation and rollback) unless dry_run."""
    import copy
    path = dotted.split(".")
    with hermes_scope(profile):
        c = cfg_mod()
        raw = copy.deepcopy(call_inproc(c.read_user_config_raw, _config_path(profile)))
        old = get_path(raw, path)
        before = None if old is _MISSING else copy.deepcopy(old)
        new = mutate(copy.deepcopy(before))
        plan = _diff_plan(dotted, before, new)
        if dry_run or new == before:
            return {**public(plan), "dry_run": dry_run, "applied": False,
                    **({"note": "no change"} if new == before else {})}
        node = raw
        for p in path[:-1]:
            node = node.setdefault(p, {})
            if not isinstance(node, dict):
                raise ToolError("rejected", f"{p} is not a mapping")
        if new is None:
            node.pop(path[-1], None)
        else:
            node[path[-1]] = new
    backup = make_backup(profile)
    with hermes_scope(profile):
        c = cfg_mod()
        with captured() as buf:
            call_inproc(c.save_config, raw)
        out = buf.getvalue()
    return _finish(profile, backup, out, plan)
