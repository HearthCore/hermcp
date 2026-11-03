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
    return create_app(load_settings({"HERMES_HOME": str(home), "HERMCP_ALLOWED_HOSTS": "testserver,127.0.0.1"}))


@pytest.fixture()
def cli_log(home):
    """Parsed list of the commands the fake `hermes` received."""
    import json

    def read():
        p = Path(os.environ["FAKE_HERMES_LOG"])
        return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []
    return read
