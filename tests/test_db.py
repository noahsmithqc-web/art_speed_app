"""SP-1: speed_######.db - created whole, version-stamped, upgraded on open, refused when newer."""

from __future__ import annotations

import sqlite3

import pytest

from art_speed.storage import db


def test_create_builds_every_table_and_stamps_the_version(tmp_path):
    path = db.create(tmp_path, "174083")
    assert path.name == "speed_174083.db" and path.exists()
    assert not (tmp_path / "speed_174083.db.creating").exists()
    conn = db.open_db(tmp_path, "174083")
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert set(db.TABLES) <= tables
        assert db.user_version(conn) == db.DB_VERSION == 1
        meta = dict(conn.execute("SELECT key, value FROM speed_meta"))
        assert meta["project_number"] == "174083" and meta["created_app_version"]
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()


def test_create_never_replaces_a_file(tmp_path):
    db.create(tmp_path, "174083")
    with pytest.raises(db.DatabaseExists):
        db.create(tmp_path, "174083")


def test_open_never_creates(tmp_path):
    with pytest.raises(db.NoSpeedData):
        db.open_db(tmp_path, "174083")
    assert list(tmp_path.iterdir()) == []
    conn, created = db.open_or_create(tmp_path, "174083")
    conn.close()
    assert created
    conn, created = db.open_or_create(tmp_path, "174083")
    conn.close()
    assert not created


def test_project_number_must_be_six_digits(tmp_path):
    for bad in ("17408", "1740831", "abcdef", ""):
        with pytest.raises(db.SpeedDbError):
            db.file_in(tmp_path, bad)


def test_a_newer_database_is_refused(tmp_path):
    path = db.create(tmp_path, "174083")
    raw = sqlite3.connect(path)
    raw.execute(f"PRAGMA user_version = {db.DB_VERSION + 1}")
    raw.close()
    with pytest.raises(db.SpeedDbError, match="newer version"):
        db.open_db(tmp_path, "174083")


def test_not_a_speed_database_is_refused(tmp_path):
    (tmp_path / "speed_174083.db").write_text("not a database at all, just text" * 10)
    with pytest.raises(db.SpeedDbError):
        db.open_db(tmp_path, "174083")
    other = tmp_path / "x"
    other.mkdir()
    sqlite3.connect(other / "speed_174083.db").close()          # an empty SQLite file: version 0
    with pytest.raises(db.SpeedDbError, match="not a speed tool database"):
        db.open_db(other, "174083")


def test_a_missing_table_is_damage(tmp_path):
    path = db.create(tmp_path, "174083")
    raw = sqlite3.connect(path)
    raw.execute("DROP TABLE speed_log")
    raw.close()
    with pytest.raises(db.SpeedDbError, match="damaged: missing speed_log"):
        db.open_db(tmp_path, "174083")


def test_an_older_database_is_upgraded_on_open(tmp_path, monkeypatch):
    """The hook, exercised with a pretend version 2 that adds a column."""
    db.create(tmp_path, "174083")

    def to_2(conn):
        conn.execute("ALTER TABLE speed_location ADD COLUMN pretend TEXT")

    monkeypatch.setattr(db, "DB_VERSION", 2)
    monkeypatch.setitem(db._MIGRATIONS, 1, to_2)
    conn = db.open_db(tmp_path, "174083")
    try:
        assert db.user_version(conn) == 2
        assert "pretend" in {r[1] for r in conn.execute("PRAGMA table_info(speed_location)")}
    finally:
        conn.close()


def test_a_failed_upgrade_leaves_the_file_as_it_was(tmp_path, monkeypatch):
    db.create(tmp_path, "174083")

    def broken(conn):
        conn.execute("ALTER TABLE speed_location ADD COLUMN pretend TEXT")
        raise RuntimeError("stop half way")

    monkeypatch.setattr(db, "DB_VERSION", 2)
    monkeypatch.setitem(db._MIGRATIONS, 1, broken)
    with pytest.raises(RuntimeError):
        db.open_db(tmp_path, "174083")
    monkeypatch.undo()
    conn = db.open_db(tmp_path, "174083")
    try:
        assert db.user_version(conn) == 1
        assert "pretend" not in {r[1] for r in conn.execute("PRAGMA table_info(speed_location)")}
    finally:
        conn.close()


def test_transaction_is_all_or_nothing(tmp_path):
    conn, _ = db.open_or_create(tmp_path, "174083")
    try:
        with pytest.raises(ZeroDivisionError):
            with db.transaction(conn):
                conn.execute("INSERT INTO speed_meta (key, value) VALUES ('a', 'b')")
                1 / 0
        assert conn.execute("SELECT COUNT(*) FROM speed_meta WHERE key = 'a'").fetchone()[0] == 0
    finally:
        conn.close()
