"""SQLite connections. Production runs the same schema on a managed database; tests and local
development use a file or an in-memory database through this module.

Rows come back as `sqlite3.Row` (index and key access). Money columns are integer cents, timestamps
are ISO-8601 strings in UTC (see `shopkit.utils.dates.utc_now`).
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 5.0
SLOW_QUERY_MS = 250


@dataclass
class QueryStats:
    """Counters kept per Database, read by the /health endpoint and in tests."""
    queries: int = 0
    slow_queries: int = 0
    total_ms: float = 0.0
    last_error: str | None = None

    def record(self, elapsed_ms: float) -> None:
        self.queries += 1
        self.total_ms += elapsed_ms
        if elapsed_ms >= SLOW_QUERY_MS:
            self.slow_queries += 1

    def as_dict(self) -> dict:
        avg = self.total_ms / self.queries if self.queries else 0.0
        return {"queries": self.queries, "slow_queries": self.slow_queries,
                "avg_ms": round(avg, 2), "last_error": self.last_error}


@dataclass
class Database:
    """One database, shared by the services of a process.

    Each thread gets its own sqlite3 connection (sqlite3 objects must not cross threads). Use
    `transaction()` for writes; plain `query()` / `query_one()` for reads.
    """
    path: str = ":memory:"
    timeout_s: float = DEFAULT_TIMEOUT_S
    stats: QueryStats = field(default_factory=QueryStats)
    _local: threading.local = field(default_factory=threading.local, repr=False)
    _shared: sqlite3.Connection | None = field(default=None, repr=False)

    def connection(self) -> sqlite3.Connection:
        # An in-memory database exists once per connection, so all threads share one there.
        if self.path == ":memory:":
            if self._shared is None:
                self._shared = self._connect()
            return self._shared
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._connect()
            self._local.conn = conn
        return conn

    def _connect(self) -> sqlite3.Connection:
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=self.timeout_s, check_same_thread=False,
                               isolation_level=None)          # autocommit; transaction() opens BEGIN
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None
        if self._shared is not None:
            self._shared.close()
            self._shared = None

    # ---- reads and writes -------------------------------------------------------------------------
    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        start = time.perf_counter()
        try:
            cur = self.connection().execute(sql, params)
        except sqlite3.Error as e:
            self.stats.last_error = f"{type(e).__name__}: {e}"
            log.warning("query failed: %s (%s)", e, sql.split("\n", 1)[0][:80])
            raise
        elapsed = (time.perf_counter() - start) * 1000
        self.stats.record(elapsed)
        if elapsed >= SLOW_QUERY_MS:
            log.info("slow query (%.0f ms): %s", elapsed, sql.split("\n", 1)[0][:120])
        return cur

    def executescript(self, sql: str) -> None:
        self.connection().executescript(sql)

    def query(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        return self.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
        return self.execute(sql, params).fetchone()

    def scalar(self, sql: str, params: tuple | dict = ()):
        row = self.query_one(sql, params)
        return None if row is None else row[0]

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """BEGIN ... COMMIT, or ROLLBACK on any exception. Nested calls join the outer transaction."""
        conn = self.connection()
        if conn.in_transaction:
            yield conn
            return
        conn.execute("BEGIN")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")

    def table_exists(self, name: str) -> bool:
        return self.scalar("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)) == 1


def connect(path: str | Path = ":memory:", *, migrate: bool = True,
            migrations_dir: Path | None = None) -> Database:
    """A Database, migrated to the latest schema unless migrate=False."""
    db = Database(str(path))
    if migrate:
        from shopkit.db.migrations import MigrationRunner
        MigrationRunner(db, migrations_dir).apply_all()
    return db
