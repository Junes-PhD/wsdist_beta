"""Versioned, bounded persistent cache for deterministic simulation results."""

from __future__ import annotations

import hashlib
import json
import numbers
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any


CACHE_SCHEMA_VERSION = 4
DEFAULT_MAX_BYTES = 250 * 1024 * 1024
DEFAULT_MAX_AGE_SECONDS = 90 * 24 * 60 * 60


def _json_value(value: Any):
    """Return a stable JSON-compatible form without serializing executable objects."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, numbers.Integral):
        return int(value)
    if isinstance(value, numbers.Real):
        return float(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (list, tuple, set, frozenset)):
        values = [_json_value(item) for item in value]
        if isinstance(value, (set, frozenset)):
            values.sort(key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=True))
        return values
    if hasattr(value, "__dict__"):
        return _json_value(vars(value))
    return repr(value)


def canonical_json(value: Any) -> str:
    return json.dumps(_json_value(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def source_fingerprint(files: list[Path]) -> str:
    """Hash calculation sources so formula/data edits invalidate old cache rows."""
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.name.encode("utf-8"))
        try:
            digest.update(path.read_bytes())
        except OSError:
            digest.update(b"<missing>")
    return digest.hexdigest()


class SimulationCache:
    """SQLite result cache with expiry and least-recently-used pruning."""

    def __init__(self, directory: Path, *, max_bytes: int = DEFAULT_MAX_BYTES,
                 max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS, source_hash: str = ""):
        self.directory = Path(directory)
        self.path = self.directory / "simulation-results.sqlite3"
        self.max_bytes = max(1, int(max_bytes))
        self.max_age_seconds = max(1, int(max_age_seconds))
        self.source_hash = str(source_hash)
        self._maintenance_done = False
        self._maintenance_lock = threading.RLock()

    @staticmethod
    def _is_corruption(error: sqlite3.DatabaseError) -> bool:
        message = str(error).casefold()
        return any(marker in message for marker in (
            "malformed", "not a database", "disk image is malformed",
            "file is encrypted", "unsupported file format",
        ))

    def _quarantine_corrupt_files(self):
        """Move a broken cache aside so a fresh database can be created."""
        stamp = time.strftime("%Y%m%d-%H%M%S")
        for path in (self.path, Path(f"{self.path}-wal"), Path(f"{self.path}-shm")):
            if not path.exists():
                continue
            target = path.with_name(f"{path.name}.corrupt-{stamp}")
            suffix = 1
            while target.exists():
                target = path.with_name(f"{path.name}.corrupt-{stamp}-{suffix}")
                suffix += 1
            try:
                path.replace(target)
            except OSError:
                pass

    def _open_database(self) -> sqlite3.Connection:
        self.directory.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=2)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA busy_timeout=2000")
            connection.execute("PRAGMA wal_autocheckpoint=256")
            table_existed = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'simulation_results'"
            ).fetchone() is not None
            previous_schema = int(connection.execute("PRAGMA user_version").fetchone()[0])
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS simulation_results (
                    cache_key TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    accessed_at REAL NOT NULL,
                    runtime_seconds REAL NOT NULL,
                    payload TEXT NOT NULL,
                    payload_bytes INTEGER NOT NULL,
                    request_summary TEXT NOT NULL DEFAULT '{}',
                    batch_id TEXT NOT NULL DEFAULT '',
                    hit_count INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(simulation_results)")}
            for name, definition in (
                ("request_summary", "TEXT NOT NULL DEFAULT '{}'"),
                ("batch_id", "TEXT NOT NULL DEFAULT ''"),
                ("hit_count", "INTEGER NOT NULL DEFAULT 0"),
            ):
                if name not in columns:
                    connection.execute(f"ALTER TABLE simulation_results ADD COLUMN {name} {definition}")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS simulation_results_lru "
                "ON simulation_results(source_hash, accessed_at)"
            )
            if table_existed and previous_schema != CACHE_SCHEMA_VERSION:
                connection.execute("DELETE FROM simulation_results")
            connection.execute(f"PRAGMA user_version = {CACHE_SCHEMA_VERSION}")
        except Exception:
            connection.close()
            raise
        return connection

    def _connect(self) -> sqlite3.Connection:
        try:
            connection = self._open_database()
        except sqlite3.DatabaseError as error:
            if not self._is_corruption(error):
                raise
            with self._maintenance_lock:
                self._quarantine_corrupt_files()
                self._maintenance_done = False
                connection = self._open_database()
        with self._maintenance_lock:
            if not self._maintenance_done:
                self._prune(connection, time.time())
                self._maintenance_done = True
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

    def key_for(self, kind: str, request: Any) -> str:
        envelope = {
            "schema": CACHE_SCHEMA_VERSION,
            "source": self.source_hash,
            "kind": str(kind),
            "request": request,
        }
        return hashlib.sha256(canonical_json(envelope).encode("utf-8")).hexdigest()

    def get(self, key: str, kind: str) -> dict | None:
        now = time.time()
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "SELECT created_at, runtime_seconds, payload FROM simulation_results "
                    "WHERE cache_key = ? AND kind = ? AND source_hash = ?",
                    (key, kind, self.source_hash),
                ).fetchone()
                if row is None:
                    return None
                created_at, runtime_seconds, payload = row
                if now - float(created_at) > self.max_age_seconds:
                    connection.execute("DELETE FROM simulation_results WHERE cache_key = ?", (key,))
                    return None
                try:
                    decoded = json.loads(payload)
                except (TypeError, ValueError, json.JSONDecodeError):
                    connection.execute("DELETE FROM simulation_results WHERE cache_key = ?", (key,))
                    return None
                connection.execute(
                    "UPDATE simulation_results SET accessed_at = ?, hit_count = hit_count + 1 "
                    "WHERE cache_key = ?",
                    (now, key),
                )
                return {"payload": decoded, "created_at": float(created_at), "runtime_seconds": float(runtime_seconds)}
        except (OSError, sqlite3.DatabaseError):
            return None

    def put(self, key: str, kind: str, payload: Any, runtime_seconds: float,
            *, request_summary: Any = None, batch_id: str = "") -> bool:
        try:
            encoded = canonical_json(payload)
            size = len(encoded.encode("utf-8"))
            now = time.time()
            with self._connection() as connection:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO simulation_results
                    (cache_key, kind, source_hash, created_at, accessed_at, runtime_seconds, payload, payload_bytes,
                     request_summary, batch_id, hit_count)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                    """,
                    (key, kind, self.source_hash, now, now, max(0.0, float(runtime_seconds)), encoded, size,
                     canonical_json(request_summary or {}), str(batch_id or "")),
                )
                self._prune(connection, now)
            return True
        except (OSError, TypeError, ValueError, sqlite3.DatabaseError):
            return False

    def _prune(self, connection: sqlite3.Connection, now: float):
        connection.execute(
            "DELETE FROM simulation_results WHERE created_at < ?", (now - self.max_age_seconds,)
        )
        # A source change makes every older row unreachable. Remove those rows
        # immediately instead of letting them consume the cache budget for 90 days.
        connection.execute(
            "DELETE FROM simulation_results WHERE source_hash != ?", (self.source_hash,)
        )
        total = connection.execute(
            "SELECT COALESCE(SUM(payload_bytes), 0) FROM simulation_results"
        ).fetchone()[0]
        while total > self.max_bytes:
            rows = connection.execute(
                "SELECT cache_key, payload_bytes FROM simulation_results "
                "ORDER BY accessed_at ASC LIMIT 128"
            ).fetchall()
            if not rows:
                break
            victims = []
            for row in rows:
                victims.append((row[0],))
                total -= int(row[1])
                if total <= self.max_bytes:
                    break
            connection.executemany(
                "DELETE FROM simulation_results WHERE cache_key = ?",
                victims,
            )

    def disk_bytes(self) -> int:
        """Return actual allocated database/WAL bytes, not just JSON payload."""
        total = 0
        for path in (self.path, Path(f"{self.path}-wal"), Path(f"{self.path}-shm")):
            try:
                total += path.stat().st_size
            except OSError:
                pass
        return total

    def clear(self) -> bool:
        try:
            with self._connection() as connection:
                connection.execute("DELETE FROM simulation_results")
            # DELETE is logical only. VACUUM and truncate the WAL so the File
            # menu's clear action actually returns disk space to the system.
            with self._connection() as connection:
                connection.execute("VACUUM")
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            return True
        except (OSError, sqlite3.DatabaseError):
            return False

    def summary(self) -> dict[str, Any]:
        try:
            with self._connection() as connection:
                entries, payload_bytes, hits = connection.execute(
                    "SELECT COUNT(*), COALESCE(SUM(payload_bytes), 0), COALESCE(SUM(hit_count), 0) FROM simulation_results"
                ).fetchone()
                kinds = {
                    str(kind): int(count) for kind, count in connection.execute(
                        "SELECT kind, COUNT(*) FROM simulation_results GROUP BY kind"
                    ).fetchall()
                }
            return {
                "entries": int(entries), "bytes": int(payload_bytes),
                "disk_bytes": self.disk_bytes(), "kinds": kinds, "hits": int(hits),
            }
        except (OSError, sqlite3.DatabaseError):
            return {"entries": 0, "bytes": 0, "disk_bytes": self.disk_bytes(), "kinds": {}, "hits": 0}
