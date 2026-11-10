"""The `default` board always exists in Hermes: its DB is <home>/kanban.db, its board.json is optional metadata."""
import json
import sqlite3

from hermcp.adapters.overview import kanban_boards


def _db(path, statuses):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY, status TEXT)")
    con.executemany("INSERT INTO tasks (status) VALUES (?)", [(s,) for s in statuses])
    con.commit()
    con.close()


def _board_json(home, slug, **extra):
    d = home / "kanban" / "boards" / slug
    d.mkdir(parents=True)
    (d / "board.json").write_text(json.dumps({"slug": slug, **extra}))
    return d


def test_default_board_listed_from_kanban_db_without_boards_dir(home):
    _db(home / "kanban.db", ["done", "done", "blocked"])
    assert kanban_boards() == [{"slug": "default", "archived": False, "counts": {"done": 2, "blocked": 1},
                                "total": 3, "board_created_at": None}]


def test_default_board_listed_next_to_other_boards(home):
    _db(home / "kanban.db", ["done"])
    other = _board_json(home, "other", created_at=1)
    _db(other / "kanban.db", ["todo", "todo"])
    rows = kanban_boards()
    assert [r["slug"] for r in rows] == ["default", "other"]
    assert rows[0]["total"] == 1 and rows[1]["total"] == 2


def test_default_board_with_board_json_is_not_duplicated(home):
    _db(home / "kanban.db", ["done"])
    _board_json(home, "default", created_at=5)
    rows = kanban_boards()
    assert [r["slug"] for r in rows] == ["default"] and rows[0]["board_created_at"] == 5


def test_no_database_means_no_boards(home):
    assert kanban_boards() == []
