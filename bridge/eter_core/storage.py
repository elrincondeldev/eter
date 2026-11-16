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
);
CREATE TABLE IF NOT EXISTS devices (
    key TEXT PRIMARY KEY,      -- model/id/channel, see decode.device_key
    model TEXT NOT NULL,
    label TEXT,                -- name given by the user
    owned INTEGER NOT NULL DEFAULT 0,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    seen_count INTEGER NOT NULL,
    freq_mhz REAL NOT NULL,
    rssi_dbm INTEGER NOT NULL,
    last_data TEXT NOT NULL    -- JSON: last decoded message
);
"""
DEVICE_KEYS = ["key", "model", "label", "owned", "first_seen", "last_seen", "seen_count"]
DEVICE_KEYS += ["freq_mhz", "rssi_dbm"]


def now_utc() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path | str) -> None:
        # Tools run in worker threads; one connection guarded by a lock is enough.
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(SCHEMA)

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

    def event_messages(self, event_id: int) -> tuple[str, list[dict[str, Any]]] | None:
        """Kind and raw node messages of one event."""
        with self._lock:
            row = self._db.execute(
                "SELECT kind, messages FROM events WHERE id = ?", (event_id,)
            ).fetchone()
        return None if row is None else (row[0], json.loads(row[1]))

    def see_device(
        self, key: str, model: str, freq_mhz: float, rssi_dbm: int, data: dict[str, Any]
    ) -> None:
        """Record a decoded transmission: create the device or update it."""
        now = now_utc()
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO devices (key, model, first_seen, last_seen, seen_count, freq_mhz,"
                " rssi_dbm, last_data) VALUES (?, ?, ?, ?, 1, ?, ?, ?)"
                " ON CONFLICT(key) DO UPDATE SET last_seen = excluded.last_seen,"
                " seen_count = seen_count + 1, freq_mhz = excluded.freq_mhz,"
                " rssi_dbm = excluded.rssi_dbm, last_data = excluded.last_data",
                (key, model, now, now, freq_mhz, rssi_dbm, json.dumps(data)),
            )

    def devices(self, limit: int) -> list[dict[str, Any]]:
        """Most recently seen first."""
        with self._lock:
            rows = self._db.execute(
                f"SELECT {', '.join(DEVICE_KEYS)}, last_data FROM devices"
                " ORDER BY last_seen DESC LIMIT ?",
                (limit,),
            ).fetchall()
        devices = []
        for row in rows:
            device = dict(zip(DEVICE_KEYS, row[:-1]))
            device["owned"] = bool(device["owned"])
            device["last_data"] = json.loads(row[-1])
            devices.append(device)
        return devices

    def label_device(self, key: str, label: str, owned: bool) -> bool:
        """Name a device. Returns False if it was never seen."""
        with self._lock, self._db:
            cursor = self._db.execute(
                "UPDATE devices SET label = ?, owned = ? WHERE key = ?", (label, int(owned), key)
            )
        return cursor.rowcount == 1

    def device_labels(self) -> dict[str, tuple[str | None, bool]]:
        with self._lock:
            rows = self._db.execute("SELECT key, label, owned FROM devices").fetchall()
        return {key: (label, bool(owned)) for key, label, owned in rows}
