"""cron toolset: list/show/create/edit/pause/resume/run/remove (reads import cron.jobs; writes use the CLI)."""
from __future__ import annotations

import contextlib
import sqlite3

from .. import meta
from ..adapters import cronroute as R
from ..adapters import listing as L
from ..adapters import safe
from ..adapters.common import call_inproc, cli_text, ensure_hermes_importable, hermes_scope, profile_home, run_hermes
from ..adapters.hsessions import slim
from ..registry import AdapterError, ToolError
from ._cli import add_opts, run_write
from . import reg

JOB = {"type": "string", "description": "Cron job id"}


@contextlib.contextmanager
def _store(profile):
    ensure_hermes_importable()
    try:
        import cron.jobs as cj
    except ImportError as exc:
        raise AdapterError(f"cron.jobs unavailable: {exc}") from exc
    with hermes_scope(profile) as home, cj.use_cron_store(home):
        yield cj


def _cli_prof(ctx):
    return None if ctx.profile == "default" else ctx.profile


def _job_rows(profile, include_prompt):
    """All jobs of one profile, read live, with route info."""
    d = R.read_defaults(profile)
    with _store(profile) as cj:
        jobs = call_inproc(cj.list_jobs, include_disabled=True)
        keep = ("id", "name", "schedule_display", "enabled", "state", "next_run_at", "last_run_at",
                "last_status", "deliver", "skills", "repeat", "script", "no_agent", "prompt")
        return d, [{**slim({k: j[k] for k in keep if k in j}, 200), "route": R.route_info(j, d), "_job": j} for j in jobs]


def _is_active(r):
    return bool(r.get("enabled")) and r.get("state") not in ("paused", "completed")


def _compact(r):
    eff = (r["route"].get("effective") or {})
    out = {"id": r["id"], "name": r.get("name"), "schedule": r.get("schedule_display"), "state": r.get("state"),
           "next_run_at": r.get("next_run_at"), "last_run_at": r.get("last_run_at"), "last_status": r.get("last_status"),
           "deliver": r.get("deliver"), "model": eff.get("model"), "pinned": r["route"]["pinned"]}
    if "profile" in r:
        out["profile"] = r["profile"]
    return out


@reg.tool("cron_list", toolset="cron", profile_aware=True, core=True,
          description=("A profile's scheduled jobs. DEFAULT: compact, only ACTIVE jobs (scheduled / running) plus any job wh"
                       'ose last run failed; `_meta.list` counts the hidden paused / completed ones (changed: it used to lis'
                       't all with prompts). include=all adds them. view=summary = counters per state plus failing jobs. pro'
                       'files=all or a comma list = jobs of those profiles, each read live, with a `profile` field. detail=t'
                       'rue restores prompt (200 chars), skills, script and `route` (pinned, provider, model, credential-fre'
                       'e base_url, `effective` model and its source). `defaults` = cron.model, main model, fallback chain. '
                       'Filters: query, source (agent | script), state, limit/offset; total, returned, truncated are always '
                       'stated.'),
          properties={**L.props(category=False, matrix=True),
                      "source": {"type": "string", "enum": ["agent", "script"], "description": "agent job or no_agent script job"},
                      "state": {"type": "string", "enum": ["scheduled", "running", "paused", "completed"]},
                      "include_disabled": {"type": "boolean", "default": True, "description": "legacy; use include"},
                      "detail": {"type": "boolean", "default": False}})
def cron_list(ctx, include_disabled=True, view="list", include="active", query=None, limit=100, offset=0, source=None,
              state=None, profiles=None, detail=False):
    names = L.pick_profiles(ctx, profiles) or [ctx.profile]
    rows, defaults = [], None
    for prof in names:
        d, prows = _job_rows(prof, detail)
        defaults = defaults or d
        for r in prows:
            r["profile"] = prof
            rows.append(r)
    rows = [r for r in rows if (not source or (r["_job"].get("no_agent") and source == "script")
                                or (not r["_job"].get("no_agent") and source == "agent"))
            and (not state or r.get("state") == state) and L.matches(query, r.get("name"), r.get("id"))]
    active = [r for r in rows if _is_active(r)]
    failing = [r for r in rows if r.get("last_status") == "error"]
    relevant = [r for r in rows if _is_active(r) or r.get("last_status") == "error"]
    shown = rows if include == "all" else relevant
    L.note(tool="cron_list", include=include, view=view, total=len(rows), active=len(active), disabled=len(rows) - len(active),
           shown=len(shown), hidden_by_default=len(rows) - len(relevant), labels=("active", "inactive"),
           extra={"failing_last_run": len(failing), "profiles": names,
                  "active_means": "enabled and state not paused / completed; failing jobs are always shown by default"})
    if view == "summary":
        return {"profiles": names, "matching": len(rows), "active": len(active),
                "by_state": L.tally([{"state": r.get("state"), "active": True} for r in rows], "state"),
                "failing": [{"id": r["id"], "name": r.get("name"), "state": r.get("state"),
                             "last_run_at": r.get("last_run_at")} for r in failing],
                "defaults": defaults}
    pr, pg = L.page(shown, limit, offset)
    if detail:
        out = [{k: v for k, v in r.items() if k != "_job"} if len(names) > 1 else
               {k: v for k, v in r.items() if k not in ("_job", "profile")} for r in pr]
    else:
        out = [_compact(r) if len(names) > 1 else {k: v for k, v in _compact(r).items() if k != "profile"} for r in pr]
    return {"count": pg["total"], **pg, "pinned_count": sum(1 for r in rows if r["route"]["pinned"]),
            "defaults": defaults, "jobs": out}


@reg.tool("cron_show", toolset="cron", profile_aware=True,
          description="Full definition of one cron job (long text fields are cut at 4000 characters) plus its `route` "
                      "(pinned, provider, model, base_url without credentials, reasoning_effort, effective route and "
                      "whether it inherits the global fallback chain), like cron_list.",
          properties={"job_id": JOB}, required=["job_id"])
def cron_show(ctx, job_id):
    d = R.read_defaults(ctx.profile)
    with _store(ctx.profile) as cj:
        j = call_inproc(cj.get_job, job_id)
        if not j:
            raise ToolError("not_found", f"no cron job {job_id!r}")
        return {**slim(j, 4000), "route": R.route_info(j, d)}


@reg.tool("cron_status", toolset="cron", profile_aware=True,
          description="Whether the cron scheduler of the profile is running (`hermes cron status`).")
def cron_status(ctx):
    return {"status": cli_text(["cron", "status"], None if ctx.profile == "default" else ctx.profile, 30).strip()}


def _executions(home, job_id, limit):
    db = home / "cron" / "executions.db"
    if not db.is_file():
        return []
    meta.note_file(db)
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    try:
        con.row_factory = sqlite3.Row
        sql = ("SELECT id, job_id, source, status, claimed_at, started_at, finished_at, error, delivery_outcome, "
               "scheduled_instant FROM executions")
        args = []
        if job_id:
            sql += " WHERE job_id LIKE ?"
            args.append(job_id + "%")
        sql += " ORDER BY claimed_at DESC, id DESC LIMIT ?"
        args.append(limit)
        return [dict(r) for r in con.execute(sql, args)]
    finally:
        con.close()


def _outputs(home, job_id, n, chars):
    root = home / "cron" / "output"
    dirs = [d for d in root.iterdir() if d.is_dir() and job_id.startswith(d.name[:12]) or d.name == job_id] if root.is_dir() else []
    if not dirs:
        return []
    d = safe.resolve_inside(root, dirs[0].name, what="job output dir")
    files = sorted((f for f in d.iterdir() if f.is_file()), key=lambda f: f.name, reverse=True)[:n]
    out = []
    for f in files:
        cap = safe.read_capped(safe.resolve_inside(d, f.name, what="output"), chars)
        out.append({"file": f.name, "mtime": cap["mtime"], "size_bytes": cap["size_bytes"],
                    "truncated": cap["truncated"], "content": cap["content"]})
    return out


@reg.tool("cron_runs", toolset="cron", profile_aware=True,
          description=("Recent execution attempts of cron jobs (optionally one job), read from the scheduler's "
                       "executions database: id, job_id, status (completed / failed / running ...), claimed / started / "
                       "finished times, error, delivery outcome, scheduled instant. `runs` is the same as free text from "
                       "the CLI. With include_output=true and a job_id, the last `outputs` files of that job's saved "
                       "output (what the job answered), each cut at output_chars and redacted."),
          properties={"job_id": JOB, "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20},
                      "include_output": {"type": "boolean", "default": False},
                      "outputs": {"type": "integer", "minimum": 1, "maximum": 10, "default": 3},
                      "output_chars": {"type": "integer", "minimum": 200, "maximum": 20000, "default": 3000}})
def cron_runs(ctx, job_id=None, limit=20, include_output=False, outputs=3, output_chars=3000):
    home = profile_home(ctx.profile)
    if job_id:
        safe.safe_name(job_id, "job_id")
    res = {"executions": _executions(home, job_id, limit)}
    try:
        argv = ["cron", "runs"] + ([job_id] if job_id else []) + ["--limit", str(limit)]
        res["runs"] = cli_text(argv, None if ctx.profile == "default" else ctx.profile, 30).strip()[-12000:]
    except ToolError as exc:
        res["runs"] = None
        res["runs_error"] = exc.message[:200]
    if include_output:
        if not job_id:
            raise ToolError("invalid_arguments", "include_output needs a job_id")
        res["outputs"] = _outputs(home, job_id, outputs, output_chars)
    return res


_CREATE_PROPS = {
    "schedule": {"type": "string", "description": "'30m', 'every 2h' or a cron expression like '0 9 * * *'"},
    "prompt": {"type": "string"}, "name": {"type": "string"},
    "deliver": {"type": "string", "description": "origin, local, telegram, platform:chat_id, bot-chat[:profile]"},
    "failure_deliver": {"type": "string"}, "repeat": {"type": "integer", "minimum": 1},
    "skills": {"type": "array", "items": {"type": "string"}},
    "script": {"type": "string", "description": "script under ~/.hermes/scripts/"},
    "no_agent": {"type": "boolean"}, "workdir": {"type": "string"},
}


def _restricted_guard(ctx, opts):
    """Profile-restricted keys: no host scripts / working directories and no delivery into another profile's chat."""
    if not ctx.restricted:
        return
    for field in ("script", "workdir"):
        if opts.get(field):
            raise ToolError("forbidden", f"profile-restricted keys cannot set cron `{field}` (it reaches host files shared by every "
                                         "profile). Use an agent prompt. Nothing was changed.")
    if opts.get("no_agent"):
        raise ToolError("forbidden", "profile-restricted keys cannot create no_agent script jobs. Nothing was changed.")
    for field in ("deliver", "failure_deliver"):
        v = opts.get(field) or ""
        if v.startswith("bot-chat"):
            target = v.split(":", 1)[1] if ":" in v else ctx.profile
            if not ctx.own(target):
                raise ToolError("forbidden", f"{field}={v!r} would deliver into another profile's chat; this key is limited to "
                                             f"{', '.join(ctx.principal.scopes.profiles)}. Nothing was changed.")


@reg.tool("cron_create", toolset="cron", access="write", profile_aware=True, core=True,
          description=("Create a scheduled job. `schedule` is '30m', 'every 2h' or a cron expression. The job runs an "
                       "agent prompt (or a script with no_agent) and delivers its output to `deliver` (for example "
                       "telegram), so it can send messages later. Use dry_run to preview."),
          properties={**_CREATE_PROPS, "paused": {"type": "boolean", "default": False}}, required=["schedule"])
def cron_create(ctx, schedule, prompt=None, paused=False, **opts):
    _restricted_guard(ctx, opts)
    argv = ["cron", "create", schedule] + ([prompt] if prompt else [])
    add_opts(argv, name=opts.get("name"), deliver=opts.get("deliver"), failure_deliver=opts.get("failure_deliver"),
             repeat=opts.get("repeat"), skill=opts.get("skills"), script=opts.get("script"),
             no_agent=opts.get("no_agent"), workdir=opts.get("workdir"), paused=paused)
    return run_write(ctx, "create cron job", argv)


@reg.tool("cron_edit", toolset="cron", access="write", profile_aware=True,
          description=("Edit a cron job (schedule, prompt, name, delivery, skills, script, workdir). Pass only the "
                       "fields to change; an empty string clears script/workdir. Model, provider, base_url and "
                       "reasoning effort are changed with cron_model_set."),
          properties={"job_id": JOB, **{k: v for k, v in _CREATE_PROPS.items()},
                      "add_skills": {"type": "array", "items": {"type": "string"}},
                      "remove_skills": {"type": "array", "items": {"type": "string"}},
                      "clear_skills": {"type": "boolean"}}, required=["job_id"])
def cron_edit(ctx, job_id, **opts):
    _restricted_guard(ctx, opts)
    argv = ["cron", "edit", job_id]
    if opts.get("no_agent") is not None:
        argv.append("--no-agent" if opts["no_agent"] else "--agent")
    add_opts(argv, schedule=opts.get("schedule"), prompt=opts.get("prompt"), name=opts.get("name"),
             deliver=opts.get("deliver"), failure_deliver=opts.get("failure_deliver"), repeat=opts.get("repeat"),
             skill=opts.get("skills"), add_skill=opts.get("add_skills"), remove_skill=opts.get("remove_skills"),
             clear_skills=opts.get("clear_skills"), script=opts.get("script"), workdir=opts.get("workdir"))
    if len(argv) == 3:
        raise ToolError("invalid_arguments", "nothing to change")
    return run_write(ctx, "edit cron job", argv)


def _simple(name, verb, desc, access="write", extra=None):
    @reg.tool(name, toolset="cron", access=access, profile_aware=True, description=desc,
              properties={"job_id": JOB, **(extra or {})}, required=["job_id"])
    def _fn(ctx, job_id, **kw):
        argv = ["cron", verb, job_id]
        if verb == "resume":
            add_opts(argv, run_now=kw.get("run_now"), at=kw.get("at"))
        return run_write(ctx, f"cron {verb} {job_id}", argv)
    return _fn


_simple("cron_pause", "pause", "Pause a scheduled job (it stays defined).")
_simple("cron_resume", "resume", "Resume a paused job (optionally re-arm at an ISO time or run it now).",
        extra={"run_now": {"type": "boolean"}, "at": {"type": "string"}})
_simple("cron_run", "run", "Trigger a job on the next scheduler tick. It runs for real, including its delivery.")
_simple("cron_remove", "remove", "Delete a scheduled job permanently.", access="admin")


# -- model routing: single and bulk ------------------------------------------------------------------
ROUTE_PROPS = {
    "model": {"type": "string", "maxLength": 200, "description": "Pin the job to this model ('' clears; needs provider unless the job has one)"},
    "provider": {"type": "string", "maxLength": 100, "description": "Inference provider paired with model ('' clears)"},
    "base_url": {"type": "string", "maxLength": 400, "description": "Endpoint override, http(s) without credentials ('' clears)"},
    "reasoning_effort": {"type": "string", "enum": ["", *R.REASONING], "description": "Reasoning effort pin ('' clears, follows config)"},
}
PIN_NOTE = ("A pin (provider, model or base_url on the job) turns OFF the global fallback chain for that job: "
            "pinned jobs never fall back to fallback_providers, only credential-pool rotation of the same provider remains. ")


def _one(ctx, cj, job_id, params, main, do_write):
    """Plan (and, if do_write, apply) one job edit. Returns a result row; never raises for per-job problems."""
    job = call_inproc(cj.get_job, job_id)
    if not job:
        return {"job_id": job_id, "status": "not_found"}
    d = main
    row = {"job_id": job_id, "name": job.get("name"), "enabled": job.get("enabled"), "state": job.get("state")}
    if job.get("no_agent"):
        return {**row, "status": "skipped", "reason": "no_agent script job has no model"}
    try:
        R.validate_edit(job, **params)
    except ToolError as exc:
        return {**row, "status": "invalid", "reason": exc.message}
    new = R.predict(job, main_model=d["main_model"], main_provider=d["main_provider"], **params)
    before = R.route_info(job, d)
    after_job = {**job, **new}
    after = R.route_info(after_job, d)
    changed = any(_t(job.get(k)) != _t(new[k]) for k in new)
    row.update(before=_brief(before), after=_brief(after), changes=changed)
    if not changed:
        return {**row, "status": "unchanged"}
    if not do_write:
        return {**row, "status": "planned"}
    argv = R.cli_argv(job_id, **{k: params[k] for k in ("pin", "unpin", "model", "provider", "reasoning_effort")})
    if len(argv) > 3:
        cp = run_hermes(argv, _cli_prof(ctx), 60)
        if cp.returncode != 0:
            return {**row, "status": "error", "reason": (cp.stderr.strip() or cp.stdout.strip() or f"exit {cp.returncode}")[-300:]}
    want_url = new["base_url"] if params["base_url"] is not None or params["unpin"] else None
    if (params["base_url"] is not None or params["unpin"]) and _t(call_inproc(cj.get_job, job_id).get("base_url")) != _t(want_url):
        call_inproc(cj.update_job, job_id, {"base_url": want_url})      # the CLI has no --base-url
    fresh = call_inproc(cj.get_job, job_id) or {}
    drift = [k for k in R.PROTECTED if fresh.get(k) != job.get(k)]
    return {**row, "status": "applied", "after": _brief(R.route_info(fresh, d)),
            "other_fields_unchanged": not drift, **({"unexpected_changes": drift} if drift else {})}


def _t(v):
    return (v or "").strip() if isinstance(v, str) or v is None else v


def _brief(r):
    e = r["effective"]
    return {"pinned": r["pinned"], "provider": r["provider"], "model": r["model"], "base_url": r["base_url"],
            "reasoning_effort": r["reasoning_effort"], "runs_on": (f'{e["provider"]} / {e["model"]}' if e.get("applies") else None),
            "inherits_fallback_chain": r["inherits_fallback_chain"]}


@reg.tool("cron_model_set", toolset="cron", access="admin", profile_aware=True,
          description=PIN_NOTE + "Change the model route of ONE job: pin=true freezes the current main model onto it; "
                      "unpin=true releases provider, model and base_url so it follows cron.model / the main model and the "
                      "fallback chain again; or set model (+provider), base_url, reasoning_effort ('' clears a value). "
                      "Schedule, prompt, script, delivery and paused state are not touched. Always try dry_run first: it "
                      "returns before/after with the effective route. Writes a timestamped jobs.json backup; the result "
                      "names it. Script jobs (no_agent) have no model and are refused.",
          properties={"job_id": JOB, "pin": {"type": "boolean", "description": "Freeze the current main model + provider onto the job"},
                      "unpin": {"type": "boolean", "description": "Release the pin; the job follows the main model and the fallback chain"},
                      **ROUTE_PROPS}, required=["job_id"])
def cron_model_set(ctx, job_id, pin=False, unpin=False, model=None, provider=None, base_url=None, reasoning_effort=None):
    params = dict(pin=bool(pin), unpin=bool(unpin), model=model, provider=provider, base_url=base_url,
                  reasoning_effort=reasoning_effort)
    d = R.read_defaults(ctx.profile)
    with _store(ctx.profile) as cj:
        job = call_inproc(cj.get_job, job_id)
        if not job:
            raise ToolError("not_found", f"no cron job {job_id!r}")
        if job.get("no_agent"):
            raise ToolError("rejected", "no_agent script jobs have no model to set")
        R.validate_edit(job, **params)
        if ctx.dry_run:
            row = _one(ctx, cj, job_id, params, d, do_write=False)
            return ctx.plan("change cron job model route", **row,
                            would_run=["hermes", *R.cli_argv(job_id, **{k: params[k] for k in ("pin", "unpin", "model", "provider", "reasoning_effort")})])
        backup = R.backup_jobs(call_inproc(cj._current_cron_store).jobs_file)
        row = _one(ctx, cj, job_id, params, d, do_write=True)
    if row["status"] in ("error", "invalid", "not_found"):
        raise ToolError("cli_error", row.get("reason", row["status"]), backup=backup)
    return {**row, "backup": backup}


@reg.tool("cron_models_bulk", toolset="cron", access="admin", profile_aware=True,
          description=PIN_NOTE + "Pin, unpin or set the model route of SEVERAL jobs at once. Select jobs with job_ids, or "
                      "select='pinned' (all agent jobs that are pinned) / 'all' (all agent jobs). action: unpin (release "
                      "the route, jobs inherit cron.model / main model and the fallback chain), pin (freeze the current main "
                      "model), or set (model + provider [+ base_url, reasoning_effort] for all of them). Without confirm=true, "
                      "or with dry_run, only the preview is returned: per job the before/after route. Paused jobs stay "
                      "paused; schedules, prompts, scripts and delivery are not touched (verified per job). One jobs.json "
                      "backup is taken before the first write. Script jobs (no_agent) are skipped.",
          properties={"action": {"type": "string", "enum": ["unpin", "pin", "set"]},
                      "job_ids": {"type": "array", "items": JOB, "minItems": 1, "maxItems": 100},
                      "select": {"type": "string", "enum": ["pinned", "all"], "description": "Alternative to job_ids"},
                      "confirm": {"type": "boolean", "default": False, "description": "Must be true to actually write"},
                      **ROUTE_PROPS}, required=["action"])
def cron_models_bulk(ctx, action, job_ids=None, select=None, confirm=False, model=None, provider=None,
                     base_url=None, reasoning_effort=None):
    if bool(job_ids) == bool(select):
        raise ToolError("invalid_arguments", "give exactly one of job_ids or select")
    if action != "set" and any(x is not None for x in (model, provider, base_url)):
        raise ToolError("invalid_arguments", "model/provider/base_url belong to action=set")
    if action == "set" and not model:
        raise ToolError("invalid_arguments", "action=set needs model (and normally provider)")
    params = dict(pin=action == "pin", unpin=action == "unpin", model=model if action == "set" else None,
                  provider=provider if action == "set" else None, base_url=base_url if action == "set" else None,
                  reasoning_effort=reasoning_effort)
    d = R.read_defaults(ctx.profile)
    write = confirm and not ctx.dry_run
    with _store(ctx.profile) as cj:
        if job_ids:
            ids = list(dict.fromkeys(job_ids))
        else:
            allj = call_inproc(cj.list_jobs, include_disabled=True)
            ids = [j["id"] for j in allj if not j.get("no_agent") and (select == "all" or R.is_pinned(j))]
        if not ids:
            return {"applied": False, "note": "no matching jobs", "jobs": []}
        backup = R.backup_jobs(call_inproc(cj._current_cron_store).jobs_file) if write else None
        rows = [_one(ctx, cj, i, params, d, do_write=write) for i in ids]
    summary = {s: sum(1 for r in rows if r["status"] == s) for s in ("applied", "planned", "unchanged", "skipped", "invalid", "error", "not_found")}
    out = {"applied": write, "action": action, "summary": {k: v for k, v in summary.items() if v}, "jobs": rows}
    if write:
        out["backup"] = backup
    else:
        out.update(dry_run=True, needed="confirm=true to apply")
    return out
