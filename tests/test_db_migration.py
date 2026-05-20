"""Regression: opening Store on a pre-content_hash DB must succeed.

Prior bug: SCHEMA's `CREATE INDEX … ON processed(content_hash)` was inside
the same executescript as the `CREATE TABLE IF NOT EXISTS`. On an existing
DB without the column, the index creation raised before MIGRATIONS could
run the ALTER TABLE, so the forward-compat path was unreachable.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from actual_tx_splitter.db import Store

OLD_SCHEMA = """
CREATE TABLE processed (
    message_id   TEXT PRIMARY KEY,
    received_at  TEXT NOT NULL,
    vendor       TEXT,
    order_id     TEXT,
    total_cents  INTEGER,
    actual_tx_id TEXT,
    archive_path TEXT
);
"""


def test_store_migrates_legacy_db(tmp_path: Path) -> None:
    db = tmp_path / "splitter.db"
    with sqlite3.connect(db) as c:
        c.executescript(OLD_SCHEMA)
        c.execute(
            "INSERT INTO processed (message_id, received_at) VALUES (?, ?)",
            ("legacy@example", "2026-05-20T00:00:00Z"),
        )

    Store(db)  # would raise before the fix

    with sqlite3.connect(db) as c:
        cols = {row[1] for row in c.execute("PRAGMA table_info(processed)")}
        assert "content_hash" in cols
        idx = {row[1] for row in c.execute("PRAGMA index_list(processed)")}
        assert "idx_processed_content_hash" in idx
        # Pre-existing row survives the migration.
        (count,) = c.execute("SELECT COUNT(*) FROM processed").fetchone()
        assert count == 1


def test_store_idempotent_on_fresh_db(tmp_path: Path) -> None:
    db = tmp_path / "splitter.db"
    Store(db)
    Store(db)  # second open must not raise
