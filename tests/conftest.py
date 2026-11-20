import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PLUGINS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLUGINS))   # `import hermcp` resolves to the plugin dir
# `python -m pytest` run from the plugin dir puts that dir on sys.path, so hermcp/tools (and other
# plugin-local names) shadow Hermes' own top-level packages (`tools.threat_patterns`, ...) during
# in-process Hermes imports. Keep only the parent on the path, whatever the invocation.
PLUGIN_DIR = Path(__file__).resolve().parents[1]
sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != PLUGIN_DIR]


def tracked_files(*patterns: str) -> list[Path]:
    """The committed repository files matching the glob `patterns` (e.g. `docs/*.md`), sorted.

    Tests that check the repository's own files must see what is committed, not what happens to
    lie in the working tree: a live install keeps git-excluded notes under `docs/prep/`, a
    developer has `node_modules/` or a scratch script next to the code. Outside a git checkout
    (an unpacked tarball) every file on disk counts — there is nothing else to go by.
    """
    try:
        out = subprocess.run(["git", "-C", str(PLUGIN_DIR), "ls-files", "-z", "--",
                              *(f":(glob){p}" for p in patterns)],
                             capture_output=True, check=True, timeout=30,
                             # a git hook exports GIT_DIR/GIT_INDEX_FILE for *its* repository
                             env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")}).stdout
    except (OSError, subprocess.SubprocessError):
        return sorted({p for pat in patterns for p in PLUGIN_DIR.glob(pat) if p.is_file()})
    return sorted(p for p in (PLUGIN_DIR / n for n in out.decode().split("\0") if n) if p.is_file())


# ---------------------------------------------------------------------------
# isolation: no test may read the developer's real plugin .env or shell settings
# ---------------------------------------------------------------------------
# Why: `load_settings` merges the plugin's `.env` and the process environment, so a host that
# serves the Ledger (`HERMCP_LEDGER=1`, `HERMCP_LEDGER_AUTH=none`, `HERMCP_HOST=0.0.0.0`) or
# exports a proxy setting failed the default-behaviour tests while CI stayed green. Every variable
# `load_settings` reads is blanked below, and the `.env` it reads is pointed at an empty temp dir,
# so a test sees the documented defaults unless it sets a value itself.
# `tests/test_settings_isolation.py` holds this list against settings.py, so a new setting cannot
# be forgotten here.
SETTINGS_ENV_VARS = (
    "HERMCP_HOST", "HERMCP_PORT", "HERMCP_PUBLIC_URL", "HERMCP_ALLOWED_HOSTS",
    "HERMCP_HERMES_BIN", "HERMCP_TRUST_PROXY", "HERMCP_TRUSTED_PROXIES", "HERMCP_CALL_TIMEOUT",
    "HERMCP_OAUTH_DCR", "HERMCP_OAUTH_REDIRECT_URIS",
    "HERMCP_INBOX_MAX_EVENTS", "HERMCP_INBOX_RETENTION_DAYS", "HERMCP_INBOX_RATE_PER_MIN",
    "HERMCP_INBOX_JOIN_WINDOW_MIN", "HERMCP_INBOX_WATCHER", "HERMCP_INBOX_WATCH_INTERVAL",
    "HERMCP_INBOX_WATCH_MAX_PER_KEY", "HERMCP_INBOX_RUN_MAX_EVENTS", "HERMCP_INBOX_WATCH_TTL_HOURS",
    "HERMCP_INBOX_APPROVALS", "HERMCP_SESSION_MAX_RUNS",
    "HERMCP_DEBUG_CAPTURE", "HERMCP_DEBUG_CAPTURE_MAX", "HERMCP_DEBUG_CAPTURE_HOURS",
    "HERMCP_SYSTEMD_SCOPE", "HERMCP_SELF_SYSTEMD_SCOPE",
    "HERMCP_LEDGER", "HERMCP_LEDGER_AUTH", "HERMCP_LEDGER_BASE_URL", "HERMCP_LEDGER_KANBAN_URL",
    "HERMCP_LEDGER_POLL_SECONDS", "HERMCP_LEDGER_WINDOW_HOURS", "HERMCP_LEDGER_CACHE_TTL",
    "HERMCP_LEDGER_CACHE_MAX", "HERMCP_LEDGER_VIEW_KEY", "HERMCP_LEDGER_LIST_LIMIT",
    "HERMCP_LEDGER_CRON_LIMIT", "HERMCP_LEDGER_SESSIONS_LIMIT", "HERMCP_LEDGER_RUNS_LIMIT",
    "HERMCP_LEDGER_DESKTOP_URL", "HERMCP_LEDGER_WEB_URL", "HERMCP_LEDGER_ASK_CLAUDE_URL",
    "HERMCP_LEDGER_ASK_CLAUDE_DESKTOP_URL",
    "HERMCP_PROFILES_IGNORE",
    "HERMCP_AUTH_SESSION_MAX_AGE", "HERMCP_AUTH_SESSION_HARD_CAP", "HERMCP_AUTH_SESSION_MAX",
    # deprecated names load_settings still maps onto the Ledger settings
    "HERMCP_LAGEBILD", "HERMCP_LAGEBILD_AUTH", "HERMCP_LAGEBILD_BASE_URL",
    "HERMCP_LAGEBILD_POLL_SECONDS", "HERMCP_LAGEBILD_WINDOW_HOURS", "HERMCP_LAGEBILD_CACHE_TTL",
    # not HERMCP_*, but read by load_settings and a secret: never from the developer's shell
    "HERMES_API_KEY",
)


@pytest.fixture(autouse=True)
def isolated_settings_env(tmp_path_factory):
    """Blank every setting `load_settings` reads and point its `.env` at an empty temp dir.

    Autouse, so it runs before the fixtures that set a value on purpose (`home` sets
    `HERMCP_HERMES_BIN`, a test sets `HERMCP_LEDGER`). `HERMES_HOME` is left alone: `home` owns it,
    and the runner (`scripts/run_tests.sh`) already points it at a throw-away directory.

    Its own `MonkeyPatch` instance, not the `monkeypatch` fixture: requesting that fixture here
    would make this autouse fixture tear down *after* the test's patches are undone, and a test
    that replaces a module attribute (e.g. `restart_units.detect_agent_dir`) would then see its
    own teardown run against the replacement.
    """
    mp = pytest.MonkeyPatch()
    for name in SETTINGS_ENV_VARS:
        mp.delenv(name, raising=False)
    mp.setenv("HERMCP_ENV_FILE", str(tmp_path_factory.mktemp("no-plugin-env") / ".env"))
    yield
    mp.undo()


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """Temporary HERMES_HOME — tests never touch the real one."""
    h = tmp_path / "hermes_home"
    (h / "profiles" / "researcher").mkdir(parents=True)
    (h / "config.yaml").write_text("model:\n  default: test-model\n  provider: test\n")
    (h / "profiles" / "researcher" / "config.yaml").write_text("model:\n  default: researcher-model\n")
    monkeypatch.setenv("HERMES_HOME", str(h))
    # Never run the real CLI from tests: it ignores a temp HERMES_HOME for kanban/profiles.
    fake = Path(__file__).with_name("fake_hermes.py")
    monkeypatch.setenv("HERMCP_HERMES_BIN", str(fake))
    monkeypatch.setenv("FAKE_HERMES_LOG", str(tmp_path / "fake_hermes.log"))
    for k in ("FAKE_HERMES_REPLY", "FAKE_HERMES_FAIL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.delenv("HERMCP_PUBLIC_URL", raising=False)
    return h


@pytest.fixture()
def happ(home):
    from hermcp.app import create_app
    from hermcp.settings import load_settings
    # The plugin's .env and the shell's settings are already blanked (isolated_settings_env);
    # these two are the values this app needs.
    return create_app(load_settings({"HERMES_HOME": str(home), "HERMCP_ALLOWED_HOSTS": "testserver,127.0.0.1",
                                     "HERMCP_DEBUG_CAPTURE": ""}))


@pytest.fixture()
def cli_log(home):
    """Parsed list of the commands the fake `hermes` received."""
    import json

    def read():
        p = Path(os.environ["FAKE_HERMES_LOG"])
        return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []
    return read


# ---------------------------------------------------------------------------
# safety net: a test must never be able to delete the live key store
# ---------------------------------------------------------------------------
# Why: `Settings.data` once re-read os.environ, so a settings object built with a temp
# HERMES_HOME still pointed at the live ~/.hermes/plugin-data/hermcp — and a test that cleaned
# up with shutil.rmtree(settings.data) deleted the running server's keys.json. The root cause
# is fixed (settings.py) and tests/test_settings_data_dir.py holds that down; this fixture is
# the belt-and-braces check that the live store is still byte-identical after every test.

LIVE_DATA = Path.home() / ".hermes" / "plugin-data" / "hermcp"


def _live_key_ids():
    """The live key ids, or None when there is no live store (CI, a fresh checkout).

    Not mtime and not the bytes: the running server legitimately rewrites keys.json when a key
    is used (last_used). What must never change is *which* keys exist — a deleted or truncated
    store loses ids, and that is exactly the accident to catch.
    """
    p = LIVE_DATA / "keys.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    rows = data if isinstance(data, list) else (data.get("keys") or [])
    return sorted(str(r.get("id")) for r in rows if isinstance(r, dict))


@pytest.fixture(autouse=True)
def live_key_store_untouched():
    """Fail the test that removed keys from the LIVE store — checked before and after."""
    before = _live_key_ids()
    yield
    after = _live_key_ids()
    assert after == before, (
        f"a test changed the LIVE key store at {LIVE_DATA / 'keys.json'} "
        f"(before={before}, after={after}). Tests must resolve settings.data inside their own "
        "tmp HERMES_HOME — see tests/test_settings_data_dir.py.")
