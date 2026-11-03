"""`python -m hermcp selftest`: checks that what hermcp reports equals what a FRESH process sees.

For every profile and a set of tools, the hermcp answer is compared field by field with a reference produced in a
brand-new subprocess (a probe, the `hermes` CLI, or a direct read of the file on disk). It also hammers the
in-process tools with interleaved, concurrent profile switches, verifies the provenance block and the redaction
echo, and (in a throw-away HERMES_HOME) proves that a change on disk is visible on the very next call.

Read-only against the real HERMES_HOME. Exit code 0 only if no check failed. Output is a short table.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

PASS, FAIL, INFO = "PASS", "FAIL", "INFO"


class Report:
    def __init__(self) -> None:
        self.rows: list[dict[str, str]] = []

    def add(self, check: str, result: str, detail: str = "") -> None:
        self.rows.append({"check": check, "result": result, "detail": detail})

    def eq(self, check: str, got: Any, want: Any, detail: str = "") -> None:
        if got == want:
            self.add(check, PASS, detail or _short(got))
        else:
            self.add(check, FAIL, f"hermcp={_short(got)} reference={_short(want)}")

    @property
    def failed(self) -> int:
        return sum(1 for r in self.rows if r["result"] == FAIL)

    def table(self) -> str:
        w = max([len(r["check"]) for r in self.rows] + [5])
        lines = [f"{'CHECK'.ljust(w)}  RESULT  DETAIL", f"{'-' * w}  ------  ------"]
        lines += [f"{r['check'].ljust(w)}  {r['result'].ljust(6)}  {r['detail']}" for r in self.rows]
        counts = {k: sum(1 for r in self.rows if r["result"] == k) for k in (PASS, FAIL, INFO)}
        lines.append(f"\n{counts[PASS]} passed, {counts[FAIL]} failed, {counts[INFO]} info")
        return "\n".join(lines)


def _short(v: Any, n: int = 110) -> str:
    s = json.dumps(v, default=str, sort_keys=True) if not isinstance(v, str) else v
    return s if len(s) <= n else s[:n] + "…"


# -- references (fresh processes) ---------------------------------------------------------------------
def _env(home: Path, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("HERMES_PLATFORM", "HERMES_SESSION"))}
    env.update({"HERMES_HOME": str(home), "NO_COLOR": "1", "TERM": "dumb", **extra})
    return env


def _hermes_cli(profile: str, argv: list[str], timeout: float = 120, **env: str) -> str:
    from .adapters.common import hermes_bin, profile_home
    home = profile_home(profile)
    base = home.parent.parent if profile != "default" else home
    cmd = [hermes_bin()] + (["-p", profile] if profile != "default" else []) + argv
    cp = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=_env(base, **env), cwd=str(base),
                        stdin=subprocess.DEVNULL)
    return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", cp.stdout + cp.stderr)


def _probe(profile: str, op: str, args: dict | None = None, **env: str) -> dict:
    from .adapters.common import PROBE, agent_dir, profile_home
    home = profile_home(profile)
    cp = subprocess.run([sys.executable, "-I", str(PROBE), str(agent_dir()), op, json.dumps(args or {})],
                        capture_output=True, text=True, timeout=120, env=_env(home, **env), cwd=str(home),
                        stdin=subprocess.DEVNULL)
    if cp.returncode != 0:
        raise RuntimeError(f"reference probe {op}/{profile} failed: {cp.stderr[-300:]}")
    return json.loads(cp.stdout.strip().splitlines()[-1])


def _raw_yaml(profile: str) -> dict:
    import yaml
    from .adapters.common import profile_home
    return yaml.safe_load((profile_home(profile) / "config.yaml").read_text()) or {}


# -- harness ------------------------------------------------------------------------------------------
class Rig:
    def __init__(self, settings=None) -> None:
        from .registry import Principal, install_meta_tools
        from .scopes import Scopes
        from .settings import load_settings
        from .tools import load_all
        self.reg = load_all()
        if "hermes_search_tools" not in self.reg.tools:
            install_meta_tools(self.reg)
        self.settings = settings or load_settings()
        self.principal = Principal("selftest", "selftest", Scopes(access="read"))

    def call(self, tool: str, **args: Any) -> dict:
        r = self.reg.invoke(tool, args, self.principal, self.settings, None)
        if not r["ok"]:
            raise RuntimeError(f"{tool} -> {r['error']}")
        if not isinstance(r["result"], dict):          # list-shaped tools carry their provenance beside the result
            return {"_list": r["result"], "_meta": r.get("meta")}
        return r["result"]


def _guard(rep: Report, name: str, fn: Callable[[], None]) -> None:
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 - a broken check is a failed check, never a crash
        rep.add(name, FAIL, f"{type(exc).__name__}: {str(exc)[:200]}")


def _deep_get(d: Any, dotted: str) -> Any:
    for p in dotted.split("."):
        if not isinstance(d, dict) or p not in d:
            return None
        d = d[p]
    return d


# -- the checks ---------------------------------------------------------------------------------------
def check_profiles(rep: Report, rig: Rig, profiles: list[str]) -> None:
    subs = {p: Report() for p in profiles}
    with ThreadPoolExecutor(max_workers=4) as ex:
        list(ex.map(lambda p: _check_one_profile(subs[p], rig, p), profiles))
    for p in profiles:
        rep.rows += subs[p].rows


def _check_one_profile(rep: Report, rig: Rig, prof: str) -> None:
    if True:
        def cfg_and_models(prof=prof):
            ref = _probe(prof, "config")["config"]
            for key in ("model.default", "model.provider", "delegation.model", "cron.model", "kanban.auto_decompose",
                        "kanban.orchestrator_profile"):
                got = rig.call("config_get", key=key, profile=prof)
                rep.eq(f"config_get {key} [{prof}]", got["value"], _deep_get(ref, key))
                if got["_meta"]["profile"] != prof or got["key"] != key:
                    rep.add(f"config_get meta/label [{prof}]", FAIL, f"profile={got['_meta']['profile']} key={got['key']}")
            cur = rig.call("models_current", profile=prof)
            rep.eq(f"models_current default [{prof}]", cur["model"]["default"], _deep_get(ref, "model.default"))
            ref_fb = [(e.get("provider"), e.get("model")) for e in (ref.get("fallback_providers") or [])]
            rep.eq(f"models_current fallbacks [{prof}]", [(e.get("provider"), e.get("model")) for e in cur["fallbacks"]], ref_fb)
        _guard(rep, f"config/models [{prof}]", cfg_and_models)

        def mcp(prof=prof):
            raw = _probe(prof, "config_raw")["raw"].get("mcp_servers") or {}
            got = rig.call("mcp_list", profile=prof, include="all")["servers"]
            rep.eq(f"mcp_list names+enabled [{prof}]", {k: v.get("enabled", True) for k, v in got.items()},
                   {k: (v or {}).get("enabled", True) for k, v in raw.items()})
        _guard(rep, f"mcp_list [{prof}]", mcp)

        def cron(prof=prof):
            ref = _probe(prof, "cron_jobs")["jobs"]
            got = rig.call("cron_list", profile=prof, include="all", limit=500, detail=True)["jobs"]
            rep.eq(f"cron_list id/state/last_status [{prof}]",
                   sorted((j["id"], j.get("state"), j.get("last_status")) for j in got),
                   sorted((j["id"], j.get("state"), j.get("last_status")) for j in ref))
        _guard(rep, f"cron_list [{prof}]", cron)

        def skills(prof=prof):
            got = rig.call("skills_list", profile=prof, limit=500)
            eff = got["effective_enabled_count"]
            for plat in ("api_server", "cron"):
                if plat not in eff:
                    continue
                ref = _probe(prof, "catalog_visible", HERMES_PLATFORM=plat)
                rep.eq(f"skills effective {plat} [{prof}]", eff[plat], ref["count"],
                       f"{eff[plat]} (session catalog in fresh process on {plat})")
            ref = _probe(prof, "catalog_visible")
            rep.eq(f"skills global enabled [{prof}]", eff["global"], ref["count"])
            names = sorted(s["name"] for s in got["skills"] if s["enabled"])
            rep.eq(f"skills enabled names [{prof}]", names, ref["names"])
            rep.eq(f"skills meta source [{prof}]", got["_meta"]["source"], "subprocess")
        _guard(rep, f"skills_list [{prof}]", skills)


def check_global_tools(rep: Report, rig: Rig) -> None:
    def health():
        gs = json.loads(Path(rig.settings.data.parent.parent / "gateway_state.json").read_text())
        h = rig.call("hermes_health", detail=False)
        rep.eq("hermes_health gateway pid vs file", h["gateway"]["pid"], gs["pid"])
        rep.eq("hermes_health meta.gateway_pid", h["_meta"]["gateway_pid"], gs["pid"])
        ver = _hermes_cli("default", ["--version"]).splitlines()[0]
        rep.add("hermes_version vs `hermes --version`", PASS if h["_meta"]["hermes_version"] and h["_meta"]["hermes_version"] in ver
                else FAIL, f"meta={h['_meta']['hermes_version']} cli={ver[:60]}")
        for pname, entry in h["gateway"]["platforms"].items():
            if not {"updated_at", "writer_pid", "stale"} <= set(entry):
                rep.add(f"gateway platform fields [{pname}]", FAIL, "missing updated_at/writer_pid/stale")
                break
        else:
            rep.add("gateway platform fields", PASS, f"{len(h['gateway']['platforms'])} live + "
                                                   f"{len(h['gateway']['stale_platforms'])} stale entries flagged")
        if h["_meta"].get("hermcp_older_than_hermes_build"):
            rep.add("hermcp process vs Hermes build", INFO, "hermcp started BEFORE the Hermes build on disk: restart hermcp "
                                                          "(in-process answers may come from old code)")
        else:
            rep.add("hermcp process vs Hermes build", PASS, f"up {h['_meta']['hermcp']['uptime_s']:.0f}s, not older than the build")
    _guard(rep, "hermes_health", health)

    def plugins():
        got = rig.call("plugins_list", include="all", limit=500)
        raw = _hermes_cli("default", ["plugins", "list", "--json"])
        ref = json.loads(raw[_first_json(raw):])
        rep.eq("plugins_list count/status", sorted((p["name"], p["status"]) for p in got["plugins"]),
               sorted((p["name"], p["status"]) for p in ref))
    _guard(rep, "plugins_list", plugins)

    def memory():
        got = rig.call("memory_status", profile="default")["output"].strip()
        ref = _hermes_cli("default", ["memory", "status"]).strip()[-4000:]
        rep.eq("memory_status text", got, ref, "identical to a fresh `hermes memory status`")
    _guard(rep, "memory_status", memory)

    def kanban():
        got = rig.call("kanban_boards", all=True)["_list"]
        n = sum(sum(v for v in b.get("counts", {}).values()) for b in got)
        from .adapters.overview import kanban_boards
        rep.eq("kanban_boards counts vs direct DB", n, sum(b["total"] for b in kanban_boards()))
    _guard(rep, "kanban_boards", kanban)


def _first_json(text: str) -> int:
    return min([i for i in (text.find("["), text.find("{")) if i >= 0], default=0)


def check_meta_contract(rep: Report, rig: Rig) -> None:
    need = {"source", "sources", "generated_at", "profile", "gateway_pid", "hermes_version", "hermcp"}
    calls = [("config_get", {"key": "model.default"}), ("models_current", {}), ("mcp_list", {}), ("cron_list", {}),
             ("skills_list", {"limit": 3}), ("status_gateway", {}), ("profiles_list", {"skills": False}),
             ("hermes_health", {"skills": False}), ("logs_tail", {"lines": 2}), ("profile_files_read", {}),
             ("hooks_list", {}), ("secrets_status", {}), ("plugin_config_get", {"plugin": "hermcp"}),
             ("cron_runs", {"limit": 2}), ("kanban_boards", {}), ("sessions_list", {"limit": 1}),
             ("orchestration_overview", {"skills": False}), ("runs_active", {})]
    bad = []
    for name, args in calls:
        try:
            m = rig.call(name, **args).get("_meta")
        except Exception as exc:  # noqa: BLE001
            bad.append(f"{name}: {str(exc)[:60]}")
            continue
        if not m or not need <= set(m) or abs(m["generated_at"] - time.time()) > 120:
            bad.append(f"{name}: meta incomplete")
    rep.add("every checked tool returns a complete _meta", FAIL if bad else PASS,
            "; ".join(bad) if bad else f"{len(calls)} tools: source, sources, generated_at, profile, gateway_pid, hermes_version, hermcp")


def check_isolation_under_load(rep: Report, rig: Rig, profiles: list[str]) -> None:
    want = {p: _deep_get(_raw_yaml(p), "model.default") for p in profiles}

    def one(i: int) -> str | None:
        p = profiles[i % len(profiles)]
        got = rig.call("config_get", key="model.default", profile=p)
        return None if got["value"] == want[p] and got["_meta"]["profile"] == p else f"{p}: {got['value']!r}"
    seq = [one(i) for i in range(len(profiles) * 6)]
    with ThreadPoolExecutor(max_workers=8) as ex:
        par = list(ex.map(one, range(len(profiles) * 12)))
    bad = [x for x in seq + par if x]
    rep.add("profile isolation (interleaved + 8 threads)", FAIL if bad else PASS,
            f"{len(seq) + len(par)} config_get calls across {len(profiles)} profiles, {len(bad)} wrong" + (f": {bad[:3]}" if bad else ""))

    def skills_one(i: int) -> str | None:
        p = profiles[i % len(profiles)]
        r = rig.call("skills_list", profile=p, limit=1)
        return None if r["_meta"]["profile"] == p and r["total"] > 0 else p
    with ThreadPoolExecutor(max_workers=4) as ex:
        sbad = [x for x in ex.map(skills_one, range(len(profiles) * 2)) if x]
    rep.add("skills_list under 4 threads", FAIL if sbad else PASS, f"{len(profiles) * 2} calls, {len(sbad)} wrong")


def check_list_matrices(rep: Report, rig: Rig, profiles: list[str], skip_slow: bool) -> None:
    """The profile matrices of the list tools against per-profile fresh-process references."""
    m = rig.call("skills_list", profiles=",".join(profiles), include="all", limit=500)
    api = rig.call("skills_list", profiles=",".join(profiles), include="all", limit=500, platform="api_server")
    for prof in profiles:
        ref = set(_probe(prof, "catalog_visible")["names"])
        on = {r["name"] for r in m["skills"] if r["profiles"][prof] == "on"}
        rep.eq(f"skills matrix global [{prof}]", on, ref, f"{len(on)} enabled")
        ref_api = set(_probe(prof, "catalog_visible", HERMES_PLATFORM="api_server")["names"])
        on_api = {r["name"] for r in api["skills"] if prof in r["active_in"]}
        rep.eq(f"skills matrix api_server [{prof}]", on_api, ref_api, f"{len(on_api)} effective on api_server")
    lst = m["_meta"]["list"]
    rep.add("skills matrix _meta.list consistent", PASS if lst["total"] == lst["active"] + lst["disabled"] == m["total"] else FAIL,
            f"total={lst['total']} active={lst['active']} disabled={lst['disabled']}")
    dflt = rig.call("skills_list", limit=500)
    rep.add("skills default hides disabled, reports it", PASS if dflt["_meta"]["list"]["hidden_by_default"] ==
            dflt["_meta"]["list"]["disabled"] and all(x["status"] == "enabled" for x in dflt["skills"]) else FAIL,
            f"hidden {dflt['_meta']['list']['hidden_by_default']}")
    mc = rig.call("mcp_list", profiles=",".join(profiles), include="all")
    exp = {}
    for prof in profiles:
        for n, c in (_probe(prof, "config_raw")["raw"].get("mcp_servers") or {}).items():
            exp.setdefault(n, {})[prof] = "on" if (c or {}).get("enabled", True) else "off"
    got = {r["name"]: {p: v for p, v in r["profiles"].items() if v != "absent"} for r in mc["servers"]}
    rep.eq("mcp matrix vs raw config of every profile", got, exp, f"{len(exp)} servers")
    cr = rig.call("cron_list", profiles=",".join(profiles), include="all", limit=500)
    # cron_list omits the `profile` key when exactly one profile is requested (the profile is implied);
    # with several profiles it is always present. Fill it in from the single requested profile.
    got_c = sorted((j.get("profile", profiles[0]), j["id"], j["state"]) for j in cr["jobs"])
    exp_c = sorted((p, j["id"], j.get("state")) for p in profiles for j in _probe(p, "cron_jobs")["jobs"])
    rep.eq("cron_list profiles vs fresh stores", got_c, exp_c, f"{len(exp_c)} jobs")
    if not skip_slow:
        pm = rig.call("plugins_list", profiles=",".join(profiles), include="all", limit=500)
        exp_p = {}
        for prof in profiles:
            raw = _hermes_cli(prof, ["plugins", "list", "--json"])
            for p in json.loads(raw[_first_json(raw):]):
                exp_p.setdefault(p["name"], {})[prof] = "on" if p["status"] == "enabled" else "off"
        got_p = {r["name"]: {p: v for p, v in r["profiles"].items() if v != "absent"} for r in pm["plugins"]}
        rep.eq("plugins matrix vs `hermes -p X plugins list`", got_p, exp_p, f"{len(exp_p)} plugins")


def check_bound_isolation(rep: Report, rig: Rig, profiles: list[str]) -> None:
    """A key bound to ONE profile must not see or touch any other profile (in-memory principals, no key is created)."""
    import sqlite3

    from .registry import Principal
    from .scopes import Scopes
    from .settings import hermes_home
    if len(profiles) < 2:
        # Cross-profile isolation needs a foreign profile to try; with one profile there is nothing to refuse.
        rep.add("bound keys", INFO, f"skipped: needs >=2 profiles, got {len(profiles)} (run with --profiles all)")
        return
    others_all = {p for p in profiles}
    owner_of: dict[str, str] = {}
    try:
        con = sqlite3.connect(f"file:{hermes_home() / 'kanban.db'}?mode=ro", uri=True)
        owner_of = {tid: a for tid, a in con.execute("SELECT id, assignee FROM tasks WHERE assignee IS NOT NULL LIMIT 400")}
        con.close()
    except sqlite3.Error:
        pass

    def call(pr, tool, **a):
        return rig.reg.invoke(tool, a, pr, rig.settings, None)
    global_tools = ["keys_list", "audit_tail", "kanban_boards", "kanban_stats", "models_providers", "gateway_restart",
                    "dashboard_status", "dashboard_restart", "ops_gateway_logs", "plugins_check_updates", "providers_list", "inbox_push"]
    for p in profiles:
        pr = Principal(f"bound-{p}", f"bound-{p}", Scopes(access="write", profiles=[p]))
        q_list = [q for q in profiles if q != p]
        bad = []
        # 1. profile argument
        q = q_list[0]
        r = call(pr, "config_get", key="model.default", profile=q)
        if r["ok"] or r["error"]["code"] != "forbidden" or f"bound to profile '{p}'" not in r["error"]["message"]:
            bad.append("config_get other profile not refused")
        mine = call(pr, "config_get", key="model.default")
        if not mine["ok"] or mine["result"]["_meta"]["profile"] != p or mine["result"]["value"] != _deep_get(_raw_yaml(p), "model.default"):
            bad.append("config_get own profile wrong")
        # 2. other tool families refuse a foreign profile
        for tool, args in (("sessions_list", {}), ("cron_list", {}), ("skills_list", {}), ("profile_files_read", {}), ("mcp_list", {}),
                           ("plugins_list", {}), ("secrets_status", {}), ("hooks_list", {}), ("logs_tail", {"log": "gateway"}),
                           ("approvals_pending", {}), ("chat_conversations", {})):
            r = call(pr, tool, profile=q, **args)
            if r["ok"] or r["error"]["code"] != "forbidden":
                bad.append(f"{tool}(profile={q}) not refused")
        for tool in global_tools:
            r = call(pr, tool)
            if r["ok"] or r["error"]["code"] not in ("forbidden", "invalid_arguments"):
                bad.append(f"{tool} reachable")
        # 3. matrix / overview views contain only this profile
        for tool, args in (("skills_list", {"profiles": "all", "view": "summary"}), ("mcp_list", {"profiles": "all"}),
                           ("cron_list", {"profiles": "all", "include": "all"}), ("profiles_list", {"skills": False}),
                           ("hermes_health", {"skills": False}), ("orchestration_overview", {"skills": False}),
                           ("status_gateway", {})):
            r = call(pr, tool, **args)
            if not r["ok"]:
                bad.append(f"{tool}: {r['error']['code']}")
                continue
            # only profile-shaped fields: a skill may legitimately be called like a profile
            res = r["result"]
            fields = [res.get("profiles"), res.get("per_profile"), res.get("profile_details"),
                      (res.get("gateway") or {}).get("served_profiles"), (res.get("gateway") or {}).get("parked_profiles"),
                      [x.get("name") for x in res.get("profiles", []) if isinstance(x, dict)] if isinstance(res.get("profiles"), list) else None,
                      list(res.get("profiles", {})) if isinstance(res.get("profiles"), dict) else None]
            dump = json.dumps(fields)
            for o in others_all - {p, "default"}:
                if f'"{o}"' in dump or f'"{o}:' in dump:
                    bad.append(f"{tool} mentions {o}")
                    break
        # 4. kanban: list is own; a task of another profile is refused
        kl = call(pr, "kanban_list", include="all", limit=500)
        if not kl["ok"] or any(t.get("assignee") != p for t in kl["result"]["tasks"]):
            bad.append("kanban_list shows foreign tasks")
        foreign = next((t for t, a in owner_of.items() if a != p), None)
        if foreign:
            for tool in ("kanban_show", "kanban_log"):
                r = call(pr, tool, task_id=foreign)
                if r["ok"] or r["error"]["code"] != "forbidden":
                    bad.append(f"{tool} of foreign task {foreign} not refused")
            r = call(pr, "logs_tail", log=f"worker:{foreign}")
            if r["ok"] or r["error"]["code"] != "forbidden":
                bad.append("worker log of foreign task not refused")
        # 5. meta tools
        listed = {t["name"] for t in call(pr, "hermes_search_tools", query="", limit=100)["result"]["tools"]}
        if listed & set(global_tools):
            bad.append(f"search lists {sorted(listed & set(global_tools))}")
        r = call(pr, "hermes_call", name="config_get", args={"key": "model.default", "profile": q})
        if r["ok"]:
            bad.append("hermes_call bypass")
        rep.add(f"bound key isolation [{p}]", FAIL if bad else PASS,
                "; ".join(bad[:4]) if bad else f"{len(profiles) - 1} foreign profiles, {len(global_tools)} global tools, matrices, kanban, meta tools")


def check_redaction_and_paths(rep: Report, rig: Rig) -> None:
    r = rig.call("config_get", key="kanban")
    rep.add("config_get echoes `key`", PASS if r["key"] == "kanban" else FAIL, f"key={r['key']!r}")
    r = rig.call("config_get", key="providers")
    rep.add("config_get still masks secrets", PASS if "sk-" not in json.dumps(r) else FAIL, "no sk- value in output")
    for tool, args in (("logs_tail", {"log": "../config.yaml"}), ("profile_files_read", {"file": "../config.yaml"}),
                       ("skills_view", {"name": "hermes-agent", "file": "../../../../etc/passwd"})):
        try:
            rig.call(tool, **args)
            rep.add(f"{tool} refuses traversal", FAIL, "call succeeded")
        except RuntimeError as exc:
            rep.add(f"{tool} refuses traversal", PASS, str(exc)[:70])


# -- change visible on the next call (throw-away HERMES_HOME, child process) ---------------------------------
def _mutation_child(home: str) -> list[dict]:
    """Runs with HERMES_HOME=<tmp>: edits files between calls, in the same long-lived process."""
    import yaml
    rows: list[dict] = []
    rig = Rig()
    h = Path(home)
    (h / "profiles" / "p1").mkdir(parents=True)
    (h / "profiles" / "p2").mkdir(parents=True)
    for n, m in (("config.yaml", "root-model"), ("profiles/p1/config.yaml", "p1-model-AAAA"), ("profiles/p2/config.yaml", "p2-model")):
        (h / n).write_text(yaml.safe_dump({"model": {"default": m}}))

    def val(p):
        return rig.call("config_get", key="model.default", profile=p)["value"]
    rows.append({"check": "mutation: alternating profiles", "ok": [val("p1"), val("p2"), val("default"), val("p1")] ==
                 ["p1-model-AAAA", "p2-model", "root-model", "p1-model-AAAA"], "detail": "p1,p2,default,p1"})
    cfg = h / "profiles" / "p1" / "config.yaml"
    st = cfg.stat()
    cfg.write_text(yaml.safe_dump({"model": {"default": "p1-model-BBBB"}}))       # same length
    os.utime(cfg, ns=(st.st_atime_ns, st.st_mtime_ns))                           # same mtime
    rows.append({"check": "mutation: config edit (same size+mtime) visible at once", "ok": val("p1") == "p1-model-BBBB",
                 "detail": val("p1")})
    sk = h / "profiles" / "p1" / "skills" / "cat" / "one"
    sk.mkdir(parents=True)
    (sk / "SKILL.md").write_text("---\nname: one\ndescription: first\n---\nbody\n")
    first = rig.call("skills_list", profile="p1", detail=True)["skills"][0]["description"]
    (sk / "SKILL.md").write_text("---\nname: one\ndescription: second\n---\nbody\n")
    second = rig.call("skills_list", profile="p1", detail=True)["skills"][0]["description"]
    rows.append({"check": "mutation: skill edited in place visible at once", "ok": (first, second) == ("first", "second"),
                 "detail": f"{first}->{second}"})
    # control: Hermes' own catalog cache WITHOUT hermcp's scope (shows why the scope clears it)
    from .adapters.common import ensure_hermes_importable
    ensure_hermes_importable()
    import hermes_constants  # type: ignore[import-not-found]
    from tools import skills_tool as st_mod  # type: ignore[import-not-found]
    tok = hermes_constants.set_hermes_home_override(h / "profiles" / "p1")
    try:
        st_mod._SKILLS_CACHE.clear()
        seen_a = st_mod.find_all_skills()[0]["description"]
        (sk / "SKILL.md").write_text("---\nname: one\ndescription: third\n---\nbody\n")
        seen_b = st_mod.find_all_skills()[0]["description"]
    finally:
        hermes_constants.reset_hermes_home_override(tok)
    rows.append({"check": "control: Hermes' own skills cache after an in-place edit", "ok": None,
                 "detail": (f"STALE for up to 30 s ({seen_a!r} after edit to 'third'): hermcp bypasses this"
                            if seen_a == seen_b else "not stale in this run")})
    return rows


def check_dashboard(rep: Report, rig: Rig) -> None:
    """dashboard_status is read-only; dashboard_restart is only ever called as dry_run / without confirm here."""
    from .registry import Principal
    from .scopes import Scopes
    st = rig.call("dashboard_status")
    rep.add("dashboard_status stages", PASS if [x["stage"] for x in st["stages"]] ==
            ["config", "hermes", "known_unit", "custom_unit", "process"] else FAIL,
            f"mode={st['mode']} stage={st.get('stage')} pid={st.get('pid')} port={st.get('port')}")
    if st["mode"] == "systemd":
        u = st["unit"]
        cp = subprocess.run(["systemctl", *(["--user"] if u["scope"] == "user" else []), "show", u["unit"], "-p", "MainPID", "--value"],
                            capture_output=True, text=True, timeout=15)
        rep.eq("dashboard_status pid vs systemctl MainPID", str(st["pid"]), cp.stdout.strip())
    adm = Principal("selftest-admin", "selftest-admin", Scopes(access="admin"))
    before = st.get("pid")
    plan = rig.reg.invoke("dashboard_restart", {"dry_run": True, "confirm": True}, adm, rig.settings, None)
    ok = plan["ok"] and plan["result"].get("applied") is False and plan["result"].get("dry_run") is True
    rep.add("dashboard_restart dry_run is a plan", PASS if ok else FAIL, (plan["result"].get("summary") if plan["ok"] else str(plan["error"]))[:100])
    nc = rig.reg.invoke("dashboard_restart", {}, adm, rig.settings, None)
    rep.add("dashboard_restart without confirm refuses", PASS if nc["ok"] and nc["result"].get("applied") is False
            and nc["result"].get("needed") == "confirm=true" else FAIL, "applied=false, needed confirm=true")
    after = rig.call("dashboard_status").get("pid")
    rep.eq("dashboard pid unchanged by dry runs", after, before)


def check_mutation(rep: Report) -> None:
    with tempfile.TemporaryDirectory(prefix="hermcp-selftest-", dir=os.environ.get("TMPDIR")) as tmp:
        home = Path(tmp) / "home"
        home.mkdir()
        code = ("import json,sys; sys.path.insert(0, sys.argv[2]); from hermcp.selftest import _mutation_child; "
                "print('@@'+json.dumps(_mutation_child(sys.argv[1])))")
        pkg_parent = str(Path(__file__).resolve().parent.parent)
        env = {**os.environ, "HERMES_HOME": str(home), "HERMCP_HERMES_AGENT_DIR": os.environ.get("HERMCP_HERMES_AGENT_DIR", "/usr/local/lib/hermes-agent")}
        env.pop("HERMES_PLATFORM", None)
        cp = subprocess.run([sys.executable, "-c", code, str(home), pkg_parent], capture_output=True, text=True, timeout=180,
                            env=env, cwd=str(home))
        line = next((ln for ln in cp.stdout.splitlines() if ln.startswith("@@")), None)
        if not line:
            rep.add("mutation child", FAIL, (cp.stderr or cp.stdout)[-300:])
            return
        for r in json.loads(line[2:]):
            rep.add(r["check"], INFO if r["ok"] is None else (PASS if r["ok"] else FAIL), str(r["detail"]))


def run(profiles: list[str] | None = None, as_json: bool = False, skip_slow: bool = False) -> int:
    from .adapters.common import list_profile_names
    rep = Report()
    rig = Rig()
    names = profiles or list_profile_names()
    t0 = time.time()
    check_profiles(rep, rig, names)
    _guard(rep, "global tools", lambda: check_global_tools(rep, rig) if not skip_slow else None)
    _guard(rep, "meta contract", lambda: check_meta_contract(rep, rig))
    _guard(rep, "isolation", lambda: check_isolation_under_load(rep, rig, names))
    _guard(rep, "list matrices", lambda: check_list_matrices(rep, rig, names, skip_slow))
    _guard(rep, "bound keys", lambda: check_bound_isolation(rep, rig, names))
    _guard(rep, "redaction/paths", lambda: check_redaction_and_paths(rep, rig))
    _guard(rep, "dashboard", lambda: check_dashboard(rep, rig) if not skip_slow else None)
    _guard(rep, "mutation", lambda: check_mutation(rep))
    rep.add("selftest duration", INFO, f"{time.time() - t0:.1f}s, {len(names)} profiles")
    print(json.dumps(rep.rows, indent=1) if as_json else rep.table())
    return 1 if rep.failed else 0
