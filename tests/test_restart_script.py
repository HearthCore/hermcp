"""deploy/systemd/hermcp-restart.sh: restarts hermcp.service only for a newer, non-adopted build.

systemctl and the clock are faked through the script's test overrides; nothing real is restarted.
"""
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path


SCRIPT = Path(__file__).resolve().parent.parent / "deploy" / "systemd" / "hermcp-restart.sh"


def _fake_systemctl(tmp_path, active="active", started_epoch=1_000.0):
    log = tmp_path / "systemctl.log"
    stamp_txt = datetime.fromtimestamp(started_epoch, timezone.utc).strftime("%a %Y-%m-%d %H:%M:%S UTC")
    bin_ = tmp_path / "systemctl"
    bin_.write_text(
        "#!/bin/sh\n"
        f"echo \"$@\" >> {log}\n"
        "case \"$1\" in\n"
        f"  is-active) echo {active}; [ {active!r} = active ] ;;\n"
        f"  show) echo '{stamp_txt}' ;;\n"
        "  restart) exit 0 ;;\n"
        "esac\n", encoding="utf-8")
    bin_.chmod(0o755)
    return bin_, log


def _run(tmp_path, stamp_obj, active="active", started_epoch=1_000.0):
    stamp = tmp_path / "install-stamp.json"
    stamp.write_text(json.dumps(stamp_obj), encoding="utf-8")
    systemctl, log = _fake_systemctl(tmp_path, active, started_epoch)
    env = dict(os.environ, HERMCP_STAMP=str(stamp), HERMCP_SYSTEMCTL=str(systemctl))
    cp = subprocess.run(["sh", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30)
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return cp, calls


def _iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def test_newer_build_restarts_only_hermcp(tmp_path):
    cp, calls = _run(tmp_path, {"builtAt": _iso(2_000.0)}, started_epoch=1_000.0)
    assert cp.returncode == 0, cp.stderr
    assert "restart hermcp.service" in calls
    assert all("gateway" not in c and "dashboard" not in c for c in calls)


def test_build_older_than_process_is_skipped(tmp_path):
    cp, calls = _run(tmp_path, {"builtAt": _iso(500.0)}, started_epoch=1_000.0)
    assert cp.returncode == 0
    assert not any(c.startswith("restart") for c in calls)
    assert "already runs this build" in cp.stdout


def test_boot_adoption_record_is_skipped_even_if_newer(tmp_path):
    cp, calls = _run(tmp_path, {"builtAt": _iso(2_000.0), "adoptedAt": _iso(2_000.0)}, started_epoch=1_000.0)
    assert cp.returncode == 0
    assert not any(c.startswith("restart") for c in calls)
    assert "boot-time adoption" in cp.stdout


def test_inactive_unit_is_left_alone(tmp_path):
    cp, calls = _run(tmp_path, {"builtAt": _iso(2_000.0)}, active="inactive", started_epoch=1_000.0)
    assert cp.returncode == 0
    assert not any(c.startswith("restart") for c in calls)


def test_units_restart_only_hermcp_and_are_debounced():
    unit_dir = SCRIPT.parent
    svc = (unit_dir / "hermcp-restart.service").read_text(encoding="utf-8")
    path = (unit_dir / "hermcp-restart.path").read_text(encoding="utf-8")
    assert "ExecStartPre=/bin/sleep 60" in svc
    assert "StartLimitBurst=3" in svc and "StartLimitIntervalSec=600" in svc
    assert "PathChanged=/usr/local/lib/hermes-agent/install-stamp.json" in path
    assert "Unit=hermcp-restart.service" in path
    assert "gateway" not in svc.lower() and "dashboard" not in svc.lower()
