"""
orm/migrations/history.py
=========================
Migration history tracking and state persistence.

Maintains the internal `orm_migrations` table in the database to prevent
duplicate migration application, record execution timestamps, and support
rollback workflows.
"""

from __future__ import annotations

import datetime
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from orm.db import Database


TABLE_NAME = "orm_migrations"


class MigrationHistory:
    """
    Manages the `orm_migrations` database history table.
    """

    def __init__(self, db: Database) -> None:
        self.db = db

    def ensure_table(self) -> None:
        """Create the `orm_migrations` table if it does not already exist."""
        dialect = self.db.dialect
        if dialect.name == "postgres":
            sql = (
                f"CREATE TABLE IF NOT EXISTS {TABLE_NAME} ("
                f"id SERIAL PRIMARY KEY, "
                f"name VARCHAR(255) NOT NULL UNIQUE, "
                f"applied_at TIMESTAMP NOT NULL"
                f")"
            )
        else:
            sql = (
                f"CREATE TABLE IF NOT EXISTS {TABLE_NAME} ("
                f"id INT AUTO_INCREMENT PRIMARY KEY, "
                f"name VARCHAR(255) NOT NULL UNIQUE, "
                f"applied_at DATETIME NOT NULL"
                f") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
            )
        self.db.execute(sql, fetch="none")

    def get_applied(self) -> list[str]:
        """Return the sorted list of migration filenames that have been applied."""
        self.ensure_table()
        sql = f"SELECT name FROM {TABLE_NAME} ORDER BY id ASC"
        rows = self.db.execute(sql, fetch="all")
        if not rows:
            return []
        return [str(row[0]) for row in rows]

    def record_applied(self, name: str) -> None:
        """Mark a migration filename as applied in the database."""
        self.ensure_table()
        placeholder = self.db.dialect.placeholder()
        now = datetime.datetime.now(datetime.timezone.utc)
        sql = f"INSERT INTO {TABLE_NAME} (name, applied_at) VALUES ({placeholder}, {placeholder})"
        self.db.execute(sql, [name, now], fetch="none")

    def record_unapplied(self, name: str) -> None:
        """Remove a migration record from the database history table (rollback)."""
        self.ensure_table()
        placeholder = self.db.dialect.placeholder()
        sql = f"DELETE FROM {TABLE_NAME} WHERE name = {placeholder}"
        self.db.execute(sql, [name], fetch="none")

    def get_pending(self, migration_dir: str = "migrations") -> list[str]:
        """Return files present in migration_dir that have not yet been applied."""
        if not os.path.isdir(migration_dir):
            return []
        applied = set(self.get_applied())
        all_files = sorted(f for f in os.listdir(migration_dir) if f.endswith(".sql"))
        return [f for f in all_files if f not in applied]
