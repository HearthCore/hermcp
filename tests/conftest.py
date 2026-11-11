import json
import os
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
    # load_settings also reads the plugin's own .env; blank the opt-in switches so a developer
    # machine that has one enabled does not leak it into the default-behaviour tests.
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
