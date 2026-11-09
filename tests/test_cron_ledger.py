"""Cron ledger: run list from executions.db, output documents by run id, scope and traversal rules."""
import json
import sqlite3

import pytest
import yaml

from hermcp.registry import Principal
from hermcp.scopes import Scopes
from hermcp.settings import load_settings

JOB_A = "agentjob1"
JOB_B = "scriptjob2"
OUT = "# Cron Job: {name}\n\n**Job ID:** {jid}\n**Run Time:** 2026-11-09 10:00:03\n\n## Prompt\n\nsummarise the news\n\n## Response\n\nAll quiet on the western front.\n"


def _exec_db(home, rows):
    p = home / "cron" / "executions.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(p)
    db.execute("CREATE TABLE executions (id TEXT PRIMARY KEY, job_id TEXT, source TEXT, process_id TEXT, pid INT,"
               " process_started_at INT, status TEXT, claimed_at TEXT, started_at TEXT, finished_at TEXT, error TEXT,"
               " handoff_pending INT, handoff_started_at REAL, delivery_outcome TEXT, scheduled_instant TEXT,"
               " progress_at TEXT)")
    db.executemany("INSERT INTO executions (id, job_id, source, process_id, pid, status, claimed_at, started_at,"
                   " finished_at, error, delivery_outcome) VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    db.commit()
    db.close()


def _state_db(home, session_id, job_id, run_id):
    p = home / "state.db"
    db = sqlite3.connect(p)
    db.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT NOT NULL, started_at REAL NOT NULL)")
    db.execute("CREATE TABLE session_admissions (seq INTEGER PRIMARY KEY AUTOINCREMENT, admission_id TEXT,"
               " request_id TEXT NOT NULL, principal_id TEXT, target_session_id TEXT NOT NULL, lineage_json TEXT,"
               " payload_json TEXT, payload_digest TEXT, intent TEXT, status TEXT)")
    db.execute("INSERT INTO sessions (id, source, started_at) VALUES (?, 'cron', 1.0)", (session_id,))
    db.execute("INSERT INTO session_admissions (admission_id, request_id, principal_id, target_session_id,"
               " lineage_json, payload_json, payload_digest, intent, status) VALUES ('a',?, 'p', ?, '{}','{}','d','queue','terminal')",
               (f"cron:{job_id}:{run_id}", session_id))
    db.commit()
    db.close()


def _jobs(home):
    (home / "cron").mkdir(parents=True, exist_ok=True)
    (home / "cron" / "jobs.json").write_text(json.dumps({"jobs": [
        {"id": JOB_A, "name": "Agent job", "no_agent": False},
        {"id": JOB_B, "name": "Script job", "no_agent": True}]}))


def _doc(home, job_id, stamp, text):
    d = home / "cron" / "output" / job_id
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{stamp}.md").write_text(text)


@pytest.fixture()
def led(home):
    from hermcp.tools import load_all
    cfg = yaml.safe_load((home / "config.yaml").read_text()) or {}
    cfg["timezone"] = "Europe/Berlin"
    (home / "config.yaml").write_text(yaml.safe_dump(cfg))
    reg = load_all()
    for n in ("p1", "p2"):
        (home / "profiles" / n).mkdir(parents=True, exist_ok=True)
        (home / "profiles" / n / "config.yaml").write_text(yaml.safe_dump({"model": {"default": "m"}}))
    st = load_settings({"HERMES_HOME": str(home)})
    P = {
        "rd": Principal("rd", "rd", Scopes(access="read")),
        "w1": Principal("w1", "w1", Scopes(access="read", profiles=["p1"])),
    }

    def call(who, tool, **args):
        return reg.invoke(tool, args, P[who], st, None)

    def ok(who, tool, **args):
        r = call(who, tool, **args)
        assert r["ok"], r
        return r["result"]

    call.ok, call.call, call.home, call.reg = ok, call, home, reg
    return call


def _standard(home):
    _jobs(home)
    _exec_db(home, [
        ("e1", JOB_A, "builtin", "p", 1, "completed", "2026-11-09T10:00:00.500+01:00",
         "2026-11-09T10:00:00.600+01:00", "2026-11-09T10:00:05+01:00", "", "sent"),
        ("e2", JOB_B, "builtin", "p", 1, "failed", "2026-11-09T10:05:00+01:00",
         "2026-11-09T10:05:00+01:00", "2026-11-09T10:05:02+01:00", "HTTP 429 from upstream", "failed"),
    ])
    _state_db(home, "local-sess-1", JOB_A, "e1")
    _doc(home, JOB_A, "2026-11-09_10-00-03", OUT.format(name="Agent job", jid=JOB_A))


def test_list_rows_have_the_shared_ledger_shape_and_session(led):
    _standard(led.home)
    r = led.ok("rd", "cron_runs")
    assert r["platform"] == "cron" and r["count"] == 2
    rows = {x["run_id"]: x for x in r["runs"]}
    a, b = rows["e1"], rows["e2"]
    assert a["run_id"] == "e1" and a["job_name"] == "Agent job" and a["platform"] == "cron"
    assert a["status"] == "completed" and a["session_id"] == "local-sess-1"
    assert a["has_output"] is True and a["output_size"] > 0 and abs(a["duration_s"] - 4.4) < 0.001
    assert a["user_id"] is None and a["chat_context"] is None and a["client_session"] is None
    assert b["session_id"] is None and b["has_output"] is False and b["error"] == "HTTP 429 from upstream"


def test_no_agent_filter_and_status_alias(led):
    _standard(led.home)
    assert [r["run_id"] for r in led.ok("rd", "cron_runs", has_session=False)["runs"]] == ["e2"]
    assert [r["run_id"] for r in led.ok("rd", "cron_runs", has_session=True)["runs"]] == ["e1"]
    assert [r["run_id"] for r in led.ok("rd", "cron_runs", status="ok")["runs"]] == ["e1"]
    assert [r["run_id"] for r in led.ok("rd", "cron_runs", status="error")["runs"]] == ["e2"]
    assert led.call("rd", "cron_runs", status="nonsense")["ok"] is False
    assert led.ok("rd", "cron_runs", since="2026-11-09T10:04:00+01:00")["count"] == 1


def test_output_full_text_and_prompt_stripping(led):
    _standard(led.home)
    r = led.ok("rd", "cron_run_output", run_id="e1")
    assert r["match"] == "heuristic" and r["ambiguous"] is False and r["output_missing"] is False
    assert "summarise the news" in r["output"]["content"] and "All quiet" in r["output"]["content"]
    assert r["untrusted"] is True
    r2 = led.ok("rd", "cron_run_output", run_id="e1", include_prompt=False)
    assert "summarise the news" not in r2["output"]["content"]
    assert "All quiet" in r2["output"]["content"]


def test_missing_output_is_reported_not_raised(led):
    _standard(led.home)
    r = led.ok("rd", "cron_run_output", run_id="e2")
    assert r["output_missing"] is True and r["has_output"] is False and "retention" in r["message"]
    assert led.call("rd", "cron_run_output", run_id="doesnotexist")["error"]["code"] == "not_found"


def test_ambiguous_window(led):
    _standard(led.home)
    _doc(led.home, JOB_A, "2026-11-09_10-00-04", OUT.format(name="Agent job", jid=JOB_A) + "second\n")
    r = led.ok("rd", "cron_run_output", run_id="e1")
    assert r["ambiguous"] is True and r["match"] == "heuristic"
    assert r["output"]["file"] == "2026-11-09_10-00-03.md"


def test_truncated_and_offset(led):
    _standard(led.home)
    _doc(led.home, JOB_B, "2026-11-09_10-05-01", "".join(f"{i:04d}" for i in range(250)))
    a = led.ok("rd", "cron_run_output", run_id="e2", max_chars=200)
    assert a["output"]["truncated"] is True and a["output"]["chars"] == 200
    nxt = a["output"]["next_offset"]
    b = led.ok("rd", "cron_run_output", run_id="e2", max_chars=200, offset=nxt)
    assert b["output"]["content"] != "" and b["output"]["offset"] == nxt
    assert b["output"]["content"] != a["output"]["content"]


def test_traversal_paths_are_rejected(led):
    _standard(led.home)
    assert led.call("rd", "cron_runs", job_id="../../etc")["error"]["code"] == "invalid_arguments"
    assert led.call("rd", "cron_run_output", run_id="../../etc/passwd")["error"]["code"] == "invalid_arguments"


def test_scope_filter(led):
    _standard(led.home)
    led.home.joinpath("profiles", "p1", "cron").mkdir(parents=True, exist_ok=True)
    d = led.home / "profiles" / "p1" / "cron"
    (d / "jobs.json").write_text(json.dumps({"jobs": []}))
    db = sqlite3.connect(d / "executions.db")
    db.execute("CREATE TABLE executions (id TEXT, job_id TEXT, status TEXT, claimed_at TEXT, started_at TEXT,"
               " finished_at TEXT, error TEXT, delivery_outcome TEXT)")
    db.execute("INSERT INTO executions VALUES ('ownseed','ownjob','completed','2026-11-09T10:00:00+01:00',"
               "'2026-11-09T10:00:00+01:00','2026-11-09T10:00:01+01:00','','sent')")
    db.commit()
    db.close()
    assert [r["run_id"] for r in led.ok("w1", "cron_runs")["runs"]] == ["ownseed"]
    assert led.call("w1", "cron_runs", profile="p2")["ok"] is False


def test_secret_in_output_is_redacted(led):
    _standard(led.home)
    secret = "sk-live-ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"
    _doc(led.home, JOB_B, "2026-11-09_10-05-01", f"token={secret}\n")
    r = led.ok("rd", "cron_run_output", run_id="e2")
    assert secret not in json.dumps(r)
