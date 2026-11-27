"""The session-metadata resolver and the list queries it enriches.

What is held down here:

* every list entry that names one entry carries `session_id`, `profile`, `session_title`, `started_by` --
  present even when unknown, then `null`, so the shape never changes;
* the title comes from the session database **per request**: a rename in the database is visible in the
  very next call (nothing is cached across calls);
* sessions, runs and agents of other clients/agents are listed too -- reading them is normal, and the answer
  says who started the work instead of hiding it. Free text (client, session handle, title, the session card)
  belongs to the key that owns the work: `runs_active` shows it for the caller's own runs and to admin keys,
  and carries identity and timing only for everybody else's;
* `hermes_health` stays the short counters-only answer (the documented exception);
* a managed unit (kanban / admission worker, `kind=managed`) carries the id of the session it runs, so
  it is resolved too -- and a pid is only believed when the session's turn lease and the process start
  time agree, never because the pid happens to be in the answer.

The gateway is faked with a real unix socket, the sessions with a real (if minimal) `state.db`: socket
paths are limited to ~108 bytes, so the HERMES_HOME lives in a short temp dir.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import sqlite3
import tempfile
import threading
from pathlib import Path

import pytest

from hermcp import ledger_detail as LD
from hermcp.adapters import active_work as AW
from hermcp.adapters import hsessions as HS
from hermcp.adapters import session_meta as SM
from hermcp.adapters.inbox_store import get_inbox
from hermcp.app import create_app
from hermcp.registry import Principal
from hermcp.scopes import Scopes
from hermcp.settings import load_settings

KEY_DEFAULT = "agent:main:telegram:dm:123456789"
KEY_RESEARCHER = "agent:researcher:email:dm:someone@example.org"


# ---------------------------------------------------------------------------
# fixtures: a short HERMES_HOME, its session databases and a fake gateway
# ---------------------------------------------------------------------------

def make_db(home: Path, profile: str, rows) -> Path:
    """A minimal `state.db` holding the columns the resolver reads (id, title, source, session_key)."""
    base = home if profile == "default" else home / "profiles" / profile
    base.mkdir(parents=True, exist_ok=True)
    path = base / "state.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, title TEXT, source TEXT, "
               "session_key TEXT, started_at REAL, ended_at REAL)")
    db.executemany("INSERT OR REPLACE INTO sessions VALUES (?,?,?,?,?,?)", rows)
    db.commit()
    db.close()
    return path


def set_title(home: Path, profile: str, session_id: str, title: str) -> None:
    path = (home if profile == "default" else home / "profiles" / profile) / "state.db"
    db = sqlite3.connect(path)
    db.execute("UPDATE sessions SET title=? WHERE id=?", (title, session_id))
    db.commit()
    db.close()


def make_lease(home: Path, profile: str, session_id: str, pid: int, acquired_at: float,
               pidns: str | None = None) -> None:
    """A `session_turn_leases` row: the pid running that session's turn, and when it took the lease."""
    path = (home if profile == "default" else home / "profiles" / profile) / "state.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE IF NOT EXISTS session_turn_leases (conversation_id TEXT PRIMARY KEY, "
               "holder TEXT NOT NULL, acquired_at REAL NOT NULL, expires_at REAL NOT NULL)")
    holder = f"pid={pid}:pidns={pidns or own_pidns()}:turn={session_id}:token=abc:platform=kanban"
    db.execute("INSERT OR REPLACE INTO session_turn_leases VALUES (?,?,?,?)",
               (session_id, holder, acquired_at, acquired_at + 600))
    db.commit()
    db.close()


def own_pidns() -> str:
    """Our own pid namespace inode as digits -- the lease of a process we can see records exactly this."""
    raw = SM.self_pidns() or "pid:[0]"
    return raw.split("[")[1].rstrip("]")


class FakeGateway:
    """One request per connection, JSON line in / JSON line out -- like gateway/control_socket.py."""

    def __init__(self, home: Path, answer: dict):
        self.path = home / "gateway.sock"
        self.answer = answer
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(str(self.path))
        os.chmod(self.path, 0o600)
        self.srv.listen(4)
        self.srv.settimeout(0.2)
        self._stop = False
        self.t = threading.Thread(target=self._loop, daemon=True)
        self.t.start()

    def _loop(self):
        while not self._stop:
            try:
                conn, _ = self.srv.accept()
            except (socket.timeout, OSError):
                continue
            with conn:
                conn.settimeout(2)
                data = b""
                while b"\n" not in data:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                req = json.loads(data.decode())
                conn.sendall(json.dumps({"ok": True, "protocol": 1, "id": req.get("id"),
                                         "result": self.answer}).encode() + b"\n")

    def close(self):
        self._stop = True
        self.t.join(2)
        self.srv.close()


@pytest.fixture()
def world(monkeypatch):
    """Short HERMES_HOME with sessions for two profiles: default (telegram) and researcher (email)."""
    home = Path(tempfile.mkdtemp(prefix="hs"))
    monkeypatch.setenv("HERMES_HOME", str(home))
    make_db(home, "default", [("local-abc", "Deploy prüfen", "telegram", KEY_DEFAULT, 10.0, None)])
    make_db(home, "researcher", [("local-res", "Recherche zu Ledger", "email", KEY_RESEARCHER, 20.0, None)])
    yield home
    shutil.rmtree(home, ignore_errors=True)


def agents(*entries) -> dict:
    return {"active_agents": len(entries), "pid": 4242, "active_work": list(entries)}


@pytest.fixture()
def app(world):
    return create_app(load_settings({"HERMES_HOME": str(world), "HERMCP_ALLOWED_HOSTS": "testserver,127.0.0.1",
                                     "HERMCP_DEBUG_CAPTURE": ""}))


def call(happ, tool, args=None, scopes=None):
    p = Principal("k1", "test key", scopes or Scopes(access="read"))
    return happ.registry.invoke(tool, args or {}, p, happ.settings, None)


# ---------------------------------------------------------------------------
# the resolver itself
# ---------------------------------------------------------------------------

def test_resolver_reads_the_title_live(world):
    assert SM.Resolver().session("default", "local-abc") == {
        "session_id": "local-abc", "profile": "default", "session_title": "Deploy prüfen",
        "started_by": "telegram"}

    set_title(world, "default", "local-abc", "Deploy erledigt")
    # a new resolver is the next request: it must see the new title, not a snapshot
    assert SM.Resolver().session("default", "local-abc")["session_title"] == "Deploy erledigt"


def test_one_request_may_remember_what_it_read(world):
    r = SM.Resolver()
    assert r.session("default", "local-abc")["session_title"] == "Deploy prüfen"
    set_title(world, "default", "local-abc", "geändert")
    # documented: the cache lives in one request scope only
    assert r.session("default", "local-abc")["session_title"] == "Deploy prüfen"
    assert SM.Resolver().session("default", "local-abc")["session_title"] == "geändert"


def test_missing_data_is_null_and_the_shape_stays_stable(world):
    cases = (SM.Resolver().session("default", "does-not-exist"),
             SM.Resolver().session("no-such-profile", "x"),
             SM.Resolver().session(None, None),
             SM.Resolver().run("default", "run_unknown"),
             SM.blank())
    for fields in cases:
        assert list(fields) == list(SM.FIELDS)          # stable shape: all four, in order
        assert fields["session_title"] is None and fields["started_by"] is None
    # what the caller named is echoed, what nobody knows stays null
    assert cases[0]["session_id"] == "does-not-exist" and cases[0]["profile"] == "default"
    assert cases[1]["session_id"] == "x"
    assert cases[2]["session_id"] is None and cases[3]["session_id"] is None


def test_a_session_is_found_by_its_gateway_session_key(world):
    fields = SM.Resolver().session(None, session_key=KEY_RESEARCHER)
    assert fields["session_id"] == "local-res" and fields["profile"] == "researcher"
    assert fields["session_title"] == "Recherche zu Ledger" and fields["started_by"] == "email"


def test_enrich_keeps_the_tools_own_vocabulary(world):
    row = {"kind": "chat", "session_ref": "abcd1234", "source": "telegram"}
    SM.Resolver().enrich(row, profile="default", session_key=KEY_DEFAULT)
    assert row["session_ref"] == "abcd1234" and row["source"] == "telegram"
    assert row["session_title"] == "Deploy prüfen" and row["started_by"] == "telegram"


# ---------------------------------------------------------------------------
# agents_live
# ---------------------------------------------------------------------------

def test_agents_live_rows_carry_the_four_fields(world):
    gw = FakeGateway(world, agents(
        {"kind": "chat", "session": KEY_DEFAULT, "pid": 1, "elapsed_s": 3.0, "idle_s": 1.0},
        {"kind": "chat", "session": KEY_RESEARCHER, "pid": 2, "elapsed_s": 4.0, "idle_s": 2.0},
        {"kind": "cron", "job_id": "33a05d408777", "pid": 3, "elapsed_s": 5.0, "idle_s": 0.5},
        {"kind": "api", "pid": 4},
    ))
    try:
        snap = AW.snapshot(settings=load_settings({"HERMES_HOME": str(world)}))
    finally:
        gw.close()
    rows = {(r.get("profile"), r.get("kind")): r for r in snap["agents"]}
    chat = rows[("default", "chat")]
    assert chat["session_id"] == "local-abc" and chat["session_title"] == "Deploy prüfen"
    assert chat["started_by"] == "telegram"
    # another agent's session (not Claude's) is listed with the same context
    foreign = rows[("researcher", "chat")]
    assert foreign["session_id"] == "local-res" and foreign["session_title"] == "Recherche zu Ledger"
    assert foreign["started_by"] == "email"
    # unknown session data is null, never missing
    for row in snap["agents"]:
        for name in SM.FIELDS:
            assert name in row
    cron = rows[(None, "cron")]
    assert cron["session_id"] is None and cron["session_title"] is None


def test_title_change_in_the_database_lands_in_the_next_agents_live_call(app, world):
    gw = FakeGateway(world, agents({"kind": "chat", "session": KEY_DEFAULT, "pid": 1, "elapsed_s": 3.0,
                                    "idle_s": 1.0}))
    try:
        first = call(app, "agents_live")
        assert first["ok"], first
        assert first["result"]["agents"][0]["session_title"] == "Deploy prüfen"

        set_title(world, "default", "local-abc", "anderer Titel")

        second = call(app, "agents_live")
        assert second["ok"], second
        assert second["result"]["agents"][0]["session_title"] == "anderer Titel"
    finally:
        gw.close()


# ---------------------------------------------------------------------------
# runs
# ---------------------------------------------------------------------------

def test_runs_active_lists_foreign_runs_with_identity_and_timing_only(app, world):
    box = get_inbox(app.settings)
    box.session_register(key_id="k1", key_name="test key", client="Cowork", kind="code", purpose="Ledger bauen",
                         role=None, native_id=None, handle_factory=lambda: "cw-1")
    box.session_register(key_id="k-other", key_name="vibe-cli", client="Claude Code", kind="code",
                         purpose="Managed units bauen", role=None, native_id=None, handle_factory=lambda: "vibe-1")
    box.add_watch(run_id="run_mine", profile="default", session_id="local-abc", key_id="k1", key_name="test key",
                  client="claude.ai", client_session="cw-1")
    # a run of another key/agent: listed, but its free text is not the caller's to read
    box.add_watch(run_id="run_foreign", profile="researcher", session_id="local-res", key_id="k-other",
                  key_name="vibe-cli", client="claude-code", client_session="vibe-1")

    r = call(app, "runs_active")
    assert r["ok"], r
    rows = {row["run_id"]: row for row in r["result"]["runs"]}
    assert set(rows) == {"run_mine", "run_foreign"}          # nothing filtered to the caller's own key
    mine = rows["run_mine"]
    assert mine["session_id"] == "local-abc" and mine["profile"] == "default"
    assert mine["session_title"] == "Deploy prüfen" and mine["started_by"] == "test key"
    assert mine["client"] == "claude.ai" and mine["client_session"] == "cw-1"
    assert mine["session"]["label"] == "Cowork · code · Ledger bauen" and "redacted" not in mine
    foreign = rows["run_foreign"]
    assert foreign["session_id"] == "local-res" and foreign["profile"] == "researcher"
    assert foreign["started_by"] == "vibe-cli"                # who it is and when, nothing else
    assert foreign["redacted"] is True and r["result"]["redacted_runs"] == 1
    for name in ("client", "client_session", "session_title", "control"):
        assert foreign[name] is None
    assert foreign.get("session") is None                     # no session card (kind, purpose) of another key

    # opt-in narrowing still works
    own = call(app, "runs_active", {"own_only": True})
    assert own["ok"] and [x["run_id"] for x in own["result"]["runs"]] == ["run_mine"]


def test_runs_active_admin_reads_the_free_text_of_foreign_runs(app, world):
    box = get_inbox(app.settings)
    box.session_register(key_id="k-other", key_name="vibe-cli", client="Claude Code", kind="code",
                         purpose="Managed units bauen", role=None, native_id=None, handle_factory=lambda: "vibe-1")
    box.add_watch(run_id="run_foreign", profile="researcher", session_id="local-res", key_id="k-other",
                  key_name="vibe-cli", client="claude-code", client_session="vibe-1")

    r = call(app, "runs_active", scopes=Scopes(access="admin"))
    assert r["ok"], r
    row = r["result"]["runs"][0]
    assert row["client"] == "claude-code" and row["client_session"] == "vibe-1"
    assert row["session_title"] == "Recherche zu Ledger" and row["started_by"] == "vibe-cli"
    assert row["session"]["label"] == "Claude Code · code · Managed units bauen"
    assert "redacted" not in row and "redacted_runs" not in r["result"]


def test_runs_active_title_is_read_again_on_every_call(app, world):
    get_inbox(app.settings).add_watch(run_id="run_mine", profile="default", session_id="local-abc",
                                      key_id="k1", key_name="claude")
    assert call(app, "runs_active")["result"]["runs"][0]["session_title"] == "Deploy prüfen"
    set_title(world, "default", "local-abc", "Titel neu")
    assert call(app, "runs_active")["result"]["runs"][0]["session_title"] == "Titel neu"


# ---------------------------------------------------------------------------
# sessions and the Ledger
# ---------------------------------------------------------------------------

def test_sessions_list_rows_carry_the_standard_fields(world):
    row = {"id": "local-abc", "title": "Deploy prüfen", "source": "telegram", "model": "m",
           "message_count": 3, "profile_name": None}
    brief = HS.brief(row, "default")
    assert brief["id"] == "local-abc" and brief["source"] == "telegram"   # the tool's own fields stay
    assert brief["session_id"] == "local-abc" and brief["profile"] == "default"
    assert brief["session_title"] == "Deploy prüfen" and brief["started_by"] == "telegram"


def test_a_missing_title_stays_null_in_a_session_row(world):
    brief = HS.brief({"id": "local-x", "source": "cli", "profile_name": "default"})
    assert brief["session_title"] is None and brief["started_by"] == "cli"


def test_ledger_run_rows_carry_title_and_started_by(world):
    settings = load_settings({"HERMES_HOME": str(world)})
    meta = SM.Resolver(settings)
    meta.prime("default", ["local-abc"])
    event = {"id": 10, "ts": "2026-11-25T10:00:00+00:00", "source": "watcher", "kind": "run_failed",
             "profile": "default", "run_id": "run_x", "session_id": "local-abc", "summary": "kaputt",
             "_bucket": "attention"}
    row = LD._run_row(event, {}, meta)
    assert row["session_title"] == "Deploy prüfen" and row["started_by"] == "telegram"

    # a run without any session data keeps the four fields, empty
    bare = LD._run_row({**event, "session_id": None, "run_id": "run_o"}, {}, meta)
    assert bare["session_id"] is None and bare["session_title"] is None and bare["started_by"] is None


# ---------------------------------------------------------------------------
# the documented exception
# ---------------------------------------------------------------------------

def test_hermes_health_stays_the_short_counters_answer(app, world):
    gw = FakeGateway(world, agents({"kind": "chat", "session": KEY_DEFAULT, "pid": 1, "elapsed_s": 3.0,
                                    "idle_s": 1.0}))
    try:
        r = call(app, "hermes_health")
        assert r["ok"], r
        result = r["result"]
        blob = json.dumps(result)
        for name in ("session_title", "session_ref", "started_by"):
            assert name not in blob
        # still the counters/overview shape: profiles, gateway state, no per-agent list
        assert "agents" not in result and "count" not in result
        assert "gateway" in result and "reachable_profiles" in result
    finally:
        gw.close()


# ---------------------------------------------------------------------------
# managed units: the gateway names a session id, not a chat key
# ---------------------------------------------------------------------------
SID_KANBAN = "local-" + "a1" * 32     # local-<64 hex>, the shape the gateway uses for a managed unit
SID_OTHER = "local-" + "b2" * 32


def snapshot_of(world, *entries, allowed_profiles=None):
    """One `agents_live` answer from a fake gateway that reports exactly these units."""
    (world / "gateway.sock").unlink(missing_ok=True)      # one fake gateway at a time on the same home
    gw = FakeGateway(world, agents(*entries))
    try:
        return AW.snapshot(allowed_profiles=allowed_profiles,
                           settings=load_settings({"HERMES_HOME": str(world)}))
    finally:
        gw.close()


def test_a_chat_key_is_never_read_as_a_session_id():
    assert SM.gateway_session_id(KEY_DEFAULT) is None
    assert SM.gateway_session_id(SID_KANBAN) == SID_KANBAN
    assert SM.gateway_session_id("local-abc") is None      # too short: not a session id
    assert SM.gateway_session_id(None) is None and SM.gateway_session_id({"a": 1}) is None


def test_a_managed_unit_is_resolved_from_the_session_id_the_gateway_names(world):
    make_db(world, "media-agent", [(SID_KANBAN, "Serie verkleinern", "kanban", None, 1000.0, None)])
    row = snapshot_of(world, {"kind": "managed", "session": SID_KANBAN, "pid": 1234, "elapsed_s": 30.0})["agents"][0]
    assert row["kind"] == "managed" and row["pid"] == 1234
    assert row["session_id"] == SID_KANBAN
    assert row["profile"] == "media-agent"                 # read in that profile's database, not guessed
    assert row["session_title"] == "Serie verkleinern"
    assert row["started_by"] == "kanban"                   # the door the session came through
    assert row["resolution_source"] == SM.RESOLVED
    assert row["pid_verified"] is None                     # nothing to check it against: not claimed


def test_a_managed_entry_without_a_session_id_stays_null(world):
    row = snapshot_of(world, {"kind": "managed", "pid": 4321})["agents"][0]
    for name in SM.FIELDS:
        assert row[name] is None
    assert row["resolution_source"] == SM.UNRESOLVED and row["pid_verified"] is None


def test_an_unknown_managed_session_id_is_reported_not_invented(world):
    unknown = "local-" + "c3" * 32
    row = snapshot_of(world, {"kind": "managed", "session": unknown, "pid": 1})["agents"][0]
    assert row["session_id"] == unknown
    assert row["profile"] is None and row["session_title"] is None and row["started_by"] is None
    assert row["resolution_source"] == SM.UNKNOWN_SESSION and row["pid_verified"] is None


def test_the_pid_is_verified_against_the_lease_and_the_process_start(world, monkeypatch):
    make_db(world, "default", [(SID_KANBAN, "Kanban-Arbeit", "kanban", None, 1000.0, None)])
    make_lease(world, "default", SID_KANBAN, 5000, 1010.0)
    # the process took the lease at 1010 and started at 1005: it is that process
    monkeypatch.setattr(SM, "proc_started_at", lambda pid: (True, 1005.0))
    row = snapshot_of(world, {"kind": "managed", "session": SID_KANBAN, "pid": 5000})["agents"][0]
    assert row["pid_verified"] is True and row["session_title"] == "Kanban-Arbeit"


def test_a_reused_pid_is_not_believed_even_when_the_unit_names_it(world, monkeypatch):
    make_db(world, "default", [(SID_KANBAN, "Kanban-Arbeit", "kanban", None, 1000.0, None)])
    make_lease(world, "default", SID_KANBAN, 5000, 1010.0)
    # pid 5000 exists, but the process behind it started at 9000 -- long after the lease: reused
    monkeypatch.setattr(SM, "proc_started_at", lambda pid: (True, 9000.0))
    row = snapshot_of(world, {"kind": "managed", "session": SID_KANBAN, "pid": 5000})["agents"][0]
    assert row["pid_verified"] is False
    # the session context still comes from the id the gateway named, not from the pid
    assert row["session_title"] == "Kanban-Arbeit" and row["profile"] == "default"


def test_a_pid_the_lease_gives_to_another_session_is_a_contradiction(world, monkeypatch):
    make_db(world, "default", [(SID_KANBAN, "Kanban-Arbeit", "kanban", None, 1000.0, None)])
    make_db(world, "researcher", [(SID_OTHER, "Fremde Arbeit", "kanban", None, 1000.0, None)])
    make_lease(world, "default", SID_KANBAN, 5000, 1010.0)
    make_lease(world, "researcher", SID_OTHER, 6000, 1010.0)
    monkeypatch.setattr(SM, "proc_started_at", lambda pid: (True, 1005.0))
    rows = snapshot_of(world,
                       {"kind": "managed", "session": SID_KANBAN, "pid": 6000},     # lease says 6000 runs SID_OTHER
                       {"kind": "managed", "session": SID_OTHER, "pid": 6000})["agents"]
    assert rows[0]["session_title"] == "Kanban-Arbeit" and rows[0]["profile"] == "default"
    assert rows[0]["pid_verified"] is False
    assert rows[1]["session_title"] == "Fremde Arbeit" and rows[1]["profile"] == "researcher"
    assert rows[1]["pid_verified"] is True
    # never a mixed pair: no row carries one session's title with the other's profile
    assert {(r["profile"], r["session_title"]) for r in rows} == {
        ("default", "Kanban-Arbeit"), ("researcher", "Fremde Arbeit")}


def test_a_gone_process_is_a_contradiction_not_a_silent_match(world, monkeypatch):
    make_db(world, "default", [(SID_KANBAN, "Kanban-Arbeit", "kanban", None, 1000.0, None)])
    make_lease(world, "default", SID_KANBAN, 5000, 1010.0)
    monkeypatch.setattr(SM, "proc_started_at", lambda pid: (False, None))     # that pid no longer exists
    row = snapshot_of(world, {"kind": "managed", "session": SID_KANBAN, "pid": 5000})["agents"][0]
    assert row["pid_verified"] is False and row["session_title"] == "Kanban-Arbeit"


def test_a_pid_from_a_foreign_namespace_is_uncheckable_not_wrong(world, monkeypatch):
    """A lease recorded in another pid namespace: its pid numbers mean nothing here, so nothing is claimed."""
    make_db(world, "default", [(SID_KANBAN, "Kanban-Arbeit", "kanban", None, 1000.0, None)])
    make_lease(world, "default", SID_KANBAN, 5000, 1010.0, pidns="999999")
    monkeypatch.setattr(SM, "proc_started_at", lambda pid: (False, None))
    row = snapshot_of(world, {"kind": "managed", "session": SID_KANBAN, "pid": 5000})["agents"][0]
    assert row["pid_verified"] is None and row["session_title"] == "Kanban-Arbeit"


def test_pidns_helpers_read_the_holder():
    assert SM.holder_pidns("pid=12:pidns=4026533254:turn=x") == "4026533254"
    assert SM.holder_pidns("pid=12:turn=x") is None and SM.holder_pidns(None) is None
    assert SM.foreign_pidns("pid=12:pidns=999999:turn=x") is True
    assert SM.foreign_pidns(f"pid=12:pidns={own_pidns()}:turn=x") is False


def test_a_managed_unit_becomes_attributable_to_its_profile(world):
    """Before, a profile-scoped key saw no managed unit at all: the gateway names no profile for them."""
    make_db(world, "researcher", [(SID_OTHER, "Fremde Arbeit", "kanban", None, 1000.0, None)])
    mine = snapshot_of(world, {"kind": "managed", "session": SID_OTHER, "pid": 1},
                       allowed_profiles={"researcher"})
    assert [r["profile"] for r in mine["agents"]] == ["researcher"] and mine["hidden"] == 0
    foreign = snapshot_of(world, {"kind": "managed", "session": SID_OTHER, "pid": 1},
                         allowed_profiles={"default"})
    assert foreign["agents"] == [] and foreign["hidden"] == 1


def test_the_managed_list_costs_the_profiles_not_the_units(world, monkeypatch):
    """One query per profile for sessions and one for leases -- the count must not follow the row count."""
    make_db(world, "media-agent", [(SID_KANBAN, "Serie verkleinern", "kanban", None, 1000.0, None)])
    make_db(world, "researcher", [(SID_OTHER, "Fremde Arbeit", "kanban", None, 1000.0, None)])
    opened: list[str | None] = []
    real = SM._connect

    def counting(profile):
        opened.append(profile)
        return real(profile)

    monkeypatch.setattr(SM, "_connect", counting)
    meta = SM.Resolver(load_settings({"HERMES_HOME": str(world)}))
    meta.prime_managed([SID_KANBAN, SID_OTHER, "local-" + "d4" * 32, None, KEY_DEFAULT])
    assert len(opened) <= 2 * len(meta.profiles())      # sessions + leases, per profile -- never per unit
    row = meta.managed({}, session_id=SID_KANBAN, pid=None)
    assert row["profile"] == "media-agent" and row["session_title"] == "Serie verkleinern"
    assert len(opened) <= 2 * len(meta.profiles())      # the primed lookup opens nothing on top


def test_agents_live_shows_managed_units_with_context(app, world):
    make_db(world, "default", [(SID_KANBAN, "Kanban-Arbeit", "kanban", None, 1000.0, None)])
    make_lease(world, "default", SID_KANBAN, 5000, 1010.0)
    monkey = pytest.MonkeyPatch()
    monkey.setattr(SM, "proc_started_at", lambda pid: (True, 1005.0))
    gw = FakeGateway(world, agents({"kind": "managed", "session": SID_KANBAN, "pid": 5000}))
    try:
        r = call(app, "agents_live")
        assert r["ok"], r
        row = r["result"]["agents"][0]
        assert row["profile"] == "default" and row["session_id"] == SID_KANBAN
        assert row["session_title"] == "Kanban-Arbeit" and row["started_by"] == "kanban"
        assert row["resolution_source"] == SM.RESOLVED and row["pid_verified"] is True
    finally:
        gw.close()
        monkey.undo()

