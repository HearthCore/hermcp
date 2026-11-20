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
# isolation: no test may read the developer's real plugin .env, shell settings or Hermes home
# ---------------------------------------------------------------------------
# Why: `load_settings` merges the plugin's `.env` and the process environment, so a host that
# serves the Ledger (`HERMCP_LEDGER=1`, `HERMCP_LEDGER_AUTH=none`, `HERMCP_HOST=0.0.0.0`) or
# exports a proxy setting failed the default-behaviour tests while CI stayed green (#81). A fixed
# list of names missed the readers outside `load_settings` (`HERMCP_API_KEY_<PROFILE>`,
# `HERMCP_DASHBOARD_*`, #87), so every `HERMCP_*` is blanked by prefix, plus the Hermes variables
# and host variables hermcp reads. `tests/test_settings_isolation.py` holds this against the source.
RUNNER_ENV_VARS = ("HERMCP_HERMES_AGENT_DIR",)     # set by scripts/run_tests.sh for the probe interpreter
HOST_ENV_VARS = (
    "HERMES_API_URL",     # load_settings: else a test could reach the host's live api_server
    "HERMES_API_KEY",     # load_settings: a secret, never from the developer's shell
    "HERMES_PROFILE",     # approvals_hook: set inside a Hermes agent session that runs the suite
    "INVOCATION_ID",      # lock: set when the suite runs under systemd (a CI runner service, a unit)
)
HERMES_ENV_PREFIXES = ("HERMES_PLATFORM", "HERMES_SESSION")   # set inside an agent session
UNREACHABLE_API = "http://127.0.0.1:1"   # refused at once; a test that wants a gateway starts a fake one
UNBOUND_PORT = "1"   # doctor/status probe hermcp's own port: never the host's live server on 9230


def _host_env_names() -> list[str]:
    return [n for n in os.environ
            if (n.startswith("HERMCP_") and n not in RUNNER_ENV_VARS)
            or n in HOST_ENV_VARS or n.startswith(HERMES_ENV_PREFIXES)]


@pytest.fixture(autouse=True)
def isolated_settings_env(tmp_path_factory):
    """Blank the host's hermcp/Hermes settings, and give every test an empty home and `.env`.

    - every `HERMCP_*` (except the runner's agent dir) and the host variables above are removed;
    - `HERMCP_ENV_FILE` points at a `.env` that does not exist, so the install's own is never read;
    - `HERMES_API_URL` is an address that refuses at once, so nothing reaches a live api_server, and
      `HERMCP_PORT` is a port nothing serves, so `doctor`/`status` never probe the live hermcp;
    - `HERMES_HOME` is a fresh empty directory per test: a plain `pytest` (CI, an IDE) would else
      fall back to the live `~/.hermes`, and one shared directory would let tests leak state into
      each other through the inbox database. The `home` fixture replaces it with a populated one.

    Autouse, so it runs before the fixtures that set a value on purpose (`home` sets
    `HERMCP_HERMES_BIN`, a test sets `HERMCP_LEDGER`). Its own `MonkeyPatch` instance, not the
    `monkeypatch` fixture: requesting that fixture here would make this autouse fixture tear down
    *after* the test's patches are undone, and a test that replaces a module attribute (e.g.
    `restart_units.detect_agent_dir`) would then see its own teardown run against the replacement.
    """
    mp = pytest.MonkeyPatch()
    for name in _host_env_names():
        mp.delenv(name, raising=False)
    mp.setenv("HERMCP_ENV_FILE", str(tmp_path_factory.mktemp("no-plugin-env") / ".env"))
    mp.setenv("HERMES_API_URL", UNREACHABLE_API)
    mp.setenv("HERMCP_PORT", UNBOUND_PORT)
    mp.setenv("HERMES_HOME", str(tmp_path_factory.mktemp("empty-hermes-home")))
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
def scratch_tools():
    """Register throw-away tools in the shared registry for one test, and take them out again.

    `hermcp.tools.reg` is a process-wide singleton. A tool a test adds and leaves behind shows up
    in every later test that walks the registry (doc counts, tools.html, descriptions, the bound-key
    classification), so the suite only passed in file order (#87).
    """
    from hermcp.tools import reg
    added = []

    def add(tool):
        reg.add(tool)
        added.append(tool.name)
        return tool
    yield add
    for name in added:
        reg.tools.pop(name, None)


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
