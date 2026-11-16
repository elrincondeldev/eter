"""SQLite history of what the bridge has observed."""

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,          -- UTC, ISO 8601
    node_id TEXT NOT NULL,
    kind TEXT NOT NULL,        -- scan | listen
    radio TEXT NOT NULL,
    freq_mhz REAL NOT NULL,    -- scan: start of range; listen: listening frequency
    summary TEXT NOT NULL,     -- JSON: what the tools returned
    messages TEXT NOT NULL     -- JSON: raw node messages, for later analysis
)
"""


def now_utc() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path | str) -> None:
        # Tools run in worker threads; one connection guarded by a lock is enough.
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._db.execute(SCHEMA)

    def add_event(
        self,
        node_id: str,
        kind: str,
        radio: str,
        freq_mhz: float,
        summary: dict[str, Any],
        messages: list[dict[str, Any]],
    ) -> int:
        with self._lock, self._db:
            cursor = self._db.execute(
                "INSERT INTO events (ts, node_id, kind, radio, freq_mhz, summary, messages)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    now_utc(),
                    node_id,
                    kind,
                    radio,
                    freq_mhz,
                    json.dumps(summary),
                    json.dumps(messages),
                ),
            )
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    def events(self, kind: str | None, since: str | None, limit: int) -> list[dict[str, Any]]:
        """Newest first. `since` is an ISO 8601 UTC timestamp."""
        query = "SELECT id, ts, node_id, kind, radio, freq_mhz, summary FROM events WHERE 1=1"
        params: list[Any] = []
        if kind:
            query += " AND kind = ?"
            params.append(kind)
        if since:
            query += " AND ts >= ?"
            params.append(since)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._db.execute(query, params).fetchall()
        keys = ["id", "ts", "node_id", "kind", "radio", "freq_mhz"]
        return [{**dict(zip(keys, row[:6])), "summary": json.loads(row[6])} for row in rows]
