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
    archive_path TEXT
);
"""


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.conn() as c:
            c.executescript(SCHEMA)

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
    ) -> None:
        with self.conn() as c:
            c.execute(
                """
                INSERT OR REPLACE INTO processed
                  (message_id, received_at, vendor, order_id, total_cents, actual_tx_id, archive_path)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (message_id, received_at, vendor, order_id, total_cents, actual_tx_id, archive_path),
            )
