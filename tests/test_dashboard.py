"""dashboard_status / dashboard_restart: every detection stage against canned systemctl, `hermes dashboard --status`
and /proc data. No test touches the real dashboard, systemd or the network."""
from __future__ import annotations

import json
import signal
from pathlib import Path

import pytest

from hermcp.adapters import dashboard as D
from hermcp.registry import Principal
from hermcp.scopes import Scopes
from hermcp.tools import dashboard as T

SECRET = "sk-live-9f8e7d6c5b4a3b2c1d0e9f8a7b6c5d4e"
ENV_SECRET = "hunter2-very-secret-value-0123456789"
UNIT_ARGV = "/usr/local/bin/hermes dashboard --port 9119 --host 0.0.0.0 --no-open"
LAUNCHER = ["/opt/py/bin/python3", "-I", "-I", "-c",
            "import sys, runpy; sys.argv = ['-c', 'dashboard', '--port', '9119', '--host', '0.0.0.0', '--no-open']; "
            "exec(\"sys.path.insert(0, '/usr/local/lib/hermes-agent')\\nimport hermes_bootstrap\\nfrom hermes_cli.main import main\")"]


def show(unit="hermes-dashboard.service", *, active="active", pid=4242, argv=UNIT_ARGV, load="loaded", mem="infinity"):
    return "\n".join([
        "Restart=always", f"MainPID={pid}", "NRestarts=0",
        f"ExecStart={{ path={argv.split()[0]} ; argv[]={argv} ; ignore_errors=no ; start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }}",
        f"ControlGroup=/system.slice/{unit}", "MemoryCurrent=1073741824", "CPUUsageNSec=5000000000", f"MemoryMax={mem}",
        "WorkingDirectory=/home/user/.hermes", f"LoadState={load}", f"ActiveState={active}", "SubState=running" if active == "active" else "SubState=dead",
        f"FragmentPath=/etc/systemd/system/{unit}", "UnitFileState=enabled", "ActiveEnterTimestamp=Mon 2026-10-26 17:33:11 CET"])


def status_text(*pids_cmds):
    if not pids_cmds:
        return "No hermes dashboard or serve processes running.\n"
    out = [f"{len(pids_cmds)} hermes dashboard/serve process(es) running:"]
    out += [f"    PID {p} [dashboard]: {c}" for p, c in pids_cmds]
    return "\n".join(out) + "\n"


class FakeEnv(D.Env):
    """Scripted host. `units` maps (scope, name) -> `systemctl show` text; `procs` maps pid -> /proc facts."""

    def __init__(self, *, units=None, procs=None, status_rc=0, status_out=None, http=None, listen=True, euid=0,
                 selfp=(1, 2), journal="", restart_pid=None):
        self.units = dict(units or {})
        self.procs = dict(procs or {})
        self.status_rc, self.status_out = status_rc, status_out
        self.http = http if http is not None else {"/": 302, "/api/status": 200}
        self.listen, self._euid, self.selfp = listen, euid, set(selfp)
        self.journal = journal
        self.restart_pid = restart_pid
        self.calls: list[list[str]] = []
        self.kills: list[tuple[int, int]] = []
        self.spawned: list[dict] = []
        self.clock = 1000.0
        self.logtext = ""
        self.restart_rc = 0

    # -- commands
    def hermes_bin(self):
        return "/usr/local/bin/hermes"

    def run(self, argv, timeout=15.0):
        self.calls.append(list(argv))
        if argv[:3] == ["/usr/local/bin/hermes", "dashboard", "--status"]:
            if self.status_rc:
                return self.status_rc, "", "boom: install lock"
            if self.status_out is not None:
                return 0, self.status_out, ""
            return 0, status_text(*[(p, " ".join(v["argv"])) for p, v in self.procs.items() if v.get("listed", True)]), ""
        if argv and argv[0] == "sudo":
            argv = argv[3:]
        if argv and argv[0] == "systemctl":
            scope = "user" if "--user" in argv else "system"
            a = [x for x in argv[1:] if x != "--user"]
            if a[0] == "show":
                txt = self.units.get((scope, a[1]))
                return (0, txt, "") if txt else (0, "LoadState=not-found\nActiveState=inactive\n", "")
            if a[0] in ("restart", "start"):
                if self.restart_rc:
                    return self.restart_rc, "", "Job failed"
                if self.restart_pid:
                    self.units[(scope, a[1])] = show(a[1], pid=self.restart_pid)
                    self.procs.pop(4242, None)
                    self.procs[self.restart_pid] = {"argv": UNIT_ARGV.split(), "cwd": "/home/user/.hermes", "environ": {},
                                                    "cgroup": f"/system.slice/{a[1]}"}
                return 0, "", ""
            return 0, "", ""
        if argv and argv[0] == "journalctl":
            return 0, self.journal, ""
        return 127, "", "unexpected"

    # -- /proc
    def proc_file(self, pid, name):
        p = self.procs.get(pid)
        if p is None:
            return None
        if name == "cmdline":
            return b"\x00".join(a.encode() for a in p["argv"]) + b"\x00"
        if name == "environ":
            return None if p.get("environ") is None else b"\x00".join(f"{k}={v}".encode() for k, v in p["environ"].items()) + b"\x00"
        if name == "cgroup":
            return f"0::{p.get('cgroup', '/user.slice/session-1.scope')}\n".encode()
        if name == "status":
            return b"Name:\thermes\nUid:\t0\t0\t0\t0\nVmRSS:\t   512000 kB\n"
        if name == "stat":
            return b"4242 (hermes) S 1 4242 4242 0 -1 4194560 0 0 0 0 1500 500 0 0 20 0 1 0 1 1 1 1 1 1"
        return None

    def proc_link(self, pid, name):
        return (self.procs.get(pid) or {}).get(name)

    def proc_pids(self):
        return sorted(self.procs)

    def pid_alive(self, pid):
        p = self.procs.get(pid)
        return bool(p and p.get("alive", True))

    def kill(self, pid, sig):
        self.kills.append((pid, sig))
        if pid in self.procs and not self.procs[pid].get("immortal"):
            self.procs[pid]["alive"] = False

    def popen_detached(self, argv, cwd, env, log):
        pid = 9000 + len(self.spawned)
        self.spawned.append({"argv": argv, "cwd": cwd, "env": env, "pid": pid})
        self.procs[pid] = {"argv": argv, "cwd": cwd, "environ": env, "listed": True}
        return pid

    def listening(self, host, port):
        return self.listen

    def http_status(self, host, port, path):
        return self.http.get(path)

    def euid(self):
        return self._euid

    def self_pids(self):
        return self.selfp

    def sleep(self, s):
        self.clock += s

    def now(self):
        return self.clock

    def read_log(self, path, tail=4000):
        return self.logtext

    def log_path(self):
        return Path("/nonexistent/dashboard-process.log")


def native_unit_env(**kw):
    procs = {4242: {"argv": UNIT_ARGV.split(), "cwd": "/home/user/.hermes", "environ": {"HERMES_HOME": "/home/user/.hermes", "OPENAI_API_KEY": ENV_SECRET},
                    "cgroup": "/system.slice/hermes-dashboard.service"}}
    return FakeEnv(units={("system", "hermes-dashboard.service"): show()}, procs=procs, **kw)


def free_env(*, cwd: str | None = "/home/user/.hermes", environ=True, argv=None, **kw):
    procs = {777: {"argv": argv or ["/usr/local/bin/hermes", "dashboard", "--port", "9120", "--no-open"], "cwd": cwd,
                   "environ": {"HERMES_HOME": "/home/user/.hermes", "OPENAI_API_KEY": ENV_SECRET} if environ else None,
                   "cgroup": "/user.slice/user-0.slice/session-3.scope"}}
    return FakeEnv(procs=procs, **kw)


def stage(det, name):
    return next(s for s in det["stages"] if s["stage"] == name)


# ---- parsing ---------------------------------------------------------------------------------------------------
def test_hermes_args_plain_and_launcher_form():
    assert D.hermes_args(["/usr/local/bin/hermes", "dashboard", "--port", "9119"]) == ["dashboard", "--port", "9119"]
    assert D.hermes_args(LAUNCHER) == ["dashboard", "--port", "9119", "--host", "0.0.0.0", "--no-open"]
    assert D.hermes_args(["/usr/local/bin/hermes", "-p", "researcher", "dashboard", "--isolated"]) == ["dashboard", "--isolated"]


@pytest.mark.parametrize("argv", [["/usr/local/bin/hermes", "dashboard", "--status"], ["/usr/local/bin/hermes", "dashboard", "--stop"],
                                  ["/usr/bin/herdr", "--session", "hermes", "server"], ["pytest", "-k", "dashboard"],
                                  ["/usr/local/bin/hermes", "gateway", "run"], []])
def test_hermes_args_rejects_non_dashboards(argv):
    assert D.hermes_args(argv) is None


def test_hermes_status_line_parse_and_launcher_cmdline():
    env = FakeEnv(procs={5: {"argv": LAUNCHER, "cwd": "/home/user/.hermes", "environ": {}}})
    procs, st = D.stage_hermes(env)
    assert st["hit"] and [p.pid for p in procs] == [5] and procs[0].port == 9119 and procs[0].host == "0.0.0.0"


def test_hermes_status_has_no_json_flag_documented_in_result():
    det = D.detect(native_unit_env())
    assert "no JSON" in det["hermes_status_note"] and "always exits 0" in det["hermes_status_note"]


# ---- stage 2: native/known unit -------------------------------------------------------------------------------
def test_known_unit_system_scope():
    det = D.detect(native_unit_env())
    assert det["mode"] == "systemd" and det["stage"] == "known_unit" and det["detected"]
    assert det["unit"]["unit"] == "hermes-dashboard.service" and det["unit"]["scope"] == "system"
    assert det["pid"] == 4242 and det["port"] == 9119
    assert det["start_command"] == UNIT_ARGV
    assert [s["stage"] for s in det["stages"]] == ["config", "hermes", "known_unit", "custom_unit", "process"]
    assert stage(det, "hermes")["hit"] and stage(det, "known_unit")["hit"]
    assert not stage(det, "custom_unit")["hit"] and "known unit" in stage(det, "custom_unit")["reason"]
    assert not stage(det, "process")["hit"]


def test_known_unit_user_scope_is_asked_first_like_hermes():
    env = native_unit_env()
    env.units = {("user", "hermes-dashboard.service"): show(pid=4242)}
    det = D.detect(env)
    assert det["unit"]["scope"] == "user"
    first_show = next(c for c in env.calls if "show" in c)
    assert "--user" in first_show


def test_known_unit_with_foreign_exec_is_not_a_dashboard_unit():
    env = FakeEnv(units={("system", "hermes-dashboard.service"): show(argv="/usr/bin/sleep infinity")})
    det = D.detect(env)
    assert det["mode"] == "none"
    assert "not a `hermes dashboard` command" in stage(det, "known_unit")["reason"]


def test_known_unit_installed_but_stopped_is_still_systemd():
    env = FakeEnv(units={("system", "hermes-dashboard.service"): show(active="inactive", pid=0)})
    det = D.detect(env)
    assert det["mode"] == "systemd" and det["pid"] is None and det["unit"]["state"] == "inactive"


# ---- stage 3: custom unit ------------------------------------------------------------------------------------
def test_custom_unit_owning_the_pid():
    procs = {4300: {"argv": ["/usr/local/bin/hermes", "dashboard", "--port", "9200"], "cwd": "/srv", "environ": {},
                    "cgroup": "/system.slice/my-dash.service"}}
    env = FakeEnv(units={("system", "my-dash.service"): show("my-dash.service", pid=4300, argv="/usr/local/bin/hermes dashboard --port 9200")},
                  procs=procs)
    det = D.detect(env)
    assert det["mode"] == "systemd" and det["stage"] == "custom_unit"
    assert det["unit"]["unit"] == "my-dash.service" and det["port"] == 9200
    assert stage(det, "known_unit")["hit"] is False and stage(det, "custom_unit")["hit"] is True


def test_pid_started_from_a_shell_inside_some_unit_is_not_owned_by_it():
    """cron.service etc.: the dashboard sits in that cgroup but is not the unit's MainPID -> free-standing process."""
    procs = {4300: {"argv": ["/usr/local/bin/hermes", "dashboard", "--port", "9200"], "cwd": "/srv", "environ": {},
                    "cgroup": "/system.slice/cron.service"}}
    env = FakeEnv(units={("system", "cron.service"): show("cron.service", pid=99, argv="/usr/sbin/cron -f")}, procs=procs)
    det = D.detect(env)
    assert det["mode"] == "process" and det["stage"] == "process"
    assert "not its MainPID" in stage(det, "custom_unit")["reason"]


# ---- stage 4: free-standing process --------------------------------------------------------------------------
def test_free_standing_process_complete_recipe():
    det = D.detect(free_env())
    assert det["mode"] == "process" and det["stage"] == "process" and det["pid"] == 777 and det["port"] == 9120
    rec = det["recipe"]
    assert rec["complete"] and rec["cwd"] == "/home/user/.hermes" and "OPENAI_API_KEY" in rec["env_names"]
    assert rec["start_command"] == "/usr/local/bin/hermes dashboard --port 9120 --no-open"
    assert ENV_SECRET not in json.dumps(D.public(det))


@pytest.mark.parametrize("kw,missing", [({"cwd": None}, "cwd"), ({"environ": False}, "environment")])
def test_incomplete_recipe_gives_manual_plan_and_never_runs(kw, missing):
    env = free_env(**kw)
    det = D.detect(env)
    pl = D.plan(env, det)
    assert pl["executable"] is False and "manual / config needed" in pl["blocked"] and missing in pl["blocked"]
    res = D.restart(env, det)
    assert res["applied"] is False and not env.kills and not env.spawned


def test_launcher_process_recipe_uses_hermes_entry_point_not_the_inline_script():
    env = FakeEnv(procs={5: {"argv": LAUNCHER, "cwd": "/home/user/.hermes", "environ": {"A": "1"}}})
    det = D.detect(env)
    assert det["recipe"]["complete"]
    assert det["recipe"]["start_command"] == "/usr/local/bin/hermes dashboard --port 9119 --host 0.0.0.0 --no-open"


def test_serve_mode_is_not_restarted():
    env = free_env(argv=["/usr/local/bin/hermes", "serve", "--port", "9121"])
    env.status_out = status_text().replace("No hermes", "No hermes")      # CLI says nothing -> own /proc scan finds it
    det = D.detect(env)
    assert det["mode"] == "process" and not det["recipe"]["complete"]
    assert "only `hermes dashboard`" in " ".join(det["recipe"]["missing"])


def test_hermes_cli_failure_falls_back_to_proc_scan():
    env = free_env(status_rc=1)
    det = D.detect(env)
    assert stage(det, "hermes")["hit"] is False and "failed" in stage(det, "hermes")["reason"]
    assert "fallback" in stage(det, "hermes") and det["mode"] == "process"


def test_process_override_on_a_unit_supervised_pid_is_blocked_not_killed():
    cfg = D.DashboardConfig(mode="process", command=["/usr/local/bin/hermes", "dashboard", "--port", "9119"], cwd="/home/user/.hermes")
    env = native_unit_env()
    det = D.detect(env, cfg)
    pl = D.plan(env, det)
    assert det["mode"] == "process" and det["process"]["supervised_by"] == "hermes-dashboard.service (system)"
    assert pl["executable"] is False and "mode=systemd" in pl["blocked"]
    assert D.restart(env, det)["applied"] is False and not env.kills and not env.spawned


# ---- nothing / ambiguous -------------------------------------------------------------------------------------
def test_nothing_found():
    det = D.detect(FakeEnv())
    assert det["mode"] == "none" and det["detected"] is False and det["stage"] is None
    assert all(not s["hit"] for s in det["stages"]) and all(s.get("reason") for s in det["stages"])
    pl = D.plan(FakeEnv(), det)
    assert not pl["executable"] and "nothing to restart" in pl["blocked"]


def test_ambiguous_unit_stopped_but_process_running():
    env = FakeEnv(units={("system", "hermes-dashboard.service"): show(active="inactive", pid=0)},
                  procs={777: {"argv": ["/usr/local/bin/hermes", "dashboard", "--port", "9119"], "cwd": "/home/user", "environ": {},
                               "cgroup": "/user.slice/session-1.scope"}})
    det = D.detect(env)
    assert det["mode"] == "ambiguous" and det["candidates"] == 2
    pl = D.plan(env, det)
    assert not pl["executable"] and "ambiguous" in pl["blocked"]
    assert D.restart(env, det)["applied"] is False and not env.kills


def test_ambiguous_two_free_processes():
    env = free_env()
    env.procs[778] = {**env.procs[777], "argv": ["/usr/local/bin/hermes", "dashboard", "--port", "9130"]}
    det = D.detect(env)
    assert det["mode"] == "ambiguous" and {o["pid"] for o in det["options"]} == {777, 778}


# ---- config override -----------------------------------------------------------------------------------------
def test_default_config_is_auto_and_needs_no_file(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    cfg, err = D.load_config("default", env={})
    assert cfg.mode == "auto" and not err and cfg.source == "defaults"


def test_override_systemd_via_env_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    cfg, err = D.load_config("default", env={"HERMCP_DASHBOARD_MODE": "systemd", "HERMCP_DASHBOARD_UNIT": "my-dash.service",
                                              "HERMCP_DASHBOARD_SCOPE": "user"})
    assert (cfg.mode, cfg.unit, cfg.scope, cfg.source) == ("systemd", "my-dash.service", "user", "env") and not err
    env = FakeEnv(units={("user", "my-dash.service"): show("my-dash.service", pid=55)},
                  procs={55: {"argv": UNIT_ARGV.split(), "cwd": "/", "environ": {}}})
    det = D.detect(env, cfg)
    assert det["mode"] == "systemd" and det["stage"] == "config" and det["unit"]["unit"] == "my-dash.service"
    assert stage(det, "config")["hit"] and "skipped" in stage(det, "known_unit")["name"]
    assert not any("hermes-dashboard.service" in " ".join(c) for c in env.calls)     # known unit never asked


def test_override_unit_missing_does_not_fall_back_to_guessing():
    cfg = D.DashboardConfig(mode="systemd", unit="nope.service", scope="system")
    det = D.detect(native_unit_env(), cfg)
    assert det["mode"] == "none" and "not usable" in stage(det, "config")["reason"]


def test_override_process_recipe_from_json_per_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    f = tmp_path / "dash.json"
    f.write_text(json.dumps({"default": {"mode": "auto"},
                             "profiles": {"researcher": {"mode": "process", "command": "/usr/local/bin/hermes dashboard --port 9300 --no-open",
                                                    "cwd": "/home/user/.hermes"}}}))
    cfg, err = D.load_config("researcher", env={}, path=f)
    assert cfg.mode == "process" and cfg.cwd == "/home/user/.hermes" and "researcher" in cfg.source and not err
    assert D.load_config("default", env={}, path=f)[0].mode == "auto"
    env = FakeEnv(procs={61: {"argv": ["/usr/local/bin/hermes", "dashboard", "--port", "9300", "--no-open"], "cwd": "/elsewhere",
                              "environ": {"X": "1"}, "cgroup": "/user.slice/s.scope"}},
                  units={("system", "hermes-dashboard.service"): show()})
    det = D.detect(env, cfg, "researcher")
    assert det["mode"] == "process" and det["stage"] == "config"
    assert det["recipe"]["cwd"] == "/home/user/.hermes" and det["recipe"]["from"] == "config"      # config wins over /proc


@pytest.mark.parametrize("raw,needle", [
    ({"mode": "systemd"}, "needs unit"), ({"mode": "process"}, "needs command"), ({"mode": "banana"}, "mode must be"),
    ({"mode": "process", "command": "/bin/rm -rf /"}, "start command"), ({"nope": 1}, "unknown key"),
    ({"mode": "process", "command": "hermes dashboard", "cwd": "rel/path"}, "absolute"),
    ({"mode": "systemd", "unit": "../evil.service"}, "needs unit")])
def test_invalid_override_is_reported_and_ignored(raw, needle):
    cfg, err = D._clean(raw, "test")
    assert cfg is None and needle in err


def test_invalid_json_config_keeps_status_working(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    f = tmp_path / "dash.json"
    f.write_text("{not json")
    cfg, err = D.load_config("default", env={}, path=f)
    assert cfg.mode == "auto" and "not valid JSON" in err


# ---- redaction ----------------------------------------------------------------------------------------------
def test_start_command_secrets_are_scrubbed():
    argv = ["/usr/local/bin/hermes", "dashboard", "--port", "9119", "--token", SECRET, f"--api-key={SECRET}", "--ssh-session-token-file", "/tmp/t"]
    cmd = D.scrub_cmd(argv)
    assert SECRET not in cmd and "--token [redacted]" in cmd and "--api-key=[redacted]" in cmd
    assert "/tmp/t" in cmd                                   # file flags are paths, not secrets


def test_unit_exec_and_status_never_contain_secrets_or_env_values():
    env = native_unit_env()
    env.units[("system", "hermes-dashboard.service")] = show(argv=f"{UNIT_ARGV} --token {SECRET}")
    out = json.dumps(D.status(env))
    assert SECRET not in out and ENV_SECRET not in out


def test_registry_output_goes_through_central_redaction(happ, monkeypatch):
    env = free_env()
    env.procs[777]["argv"] = ["/usr/local/bin/hermes", "dashboard", "--port", "9120", "--password", ENV_SECRET]
    monkeypatch.setattr(T, "get_env", lambda: env)
    p = Principal("a", "a", Scopes(access="admin"))
    for name, args in (("dashboard_status", {}), ("dashboard_restart", {"dry_run": True})):
        r = happ.registry.invoke(name, args, p, happ.settings, None)
        assert r["ok"], r
        text = json.dumps(r)
        assert ENV_SECRET not in text and "OPENAI_API_KEY" in text          # names yes, values never


# ---- status payload -----------------------------------------------------------------------------------------
def test_status_has_http_resources_and_meta(happ, monkeypatch):
    monkeypatch.setattr(T, "get_env", lambda: native_unit_env())
    r = happ.registry.invoke("dashboard_status", {}, Principal("a", "a", Scopes(access="read")), happ.settings, None)
    assert r["ok"]
    res = r["result"]
    assert res["http"]["/"] == 302 and res["http"]["/api/status"] == 200 and res["http"]["port"] == 9119
    assert res["resources"]["rss_mb"] == 500.0 and res["resources"]["memory_max"] == "none (infinity)"
    assert res["resources"]["cgroup_memory_mb"] == 1024.0
    assert "_meta" in res and res["running"] is True


def test_status_memory_max_reported_when_set():
    env = native_unit_env()
    env.units[("system", "hermes-dashboard.service")] = show(mem="2147483648")
    assert D.status(env)["resources"]["memory_max"] == "2147483648"


def test_status_of_nothing_is_a_clean_answer():
    out = D.status(FakeEnv())
    assert out["mode"] == "none" and "http" not in out


# ---- dry_run / confirm -------------------------------------------------------------------------------------
def _restart(happ, monkeypatch, env, args, access="admin"):
    monkeypatch.setattr(T, "get_env", lambda: env)
    return happ.registry.invoke("dashboard_restart", args, Principal("a", "a", Scopes(access=access)), happ.settings, None)


def _mutating(env):
    return [c for c in env.calls if any(v in c for v in ("restart", "start", "reset-failed"))]


def test_dry_run_changes_nothing_systemd(happ, monkeypatch):
    env = native_unit_env()
    r = _restart(happ, monkeypatch, env, {"dry_run": True, "confirm": True})["result"]
    assert r["applied"] is False and r["dry_run"] is True
    assert r["plan"]["commands"] == ["systemctl restart hermes-dashboard.service"]
    assert r["plan"]["side_effects"] and r["plan"]["after"]
    assert not _mutating(env) and not env.kills and not env.spawned


def test_dry_run_changes_nothing_process(happ, monkeypatch):
    env = free_env()
    r = _restart(happ, monkeypatch, env, {"dry_run": True, "confirm": True})["result"]
    assert r["applied"] is False and r["plan"]["executable"] is True
    assert r["plan"]["commands"][0].startswith("kill -TERM 777")
    assert not env.kills and not env.spawned


def test_without_confirm_it_refuses(happ, monkeypatch):
    env = native_unit_env()
    r = _restart(happ, monkeypatch, env, {})["result"]
    assert r["applied"] is False and r["needed"] == "confirm=true" and "confirm=true" in r["refused"]
    assert r["plan"]["commands"]
    assert not _mutating(env)


def test_restart_needs_admin_scope(happ, monkeypatch):
    r = _restart(happ, monkeypatch, native_unit_env(), {"confirm": True}, access="write")
    assert r["ok"] is False and r["error"]["code"] == "forbidden"


def test_bound_key_cannot_see_dashboard_tools(happ):
    p = Principal("b", "b", Scopes(access="admin", profiles=["researcher"]))
    for n in ("dashboard_status", "dashboard_restart"):
        assert happ.registry.invoke(n, {}, p, happ.settings, None)["error"]["code"] == "forbidden"


def test_not_restartable_is_an_error_with_the_plan(happ, monkeypatch):
    r = _restart(happ, monkeypatch, FakeEnv(), {"confirm": True})
    assert r["ok"] is False and r["error"]["code"] == "not_restartable" and r["error"]["plan"]["blocked"]


def test_incomplete_recipe_with_confirm_is_refused_not_guessed(happ, monkeypatch):
    env = free_env(cwd=None)
    r = _restart(happ, monkeypatch, env, {"confirm": True})
    assert r["ok"] is False and r["error"]["code"] == "not_restartable"
    assert "manual / config needed" in r["error"]["message"] and not env.kills and not env.spawned


def test_target_pid_of_gateway_or_hermcp_is_refused():
    env = native_unit_env(selfp=(1, 4242))          # the unit's MainPID is "the gateway"
    det = D.detect(env)
    pl = D.plan(env, det)
    assert pl["executable"] is False and "refused" in pl["blocked"]
    assert D.restart(env, det)["applied"] is False and not _mutating(env)


# ---- real restart (fakes) ---------------------------------------------------------------------------------
def test_restart_systemd_verified(happ, monkeypatch):
    env = native_unit_env(restart_pid=5555, journal="Started Hermes Agent Web Dashboard\nINFO listening\n")
    r = _restart(happ, monkeypatch, env, {"confirm": True})
    assert r["ok"], r
    res = r["result"]
    assert res["applied"] is True and res["old_pid"] == 4242 and res["new_pid"] == 5555
    assert res["verify"]["healthy"] and res["verify"]["http"]["/"] == 302 and res["verify"]["http"]["/api/status"] == 200
    assert res["verify"]["log_errors"] == []
    restarts = _mutating(env)
    assert restarts == [["systemctl", "restart", "hermes-dashboard.service"]]       # exactly this unit, nothing else
    assert not env.kills


def test_restart_reports_error_lines_but_stays_applied(happ, monkeypatch):
    env = native_unit_env(restart_pid=5555, journal="Traceback (most recent call last):\nValueError: x\nERROR plugin foo failed\n")
    res = _restart(happ, monkeypatch, env, {"confirm": True})["result"]
    assert res["applied"] is True and res["verify"]["log_errors"] and "error-like" in res["note"]


def test_restart_systemd_failure_retries_once_then_reports(happ, monkeypatch):
    env = native_unit_env(restart_pid=None)       # PID never changes: dashboard does not come back
    r = _restart(happ, monkeypatch, env, {"confirm": True})
    assert r["ok"] is False and r["error"]["code"] == "restart_failed"
    assert "journalctl -u hermes-dashboard.service" in r["error"]["manual"]
    cmds = [" ".join(c) for c in env.calls if c[0] == "systemctl" and c[1] in ("restart", "reset-failed", "start")]
    assert cmds == ["systemctl restart hermes-dashboard.service", "systemctl reset-failed hermes-dashboard.service",
                    "systemctl start hermes-dashboard.service"]


def test_restart_process_stops_and_starts_with_the_recipe(happ, monkeypatch):
    env = free_env()
    r = _restart(happ, monkeypatch, env, {"confirm": True})
    assert r["ok"], r
    res = r["result"]
    assert res["applied"] and res["old_pid"] == 777 and res["new_pid"] == 9000
    assert env.kills == [(777, signal.SIGTERM)]                       # SIGTERM only, never SIGKILL
    sp = env.spawned[0]
    assert sp["argv"] == ["/usr/local/bin/hermes", "dashboard", "--port", "9120", "--no-open"] and sp["cwd"] == "/home/user/.hermes"
    assert sp["env"]["OPENAI_API_KEY"] == ENV_SECRET                  # env reaches the child ...
    assert ENV_SECRET not in json.dumps(r)                            # ... but never the answer


def test_restart_process_that_will_not_stop_starts_nothing(happ, monkeypatch):
    env = free_env()
    env.procs[777]["immortal"] = True
    r = _restart(happ, monkeypatch, env, {"confirm": True})
    assert r["ok"] is False and r["error"]["code"] == "restart_failed" and "did not stop" in r["error"]["message"]
    assert not env.spawned and env.kills == [(777, signal.SIGTERM)]


def test_restart_process_failed_start_is_retried_once(happ, monkeypatch):
    env = free_env(listen=False)                                       # port never comes up
    r = _restart(happ, monkeypatch, env, {"confirm": True})
    assert r["ok"] is False and len(env.spawned) == 2
    assert "start by hand" in r["error"]["manual"]


# ---- registration / audit -----------------------------------------------------------------------------------
def test_registration_matches_gateway_restart(happ):
    t = happ.registry.tools
    g, d, s = t["gateway_restart"], t["dashboard_restart"], t["dashboard_status"]
    assert (d.toolset, d.access, d.core) == (g.toolset, g.access, g.core) == ("ops", "admin", True)
    assert (s.toolset, s.access) == ("ops", "read")
    assert "dry_run" in d.input_schema()["properties"] and "confirm" in d.input_schema()["properties"]


def test_audit_entry_is_written(tmp_path, happ, monkeypatch):
    from hermcp.audit import AuditLog
    audit = AuditLog(tmp_path / "audit.jsonl")
    monkeypatch.setattr(T, "get_env", lambda: native_unit_env())
    p = Principal("k1", "claude", Scopes(access="admin"))
    happ.registry.invoke("dashboard_restart", {"dry_run": True}, p, happ.settings, audit)
    happ.registry.invoke("dashboard_restart", {}, p, happ.settings, audit)
    happ.registry.invoke("dashboard_status", {}, p, happ.settings, audit)
    rows = audit.tail(10)
    assert [r["tool"] for r in rows] == ["dashboard_restart", "dashboard_restart", "dashboard_status"]
    assert all(r["status"] == "ok" and r["key_name"] == "claude" for r in rows)
