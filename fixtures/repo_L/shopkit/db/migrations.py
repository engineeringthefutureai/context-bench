"""Schema migrations: numbered SQL files in `migrations/` (repo root), applied in order.

    migrations/0001_init.sql
    migrations/0002_inventory.sql
    ...

Each file runs in one transaction and is recorded in `schema_migrations` with its checksum. A file
that changed after it was applied is an error (never edit an applied migration; add a new one).
`migrations/legacy/` holds one-off scripts from before the runner existed; they are never applied.

Statements are split on `;` at the end of a line. Lines starting with `--` are comments. A file may
start with `-- requires: 0007` to fail early when run against a database missing that migration (used
by the ops tooling that runs single migrations by hand).
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from shopkit.db.connection import Database
from shopkit.errors import ShopError
from shopkit.utils.dates import utc_now

log = logging.getLogger(__name__)

DEFAULT_DIR = Path(__file__).resolve().parents[2] / "migrations"
_NAME = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")
_REQUIRES = re.compile(r"^--\s*requires:\s*(\d{4})\s*$", re.M)


class MigrationError(ShopError):
    http_status = 500


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    path: Path

    @property
    def sql(self) -> str:
        return self.path.read_text()

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode()).hexdigest()[:16]

    @property
    def requires(self) -> int | None:
        m = _REQUIRES.search(self.sql)
        return int(m.group(1)) if m else None


def discover(directory: Path | None = None) -> list[Migration]:
    """Every NNNN_name.sql in the directory, sorted by version. Gaps are allowed, duplicates are not."""
    directory = directory or DEFAULT_DIR
    found: dict[int, Migration] = {}
    for path in sorted(directory.glob("*.sql")):
        m = _NAME.match(path.name)
        if not m:
            log.warning("ignoring %s: not NNNN_name.sql", path.name)
            continue
        version = int(m.group(1))
        if version in found:
            raise MigrationError(f"two migrations with version {version:04d}",
                                 files=[found[version].path.name, path.name])
        found[version] = Migration(version, m.group(2), path)
    return [found[v] for v in sorted(found)]


def split_statements(sql: str) -> list[str]:
    """Statements of a migration file: comments dropped, split on a `;` that ends a line."""
    out, current = [], []
    for line in sql.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        current.append(line)
        if stripped.endswith(";"):
            stmt = "\n".join(current).strip().rstrip(";").strip()
            if stmt:
                out.append(stmt)
            current = []
    tail = "\n".join(current).strip()
    if tail:
        out.append(tail)
    return out


class MigrationRunner:
    def __init__(self, db: Database, directory: Path | None = None):
        self.db = db
        self.directory = directory or DEFAULT_DIR

    def _ensure_table(self) -> None:
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version INTEGER PRIMARY KEY, name TEXT NOT NULL, checksum TEXT NOT NULL,"
            " applied_at TEXT NOT NULL)")

    def applied(self) -> dict[int, tuple[str, str]]:
        """version -> (name, checksum) of every applied migration."""
        self._ensure_table()
        rows = self.db.query("SELECT version, name, checksum FROM schema_migrations ORDER BY version")
        return {r["version"]: (r["name"], r["checksum"]) for r in rows}

    def current_version(self) -> int:
        applied = self.applied()
        return max(applied) if applied else 0

    def pending(self) -> list[Migration]:
        applied = self.applied()
        out = []
        for m in discover(self.directory):
            if m.version in applied:
                name, checksum = applied[m.version]
                if checksum != m.checksum:
                    raise MigrationError(f"migration {m.version:04d}_{m.name} changed after it was applied",
                                         version=m.version, recorded=checksum, current=m.checksum)
                continue
            out.append(m)
        return out

    def apply(self, migration: Migration) -> None:
        req = migration.requires
        if req is not None and req not in self.applied():
            raise MigrationError(f"{migration.path.name} requires migration {req:04d}", requires=req)
        statements = split_statements(migration.sql)
        log.info("applying %s (%d statements)", migration.path.name, len(statements))
        with self.db.transaction() as conn:
            for stmt in statements:
                try:
                    conn.execute(stmt)
                except Exception as e:
                    raise MigrationError(f"{migration.path.name}: {e}", statement=stmt[:200]) from e
            conn.execute("INSERT INTO schema_migrations (version, name, checksum, applied_at) "
                         "VALUES (?, ?, ?, ?)",
                         (migration.version, migration.name, migration.checksum, utc_now().isoformat()))

    def apply_all(self, up_to: int | None = None) -> list[int]:
        """Apply pending migrations (up to and including `up_to`); returns the versions applied."""
        done = []
        for m in self.pending():
            if up_to is not None and m.version > up_to:
                break
            self.apply(m)
            done.append(m.version)
        return done

    def status(self) -> list[dict]:
        """One row per migration file: version, name, applied (bool). For `shopkit-admin migrate --status`."""
        applied = self.applied()
        return [{"version": m.version, "name": m.name, "applied": m.version in applied}
                for m in discover(self.directory)]
