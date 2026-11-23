"""Persistence: SQLite connection, schema migrations (migrations/*.sql) and repositories."""
from shopkit.db.connection import Database, connect

__all__ = ["Database", "connect"]
