from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


SCHEMA = """
CREATE TABLE IF NOT EXISTS event_queue (
    id TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    synced INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT
);
CREATE INDEX IF NOT EXISTS ix_event_queue_synced ON event_queue(synced);
"""


class EventQueue:
    def __init__(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def enqueue(self, payload: dict[str, Any]) -> str:
        event_id = payload.get("id") or str(uuid4())
        payload["id"] = event_id
        import json

        self._conn.execute(
            "INSERT OR IGNORE INTO event_queue (id, payload_json, created_at, synced) VALUES (?, ?, ?, 0)",
            (event_id, json.dumps(payload), datetime.now(UTC).isoformat()),
        )
        self._conn.commit()
        return event_id

    def pending(self, limit: int = 50) -> list[dict[str, Any]]:
        import json

        rows = self._conn.execute(
            "SELECT id, payload_json FROM event_queue WHERE synced = 0 ORDER BY created_at LIMIT ?",
            (limit,),
        ).fetchall()
        return [json.loads(r["payload_json"]) for r in rows]

    def mark_synced(self, event_ids: list[str]) -> None:
        self._conn.executemany(
            "UPDATE event_queue SET synced = 1, last_error = NULL WHERE id = ?",
            [(eid,) for eid in event_ids],
        )
        self._conn.commit()

    def mark_failed(self, event_id: str, error: str) -> None:
        self._conn.execute(
            "UPDATE event_queue SET attempts = attempts + 1, last_error = ? WHERE id = ?",
            (error, event_id),
        )
        self._conn.commit()

    def size(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS c FROM event_queue WHERE synced = 0").fetchone()
        return int(row["c"])
