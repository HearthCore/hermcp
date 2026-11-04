"""profile_keys: per-profile api_server key resolution, .env writing, status/setup/rotate.

Every test runs against a temporary HERMES_HOME (see conftest) and never touches the real
profiles' .env files or a live api_server: probes are stubbed.
"""
from __future__ import annotations

import io
import json
import os
import stat
import urllib.error
from pathlib import Path

import pytest

from hermcp import profile_keys as pk
from hermcp.settings import load_settings

KEY_A = "a" * 64
KEY_B = "b" * 64
GLOBAL = "g" * 32


def cfg(home: Path, **env) -> "object":
    base = {"HERMES_HOME": str(home), "HERMES_API_KEY": ""}
    base.update(env)
    return load_settings(base)


def env_path(home: Path, profile: str) -> Path:
    return home / ".env" if profile == "default" else home / "profiles" / profile / ".env"


def write_env(home: Path, profile: str, text: str) -> Path:
    p = env_path(home, profile)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


class _Resp:
    status = 200

    def read(self, n=None):
        return b'{"data":[]}'

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def stub_probe(code=200, detail=""):
    calls = []

    def _probe(settings, profile, **kw):
        calls.append((profile, kw.get("key")))
        return code, detail
    _probe.calls = calls
    return _probe


def collect(lines):
    return lines.append


def text(lines):
    return "\n".join(lines)


# ---- paths, names, fingerprints ----------------------------------------------------------
def test_known_profiles(home):
    assert pk.known_profiles() == ["default", "researcher"]
    (home / "profiles" / "media-agent").mkdir()
    (home / "profiles" / ".hidden").mkdir()
    assert pk.known_profiles() == ["default", "media-agent", "researcher"]


def test_override_var_name():
    assert pk.override_var("default") == "HERMCP_API_KEY_DEFAULT"
    assert pk.override_var("media-agent") == "HERMCP_API_KEY_MEDIA_AGENT"
    assert pk.override_var("qa.reviewer") == "HERMCP_API_KEY_QA_REVIEWER"
    assert pk.override_var(None) == "HERMCP_API_KEY_DEFAULT"


def test_profile_env_path(home):
    assert pk.profile_env_path("default") == home / ".env"
    assert pk.profile_env_path("researcher") == home / "profiles" / "researcher" / ".env"


def test_fingerprint_is_short_and_key_free():
    fp = pk.fingerprint(KEY_A)
    assert fp == "ffe054fe" and len(fp) == pk.FINGERPRINT_LEN and KEY_A not in fp
    assert pk.fingerprint("") == ""


# ---- resolution order --------------------------------------------------------------------
def test_resolution_order_override_then_profile_then_global(home, monkeypatch):
    s = cfg(home, HERMES_API_KEY=GLOBAL)
    # (c) global fallback
    r = pk.resolve(s, "researcher")
    assert (r.source, r.key) == (pk.SOURCE_GLOBAL, GLOBAL)
    assert r.present and r.fingerprint == pk.fingerprint(GLOBAL)
    assert len(r.tried) == 3 and all(GLOBAL not in t for t in r.tried)
    # (b) the profile's own API_SERVER_KEY wins over the global one
    write_env(home, "researcher", f"API_SERVER_KEY={KEY_A}\n")
    assert (pk.resolve(s, "researcher").source, pk.resolve(s, "researcher").key) == (pk.SOURCE_PROFILE, KEY_A)
    # (a) the explicit override wins over both
    monkeypatch.setenv(pk.override_var("researcher"), KEY_B)
    r = pk.resolve(s, "researcher")
    assert (r.source, r.key) == (pk.SOURCE_OVERRIDE, KEY_B)


def test_resolution_missing_key(home):
    r = pk.resolve(cfg(home), "researcher")
    assert not r.present and r.source == pk.SOURCE_NONE and r.fingerprint == "" and r.key == ""


def test_default_profile_reads_root_env(home):
    write_env(home, "default", "API_SERVER_KEY=" + KEY_A + "\n")
    r = pk.resolve(cfg(home), "default")
    assert (r.source, r.key, r.path) == (pk.SOURCE_PROFILE, KEY_A, home / ".env")


def test_global_key_still_falls_back_for_any_profile(home):
    """Legacy single-key setups keep working (that is how the fake api_server tests run)."""
    s = cfg(home, HERMES_API_KEY="legacy-key-of-16")
    assert pk.resolve(s, "researcher").key == "legacy-key-of-16"


def test_global_key_is_also_read_from_the_root_env(home):
    write_env(home, "default", "HERMES_API_KEY=" + GLOBAL + "\n")
    assert pk.resolve(cfg(home), "researcher").key == GLOBAL


def test_override_is_read_from_the_plugin_dotenv_too(home, monkeypatch):
    var = pk.override_var("researcher")
    monkeypatch.setattr(pk, "read_env", lambda path: {var: KEY_A} if path == pk.PKG_DIR / ".env" else {})
    monkeypatch.delenv(var, raising=False)
    assert pk.resolve(cfg(home), "researcher").key == KEY_A


# ---- .env parsing + caching --------------------------------------------------------------
def test_env_read_handles_comments_quotes_and_missing_newline(home):
    write_env(home, "researcher", '# comment with API_SERVER_KEY=ignored\nOTHER="x"\n'
                                    "API_SERVER_KEY='%s'" % KEY_A)
    assert pk.resolve(cfg(home), "researcher").key == KEY_A


def test_env_read_skips_commented_line(home):
    write_env(home, "researcher", "# API_SERVER_KEY=" + KEY_A + "\n")
    assert not pk.resolve(cfg(home), "researcher").present


def test_env_is_cached_by_mtime_and_re_read_after_a_change(home, monkeypatch):
    p = write_env(home, "researcher", f"API_SERVER_KEY={KEY_A}\n")
    calls = []
    real = pk._read_dotenv

    def counting(path):
        if path == p:
            calls.append(path)
        return real(path)
    monkeypatch.setattr(pk, "_read_dotenv", counting)
    pk.clear_env_cache()
    s = cfg(home)
    assert pk.resolve(s, "researcher").key == KEY_A
    assert pk.resolve(s, "researcher").key == KEY_A
    assert len(calls) == 1                                   # second call hits the cache
    p.write_text(f"API_SERVER_KEY={KEY_B}\n", encoding="utf-8")   # operator edit, no restart
    assert pk.resolve(s, "researcher").key == KEY_B
    assert len(calls) == 2


# ---- .env writing ------------------------------------------------------------------------
def test_render_env_replaces_one_line_and_keeps_everything_else():
    old = "# kept\nOTHER=\"x\"\nAPI_SERVER_KEY=old\nTAIL=1\n"
    new, action = pk._render_env(old, KEY_A)
    assert action == "replace"
    assert new == f"# kept\nOTHER=\"x\"\nAPI_SERVER_KEY={KEY_A}\nTAIL=1\n"
    assert new.count("API_SERVER_KEY=") == 1


def test_render_env_drops_duplicate_lines():
    new, action = pk._render_env("API_SERVER_KEY=old\nX=1\nAPI_SERVER_KEY=old2\n", KEY_A)
    assert action == "replace" and new.count("API_SERVER_KEY=") == 1
    assert new == f"API_SERVER_KEY={KEY_A}\nX=1\n"


def test_render_env_appends_without_a_trailing_newline():
    new, action = pk._render_env("X=1", KEY_A)                # no trailing newline
    assert action == "append" and new == f"X=1\nAPI_SERVER_KEY={KEY_A}\n"


def test_render_env_creates_empty_file():
    assert pk._render_env("", KEY_A) == (f"API_SERVER_KEY={KEY_A}\n", "create")
    assert pk._render_env(None, KEY_A) == (f"API_SERVER_KEY={KEY_A}\n", "create")


def test_write_api_key_is_0600_and_backs_up_the_original(home):
    p = home / "profiles" / "researcher" / ".env"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("# mine\nAPI_SERVER_KEY=old-key\n", encoding="utf-8")
    os.chmod(p, 0o644)
    r = pk.write_api_key(p, KEY_A)
    assert r["changed"] and r["action"] == "replace"
    assert stat.S_IMODE(p.stat().st_mode) == 0o600              # tightened/kept at 0600
    assert KEY_A in p.read_text()
    backup = r["backup"]
    assert backup is not None and backup.parent.parent == home / "backups"
    assert backup.parent.name.startswith(pk.BACKUP_DIR_PREFIX)
    assert backup.read_text() == "# mine\nAPI_SERVER_KEY=old-key\n"   # original preserved
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600


def test_write_api_key_creates_missing_file_without_backup(home):
    p = home / "profiles" / "researcher" / ".env"
    r = pk.write_api_key(p, KEY_A)
    assert r["action"] == "create" and r["backup"] is None and r["changed"]
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert p.read_text() == f"API_SERVER_KEY={KEY_A}\n"


def test_write_api_key_is_idempotent(home):
    p = env_path(home, "researcher")
    pk.write_api_key(p, KEY_A)
    again = pk.write_api_key(p, KEY_A)
    assert again["changed"] is False and again["backup"] is None
    assert (home / "backups").exists() is False or not any((home / "backups").iterdir())


def test_write_api_key_rejects_bad_keys(home):
    for bad in ("", "short", KEY_A + "\nAPI_SERVER_KEY=x", "x" * 15):
        with pytest.raises(ValueError):
            pk.write_api_key(env_path(home, "researcher"), bad)


def test_new_key_shape():
    k = pk.new_key()
    assert len(k) == 64 and int(k, 16) >= 0 and k != pk.new_key()


def test_write_api_key_does_not_clear_other_profiles(home):
    pk.write_api_key(env_path(home, "researcher"), KEY_A)
    pk.write_api_key(env_path(home, "default"), KEY_B)
    assert pk.resolve(cfg(home), "researcher").key == KEY_A
    assert pk.resolve(cfg(home), "default").key == KEY_B


# ---- probe -------------------------------------------------------------------------------
def test_probe_sends_the_profile_key_and_path(home, monkeypatch):
    write_env(home, "researcher", f"API_SERVER_KEY={KEY_A}\n")
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"], seen["auth"], seen["timeout"] = req.full_url, req.get_header("Authorization"), timeout
        return _Resp()
    monkeypatch.setattr(pk.urllib.request, "urlopen", fake_urlopen)
    code, detail = pk.probe(cfg(home), "researcher", timeout=3)
    assert (code, detail) == (200, "")
    assert seen["url"].endswith("/p/researcher/v1/models") and seen["auth"] == f"Bearer {KEY_A}"


def test_probe_default_profile_uses_no_prefix(home, monkeypatch):
    write_env(home, "default", f"API_SERVER_KEY={KEY_A}\n")
    seen = {}
    monkeypatch.setattr(pk.urllib.request, "urlopen",
                        lambda req, timeout=None: seen.update(url=req.full_url) or _Resp())
    assert pk.probe(cfg(home), "default")[0] == 200
    assert seen["url"].endswith("/v1/models") and "/p/" not in seen["url"]


def test_probe_reports_401_and_unreachable(home, monkeypatch):
    write_env(home, "researcher", f"API_SERVER_KEY={KEY_A}\n")
    err = urllib.error.HTTPError("http://x/p/researcher/v1/models", 401, "Unauthorized", {},
                                 io.BytesIO(b'{"error":"unauthorized"}'))
    monkeypatch.setattr(pk.urllib.request, "urlopen", lambda req, timeout=None: (_ for _ in ()).throw(err))
    assert pk.probe(cfg(home), "researcher") == (401, '{"error":"unauthorized"}')
    monkeypatch.setattr(pk.urllib.request, "urlopen",
                        lambda req, timeout=None: (_ for _ in ()).throw(urllib.error.URLError("refused")))
    code, detail = pk.probe(cfg(home), "researcher")
    assert code is None and "refused" in detail


# ---- status ------------------------------------------------------------------------------
def test_status_marks_shared_keys(home):
    pk.write_api_key(env_path(home, "default"), KEY_A)
    pk.write_api_key(env_path(home, "researcher"), KEY_A)
    rows = pk.statuses(cfg(home), do_probe=False)
    assert [r.profile for r in rows] == ["default", "researcher"]
    assert all(r.shared_with == [p] for r, p in zip(rows, ["researcher", "default"]))
    assert all(r.working for r in rows)


def test_status_missing_key_is_not_working(home):
    rows = pk.statuses(cfg(home), do_probe=False)
    assert not any(r.working for r in rows)
    assert "profile-keys setup" in rows[0].note() and "MISSING" in rows[0].note()


def test_status_and_render_exit_codes(home, capsys):
    pk.write_api_key(env_path(home, "default"), KEY_A)
    pk.write_api_key(env_path(home, "researcher"), KEY_B)
    out = []
    fn = lambda settings, profile, **kw: ((200 if profile == "default" else 401), "")  # noqa: E731
    assert pk.cmd_status(cfg(home), do_probe=True, out=out.append, probe_fn=fn) == 1
    rendered = text(out)
    assert "default" in rendered and "researcher" in rendered
    assert pk.fingerprint(KEY_A) in rendered and pk.fingerprint(KEY_B) in rendered
    assert "401" in rendered and "not working: researcher" in rendered and "profile-keys setup" in rendered
    assert KEY_A not in rendered and KEY_B not in rendered and GLOBAL not in rendered
    fn2 = lambda settings, profile, **kw: (200, "")  # noqa: E731
    out2 = []
    assert pk.cmd_status(cfg(home), do_probe=True, out=out2.append, probe_fn=fn2) == 0
    assert "not working" not in text(out2)


def test_status_unreachable_server_is_not_a_failure(home):
    pk.write_api_key(env_path(home, "researcher"), KEY_A)
    fn = lambda settings, profile, **kw: (None, "refused")  # noqa: E731
    lines = []
    assert pk.cmd_status(cfg(home), profiles=["researcher"], do_probe=True, out=lines.append, probe_fn=fn) == 0
    assert "unreachable" in text(lines)


def test_status_offline_skips_the_probe(home):
    pk.write_api_key(env_path(home, "researcher"), KEY_A)
    fn = stub_probe(200)
    lines = []
    assert pk.cmd_status(cfg(home), profiles=["researcher"], do_probe=False, out=lines.append, probe_fn=fn) == 0
    assert fn.calls == [] and "researcher" in text(lines)


def test_status_json_is_machine_readable_and_key_free(home):
    pk.write_api_key(env_path(home, "researcher"), KEY_A)
    lines = []
    assert pk.cmd_status(cfg(home), profiles=["researcher"], do_probe=False, as_json=True, out=lines.append) == 0
    data = json.loads(text(lines))
    row = [r for r in data if r["profile"] == "researcher"][0]
    assert row["fingerprint"] == pk.fingerprint(KEY_A) and row["source"] == pk.SOURCE_PROFILE
    assert KEY_A not in text(lines)


# ---- setup -------------------------------------------------------------------------------
def test_setup_dry_run_writes_nothing(home):
    lines = []
    rc = pk.cmd_setup(cfg(home), profiles=["researcher"], interactive=False, yes=True, generate=True,
                      dry_run=True, do_probe=False, out=lines.append)
    assert rc == 0
    out = text(lines)
    assert "[dry-run] would generate" in out and "researcher" in out and "[dry-run] nothing was written" in out
    assert not env_path(home, "researcher").exists()
    assert not (home / "backups").exists()


def test_setup_generates_and_verifies(home):
    lines = []
    fn = stub_probe(200)
    rc = pk.cmd_setup(cfg(home), profiles=["researcher"], interactive=False, yes=True, generate=True,
                      out=lines.append, probe_fn=fn)
    assert rc == 0
    written = pk.resolve(cfg(home), "researcher")
    assert written.source == pk.SOURCE_PROFILE and len(written.key) == 64
    assert fn.calls == [("researcher", written.key)]
    assert written.key not in text(lines)                     # never printed
    assert written.fingerprint in text(lines) and "probe 200" in text(lines)
    assert stat.S_IMODE(env_path(home, "researcher").stat().st_mode) == 0o600


def test_setup_targets_only_missing_or_shared_profiles(home):
    pk.write_api_key(env_path(home, "default"), KEY_A)
    (home / "profiles" / "media-agent").mkdir()
    pk.write_api_key(env_path(home, "media-agent"), KEY_A)     # same key as default -> shared
    lines = []
    rc = pk.cmd_setup(cfg(home), interactive=False, yes=True, generate=True, do_probe=False, out=lines.append)
    assert rc == 0
    out = text(lines)
    assert all(f"[{p}]" in out for p in ("default", "media-agent", "researcher"))
    keys = {p: pk.resolve(cfg(home), p).key for p in ("default", "media-agent", "researcher")}
    assert all(len(k) == 64 for k in keys.values())
    assert len(set(keys.values())) == 3                        # the shared pair was split up
    assert not any(r.shared_with for r in pk.statuses(cfg(home), do_probe=False))


def test_setup_says_nothing_to_do(home):
    pk.write_api_key(env_path(home, "default"), KEY_A)
    pk.write_api_key(env_path(home, "researcher"), KEY_B)
    lines = []
    assert pk.cmd_setup(cfg(home), interactive=False, yes=True, generate=True, do_probe=False,
                        out=lines.append) == 0
    assert "nothing to do" in text(lines)


def test_setup_noninteractive_needs_a_mode(home):
    assert pk.cmd_setup(cfg(home), profiles=["researcher"], interactive=False, yes=True,
                        do_probe=False, out=lambda *_: None) == 2
    assert not env_path(home, "researcher").exists()


def test_setup_copy_from_global(home):
    s = cfg(home, HERMES_API_KEY=GLOBAL)
    lines = []
    rc = pk.cmd_setup(s, profiles=["researcher"], interactive=False, yes=True, copy_from="global",
                      do_probe=False, out=lines.append)
    assert rc == 0
    assert pk.resolve(cfg(home), "researcher").key == GLOBAL
    assert GLOBAL not in text(lines) and pk.fingerprint(GLOBAL) in text(lines)


def test_setup_copy_from_missing_source_skips(home):
    lines = []
    rc = pk.cmd_setup(cfg(home), profiles=["researcher"], interactive=False, yes=True,
                      copy_from="nobody", do_probe=False, out=lines.append)
    assert rc == 0 and "skipped" in text(lines)
    assert not env_path(home, "researcher").exists()


def test_setup_interactive_generate_and_skip(home):
    lines = []
    answers = iter(["1"])
    rc = pk.cmd_setup(cfg(home), profiles=["researcher"], interactive=True, do_probe=False,
                      out=lines.append, input_fn=lambda prompt: next(answers))
    assert rc == 0 and pk.resolve(cfg(home), "researcher").present
    assert "generate a new key" in text(lines)
    lines2 = []
    answers2 = iter(["3"])
    rc = pk.cmd_setup(cfg(home), profiles=["default"], interactive=True, do_probe=False,
                      out=lines2.append, input_fn=lambda prompt: next(answers2))
    assert rc == 0 and "skipped" in text(lines2) and not env_path(home, "default").exists()


def test_setup_interactive_copy_lists_sources(home):
    pk.write_api_key(env_path(home, "default"), KEY_A)
    lines = []
    answers = iter(["2", "1"])
    rc = pk.cmd_setup(cfg(home), profiles=["researcher"], interactive=True, do_probe=False,
                      out=lines.append, input_fn=lambda prompt: next(answers))
    assert rc == 0
    assert "fingerprint " + pk.fingerprint(KEY_A) in text(lines)
    assert pk.resolve(cfg(home), "researcher").key == KEY_A


def test_setup_reports_a_rejected_key(home):
    lines = []
    rc = pk.cmd_setup(cfg(home), profiles=["researcher"], interactive=False, yes=True, generate=True,
                      out=lines.append, probe_fn=stub_probe(401))
    assert rc == 1 and "rejected" in text(lines)


def test_setup_backup_of_an_existing_file_before_replacing_it(home):
    pk.write_api_key(env_path(home, "researcher"), KEY_A)
    lines = []
    pk.cmd_setup(cfg(home), profiles=["researcher"], interactive=False, yes=True, generate=True,
                 do_probe=False, out=lines.append)
    p = env_path(home, "researcher")
    assert KEY_A not in p.read_text()
    backups = [f for f in (home / "backups").rglob("researcher.env")]
    assert len(backups) == 1 and backups[0].read_text() == f"API_SERVER_KEY={KEY_A}\n"


# ---- rotate ------------------------------------------------------------------------------
def test_rotate_replaces_the_key_and_prints_only_fingerprints(home):
    pk.write_api_key(env_path(home, "researcher"), KEY_A)
    lines = []
    rc = pk.cmd_rotate(cfg(home), ["researcher"], yes=True, probe_fn=stub_probe(200), out=lines.append)
    assert rc == 0
    new = pk.resolve(cfg(home), "researcher").key
    out = text(lines)
    assert new != KEY_A and len(new) == 64
    assert KEY_A not in out and new not in out
    assert pk.fingerprint(KEY_A) in out and pk.fingerprint(new) in out and "probe 200" in out
    assert len(list((home / "backups").rglob("researcher.env"))) == 1


def test_rotate_requires_yes(home):
    pk.write_api_key(env_path(home, "researcher"), KEY_A)
    lines = []
    assert pk.cmd_rotate(cfg(home), ["researcher"], yes=False, out=lines.append) == 2
    assert pk.resolve(cfg(home), "researcher").key == KEY_A
    assert "refusing" in text(lines)


def test_rotate_dry_run_writes_nothing(home, capsys):
    pk.write_api_key(env_path(home, "researcher"), KEY_A)
    lines = []
    assert pk.cmd_rotate(cfg(home), ["researcher"], dry_run=True, out=lines.append) == 0
    assert pk.resolve(cfg(home), "researcher").key == KEY_A
    assert "[dry-run] would rotate" in text(lines) and "simulated" in text(lines)
    assert not (home / "backups").exists()


def test_rotate_reports_a_rejected_key(home):
    pk.write_api_key(env_path(home, "researcher"), KEY_A)
    lines = []
    assert pk.cmd_rotate(cfg(home), ["researcher"], yes=True, probe_fn=stub_probe(401),
                         out=lines.append) == 1
    assert "HTTP 401" in text(lines)


def test_rotate_without_target_is_usage_error(home):
    assert pk.cmd_rotate(cfg(home), [], yes=True, out=lambda *_: None) == 2


# ---- CLI wiring --------------------------------------------------------------------------
def test_cli_status_and_setup_dry_run(home, capsys):
    from hermcp import cli
    write_env(home, "default", f"API_SERVER_KEY={KEY_A}\n")
    assert cli.main(["profile-keys", "status", "--no-probe"]) == 1        # researcher has no key
    out = capsys.readouterr().out
    assert "profile-keys status" in out and "researcher" in out and KEY_A not in out
    assert cli.main(["profile-keys", "setup", "--yes", "--generate", "--dry-run", "--no-probe",
                     "--profiles", "researcher"]) == 0
    out = capsys.readouterr().out
    assert "[dry-run] would generate" in out and KEY_A not in out
    assert not env_path(home, "researcher").exists()


def test_cli_rotate_rejects_an_unknown_profile(home, capsys):
    from hermcp import cli
    assert cli.main(["profile-keys", "rotate", "nope", "--yes"]) == 2
    assert "unknown profile" in capsys.readouterr().err


def test_cli_rotate_all_requires_yes(home, capsys):
    from hermcp import cli
    write_env(home, "default", f"API_SERVER_KEY={KEY_A}\n")
    assert cli.main(["profile-keys", "rotate", "--all"]) == 2
    assert "refusing" in capsys.readouterr().out


# ---- adapters/hapi + doctor integration --------------------------------------------------
def test_hapi_api_key_is_per_profile(home, monkeypatch):
    from hermcp.adapters import hapi
    write_env(home, "researcher", f"API_SERVER_KEY={KEY_A}\n")
    s = cfg(home, HERMES_API_KEY=GLOBAL)
    assert hapi.api_key(s, "researcher") == KEY_A
    assert hapi.api_key(s, "media-agent") == GLOBAL            # no profile key -> legacy fallback
    monkeypatch.setenv(pk.override_var("researcher"), KEY_B)
    assert hapi.api_key(s, "researcher") == KEY_B


def test_hapi_auth_error_names_profile_and_sources_without_values(home):
    from hermcp.adapters import hapi
    write_env(home, "researcher", f"API_SERVER_KEY={KEY_A}\n")
    err = hapi.auth_error(cfg(home), "researcher", 401)
    assert err.code == "api_auth" and "researcher" in err.message
    assert "profile .env" in err.message and "fingerprint " + pk.fingerprint(KEY_A) in err.message
    assert "profile-keys status" in err.message
    assert KEY_A not in err.message and GLOBAL not in err.message
    missing = hapi.auth_error(cfg(home), "media-agent", 401)
    assert "no key found" in missing.message and "HERMES_API_KEY" in missing.message


def test_hapi_request_status_never_raises_on_401(home, monkeypatch):
    from hermcp.adapters import hapi
    write_env(home, "researcher", f"API_SERVER_KEY={KEY_A}\n")
    err = urllib.error.HTTPError("http://x/p/researcher/v1/runs", 401, "Unauthorized", {},
                                 io.BytesIO(b'{"error":"unauthorized"}'))
    monkeypatch.setattr(hapi.urllib.request, "urlopen", lambda req, timeout=None: (_ for _ in ()).throw(err))
    assert hapi.request_status(cfg(home), "GET", "/v1/runs/x", profile="researcher") == (401, {"error": "unauthorized"})


def test_chat_send_401_error_is_actionable_and_key_free(home, monkeypatch):
    """What a client sees on a per-profile 401: the profile, the sources tried, no key value."""
    from types import SimpleNamespace

    from hermcp.adapters import hapi
    from hermcp.tools import chat
    write_env(home, "researcher", f"API_SERVER_KEY={KEY_A}\n")
    s = cfg(home, HERMES_API_KEY=GLOBAL)
    monkeypatch.setattr(hapi, "request_status", lambda *a, **kw: (401, {"error": "unauthorized"}))
    ctx = SimpleNamespace(settings=s, profile="researcher")
    with pytest.raises(Exception) as info:
        chat._start_run(ctx, {"message": "hi"}, "idem-1234567890")
    msg = getattr(info.value, "message", str(info.value))
    assert info.value.code == "api_auth"
    assert "researcher" in msg and "profile .env" in msg
    assert KEY_A not in msg and GLOBAL not in msg
    assert "profile-keys status" in msg


def test_doctor_warns_about_a_missing_key(home, monkeypatch, capsys):
    from hermcp import doctor
    monkeypatch.setenv("HERMES_API_URL", "http://127.0.0.1:1")     # unreachable: no probe
    doctor.run()
    out = capsys.readouterr().out
    assert "[warn] api_server key [researcher] missing" in out
    assert "python -m hermcp profile-keys setup" in out
    assert "doctor:" in out
    # A missing key is advice: no [FAIL] row for it, and no key value anywhere. (The global exit
    # code is not asserted here: unrelated checks — e.g. `hermes internals` in a bare CI runner —
    # can fail and make doctor return 1.)
    assert "[FAIL] api_server key" not in out
    assert KEY_A not in out


def test_doctor_warns_about_a_shared_key(home, monkeypatch, capsys):
    from hermcp import doctor
    monkeypatch.setenv("HERMES_API_URL", "http://127.0.0.1:1")
    pk.write_api_key(env_path(home, "default"), KEY_A)
    pk.write_api_key(env_path(home, "researcher"), KEY_A)
    doctor.run()
    out = capsys.readouterr().out
    assert "[warn] api_server key [researcher] shared with default" in out
    assert "[warn] api_server key [default] shared with researcher" in out
    assert "every profile needs its own key" in out
    assert KEY_A not in out


def test_doctor_fails_on_a_rejected_key(home, monkeypatch, capsys):
    from hermcp import doctor
    monkeypatch.setenv("HERMES_API_URL", "http://127.0.0.1:1")
    write_env(home, "researcher", f"API_SERVER_KEY={KEY_A}\n")
    monkeypatch.setattr(pk, "statuses", lambda *a, **kw: [
        pk.ProfileStatus("researcher", True, pk.SOURCE_PROFILE, pk.fingerprint(KEY_A),
                         env_path(home, "researcher"), [], 401, True, "unauthorized")])
    monkeypatch.setattr(pk, "known_profiles", lambda: ["researcher"])
    rc = doctor.run()
    out = capsys.readouterr().out
    assert "[FAIL] api_server key [researcher]" in out and "HTTP 401" in out
    assert "profile-keys setup" in out and rc == 1
    assert KEY_A not in out


def test_doctor_ok_when_the_key_is_accepted(home, monkeypatch, capsys):
    from hermcp import doctor
    monkeypatch.setenv("HERMES_API_URL", "http://127.0.0.1:1")
    write_env(home, "researcher", f"API_SERVER_KEY={KEY_A}\n")
    monkeypatch.setattr(pk, "statuses", lambda *a, **kw: [
        pk.ProfileStatus("researcher", True, pk.SOURCE_PROFILE, pk.fingerprint(KEY_A),
                         env_path(home, "researcher"), [], 200, True, "")])
    monkeypatch.setattr(pk, "known_profiles", lambda: ["researcher"])
    doctor.run()
    out = capsys.readouterr().out
    assert f"[ok] api_server key [researcher]: profile .env fingerprint {pk.fingerprint(KEY_A)}" in out
    assert "[FAIL] api_server key" not in out and "[warn] api_server key" not in out
    assert KEY_A not in out
