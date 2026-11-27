"""The session-title cleanup: what it finds, what it writes, and that a second run finds nothing."""
from __future__ import annotations

import contextlib
import json
import sqlite3
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


def test_apply_replaces_the_titles_backs_the_database_up_and_is_idempotent(home):
    db_path = _state_db(home, "developer", {
        "local-aaaa": "Passwort im Klartext",
        "local-bbbb": "Ledger-Session",
    })
    original = db_path.read_bytes()

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
    assert backup.read_bytes() == original                       # a copy taken before the write
    rows = dict(real_connect(db_path).execute("SELECT id, title FROM sessions"))
    assert rows["local-aaaa"] != "Passwort im Klartext"
    assert rows["local-bbbb"] == "Ledger-Session"                # untouched
    # and the second run is a no-op: the replacement matches no detector
    assert TS.scan(["developer"])[0]["items"] == []


def test_a_profile_without_a_state_db_is_simply_skipped(home):
    assert TS.scan(["developer"]) == []
