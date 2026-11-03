"""providers toolset: provider inventory, provider add, and remote-capable provider auth.

Remote auth is the point of this module: `hermes auth add <provider> --type oauth --no-browser`
prints a verification URL (and, for device-code providers, a user code) and then waits on
stdin. hermcp keeps that process alive on a PTY, hands the URL/code back over MCP so the
user can finish on a phone or another machine, and feeds whatever they paste back into the
still-running process. Nothing secret is ever returned.

Secret tools (`secret_*`) are flagged `secrets=True`: they need the key's `secrets_write`
scope, their arguments are fully redacted in the audit log, and a value is never echoed back.
"""
from __future__ import annotations

import os
import pty
import re
import select
import subprocess
import time
from pathlib import Path

from ..adapters.common import hermes_bin, profile_home
from ..registry import AdapterError, ToolError
from ._cli import run_write
from . import reg

NAME = {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"}
URL_RE = re.compile(r"https?://[^\s<>\"']+")
CODE_RE = re.compile(r"\b[A-Z0-9]{4,8}-[A-Z0-9]{4,8}\b")

# auth_id -> session dict (pty fd, process, transcript, timestamps)
_SESSIONS: dict[str, dict] = {}
_MAX_TRANSCRIPT = 20000


def _sessions_gc(max_age: float = 1800.0) -> None:
    now = time.time()
    for aid, s in list(_SESSIONS.items()):
        if s["proc"].poll() is not None and now - s["last"] > max_age:
            _close(aid)


def _close(auth_id: str) -> None:
    s = _SESSIONS.pop(auth_id, None)
    if not s:
        return
    try:
        if s["proc"].poll() is None:
            s["proc"].terminate()
            try:
                s["proc"].wait(timeout=3)
            except subprocess.TimeoutExpired:
                s["proc"].kill()
    finally:
        try:
            os.close(s["fd"])
        except OSError:
            pass


def _read_available(s: dict, wait: float = 1.0) -> str:
    """Drain the PTY for up to `wait` seconds, returning newly read text."""
    out, deadline = [], time.time() + wait
    while time.time() < deadline:
        r, _, _ = select.select([s["fd"]], [], [], 0.2)
        if not r:
            continue
        try:
            chunk = os.read(s["fd"], 65536)
        except OSError:
            break
        if not chunk:
            break
        text = chunk.decode("utf-8", "replace")
        out.append(text)
        s["transcript"] = (s["transcript"] + text)[-_MAX_TRANSCRIPT:]
        if len(text) < 4096:
            break
    return "".join(out)


def _spawn(argv: list[str]) -> dict:
    master, slave = pty.openpty()
    env = dict(os.environ)
    env.setdefault("TERM", "dumb")
    env["NO_COLOR"] = "1"
    proc = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave,
                            close_fds=True, env=env, start_new_session=True)
    os.close(slave)
    return {"proc": proc, "fd": master, "transcript": "", "created": time.time(), "last": time.time()}


def _interesting(text: str) -> dict:
    url = CODE_RE_ = None
    urls = [u.rstrip(".,)") for u in URL_RE.findall(text)]
    url = urls[-1] if urls else None
    codes = CODE_RE.findall(text)
    CODE_RE_ = codes[-1] if codes else None
    return {"url": url, "user_code": CODE_RE_}


# --------------------------------------------------------------------------- inventory
@reg.tool("providers_list", toolset="providers", core=True,
          description=("Names of configured providers and custom providers, plus a text listing of the pooled "
                       "credentials from `hermes auth list` (redacted by hermcp)."),
          properties={"include_credentials": {"type": "boolean", "default": True}})
def providers_list(ctx, include_credentials=True):
    from ..adapters.common import cli_text
    out: dict = {}
    try:
        from ..adapters import hconfig as H
        cfg = H.get_value(ctx.profile, None).get("value") or {}
        out["providers"] = sorted((cfg.get("providers") or {}).keys())
        out["custom_providers"] = sorted((cfg.get("custom_providers") or {}).keys())
    except Exception as exc:  # pragma: no cover - config adapter optional
        out["config_error"] = str(exc)[:200]
    if include_credentials:
        try:
            out["credentials_text"] = cli_text(["auth", "list"], None, 30).strip()[-4000:]
        except Exception as exc:
            out["credentials_error"] = str(exc)[:300]
    return out


@reg.tool("providers_add", toolset="providers", access="admin", core=True,
          description=(("Register a custom OpenAI-compatible provider (base_url + name of the key env var). Writes "
                        "config.yaml through `hermes config set` (backup + validation). The API key itself goes through "
                        "secret_set, never through this tool.")),
          properties={"name": NAME, "base_url": {"type": "string", "minLength": 8, "maxLength": 400},
                      "key_env": {"type": "string", "pattern": "^[A-Z][A-Z0-9_]{2,64}$"},
                      "models": {"type": "array", "items": {"type": "string"}, "description": "optional model ids"}},
          required=["name", "base_url"])
def providers_add(ctx, name, base_url, key_env=None, models=None):
    if not re.match(r"^https?://", base_url):
        raise ToolError("invalid_arguments", "base_url must start with http:// or https://")
    steps = [[f"providers.{name}.base_url", base_url]]
    if key_env:
        steps.append([f"providers.{name}.key_env", key_env])
    if models:
        steps.append([f"providers.{name}.models", ",".join(models)])
    if ctx.dry_run:
        return ctx.plan(f"would register provider '{name}'", command=steps,
                        note="run with dry_run=false to apply; then secret_set for the API key")
    applied = []
    for key, value in steps:
        run_write(ctx, f"config set {key}", ["config", "set", key, value, "--force"])
        applied.append(key)
    return {"applied": True, "provider": name, "keys": applied,
            "next": f"call secret_set with name={key_env or '<ENV_VAR>'} to store the API key"}


# --------------------------------------------------------------------------- remote auth
@reg.tool("provider_auth_start", toolset="providers", access="write", core=True,
          description=(("Start an OAuth login for a provider WITHOUT a local browser. Returns an auth_id plus the "
                        "verification URL (and the user code when the provider uses device flow) so the user can finish "
                        "it on a phone. Follow up with provider_auth_continue if the provider asks for a pasted code; "
                        "provider_auth_cancel aborts.")),
          properties={"provider": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"},
                      "label": {"type": "string", "maxLength": 120},
                      "credential_type": {"type": "string", "enum": ["oauth", "api-key"], "default": "oauth"},
                      "wait_seconds": {"type": "integer", "minimum": 2, "maximum": 40, "default": 12}},
          required=["provider"])
def provider_auth_start(ctx, provider, label=None, credential_type="oauth", wait_seconds=12):
    _sessions_gc()
    argv = [hermes_bin(), "auth", "add", provider, "--type", credential_type, "--no-browser"]
    if label:
        argv += ["--label", label]
    if ctx.dry_run:
        return ctx.plan(f"would start {credential_type} auth for {provider}", command=argv)
    sess = _spawn(argv)
    auth_id = f"auth_{int(time.time())}_{len(_SESSIONS)}"
    sess["provider"] = provider
    _SESSIONS[auth_id] = sess
    text = _read_available(sess, wait=float(wait_seconds))
    sess["last"] = time.time()
    info = _interesting(text)
    running = sess["proc"].poll() is None
    if not running and sess["proc"].returncode not in (0, None):
        excerpt = sess["transcript"].strip()[-1500:]
        _close(auth_id)
        raise ToolError("cli_error", f"auth command exited {sess['proc'].returncode}: {excerpt}")
    return {
        "auth_id": auth_id, "provider": provider, "running": running,
        "url": info["url"], "user_code": info["user_code"],
        "instructions": ("Open the URL on any device and finish the login. If the provider shows "
                         "a code, it is returned in user_code. If the CLI asks you to paste a code "
                         "or a redirect URL back, call provider_auth_continue with it."),
        "needs_input": running and bool(re.search(r"(?i)(paste|enter|code:|url:)", sess["transcript"][-400:])),
        "output_excerpt": sess["transcript"].strip()[-1200:],
    }


@reg.tool("provider_auth_continue", toolset="providers", access="write",
          description="Send the code or redirect URL the user got from the provider to a running auth session.",
          properties={"auth_id": {"type": "string", "pattern": "^auth_[0-9]+_[0-9]+$"},
                      "input": {"type": "string", "minLength": 1, "maxLength": 4000},
                      "wait_seconds": {"type": "integer", "minimum": 1, "maximum": 40, "default": 8}},
          required=["auth_id", "input"])
def provider_auth_continue(ctx, auth_id, input, wait_seconds=8):
    sess = _SESSIONS.get(auth_id)
    if not sess:
        raise ToolError("not_found", f"unknown or finished auth session {auth_id}")
    if ctx.dry_run:
        return ctx.plan(f"would send {len(input)} chars to {auth_id}")
    try:
        os.write(sess["fd"], (input.strip() + "\r").encode())
    except OSError as exc:
        _close(auth_id)
        raise ToolError("adapter_error", f"auth session closed: {exc}") from exc
    text = _read_available(sess, wait=float(wait_seconds))
    sess["last"] = time.time()
    running = sess["proc"].poll() is None
    rc = sess["proc"].returncode
    done = (not running) and rc == 0
    info = _interesting(text)
    if not running:
        _close(auth_id)
    return {"auth_id": auth_id, "running": running, "exit_code": rc, "success": bool(done),
            "url": info["url"], "user_code": info["user_code"],
            "output_excerpt": text.strip()[-1500:] or sess["transcript"].strip()[-1500:]}


@reg.tool("provider_auth_status", toolset="providers",
          description="State of running auth sessions (no secrets).",
          properties={"auth_id": {"type": "string"}})
def provider_auth_status(ctx, auth_id=None):
    _sessions_gc()
    ids = [auth_id] if auth_id else list(_SESSIONS)
    out = []
    for aid in ids:
        s = _SESSIONS.get(aid)
        if not s:
            out.append({"auth_id": aid, "state": "unknown or finished"})
            continue
        info = _interesting(s["transcript"])
        out.append({"auth_id": aid, "provider": s.get("provider"),
                    "running": s["proc"].poll() is None,
                    "exit_code": s["proc"].returncode,
                    "age_seconds": round(time.time() - s["created"], 1),
                    "url": info["url"], "user_code": info["user_code"],
                    "tail": s["transcript"].strip()[-600:]})
    return {"sessions": out, "count": len(out)}


@reg.tool("provider_auth_cancel", toolset="providers", access="write",
          description="Abort a running auth session and discard its process.",
          properties={"auth_id": {"type": "string", "pattern": "^auth_[0-9]+_[0-9]+$"}},
          required=["auth_id"])
def provider_auth_cancel(ctx, auth_id):
    if auth_id not in _SESSIONS:
        raise ToolError("not_found", f"unknown auth session {auth_id}")
    if ctx.dry_run:
        return ctx.plan(f"would cancel {auth_id}")
    _close(auth_id)
    return {"cancelled": auth_id}


# --------------------------------------------------------------------------- secrets (write-only)
def _env_path(ctx) -> Path:
    return profile_home(ctx.profile) / ".env"


def _read_env(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []


@reg.tool("secret_set", toolset="providers", access="write", profile_aware=True, secrets=True,
          description=(("Store an API key / token in the profile's .env (NAME=value, NAME in UPPER_SNAKE). WRITE-ONLY: "
                        "the value is never returned, never logged and is redacted in the audit log. Use it to hand a "
                        "provider key to Hermes from chat; a restart of the gateway is needed before every process sees "
                        "it.")),
          properties={"name": {"type": "string", "pattern": "^[A-Z][A-Z0-9_]{2,64}$"},
                      "value": {"type": "string", "minLength": 1, "maxLength": 8000}},
          required=["name", "value"])
def secret_set(ctx, name, value):
    path = _env_path(ctx)
    if ctx.dry_run:
        return ctx.plan(f"would set {name} in {path}", path=str(path), bytes=len(value))
    lines = _read_env(path)
    new = [ln for ln in lines if not re.match(rf"^\s*(export\s+)?{re.escape(name)}\s*=", ln)]
    new.append(f"{name}={value}")
    tmp = path.with_suffix(".env.tmp")
    tmp.write_text("\n".join(new) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return {"applied": True, "name": name, "path": str(path), "length": len(value),
            "note": "stored write-only; restart the gateway for the value to reach every process"}


_SECRETISH = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|PASS|CREDENTIAL|COOKIE|AUTH)", re.I)


def _env_registry() -> dict:
    """Hermes' own catalogue of known env vars (description, category, url, password flag)."""
    try:
        from ..adapters.common import ensure_hermes_importable
        ensure_hermes_importable()
        from hermes_cli.config import OPTIONAL_ENV_VARS
        return dict(OPTIONAL_ENV_VARS)
    except Exception:
        return {}


@reg.tool("secret_list", toolset="providers", profile_aware=True,
          description=(("Which credentials/settings exist in the profile's .env: name, whether it holds a non-empty "
                        "value, and a description. NEVER returns values or anything derived from them. Use it to tell "
                        "Hermes which secret to use by name.")),
          properties={"prefix": {"type": "string", "maxLength": 40},
                      "secrets_only": {"type": "boolean",
                                       "description": "Only entries that look like credentials."},
                      "category": {"type": "string", "maxLength": 40,
                                   "description": "Filter by Hermes catalogue category, e.g. provider, tool."}})
def secret_list(ctx, prefix=None, secrets_only=False, category=None):
    path = _env_path(ctx)
    reg_ = _env_registry()
    out, comment = [], None
    for ln in _read_env(path):
        s = ln.strip()
        if s.startswith("#"):
            comment = s.lstrip("#").strip() or None
            continue
        m = re.match(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=(.*)$", ln)
        if not m:
            comment = None
            continue
        name, has_value = m.group(1), bool(m.group(2).strip().strip("'\""))
        meta = reg_.get(name) or {}
        is_secret = bool(meta.get("password")) or bool(_SECRETISH.search(name))
        cat = meta.get("category")
        hint, comment = comment, None
        if prefix and not name.startswith(prefix):
            continue
        if secrets_only and not is_secret:
            continue
        if category and cat != category:
            continue
        item = {"name": name, "set": has_value, "secret": is_secret,
                "description": meta.get("description") or hint, "known_to_hermes": bool(meta)}
        if cat:
            item["category"] = cat
        if meta.get("url"):
            item["docs_url"] = meta["url"]
        out.append(item)
    return {"path": str(path), "count": len(out), "secrets": out,
            "note": "values are never exposed; refer to a secret by name"}


@reg.tool("vault_list", toolset="providers", profile_aware=True,
          description=(("Items in the profile's own Hermes vault (logins, cards, addresses): handle, kind, label, "
                        "identifier (username/email), origin. Passwords and card data are never returned; Hermes fills "
                        "them from the handle, so name the handle or label when asking Hermes to log in somewhere. "
                        "External password managers are not listed.")))
def vault_list(ctx):
    base = profile_home(ctx.profile) / "vault"
    if not (base / "vault.json.enc").exists():
        return {"path": str(base), "count": 0, "items": []}
    try:
        from ..adapters.common import ensure_hermes_importable
        ensure_hermes_importable()
        from agent.vault_store import VaultStore
        metas = VaultStore(base_dir=base).list_items()
    except Exception as e:
        raise AdapterError(f"vault unreadable: {type(e).__name__}")
    items = [{"handle": m.id, "kind": m.kind, "label": m.label, "identifier": m.identifier,
              "identifier_type": m.identifier_type, "origin": m.origin, "created_at": m.created_at}
             for m in metas]
    return {"path": str(base), "count": len(items), "items": items}


@reg.tool("secret_delete", toolset="providers", access="admin", profile_aware=True, secrets=True,
          description="Remove a secret from the profile's .env by name.",
          properties={"name": {"type": "string", "pattern": "^[A-Z][A-Z0-9_]{2,64}$"}}, required=["name"])
def secret_delete(ctx, name):
    path = _env_path(ctx)
    lines = _read_env(path)
    kept = [ln for ln in lines if not re.match(rf"^\s*(export\s+)?{re.escape(name)}\s*=", ln)]
    if len(kept) == len(lines):
        raise ToolError("not_found", f"{name} not present in {path}")
    if ctx.dry_run:
        return ctx.plan(f"would remove {name} from {path}", path=str(path))
    tmp = path.with_suffix(".env.tmp")
    tmp.write_text("\n".join(kept) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return {"applied": True, "removed": name, "path": str(path)}
