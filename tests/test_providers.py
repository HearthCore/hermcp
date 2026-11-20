"""providers toolset: inventory, provider add, remote (PTY) auth sessions and the write-only secret tools.

Synthetic values only. The `hermes` CLI is a script the test writes, so the PTY session lifecycle
(spawn, read, continue, cancel) runs for real without touching a Hermes install.
"""
import json
import os
import subprocess
import time

import pytest
import yaml

from hermcp.audit import AuditLog
from hermcp.registry import Principal
from hermcp.scopes import Scopes
from hermcp.settings import load_settings

SECRET = "SYNTHVALUE0123456789ABCDEF"

# Stand-in for `hermes auth add <provider> --type oauth --no-browser`: prints a verification URL and a
# device code, then waits on stdin exactly like the real command does.
FAKE_AUTH = '''#!/usr/bin/env python3
import os, sys, time
mode = os.environ.get("FAKE_AUTH_MODE", "prompt")
if mode == "fail":
    print("error: unknown provider", file=sys.stderr)
    sys.exit(3)
print("Opening https://example.invalid/device?user_code=ABCD-1234.")
print("Enter code: ABCD-1234")
sys.stdout.flush()
line = sys.stdin.readline()
if mode == "hang":
    print("received:", line.strip())
    sys.stdout.flush()
    time.sleep(60)
sys.exit(0)
'''


class Rig:
    """Registry + settings + audit log, with the call helpers the tests use."""

    def __init__(self, reg, settings, home, audit):
        self.reg, self.settings, self.home, self.audit = reg, settings, home, audit
        self.admin = Principal("t", "t", Scopes(access="admin", approvals=True, secrets_write=True))

    def call(self, tool, /, principal=None, **args):
        return self.reg.invoke(tool, args, principal or self.admin, self.settings, self.audit)

    def ok(self, tool, /, **args):
        """The result body, without the provenance block every tool result carries."""
        r = self.call(tool, **args)
        assert r["ok"], r
        res = r["result"]
        if isinstance(res, dict):
            res.pop("_meta", None)
        return res

    def err(self, tool, /, **args):
        r = self.call(tool, **args)
        assert r["ok"] is False, r
        return r["error"]

    def audit_lines(self):
        return [json.loads(x) for x in self.audit.path.read_text().splitlines()]


@pytest.fixture()
def rig(home, tmp_path):
    from hermcp.tools import load_all
    return Rig(load_all(), load_settings({"HERMES_HOME": str(home)}), home, AuditLog(tmp_path / "audit.jsonl"))


@pytest.fixture(autouse=True)
def _no_leftover_sessions():
    """The session table is module state: never let one test's PTY leak into the next."""
    from hermcp.tools import providers
    providers._SESSIONS.clear()
    yield
    for aid in list(providers._SESSIONS):
        providers._close(aid)
    providers._SESSIONS.clear()


@pytest.fixture()
def auth_cli(tmp_path, monkeypatch):
    """A `hermes` that behaves like `auth add`: URL + code, then blocks on stdin."""
    p = tmp_path / "fake_hermes_auth"
    p.write_text(FAKE_AUTH, encoding="utf-8")
    p.chmod(0o755)
    monkeypatch.setenv("HERMCP_HERMES_BIN", str(p))
    return p


class _Proc:
    """Minimal Popen stand-in for the GC/close paths and for sessions that already exited."""

    def __init__(self, rc=None, stubborn=False):
        self.rc, self.returncode, self.stubborn = rc, rc, stubborn
        self.terminated = self.killed = False

    def poll(self):
        return self.rc

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        if self.stubborn:
            raise subprocess.TimeoutExpired("hermes", timeout)
        return self.rc

    def kill(self):
        self.killed = True


def _fake_session(rc=None, age=0.0, stubborn=False):
    fd = os.open(os.devnull, os.O_RDONLY)
    return {"proc": _Proc(rc, stubborn), "fd": fd, "transcript": "", "created": time.time(),
            "last": time.time() - age, "provider": "p"}


@pytest.fixture()
def fd_above_select_limit():
    """Returns a function that picks a free fd number above FD_SETSIZE (1024) and makes it usable.

    Call it only after the fds the test needs are open: a process that has run much of the suite holds
    thousands of fds, so the lowest free number above 1024 is also the one `openpty()` hands out next
    (picking it first made dup2 clobber the PTY's own slave on CI). The common default soft limit is
    1024 (`ulimit -n`), where dup2 onto 1024 fails with EBADF, so the soft limit is raised for the
    test and restored afterwards; without a hard limit above the target the test skips.
    """
    import resource
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)

    def pick():
        for cand in range(1024, 65536):
            try:
                os.fstat(cand)
            except OSError:
                break
        else:
            pytest.skip("no free fd number below 65536")
        if hard != resource.RLIM_INFINITY and hard <= cand:
            pytest.skip(f"hard fd limit {hard} leaves no fd above 1024")
        if soft != resource.RLIM_INFINITY and soft <= cand:
            resource.setrlimit(resource.RLIMIT_NOFILE, (cand + 1, hard))
        return cand
    try:
        yield pick
    finally:
        resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))


def _exited_session(rc, text=""):
    """A session whose process is already gone and whose PTY still holds `text` (then EOF)."""
    master, slave = os.openpty()
    if text:
        os.write(slave, text.encode())
    os.close(slave)
    return {"proc": _Proc(rc), "fd": master, "transcript": text, "created": time.time(),
            "last": time.time(), "provider": "acme"}


def _live_session(text=""):
    """A session whose process is still running: the slave stays open, so writes do not fail."""
    master, slave = os.openpty()
    if text:
        os.write(slave, text.encode())
    return {"proc": _Proc(0), "fd": master, "transcript": text, "created": time.time(),
            "last": time.time(), "provider": "acme"}, slave


# ---- _interesting: what the user is told to do -------------------------------------------------
def test_interesting_takes_the_last_url_and_strips_sentence_punctuation():
    from hermcp.tools.providers import _interesting
    info = _interesting("see https://a.invalid/one. then https://b.invalid/two?x=1).")
    assert info["url"] == "https://b.invalid/two?x=1"


def test_interesting_reads_a_device_code_and_ignores_lowercase_or_short_ones():
    from hermcp.tools.providers import _interesting
    assert _interesting("Enter code: ABCD-1234")["user_code"] == "ABCD-1234"
    assert _interesting("code abcd-1234")["user_code"] is None
    assert _interesting("code AB-12")["user_code"] is None


# ---- session table: close, read ----------------------------------------------------------------
# The time and count bounds of the table are pinned in tests/test_providers_auth_sessions.py; what is
# here is the PTY mechanics those tests stub out (`_read_available`, `_close`).
def test_close_kills_a_process_that_ignores_terminate_and_closes_the_fd():
    from hermcp.tools import providers
    s = _fake_session(None, stubborn=True)
    providers._SESSIONS["auth_1_0"] = s
    providers._close("auth_1_0")
    assert s["proc"].terminated and s["proc"].killed
    assert "auth_1_0" not in providers._SESSIONS
    with pytest.raises(OSError):
        os.fstat(s["fd"])                                 # the master fd is gone


def test_read_available_returns_new_text_and_appends_to_the_transcript():
    from hermcp.tools import providers
    master, slave = os.openpty()
    s = {"fd": master, "transcript": "old ", "proc": _Proc(None), "last": time.time()}
    os.write(slave, b"Enter code: ABCD-1234\n")
    text = providers._read_available(s, wait=1.0)
    assert "ABCD-1234" in text and s["transcript"].startswith("old ") and "ABCD-1234" in s["transcript"]
    os.close(slave)
    os.close(master)


def test_read_available_keeps_the_transcript_bounded():
    from hermcp.tools import providers
    master, slave = os.openpty()
    s = {"fd": master, "transcript": "y" * (providers._MAX_TRANSCRIPT - 5), "proc": _Proc(None), "last": time.time()}
    os.write(slave, b"0123456789")
    providers._read_available(s, wait=1.0)
    assert len(s["transcript"]) == providers._MAX_TRANSCRIPT and s["transcript"].endswith("0123456789")
    os.close(slave)
    os.close(master)


def test_read_available_returns_promptly_when_the_other_side_is_gone():
    """A CLI that exited closes the PTY: the read must end at EOF/EIO, not sit out the whole wait."""
    from hermcp.tools import providers
    master, slave = os.openpty()
    os.close(slave)
    s = {"fd": master, "transcript": "", "proc": _Proc(0), "last": time.time()}
    t0 = time.monotonic()
    try:
        assert providers._read_available(s, wait=5.0) == ""
        assert time.monotonic() - t0 < 2.5
    finally:
        os.close(master)


def test_read_available_keeps_waiting_while_the_pty_is_silent():
    """Output that arrives after a quiet spell inside `wait` is still collected."""
    import threading
    from hermcp.tools import providers
    master, slave = os.openpty()
    s = {"fd": master, "transcript": "", "proc": _Proc(None), "last": time.time()}
    later = threading.Timer(0.3, os.write, (slave, b"late\n"))
    later.start()
    try:
        assert "late" in providers._read_available(s, wait=3.0)
    finally:
        later.cancel()
        os.close(slave)
        os.close(master)


def test_read_available_works_above_the_select_fd_limit(fd_above_select_limit):
    """`select.select` refuses any fd >= 1024; a long-running server hands out PTY fds that high."""
    from hermcp.tools import providers
    master, slave = os.openpty()
    high = fd_above_select_limit()
    os.dup2(master, high)
    os.close(master)
    s = {"fd": high, "transcript": "", "proc": _Proc(None), "last": time.time()}
    os.write(slave, b"Enter code: ABCD-1234\n")
    try:
        assert "ABCD-1234" in providers._read_available(s, wait=1.0)
    finally:
        os.close(high)
        os.close(slave)


# ---- providers_list ----------------------------------------------------------------------------
def test_providers_list_reads_config_and_the_credential_listing(rig, cli_log):
    cfg = yaml.safe_load((rig.home / "config.yaml").read_text())
    cfg["providers"] = {"zeta": {"base_url": "https://z.invalid/v1"}, "alpha": {}}
    cfg["custom_providers"] = {"mine": {}}
    (rig.home / "config.yaml").write_text(yaml.safe_dump(cfg))
    r = rig.ok("providers_list")
    assert r["providers"] == ["alpha", "zeta"] and r["custom_providers"] == ["mine"]
    assert "credentials_text" in r and [c["argv"] for c in cli_log()] == [["auth", "list"]]


def test_providers_list_can_skip_the_credential_listing(rig, cli_log):
    r = rig.ok("providers_list", include_credentials=False)
    assert "credentials_text" not in r and cli_log() == []


def test_providers_list_reports_a_config_failure_instead_of_failing(rig, monkeypatch):
    from hermcp.adapters import hconfig
    monkeypatch.setattr(hconfig, "get_value", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no config")))
    r = rig.ok("providers_list")
    assert "no config" in r["config_error"] and "providers" not in r


def test_providers_list_reports_a_cli_failure_instead_of_failing(rig, monkeypatch):
    monkeypatch.setenv("FAKE_HERMES_FAIL", "auth list exploded")
    r = rig.ok("providers_list")
    assert "credentials_error" in r and "credentials_text" not in r


# ---- providers_add -----------------------------------------------------------------------------
def test_providers_add_dry_run_lists_every_config_write(rig, cli_log):
    r = rig.ok("providers_add", name="mine", base_url="https://api.invalid/v1", key_env="MINE_API_KEY",
               models=["m1", "m2"], dry_run=True)
    assert r["dry_run"] is True and r["applied"] is False
    assert r["command"] == [["providers.mine.base_url", "https://api.invalid/v1"],
                            ["providers.mine.key_env", "MINE_API_KEY"],
                            ["providers.mine.models", "m1,m2"]]
    assert cli_log() == []


def test_providers_add_rejects_a_base_url_without_a_scheme(rig):
    assert rig.err("providers_add", name="mine", base_url="api.invalid/v1")["code"] == "invalid_arguments"


def test_providers_add_writes_each_key_through_config_set(rig, cli_log):
    r = rig.ok("providers_add", name="mine", base_url="https://api.invalid/v1", key_env="MINE_API_KEY")
    assert r["applied"] is True and r["keys"] == ["providers.mine.base_url", "providers.mine.key_env"]
    assert "secret_set" in r["next"] and "MINE_API_KEY" in r["next"]
    assert [c["argv"] for c in cli_log()] == [
        ["config", "set", "providers.mine.base_url", "https://api.invalid/v1", "--force"],
        ["config", "set", "providers.mine.key_env", "MINE_API_KEY", "--force"]]


# ---- provider_auth_start -----------------------------------------------------------------------
def test_provider_auth_start_dry_run_shows_the_command(rig, auth_cli):
    r = rig.ok("provider_auth_start", provider="acme", label="work", dry_run=True)
    assert r["dry_run"] is True and r["applied"] is False
    assert r["command"][1:] == ["auth", "add", "acme", "--type", "oauth", "--no-browser", "--label", "work"]


def test_provider_auth_start_returns_the_url_and_code_and_keeps_the_process(rig, auth_cli):
    r = rig.ok("provider_auth_start", provider="acme", wait_seconds=2)
    assert r["auth_id"].startswith("auth_") and r["provider"] == "acme"
    assert r["running"] is True and r["needs_input"] is True
    assert r["url"].startswith("https://example.invalid/device?user_code=")   # the query value is masked
    assert r["user_code"] == "ABCD-1234" and "Enter code" in r["output_excerpt"]
    from hermcp.tools import providers
    assert providers._SESSIONS[r["auth_id"]]["provider"] == "acme"


def test_provider_auth_start_passes_the_credential_type(rig, auth_cli, monkeypatch):
    from hermcp.tools import providers
    seen, real = [], providers._spawn
    monkeypatch.setattr(providers, "_spawn", lambda argv: (seen.append(argv), real(argv))[1])
    rig.ok("provider_auth_start", provider="acme", credential_type="api-key", wait_seconds=2)
    assert seen[0][1:] == ["auth", "add", "acme", "--type", "api-key", "--no-browser"]


def test_provider_auth_start_reports_a_cli_that_exits_and_discards_the_session(rig, monkeypatch):
    from hermcp.tools import providers
    monkeypatch.setattr(providers, "_spawn", lambda argv: _exited_session(3, "error: unknown provider\n"))
    err = rig.err("provider_auth_start", provider="acme", wait_seconds=2)
    assert err["code"] == "cli_error" and "unknown provider" in err["message"] and "exited 3" in err["message"]
    assert providers._SESSIONS == {}


# ---- provider_auth_continue --------------------------------------------------------------------
def test_provider_auth_continue_rejects_an_unknown_session(rig):
    assert rig.err("provider_auth_continue", auth_id="auth_1_0", input="x")["code"] == "not_found"


def test_provider_auth_continue_dry_run_sends_nothing(rig, auth_cli):
    started = rig.ok("provider_auth_start", provider="acme", wait_seconds=2)
    r = rig.ok("provider_auth_continue", auth_id=started["auth_id"], input="ABCD-1234", dry_run=True)
    assert r["dry_run"] is True and "9 chars" in r["summary"]


def test_provider_auth_continue_feeds_the_code_to_the_running_process(rig, auth_cli, monkeypatch):
    monkeypatch.setenv("FAKE_AUTH_MODE", "hang")
    started = rig.ok("provider_auth_start", provider="acme", wait_seconds=2)
    r = rig.ok("provider_auth_continue", auth_id=started["auth_id"], input="ABCD-1234", wait_seconds=2)
    assert r["running"] is True and r["success"] is False and r["exit_code"] is None
    assert "ABCD-1234" in r["output_excerpt"]


def test_provider_auth_continue_reports_success_and_forgets_the_session(rig):
    from hermcp.tools import providers
    sess, slave = _live_session("done\n")
    providers._SESSIONS["auth_1_0"] = sess
    try:
        r = rig.ok("provider_auth_continue", auth_id="auth_1_0", input="ABCD-1234", wait_seconds=2)
    finally:
        os.close(slave)
    assert r["running"] is False and r["exit_code"] == 0 and r["success"] is True
    assert "auth_1_0" not in providers._SESSIONS


def test_provider_auth_continue_reports_a_closed_session(rig, auth_cli):
    started = rig.ok("provider_auth_start", provider="acme", wait_seconds=2)
    from hermcp.tools import providers
    os.close(providers._SESSIONS[started["auth_id"]]["fd"])
    err = rig.err("provider_auth_continue", auth_id=started["auth_id"], input="ABCD-1234", wait_seconds=2)
    assert err["code"] == "adapter_error" and "closed" in err["message"]
    assert started["auth_id"] not in providers._SESSIONS


# ---- provider_auth_status / cancel -------------------------------------------------------------
def test_provider_auth_status_lists_nothing_without_sessions(rig):
    r = rig.ok("provider_auth_status")
    assert r["sessions"] == [] and r["count"] == 0
    assert set(r["limits"]) == {"idle_seconds", "hard_cap_seconds", "max_sessions"}


def test_provider_auth_status_reports_an_unknown_id(rig):
    r = rig.ok("provider_auth_status", auth_id="auth_9_9")
    assert r["sessions"] == [{"auth_id": "auth_9_9", "state": "unknown or finished"}]


def test_provider_auth_status_describes_a_running_session(rig, auth_cli):
    started = rig.ok("provider_auth_start", provider="acme", wait_seconds=2)
    row = rig.ok("provider_auth_status", auth_id=started["auth_id"])["sessions"][0]
    assert row["provider"] == "acme" and row["running"] is True and row["exit_code"] is None
    assert row["age_seconds"] >= 0 and row["url"].startswith("https://example.invalid/")
    assert row["user_code"] == "ABCD-1234" and "Enter code" in row["tail"]


def test_provider_auth_cancel_rejects_an_unknown_session(rig):
    assert rig.err("provider_auth_cancel", auth_id="auth_9_9")["code"] == "not_found"


def test_provider_auth_cancel_dry_run_keeps_the_session(rig, auth_cli):
    started = rig.ok("provider_auth_start", provider="acme", wait_seconds=2)
    r = rig.ok("provider_auth_cancel", auth_id=started["auth_id"], dry_run=True)
    assert r["dry_run"] is True
    from hermcp.tools import providers
    assert started["auth_id"] in providers._SESSIONS


def test_provider_auth_cancel_terminates_the_process(rig, auth_cli):
    started = rig.ok("provider_auth_start", provider="acme", wait_seconds=2)
    from hermcp.tools import providers
    proc = providers._SESSIONS[started["auth_id"]]["proc"]
    assert rig.ok("provider_auth_cancel", auth_id=started["auth_id"]) == {"cancelled": started["auth_id"]}
    assert started["auth_id"] not in providers._SESSIONS and proc.poll() is not None


# ---- secret_set --------------------------------------------------------------------------------
def test_secret_set_dry_run_writes_nothing_and_never_echoes_the_value(rig):
    r = rig.ok("secret_set", name="ACME_API_KEY", value=SECRET, dry_run=True)
    assert r["dry_run"] is True and r["applied"] is False and r["bytes"] == len(SECRET)
    assert SECRET not in json.dumps(r) and not (rig.home / ".env").exists()


def test_secret_set_writes_a_0600_env_file_without_returning_the_value(rig):
    r = rig.ok("secret_set", name="ACME_API_KEY", value=SECRET)
    env = rig.home / ".env"
    assert r["applied"] is True and r["length"] == len(SECRET) and SECRET not in json.dumps(r)
    assert env.read_text() == f"ACME_API_KEY={SECRET}\n"
    assert (env.stat().st_mode & 0o777) == 0o600


def test_secret_set_replaces_the_line_and_keeps_the_rest(rig):
    (rig.home / ".env").write_text("# keep me\nOTHER=1\nACME_API_KEY=old\n")
    rig.ok("secret_set", name="ACME_API_KEY", value=SECRET)
    assert (rig.home / ".env").read_text() == f"# keep me\nOTHER=1\nACME_API_KEY={SECRET}\n"


def test_secret_set_writes_into_the_requested_profile(rig):
    rig.ok("secret_set", name="ACME_API_KEY", value=SECRET, profile="researcher")
    assert (rig.home / "profiles" / "researcher" / ".env").read_text() == f"ACME_API_KEY={SECRET}\n"
    assert not (rig.home / ".env").exists()


def test_secret_set_needs_write_access_and_the_secrets_write_scope(rig):
    reader = Principal("r", "r", Scopes(access="read", secrets_write=True))
    assert rig.call("secret_set", principal=reader, name="ACME_API_KEY", value=SECRET)["error"]["code"] == "forbidden"
    writer = Principal("w", "w", Scopes(access="write"))
    err = rig.call("secret_set", principal=writer, name="ACME_API_KEY", value=SECRET)["error"]
    assert err["code"] == "forbidden" and "secrets_write" in err["message"]
    assert not (rig.home / ".env").exists()


def test_secret_set_keeps_the_value_out_of_the_audit_log(rig):
    rig.ok("secret_set", name="ACME_API_KEY", value=SECRET)
    rec = [x for x in rig.audit_lines() if x["tool"] == "secret_set"][0]
    assert rec["args"] == {} and rec["status"] == "ok"
    assert SECRET not in rig.audit.path.read_text()


# ---- secret_list -------------------------------------------------------------------------------
def test_secret_list_on_a_missing_env_file_is_empty(rig):
    assert rig.ok("secret_list") == {"path": str(rig.home / ".env"), "count": 0, "secrets": [],
                                     "note": "values are never exposed; refer to a secret by name"}


def test_secret_list_reports_names_set_state_and_the_comment_above(rig):
    (rig.home / ".env").write_text("# the acme key\nACME_API_KEY=" + SECRET + "\nEMPTY_ONE=\nQUOTED=\"\"\nPLAIN_SETTING=1\n")
    r = rig.ok("secret_list")
    rows = {x["name"]: x for x in r["secrets"]}
    assert r["count"] == 4 and SECRET not in json.dumps(r)
    assert rows["ACME_API_KEY"]["set"] is True and rows["ACME_API_KEY"]["secret"] is True
    assert rows["ACME_API_KEY"]["description"] == "the acme key" and rows["ACME_API_KEY"]["known_to_hermes"] is False
    assert rows["EMPTY_ONE"]["set"] is False and rows["QUOTED"]["set"] is False
    assert rows["PLAIN_SETTING"]["secret"] is False and rows["PLAIN_SETTING"]["description"] is None


def test_secret_list_skips_blank_and_non_assignment_lines(rig):
    (rig.home / ".env").write_text("# a hint\n\nnot an assignment\nACME_API_KEY=" + SECRET + "\n")
    rows = rig.ok("secret_list")["secrets"]
    assert [x["name"] for x in rows] == ["ACME_API_KEY"] and rows[0]["description"] is None


def test_secret_list_uses_the_hermes_catalogue_for_description_category_and_docs(rig, monkeypatch):
    from hermcp.tools import providers
    monkeypatch.setattr(providers, "_env_registry", lambda: {
        "ACME_API_KEY": {"password": True, "category": "provider", "description": "Acme key",
                         "url": "https://docs.invalid/acme"},
        "PLAIN_SETTING": {"category": "tool", "description": "not a secret"}})
    (rig.home / ".env").write_text("ACME_API_KEY=" + SECRET + "\nPLAIN_SETTING=1\n")
    rows = {x["name"]: x for x in rig.ok("secret_list")["secrets"]}
    assert rows["ACME_API_KEY"]["known_to_hermes"] is True and rows["ACME_API_KEY"]["category"] == "provider"
    assert rows["ACME_API_KEY"]["description"] == "Acme key" and rows["ACME_API_KEY"]["docs_url"] == "https://docs.invalid/acme"
    assert rows["PLAIN_SETTING"]["secret"] is False and rows["PLAIN_SETTING"]["category"] == "tool"


def test_secret_list_filters_by_prefix_secret_and_category(rig, monkeypatch):
    from hermcp.tools import providers
    monkeypatch.setattr(providers, "_env_registry", lambda: {"ACME_API_KEY": {"password": True, "category": "provider"},
                                                             "OTHER_TOKEN": {"category": "tool"}})
    (rig.home / ".env").write_text("ACME_API_KEY=" + SECRET + "\nOTHER_TOKEN=2\nPLAIN_SETTING=3\n")
    assert [x["name"] for x in rig.ok("secret_list", prefix="ACME")["secrets"]] == ["ACME_API_KEY"]
    assert [x["name"] for x in rig.ok("secret_list", secrets_only=True)["secrets"]] == ["ACME_API_KEY", "OTHER_TOKEN"]
    assert [x["name"] for x in rig.ok("secret_list", category="tool")["secrets"]] == ["OTHER_TOKEN"]


def test_secret_list_survives_a_missing_hermes_catalogue(rig, monkeypatch):
    from hermcp.adapters import common
    from hermcp.tools import providers
    monkeypatch.setattr(common, "ensure_hermes_importable", lambda: (_ for _ in ()).throw(ImportError("no hermes")))
    assert providers._env_registry() == {}
    (rig.home / ".env").write_text("ACME_API_KEY=" + SECRET + "\n")
    assert rig.ok("secret_list")["secrets"][0]["secret"] is True       # name-based detection still works


# ---- secret_delete -----------------------------------------------------------------------------
def test_secret_delete_reports_a_name_that_is_not_there(rig):
    (rig.home / ".env").write_text("OTHER=1\n")
    err = rig.err("secret_delete", name="ACME_API_KEY")
    assert err["code"] == "not_found" and "ACME_API_KEY" in err["message"]


def test_secret_delete_dry_run_keeps_the_file(rig):
    (rig.home / ".env").write_text("ACME_API_KEY=" + SECRET + "\nOTHER=1\n")
    r = rig.ok("secret_delete", name="ACME_API_KEY", dry_run=True)
    assert r["dry_run"] is True and "ACME_API_KEY" in r["summary"]
    assert (rig.home / ".env").read_text() == f"ACME_API_KEY={SECRET}\nOTHER=1\n"


def test_secret_delete_removes_only_that_line_and_keeps_0600(rig):
    (rig.home / ".env").write_text("ACME_API_KEY=" + SECRET + "\nOTHER=1\n")
    r = rig.ok("secret_delete", name="ACME_API_KEY")
    env = rig.home / ".env"
    assert r["applied"] is True and r["removed"] == "ACME_API_KEY"
    assert env.read_text() == "OTHER=1\n" and (env.stat().st_mode & 0o777) == 0o600


def test_secret_delete_needs_admin(rig):
    (rig.home / ".env").write_text("ACME_API_KEY=" + SECRET + "\n")
    writer = Principal("w", "w", Scopes(access="write", secrets_write=True))
    err = rig.call("secret_delete", principal=writer, name="ACME_API_KEY")["error"]
    assert err["code"] == "forbidden" and "admin" in err["message"]
    assert (rig.home / ".env").read_text() == f"ACME_API_KEY={SECRET}\n"


# ---- vault_list --------------------------------------------------------------------------------
def test_vault_list_without_a_vault_is_empty(rig):
    assert rig.ok("vault_list") == {"path": str(rig.home / "vault"), "count": 0, "items": []}


def test_vault_list_reports_an_unreadable_vault(rig):
    base = rig.home / "vault"
    base.mkdir()
    (base / "vault.json.enc").write_bytes(b"not a fernet token")
    err = rig.err("vault_list")
    assert err["code"] == "adapter_error" and err["message"].startswith("vault unreadable:")


def test_vault_list_returns_metadata_never_the_secret(rig):
    from hermcp.adapters.common import ensure_hermes_importable
    ensure_hermes_importable()
    from agent.vault_store import VaultStore
    VaultStore(base_dir=rig.home / "vault").add_item(
        kind="login", label="acme", origin="https://acme.invalid",
        secret={"identifier_type": "email", "identifier": "me@example.invalid", "password": SECRET})
    r = rig.ok("vault_list")
    assert r["count"] == 1
    item = r["items"][0]
    assert item["handle"].startswith("vault_") and item["kind"] == "login" and item["label"] == "acme"
    assert item["identifier"] == "me@example.invalid" and item["origin"] == "https://acme.invalid"
    assert SECRET not in json.dumps(r)
