"""config toolset: get/set/unset (validated + backup), check, backups."""
from __future__ import annotations

from ..adapters import hconfig as H
from ..registry import ToolError
from . import reg

KEY = {"type": "string", "description": "Dotted config key, e.g. model.default or compression.threshold"}


@reg.tool("config_get", toolset="config", profile_aware=True, core=True,
          description=("Read a config value by dotted key (for example model.default), or the whole config without a "
                       "key. Credential-like values are masked (abcd********wxyz, or ******** below 16 characters; write tools show [redacted]); UPPER_CASE env names only report "
                       "whether they are set. Never repeat or try to reconstruct redacted values."),
          properties={"key": KEY})
def config_get(ctx, key: str | None = None):
    return H.get_value(ctx.profile, key)


@reg.tool("config_set", toolset="config", access="write", profile_aware=True, core=True,
          description=("Set a config value by dotted key. The value is passed as a string and type-coerced; the file is "
                       "validated, a backup is made first, and the change is rolled back if the result is invalid. "
                       "UPPER_CASE env names are refused (use secret_set). Returns a diff of the changed keys (secret values never shown: '(secret geändert)') and the "
                       "backup name; use dry_run to preview."),
          properties={"key": KEY, "value": {"type": "string"}, "force": {"type": "boolean", "default": False,
                                                          "description": "Allow replacing a whole section with a scalar and writing a known key under a wrong prefix"}},
          required=["key", "value"])
def config_set(ctx, key: str, value: str, force: bool = False):
    if H.is_env_style(key):
        raise ToolError("rejected", "env/secret names are not editable here; use secret_set (needs secrets_write)")
    if ctx.dry_run:
        return ctx.plan(f"set {key}", **H.public(H.plan_set(ctx.profile, key, value)))
    return H.apply_set(ctx.profile, key, value, force)


@reg.tool("config_unset", toolset="config", access="write", profile_aware=True,
          description=("Remove a user-set config key so it falls back to its default. Backed up first; returns a diff "
                       "of the removed keys (secret values never shown)."),
          properties={"key": KEY}, required=["key"])
def config_unset(ctx, key: str):
    if H.is_env_style(key):
        raise ToolError("rejected", "env/secret names are not editable here")
    if ctx.dry_run:
        return ctx.plan(f"unset {key}", **H.public(H.plan_unset(ctx.profile, key)))
    return H.apply_unset(ctx.profile, key)


@reg.tool("config_check", toolset="config", profile_aware=True,
          description=("Validate config.yaml (YAML parse + Hermes structural checks); optionally check whether one key "
                       "name is known."),
          properties={"key": KEY})
def config_check(ctx, key: str | None = None):
    out = {"issues": H.validate_file(ctx.profile)}
    if key:
        known, sug = H.check_key(ctx.profile, key)
        out["key"] = {"name": key, "known": known, "suggestion": sug}
    out["ok"] = not any(i["severity"] == "error" for i in out["issues"])
    return out


@reg.tool("config_backups", toolset="config", profile_aware=True,
          description="List the config.yaml backups hermcp made before each change (newest first).")
def config_backups(ctx):
    return {"backups": H.list_backups(ctx.profile)}


@reg.tool("config_restore", toolset="config", access="admin", profile_aware=True,
          description=("Restore config.yaml from a hermcp backup (take the name from config_backups). The current file "
                       "is backed up first."),
          properties={"backup": {"type": "string"}}, required=["backup"])
def config_restore(ctx, backup: str):
    if ctx.dry_run:
        return ctx.plan(f"restore {backup}", backup=backup, exists=any(b["name"] == backup for b in H.list_backups(ctx.profile)))
    return H.restore_backup(ctx.profile, backup)
