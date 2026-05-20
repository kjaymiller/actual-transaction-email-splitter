from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS processed (
    message_id   TEXT PRIMARY KEY,
    received_at  TEXT NOT NULL,
    vendor       TEXT,
    order_id     TEXT,
    total_cents  INTEGER,
    actual_tx_id TEXT,
    archive_path TEXT,
    content_hash TEXT
);
CREATE INDEX IF NOT EXISTS idx_processed_content_hash ON processed(content_hash);
"""

# Forward-compat: older DBs may not have content_hash column yet.
MIGRATIONS = [
    "ALTER TABLE processed ADD COLUMN content_hash TEXT",
    "CREATE INDEX IF NOT EXISTS idx_processed_content_hash ON processed(content_hash)",
]


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.conn() as c:
            c.executescript(SCHEMA)
            for stmt in MIGRATIONS:
                try:
                    c.execute(stmt)
                except sqlite3.OperationalError:
                    pass  # column/index already exists

    @contextmanager
    def conn(self):
        c = sqlite3.connect(self.path, isolation_level=None)
        c.row_factory = sqlite3.Row
        try:
            yield c
        finally:
            c.close()

    def seen(self, message_id: str) -> bool:
        with self.conn() as c:
            row = c.execute(
                "SELECT 1 FROM processed WHERE message_id = ?", (message_id,)
            ).fetchone()
            return row is not None

    def seen_by_hash(self, content_hash: str) -> str | None:
        """Return the message_id of a prior row with this content_hash, or None."""
        if not content_hash:
            return None
        with self.conn() as c:
            row = c.execute(
                "SELECT message_id FROM processed WHERE content_hash = ? LIMIT 1",
                (content_hash,),
            ).fetchone()
            return row["message_id"] if row else None

    def record(
        self,
        *,
        message_id: str,
        received_at: str,
        vendor: str | None,
        order_id: str | None,
        total_cents: int | None,
        actual_tx_id: str | None,
        archive_path: str | None,
        content_hash: str | None = None,
    ) -> None:
        with self.conn() as c:
            c.execute(
                """
                INSERT OR REPLACE INTO processed
                  (message_id, received_at, vendor, order_id, total_cents, actual_tx_id, archive_path, content_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (message_id, received_at, vendor, order_id, total_cents, actual_tx_id, archive_path, content_hash),
            )
