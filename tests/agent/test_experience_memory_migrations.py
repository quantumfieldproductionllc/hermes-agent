import sqlite3

import pytest

from agent.experience_memory import migrations
from agent.experience_memory.schema import SCHEMA_VERSION
from agent.experience_memory.store import ExperienceStore


def test_fresh_store_creates_database_and_schema(tmp_path):
    store = ExperienceStore(db_path=tmp_path / "experience.db")
    store.open()
    try:
        assert (tmp_path / "experience.db").exists()
        assert store.status()["schema_version"] == SCHEMA_VERSION

        tables = {
            row[0]
            for row in store.conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'virtual table')"
            )
        }
        assert "experience_schema_version" in tables
        assert "experience_records" in tables
        assert "experience_extraction_jobs" in tables
        assert "experience_projections" in tables

        store.conn.execute("SELECT * FROM experience_fts LIMIT 0")
        assert store.conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert store.conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    finally:
        store.close()


def test_migration_is_idempotent(tmp_path):
    path = tmp_path / "experience.db"
    store = ExperienceStore(db_path=path)
    store.open()
    store.close()

    reopened = ExperienceStore(db_path=path)
    reopened.open()
    try:
        assert reopened.status()["schema_version"] == SCHEMA_VERSION
        assert reopened.status()["records_total"] == 0
    finally:
        reopened.close()


def test_migration_failure_rolls_back_partial_schema(tmp_path, monkeypatch):
    conn = sqlite3.connect(tmp_path / "broken.db")
    monkeypatch.setattr(
        migrations,
        "SCHEMA_SQL",
        """
        CREATE TABLE partial_experience_table (id INTEGER PRIMARY KEY);
        SELECT * FROM missing_table_to_force_failure;
        """,
    )
    try:
        with pytest.raises(sqlite3.Error):
            migrations.apply_migrations(conn)
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'index')"
            )
        }
        assert "partial_experience_table" not in tables
        assert "experience_schema_version" not in tables
    finally:
        conn.close()


def test_legacy_schema_without_superseded_by_is_upgraded(tmp_path):
    conn = sqlite3.connect(tmp_path / "legacy.db")
    legacy_sql = "\n".join(
        line
        for line in migrations.SCHEMA_SQL.splitlines()
        if "superseded_by TEXT" not in line
    )
    try:
        conn.executescript(legacy_sql)
        conn.commit()

        migrations.apply_migrations(conn)

        columns = [row[1] for row in conn.execute("PRAGMA table_info(experience_records)")]
        assert "superseded_by" in columns
    finally:
        conn.close()


def test_wal_fallback_result_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "agent.experience_memory.store.apply_wal_with_fallback",
        lambda conn, db_label="experience.db": "delete",
    )

    store = ExperienceStore(db_path=tmp_path / "experience.db")
    store.open()
    try:
        assert store.status()["journal_mode"] == "delete"
    finally:
        store.close()


def test_fts5_required(tmp_path):
    conn = sqlite3.connect(tmp_path / "probe.db")
    try:
        conn.execute("CREATE VIRTUAL TABLE probe_fts USING fts5(content)")
        conn.execute("SELECT * FROM probe_fts LIMIT 0")
    finally:
        conn.close()
