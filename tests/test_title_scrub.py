"""The session-title cleanup: what it finds, what it writes, and that a second run finds nothing."""
from __future__ import annotations

import contextlib
import json
import shutil
import sqlite3
import threading
from pathlib import Path

import pytest

from hermcp import title_scrub as TS


# ---------------------------------------------------------------------------
# detection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("title,reason", [
    ("Passwort im Klartext", "credential-word"),
    ("mein Passwort ist hunter2", "credential-word"),
    ("API-Key eingetragen", "credential-word"),
    ("Zugang: token = abc123", "credential-word"),
    ("Schlüssel hmcp_live_9f8e7d6c5b4a3210deadbeef", "secret-value"),
    ("ghp_9f8e7d6c5b4a3210deadbeef", "secret-value"),
    ("Passwort: ****", "masked-value"),
    ("wert q7Zr2LmX9pK4vN8sT3wB6yC1dF5gH0jQ2eR7uZ", "random-string"),
])
def test_a_title_with_a_secret_is_recognised(title, reason):
    assert TS.title_reason(title) == reason


@pytest.mark.parametrize("title", [
    "", None, "Kredit- und Quota-Status", "Ledger-Session", "developer session",
    "Notizen zur Steuererklärung", "keyboard layout", "2026-11-27 Planung",
])
def test_an_ordinary_title_is_left_alone(title):
    assert TS.title_reason(title) is None


# ---------------------------------------------------------------------------
# scan + apply against a throw-away profile home
# ---------------------------------------------------------------------------

def _state_db(home: Path, profile: str, titles: dict[str, str]) -> Path:
    directory = home / "profiles" / profile
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "state.db"
    db = sqlite3.connect(path)
    try:
        db.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT, source TEXT)")
        for sid, title in titles.items():
            db.execute("INSERT INTO sessions (id, title, source) VALUES (?,?,?)",
                       (sid, title, "cli" if sid.startswith("local") else "telegram"))
        db.commit()
    finally:
        db.close()
    return path


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "profiles").mkdir()
    return tmp_path


def test_scan_reports_counts_and_reasons_but_never_the_title(home):
    _state_db(home, "developer", {
        "local-aaaa": "Passwort im Klartext",
        "local-bbbb": "Ledger-Session",
        "local-cccc": "Token: hmcp_live_9f8e7d6c5b4a3210deadbeef",
    })
    plan = TS.scan(["developer"])
    entry = plan[0]
    assert entry["profile"] == "developer" and entry["scanned"] == 3
    assert [item["session_id"] for item in entry["items"]] == ["local-aaaa", "local-cccc"]
    blob = json.dumps(plan)
    assert "Passwort im Klartext" not in blob          # a maintenance log must not be the next leak
    assert "hmcp_live" not in blob
    assert all(item["replacement"] for item in entry["items"])
    # the dry-run output carries the same restraint
    assert "Passwort im Klartext" not in TS.render(plan)
    assert "2 of 3 titles would be replaced" in TS.render(plan)


def _rows(path: Path) -> list[tuple]:
    """(id, title, source) of a database, after proving the file is intact."""
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        return [tuple(row) for row in db.execute("SELECT id, title, source FROM sessions ORDER BY id")]
    finally:
        db.close()


def test_apply_replaces_the_titles_backs_the_database_up_and_is_idempotent(home):
    db_path = _state_db(home, "developer", {
        "local-aaaa": "Passwort im Klartext",
        "local-bbbb": "Ledger-Session",
    })
    before = _rows(db_path)

    real_connect = sqlite3.connect

    class FakeDB:
        def set_session_title(self, session_id, title):
            db = real_connect(db_path)
            try:
                cur = db.execute("UPDATE sessions SET title=? WHERE id=?", (title, session_id))
                db.commit()
                return cur.rowcount == 1
            finally:
                db.close()

    @contextlib.contextmanager
    def fake_open_db(profile, read_only=True):
        assert profile == "developer" and read_only is False
        yield FakeDB()

    import hermcp.adapters.hsessions as HS
    original_open_db, HS.open_db = HS.open_db, fake_open_db
    try:
        plan = TS.scan(["developer"])
        done = TS.apply_plan(plan, backup_dir=home / "backups" / "manual")
    finally:
        HS.open_db = original_open_db

    assert done[0]["replaced"] == 1 and done[0]["failed"] == []
    backup = Path(done[0]["backup"])
    assert _rows(backup) == before                               # a snapshot taken before the write
    assert backup.stat().st_mode & 0o777 == 0o600                # a session store is not world-readable
    # one self-contained file: no write-ahead log or shared-memory sidecar travels with it
    assert not backup.with_name(backup.name + "-wal").exists()
    assert not backup.with_name(backup.name + "-shm").exists()
    rows = dict(real_connect(db_path).execute("SELECT id, title FROM sessions"))
    assert rows["local-aaaa"] != "Passwort im Klartext"
    assert rows["local-bbbb"] == "Ledger-Session"                # untouched
    # and the second run is a no-op: the replacement matches no detector
    assert TS.scan(["developer"])[0]["items"] == []


def test_a_backup_taken_while_another_connection_writes_is_consistent(home):
    """The snapshot is taken under concurrent commits: intact, and its row count is one real instant."""
    db_path = _state_db(home, "developer", {"local-0001": "Ledger-Session"})
    writer = sqlite3.connect(db_path, timeout=5.0)
    writer.execute("PRAGMA journal_mode=WAL")                    # the live shape: WAL, writers alongside
    writer.commit()
    stop = threading.Event()
    progress = {"rows": 1, "error": None}

    def keep_writing():
        conn = sqlite3.connect(db_path, timeout=5.0)
        try:
            n = 1
            while not stop.is_set():
                n += 1
                conn.execute("INSERT INTO sessions (id, title, source) VALUES (?,?,?)",
                             (f"local-{n:04d}", f"row {n}", "cli"))
                conn.commit()
                progress["rows"] = n                             # committed, so the count is real
        except Exception as exc:  # noqa: BLE001 - surfaced below, a silent thread would hide a bug
            progress["error"] = exc
        finally:
            conn.close()

    thread = threading.Thread(target=keep_writing, daemon=True)
    thread.start()
    counts = []
    try:
        for _ in range(3):
            rows_before = progress["rows"]
            target = TS.backup_db("developer", home / "backups" / "concurrent")
            rows_after = progress["rows"]
            assert progress["error"] is None
            assert target is not None
            snapshot = _rows(target)                             # PRAGMA integrity_check inside
            counts.append(len(snapshot))
            # the count belongs to a point in time between the two reads, not to a torn set
            assert rows_before <= len(snapshot) <= rows_after, (rows_before, len(snapshot), rows_after)
            by_id = dict((row[0], row[1]) for row in snapshot)
            assert by_id["local-0001"] == "Ledger-Session"       # the row from before the writes
            for sid, title in by_id.items():                     # every row is whole, none torn
                assert title == "Ledger-Session" or title == f"row {int(sid.split('-')[1])}"
    finally:
        stop.set()
        thread.join(timeout=10)
        writer.close()
    # the three snapshots were taken while the writer was actually committing, not on an idle file
    assert progress["rows"] > 10, progress["rows"]
    assert counts == sorted(counts) and counts[-1] > counts[0], counts


def test_the_snapshot_restores_into_a_working_database(home):
    """What --apply leaves behind must be restorable, not merely readable."""
    db_path = _state_db(home, "developer", {
        "local-aaaa": "Passwort im Klartext",
        "local-bbbb": "Ledger-Session",
    })
    before = _rows(db_path)
    target = TS.backup_db("developer", home / "backups" / "restore")
    assert target is not None and target.is_file()

    restored = home / "restored" / "state.db"
    restored.parent.mkdir(parents=True)
    shutil.copyfile(target, restored)                            # the restore step an operator runs
    assert _rows(restored) == before
    db = sqlite3.connect(restored)
    try:
        db.execute("UPDATE sessions SET title='nach dem Restore' WHERE id='local-bbbb'")
        db.commit()
        assert dict(db.execute("SELECT id, title FROM sessions"))["local-bbbb"] == "nach dem Restore"
    finally:
        db.close()
    # the snapshot is the pre-write state: nothing was applied here, so the cleanup still finds it
    assert [item["session_id"] for item in TS.scan(["developer"])[0]["items"]] == ["local-aaaa"]


def test_a_stale_sidecar_next_to_an_older_copy_does_not_survive_the_next_backup(home):
    _state_db(home, "developer", {"local-aaaa": "Ledger-Session"})
    root = home / "backups" / "reused"
    root.mkdir(parents=True)
    target = root / "developer-state.db"
    for suffix in ("-wal", "-shm"):                              # what the old three-copy backup left
        target.with_name(target.name + suffix).write_bytes(b"stale")
    TS.backup_db("developer", root)
    assert not target.with_name(target.name + "-wal").exists()
    assert not target.with_name(target.name + "-shm").exists()
    assert _rows(target) == [("local-aaaa", "Ledger-Session", "cli")]


def test_a_snapshot_of_a_wal_database_is_one_plain_file_and_restores_beside_a_leftover_log(home):
    """The live databases run in WAL mode: the snapshot must not inherit that and then replay it."""
    db_path = _state_db(home, "developer", {"local-aaaa": "Passwort im Klartext"})
    writer = sqlite3.connect(db_path)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("INSERT INTO sessions (id, title, source) VALUES ('local-bbbb','nur im WAL','cli')")
        writer.commit()
        assert db_path.with_name(db_path.name + "-wal").exists()   # still in the log, not in the file
        before = _rows(db_path)
        target = TS.backup_db("developer", home / "backups" / "wal")
        db = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
        try:
            assert db.execute("PRAGMA journal_mode").fetchone()[0].lower() == "delete"
        finally:
            db.close()
    finally:
        writer.close()
    assert _rows(target) == before                                # the WAL content is in the snapshot

    # the restore an operator runs after --apply: the file goes back, the live -wal is still there
    restore = home / "restore"
    restore.mkdir()
    (restore / "state.db").write_bytes(target.read_bytes())
    (restore / "state.db-wal").write_bytes(b"a leftover log from the replaced database\n")
    assert _rows(restore / "state.db") == before


def test_a_profile_without_a_state_db_is_simply_skipped(home):
    assert TS.scan(["developer"]) == []
    assert TS.backup_db("developer") is None
