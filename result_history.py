"""Durable, compact history for completed simulation results.

History is intentionally separate from the candidate/evaluation cache.  The
cache answers "can this calculation be reused?"; this store answers "what did
I run, and what did it look like?" and therefore keeps display metadata,
compact gear snapshots, and plot-ready summaries.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from simulation_cache import canonical_json


HISTORY_SCHEMA_VERSION = 1
DEFAULT_PER_CHARACTER_LIMIT = 100


class ResultHistory:
    """SQLite-backed recent-result history with pin-aware pruning."""

    def __init__(self, directory: Path, *, source_hash: str = "", limit: int = DEFAULT_PER_CHARACTER_LIMIT):
        self.directory = Path(directory)
        self.path = self.directory / "simulation-history.sqlite3"
        self.source_hash = str(source_hash)
        self.limit = max(1, int(limit))
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        self.directory.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path), timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self):
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS simulation_history (
                    result_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    character_key TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL,
                    pinned INTEGER NOT NULL DEFAULT 0,
                    source_hash TEXT NOT NULL DEFAULT '',
                    payload TEXT NOT NULL,
                    payload_bytes INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS simulation_history_recent "
                "ON simulation_history(character_key, pinned, created_at DESC)"
            )

    def _decode(self, row: sqlite3.Row) -> dict:
        corrupt = False
        try:
            payload = json.loads(row["payload"])
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}
            corrupt = True
        if not isinstance(payload, dict):
            payload = {}
            corrupt = True
        schema_mismatch = payload.get("schema_version") not in (None, HISTORY_SCHEMA_VERSION)
        return {
            "id": int(row["result_id"]),
            "character_key": str(row["character_key"]),
            "created_at": float(row["created_at"]),
            "updated_at": float(row["updated_at"]),
            "kind": str(row["kind"]),
            "title": str(row["title"]),
            "pinned": bool(row["pinned"]),
            "source_hash": str(row["source_hash"]),
            "stale": bool(
                (row["source_hash"] and row["source_hash"] != self.source_hash)
                or schema_mismatch
            ),
            "corrupt": corrupt,
            "payload": payload,
        }

    def add(self, character_key: str, kind: str, title: str, payload: Any, *, pinned: bool = False) -> int:
        now = time.time()
        record = dict(payload or {})
        record.setdefault("schema_version", HISTORY_SCHEMA_VERSION)
        record.setdefault("source_hash", self.source_hash)
        encoded = canonical_json(record)
        with self._connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO simulation_history
                    (character_key, created_at, updated_at, kind, title, pinned, source_hash, payload, payload_bytes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(character_key or ""), now, now, str(kind), str(title), int(bool(pinned)),
                    self.source_hash, encoded, len(encoded.encode("utf-8")),
                ),
            )
            result_id = int(cursor.lastrowid)
            self._prune_character(connection, str(character_key or ""))
        return result_id

    def _prune_character(self, connection: sqlite3.Connection, character_key: str):
        rows = connection.execute(
            "SELECT result_id FROM simulation_history WHERE character_key = ? AND pinned = 0 "
            "ORDER BY created_at DESC",
            (character_key,),
        ).fetchall()
        victims = [(int(row["result_id"]),) for row in rows[self.limit:]]
        if victims:
            connection.executemany("DELETE FROM simulation_history WHERE result_id = ?", victims)

    def list(self, character_key: str = "", *, include_all_characters: bool = False) -> list[dict]:
        with self._connection() as connection:
            if include_all_characters:
                rows = connection.execute(
                    "SELECT * FROM simulation_history ORDER BY created_at DESC"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM simulation_history WHERE character_key = ? ORDER BY created_at DESC",
                    (str(character_key or ""),),
                ).fetchall()
        return [self._decode(row) for row in rows]

    def get(self, result_id: int) -> dict | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM simulation_history WHERE result_id = ?", (int(result_id),)
            ).fetchone()
        return self._decode(row) if row is not None else None

    def update(self, result_id: int, *, title: str | None = None, pinned: bool | None = None, payload: Any = None) -> bool:
        changes = []
        values = []
        if title is not None:
            changes.append("title = ?")
            values.append(str(title))
        if pinned is not None:
            changes.append("pinned = ?")
            values.append(int(bool(pinned)))
        if payload is not None:
            encoded = canonical_json(payload)
            changes.extend(("payload = ?", "payload_bytes = ?"))
            values.extend((encoded, len(encoded.encode("utf-8"))))
        if not changes:
            return False
        changes.append("updated_at = ?")
        values.append(time.time())
        values.append(int(result_id))
        with self._connection() as connection:
            cursor = connection.execute(
                f"UPDATE simulation_history SET {', '.join(changes)} WHERE result_id = ?", values
            )
        return cursor.rowcount > 0

    def delete(self, result_id: int) -> bool:
        with self._connection() as connection:
            cursor = connection.execute("DELETE FROM simulation_history WHERE result_id = ?", (int(result_id),))
        return cursor.rowcount > 0

    def clear(self, character_key: str = "", *, pinned: bool = False) -> int:
        with self._connection() as connection:
            if pinned:
                cursor = connection.execute(
                    "DELETE FROM simulation_history WHERE character_key = ? AND pinned = 0",
                    (str(character_key or ""),),
                )
            else:
                cursor = connection.execute(
                    "DELETE FROM simulation_history WHERE character_key = ?", (str(character_key or ""),)
                )
        return int(cursor.rowcount)

    def summary(self, character_key: str = "") -> dict:
        with self._connection() as connection:
            count, pinned = connection.execute(
                "SELECT COUNT(*), COALESCE(SUM(pinned), 0) FROM simulation_history WHERE character_key = ?",
                (str(character_key or ""),),
            ).fetchone()
        return {"entries": int(count), "pinned": int(pinned)}
