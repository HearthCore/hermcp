"""Per-profile Hermes api_server keys: resolution, .env read/write, status/setup/rotate.

hermcp talks to Hermes' api_server, which authorizes `/p/<profile>/...` against the *profile's
own* `API_SERVER_KEY` (read live from `$HERMES_HOME/profiles/<profile>/.env`; the `default`
profile reads `$HERMES_HOME/.env`). One shared key therefore only works for one profile — hence
the 401s this module fixes.

Resolution order (highest first):
  1. override  `HERMCP_API_KEY_<PROFILE>`   (process env, then hermcp's own `.env`)
  2. profile   `API_SERVER_KEY` in the profile's `.env` (read at call time, mtime-cached)
  3. global    `HERMES_API_KEY`             (legacy single-key setups)

Keys never leave this module as text: `status`/`setup`/`rotate` print only a sha256
fingerprint (first 8 hex chars), the resolved source and probe results.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from .settings import PKG_DIR, Settings, _read_dotenv, hermes_home

ENV_VAR = "API_SERVER_KEY"
GLOBAL_VAR = "HERMES_API_KEY"
OVERRIDE_PREFIX = "HERMCP_API_KEY_"
BACKUP_DIR_PREFIX = "hermcp-profile-keys-"
MIN_KEY_LEN = 16
FINGERPRINT_LEN = 8

SOURCE_OVERRIDE = "override"
SOURCE_PROFILE = "profile .env"
SOURCE_GLOBAL = "global"
SOURCE_NONE = "none"

_LINE_RX = re.compile(r"^\s*(?:export\s+)?" + ENV_VAR + r"\s*=")

# -- .env reading (mtime-cached) ----------------------------------------------------------
_ENV_CACHE: dict[str, tuple[int, int, dict[str, str]]] = {}
_ENV_LOCK = threading.Lock()


def read_env(path: Path) -> dict[str, str]:
    """Parse a .env file, cached by (mtime_ns, size) so an operator edit applies without a restart."""
    try:
        st = path.stat()
    except OSError:
        return {}
    sig = (st.st_mtime_ns, st.st_size)
    cache_key = str(path)
    with _ENV_LOCK:
        hit = _ENV_CACHE.get(cache_key)
        if hit is not None and hit[0] == sig[0] and hit[1] == sig[1]:
            return dict(hit[2])
    values = _read_dotenv(path)
    with _ENV_LOCK:
        _ENV_CACHE[cache_key] = (sig[0], sig[1], values)
    return dict(values)


def clear_env_cache() -> None:
    """Drop the .env cache (tests, and after a write so the next read sees the new file)."""
    with _ENV_LOCK:
        _ENV_CACHE.clear()


# -- profiles / paths ---------------------------------------------------------------------
def known_profiles() -> list[str]:
    """All profile names: `default` plus every directory under $HERMES_HOME/profiles."""
    root = hermes_home() / "profiles"
    names = ["default"]
    if root.is_dir():
        names += sorted(d.name for d in root.iterdir() if d.is_dir() and not d.name.startswith("."))
    return names


def normalize_profile(profile: str | None) -> str:
    return profile or "default"


def override_var(profile: str) -> str:
    """`HERMCP_API_KEY_<PROFILE>` — upper-cased, `-`/`.` replaced by `_`."""
    return OVERRIDE_PREFIX + re.sub(r"[^A-Za-z0-9]+", "_", normalize_profile(profile)).upper()


def profile_env_path(profile: str) -> Path:
    """The .env the api_server reads this profile's API_SERVER_KEY from."""
    home = hermes_home()
    p = normalize_profile(profile)
    return home / ".env" if p == "default" else home / "profiles" / p / ".env"


def fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:FINGERPRINT_LEN] if key else ""


def backup_root() -> Path:
    return hermes_home() / "backups" / (BACKUP_DIR_PREFIX + time.strftime("%Y%m%d-%H%M%S"))


def _backup_name(path: Path) -> str:
    return "default.env" if path == hermes_home() / ".env" else f"{path.parent.name or 'profile'}.env"


# -- resolution ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Resolution:
    profile: str
    key: str
    source: str
    tried: tuple[str, ...]
    path: Path

    @property
    def present(self) -> bool:
        return bool(self.key)

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.key)


def _override(profile: str) -> str:
    var = override_var(profile)
    return os.environ.get(var) or read_env(PKG_DIR / ".env").get(var, "")


def _global(settings: Settings) -> str:
    if settings.hermes_api_key:
        return settings.hermes_api_key
    return read_env(hermes_home() / ".env").get(GLOBAL_VAR, "")


def resolve(settings: Settings, profile: str = "default") -> Resolution:
    """Resolve the api_server key for *profile* plus the sources that were tried (labels only)."""
    p = normalize_profile(profile)
    path = profile_env_path(p)
    tried = [f"override {override_var(p)}",
             f"profile .env {path} ({ENV_VAR})",
             f"global {GLOBAL_VAR}"]
    key = _override(p)
    if key:
        return Resolution(p, key, SOURCE_OVERRIDE, tuple(tried), path)
    key = read_env(path).get(ENV_VAR, "")
    if key:
        return Resolution(p, key, SOURCE_PROFILE, tuple(tried), path)
    key = _global(settings)
    if key:
        return Resolution(p, key, SOURCE_GLOBAL, tuple(tried), path)
    return Resolution(p, "", SOURCE_NONE, tuple(tried), path)


def tried_hint(settings: Settings, profile: str) -> str:
    """`key sources tried: …` — labels only, never a key value."""
    return ", ".join(resolve(settings, profile).tried)


# -- probe (live) -------------------------------------------------------------------------
def probe(settings: Settings, profile: str, *, key: str | None = None, timeout: float = 4.0) -> tuple[int | None, str]:
    """GET `<api>/p/<profile>/v1/models` with this profile's key.

    Returns (http_status, detail); http_status is None when the api_server is unreachable.
    The resolved key is sent as a bearer token and never returned.
    """
    p = normalize_profile(profile)
    token = key if key is not None else resolve(settings, p).key
    prefix = "" if p == "default" else f"/p/{p}"
    req = urllib.request.Request(f"{settings.hermes_api_url}{prefix}/v1/models", method="GET")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed http(s) base URL
            resp.read(256)
            return resp.status, ""
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        return exc.code, " ".join(detail.split())
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return None, str(exc)[:200]


# -- status -------------------------------------------------------------------------------
@dataclass
class ProfileStatus:
    profile: str
    present: bool
    source: str
    fingerprint: str
    path: Path
    shared_with: list[str] = field(default_factory=list)
    probe_code: int | None = None
    probed: bool = False
    detail: str = ""

    @property
    def working(self) -> bool:
        """A key that the api_server accepted (or that could not be judged: server unreachable)."""
        if not self.present:
            return False
        return self.probe_code in (200, None)

    @property
    def probe_text(self) -> str:
        if not self.probed:
            return "-"
        if self.probe_code is None:
            return "unreachable"
        return str(self.probe_code)

    def note(self) -> str:
        bits: list[str] = []
        if not self.present:
            bits.append("MISSING key (run: python -m hermcp profile-keys setup)")
        if self.shared_with:
            bits.append("SHARED with " + ", ".join(self.shared_with))
        if self.probed and self.probe_code is not None and self.probe_code != 200:
            bits.append(f"rejected by the api_server (HTTP {self.probe_code}; run: python -m hermcp profile-keys setup)")
        if self.probed and self.probe_code is None and self.detail:
            bits.append("api_server unreachable")
        return " | ".join(bits)


def statuses(settings: Settings, *, profiles: Iterable[str] | None = None, do_probe: bool = True,
             probe_fn: Callable[..., tuple[int | None, str]] = probe, timeout: float = 4.0) -> list[ProfileStatus]:
    names = list(profiles) if profiles is not None else known_profiles()
    rows: list[ProfileStatus] = []
    for name in names:
        res = resolve(settings, name)
        row = ProfileStatus(name, res.present, res.source if res.present else SOURCE_NONE,
                            res.fingerprint, res.path)
        if row.present and do_probe:
            code, detail = probe_fn(settings, name, key=res.key, timeout=timeout)
            row.probed, row.probe_code, row.detail = True, code, detail
        rows.append(row)
    by_fp: dict[str, list[str]] = {}
    for row in rows:
        if row.fingerprint:
            by_fp.setdefault(row.fingerprint, []).append(row.profile)
    for row in rows:
        row.shared_with = [p for p in by_fp.get(row.fingerprint, []) if p != row.profile]
    return rows


def render_status(rows: list[ProfileStatus]) -> str:
    out = [f"{'profile':<16}{'key':<6}{'source':<14}{'fingerprint':<13}{'probe':<12}notes"]
    for row in rows:
        out.append(f"{row.profile:<16}{'yes' if row.present else 'no':<6}{row.source:<14}"
                   f"{(row.fingerprint or '-'):<13}{row.probe_text:<12}{row.note()}")
    return "\n".join(out)


# -- .env writing -------------------------------------------------------------------------
def _render_env(old_text: str | None, key: str) -> tuple[str, str]:
    """Return (new_text, action). Replaces the single API_SERVER_KEY line, appends when absent,
    drops duplicates. Comments, quotes and every other line stay untouched."""
    lines = (old_text or "").splitlines()
    out: list[str] = []
    replaced = False
    for line in lines:
        if _LINE_RX.match(line):
            if replaced:
                continue                      # duplicate line: keep only the first (now new) one
            out.append(f"{ENV_VAR}={key}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"{ENV_VAR}={key}")
    action = "replace" if replaced else ("append" if old_text is not None and old_text.strip() else "create")
    return "\n".join(out) + "\n", action


def write_api_key(path: Path, key: str, *, backup_dir: Path | None = None, dry_run: bool = False) -> dict:
    """Write `API_SERVER_KEY=<key>` into *path* (0600), after a timestamped backup of the original.

    Never prints the key. Returns {path, action, changed, backup, new_text_chars}.
    """
    key = (key or "").strip()
    if not key or len(key) < MIN_KEY_LEN or "\n" in key or "\r" in key:
        raise ValueError(f"{ENV_VAR} must be a single line of at least {MIN_KEY_LEN} characters")
    old_text: str | None = None
    if path.exists():
        try:
            old_text = path.read_text(encoding="utf-8")
        except OSError:
            old_text = ""
    new_text, action = _render_env(old_text, key)
    changed = old_text != new_text
    result = {"path": path, "action": action, "changed": changed, "backup": None,
              "new_text_chars": len(new_text), "dry_run": dry_run}
    if dry_run or not changed:
        return result
    backup: Path | None = None
    if old_text is not None:
        root = backup_dir or backup_root()
        root.mkdir(parents=True, exist_ok=True)
        backup = root / _backup_name(path)
        shutil.copyfile(path, backup)
        os.chmod(backup, 0o600)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(new_text)
    os.chmod(path, 0o600)
    clear_env_cache()
    result["backup"] = backup
    return result


def new_key() -> str:
    """64 hex chars, same shape `openssl rand -hex 32` produces."""
    return secrets.token_hex(32)


# -- copy sources -------------------------------------------------------------------------
def available_sources(settings: Settings, exclude: Iterable[str] = ()) -> list[tuple[str, str, str]]:
    """(label, key, fingerprint) of keys that could be copied from: the global key and every
    profile that already has one."""
    skip = {normalize_profile(p) for p in exclude}
    sources: list[tuple[str, str, str]] = []
    g = _global(settings)
    if g:
        sources.append((f"global {GLOBAL_VAR}", g, fingerprint(g)))
    for name in known_profiles():
        if name in skip:
            continue
        res = resolve(settings, name)
        if res.present and res.source == SOURCE_PROFILE:
            sources.append((f"{name} ({res.path})", res.key, res.fingerprint))
    return sources


def pick_source(settings: Settings, spec: str, exclude: Iterable[str] = ()) -> tuple[str, str] | None:
    """`global` or a profile name -> (label, key); None when that source has no key."""
    spec = (spec or "").strip()
    if not spec:
        return None
    if spec in ("global", GLOBAL_VAR):
        g = _global(settings)
        return (f"global {GLOBAL_VAR}", g) if g else None
    res = resolve(settings, spec)
    if res.present and normalize_profile(spec) not in {normalize_profile(p) for p in exclude}:
        return (f"{spec} ({res.path})", res.key)
    return None


# -- CLI ----------------------------------------------------------------------------------
def _stderr(msg: str) -> None:
    print(msg, file=sys.stderr)


def cmd_status(settings: Settings, *, profiles: Iterable[str] | None = None, do_probe: bool = True,
               as_json: bool = False, out: Callable[[str], None] = print,
               probe_fn: Callable[..., tuple[int | None, str]] = probe, timeout: float = 4.0) -> int:
    rows = statuses(settings, profiles=profiles, do_probe=do_probe, probe_fn=probe_fn, timeout=timeout)
    broken = [r for r in rows if not r.working]
    if as_json:
        out(json.dumps([{"profile": r.profile, "key_present": r.present, "source": r.source,
                         "fingerprint": r.fingerprint or None, "shared_with": r.shared_with,
                         "probe": r.probe_text, "working": r.working, "env": str(r.path)} for r in rows], indent=2))
        return 1 if broken else 0
    out(f"profile-keys status (HERMES_HOME={hermes_home()})")
    out(render_status(rows))
    if broken:
        out("")
        out(f"not working: {', '.join(r.profile for r in broken)}")
        out("next: python -m hermcp profile-keys setup   (add --dry-run to preview)")
        return 1
    return 0


def _choose_source(sources: list[tuple[str, str, str]], out: Callable[[str], None],
                   input_fn: Callable[[str], str]) -> tuple[str, str] | None:
    if not sources:
        out("  no existing key to copy from — choose 1 instead")
        return None
    for i, (label, _key, fp) in enumerate(sources, 1):
        out(f"    {i}) {label}  fingerprint {fp}")
    try:
        ans = (input_fn(f"  source [{len(sources)}]: ") or "").strip() or str(len(sources))
    except EOFError:
        return None
    try:
        idx = int(ans)
    except ValueError:
        out("  not a number — skipped")
        return None
    if not 1 <= idx <= len(sources):
        out("  out of range — skipped")
        return None
    label, key, _fp = sources[idx - 1]
    return label, key


def _choose_action(profile: str, row: ProfileStatus, sources: list[tuple[str, str, str]],
                   out: Callable[[str], None], input_fn: Callable[[str], str]) -> tuple[str, str, str] | None:
    """Interactive per-profile choice -> (action, key, label) or None for skip."""
    state = row.note() or f"{row.source} fingerprint {row.fingerprint or '-'}"
    out(f"\n[{profile}] {state}")
    out("  1) generate a new key (secrets.token_hex(32))")
    out("  2) copy from an existing source")
    out("  3) skip")
    try:
        ans = (input_fn("choose [1/2/3] (1): ") or "").strip() or "1"
    except EOFError:
        return None
    if ans == "1":
        return "generate", "", "generate new"
    if ans == "2":
        picked = _choose_source(sources, out, input_fn)
        return ("copy", picked[1], picked[0]) if picked else None
    return None


def cmd_setup(settings: Settings, *, profiles: Iterable[str] | None = None, interactive: bool = True,
              generate: bool = False, copy_from: str | None = None, yes: bool = False, dry_run: bool = False,
              do_probe: bool = True, out: Callable[[str], None] = print,
              input_fn: Callable[[str], str] = input, probe_fn: Callable[..., tuple[int | None, str]] = probe,
              timeout: float = 4.0, backup_dir: Path | None = None) -> int:
    """Give every profile its own api_server key (or copy one). Interactive by default."""
    explicit = list(profiles) if profiles is not None else None
    rows = statuses(settings, profiles=explicit, do_probe=do_probe, probe_fn=probe_fn, timeout=timeout)
    targets = rows if explicit is not None else [r for r in rows if not r.present or r.shared_with]
    out(f"profile-keys setup (HERMES_HOME={hermes_home()}{', dry-run' if dry_run else ''})")
    if not targets:
        out("nothing to do: every profile has its own working key")
        return 0
    if not interactive and not generate and not copy_from:
        _stderr("error: non-interactive setup needs --generate or --copy-from <global|profile>")
        return 2
    failed = 0
    for row in targets:
        sources = available_sources(settings, exclude=[row.profile])
        action, key, label = "", "", ""
        if interactive:
            picked = _choose_action(row.profile, row, sources, out, input_fn)
            if picked is None:
                out(f"[{row.profile}] skipped")
                continue
            action, key, label = picked
        elif copy_from:
            src = pick_source(settings, copy_from, exclude=[row.profile])
            if not src:
                out(f"[{row.profile}] skipped: source {copy_from!r} has no key")
                continue
            action, key, label = "copy", src[1], src[0]
        else:
            action, label = "generate", "generate new"
        if action == "generate":
            key = new_key()
        fp = fingerprint(key)
        fp_note = f"{fp}{' (simulated)' if dry_run and action == 'generate' else ''}"
        if dry_run:
            out(f"[{row.profile}] [dry-run] would {action} ({label}) -> {row.path} "
                f"(API_SERVER_KEY, mode 0600, timestamped backup first); new fingerprint {fp_note}")
            continue
        written = write_api_key(row.path, key, backup_dir=backup_dir)
        code, _detail = probe_fn(settings, row.profile, key=key, timeout=timeout) if do_probe else (None, "")
        status = "probe skipped (--no-probe)" if not do_probe else (
            "probe 200 (accepted)" if code == 200 else (
                "api_server unreachable" if code is None else f"probe HTTP {code} (rejected)"))
        out(f"[{row.profile}] wrote {row.path} ({written['action']}); backup {written['backup'] or '-'}; "
            f"fingerprint {fp}; {status}")
        if do_probe and code is not None and code != 200:
            failed += 1
    if dry_run:
        out("\n[dry-run] nothing was written")
    return 1 if failed else 0


def cmd_rotate(settings: Settings, profiles: list[str], *, yes: bool = False, dry_run: bool = False,
               do_probe: bool = True, out: Callable[[str], None] = print,
               probe_fn: Callable[..., tuple[int | None, str]] = probe, timeout: float = 4.0,
               backup_dir: Path | None = None) -> int:
    """Issue a fresh key for each profile (same write path as setup) and verify it by probe."""
    if not profiles:
        _stderr("usage: profile-keys rotate <profile> | --all")
        return 2
    if not yes and not dry_run:
        out(f"refusing to rotate {', '.join(profiles)} without --yes (this rewrites profile .env files; "
            "the old keys keep working until the file is replaced, and a backup is written first)")
        out(f"preview: python -m hermcp profile-keys rotate {'--all' if len(profiles) > 1 else profiles[0]} --dry-run")
        return 2
    out(f"profile-keys rotate (HERMES_HOME={hermes_home()}{', dry-run' if dry_run else ''})")
    failed = 0
    for name in profiles:
        old = resolve(settings, name)
        old_fp = old.fingerprint or "none"
        key = new_key()
        if dry_run:
            out(f"[{name}] [dry-run] would rotate {old.path} (was {old_fp}); new fingerprint "
                f"{fingerprint(key)} (simulated); backup first")
            continue
        written = write_api_key(old.path, key, backup_dir=backup_dir)
        code, _detail = probe_fn(settings, name, key=key, timeout=timeout) if do_probe else (None, "")
        status = "probe skipped (--no-probe)" if not do_probe else (
            "probe 200 (accepted)" if code == 200 else (
                "api_server unreachable" if code is None else f"probe HTTP {code} (rejected)"))
        out(f"[{name}] rotated {old.path} (was {old_fp} -> {fingerprint(key)}); "
            f"backup {written['backup'] or '-'}; {status}")
        if do_probe and code is not None and code != 200:
            failed += 1
    if dry_run:
        out("\n[dry-run] nothing was written")
    return 1 if failed else 0


def cli_profile_keys(args, *, settings: Settings | None = None, out: Callable[[str], None] = print,
                     input_fn: Callable[[str], str] = input) -> int:
    """Entry point for `python -m hermcp profile-keys {status,setup,rotate}`."""
    from .settings import load_settings
    s = settings or load_settings()
    cmd = getattr(args, "profile_keys_cmd", None)
    raw = getattr(args, "profiles", None)
    profiles = [p.strip() for p in raw.split(",") if p.strip()] if raw else None
    if cmd == "status":
        return cmd_status(s, profiles=profiles, do_probe=not args.no_probe, as_json=getattr(args, "json", False), out=out)
    if cmd == "setup":
        return cmd_setup(s, profiles=profiles, interactive=not args.yes, generate=args.generate,
                         copy_from=args.copy_from, yes=args.yes, dry_run=args.dry_run,
                         do_probe=not args.no_probe, out=out, input_fn=input_fn)
    if cmd == "rotate":
        one = getattr(args, "profile", None)
        if args.all and one:
            _stderr("error: use either <profile> or --all")
            return 2
        targets = [one] if one else (known_profiles() if args.all else [])
        if targets and not args.all and targets[0] not in known_profiles():
            _stderr(f"error: unknown profile {targets[0]!r} (known: {', '.join(known_profiles())})")
            return 2
        return cmd_rotate(s, targets, yes=args.yes, dry_run=args.dry_run, do_probe=not args.no_probe,
                          out=out)
    _stderr("usage: profile-keys {status,setup,rotate}")
    return 2

