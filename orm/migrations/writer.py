"""
orm/migrations/writer.py
========================
Turns a SchemaDiff into SQL statements or migration files.

v1 strategy: plain .sql files
------------------------------
Migration files are named ``YYYYMMDD_HHMMSS_migration.sql`` and stored in a
``migrations/`` directory at the project root (configurable).  Each file
contains the SQL to apply the diff at that point in time.

Why plain SQL files instead of Python migration functions?
----------------------------------------------------------
SQL files are:
- Language-agnostic (any tool can apply them)
- Trivially reviewable in PRs
- Safely re-runnable if written with IF NOT EXISTS / IF NOT EXISTS guards

Python migration functions (like Django) are more flexible (data migrations)
and are planned for v2.

Migration history / rollback:
------------------------------
v1 does NOT track which migrations have been applied.  The ``migrate`` CLI
applies all .sql files in lexicographic order.  The files use
``CREATE TABLE IF NOT EXISTS`` and ``ALTER TABLE ... ADD COLUMN IF NOT EXISTS``
(Postgres) so re-running them is idempotent on Postgres.

MySQL does NOT support ``ADD COLUMN IF NOT EXISTS`` before MySQL 8.0.31+.
For MySQL, the writer wraps ADD COLUMN in a stored procedure or documents
that the migration should be applied exactly once.  In practice, teams
should track applied migrations manually in v1; the history table is v2.
"""

from __future__ import annotations

import datetime
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from orm.db import Database
    from orm.migrations.detector import SchemaDiff
    from orm.models import Model


class MigrationWriter:
    """
    Generates SQL migration statements from a SchemaDiff.

    Parameters
    ----------
    db:
        The Database (used to access the dialect for DDL generation).
    migration_dir:
        Directory where .sql migration files are written.  Defaults to
        ``./migrations`` relative to the current working directory.
    """

    def __init__(self, db: Database, migration_dir: str = "migrations") -> None:
        self._db = db
        self._migration_dir = migration_dir

    def generate_sql(self, diff: SchemaDiff) -> list[str]:
        """
        Generate a list of SQL statements for the given diff.

        Returns an empty list if there are no changes.
        """
        statements: list[str] = []

        # New tables: full CREATE TABLE
        for model in diff.new_tables:
            sql = self._db.dialect.create_table_sql(model)
            statements.append(sql)

        # New columns on existing tables: ALTER TABLE ADD COLUMN
        for table_name, cols in diff.new_columns.items():
            for attr_name, field_obj in cols:
                col_name = field_obj.column_name or attr_name
                ddl_fragment = self._db.dialect.column_ddl(field_obj, col_name)

                if self._db.dialect.name == "postgres":
                    # PostgreSQL 9.6+ supports ADD COLUMN IF NOT EXISTS
                    stmt = (
                        f"ALTER TABLE {table_name} "
                        f"ADD COLUMN IF NOT EXISTS {ddl_fragment};"
                    )
                else:
                    # MySQL: no IF NOT EXISTS for ADD COLUMN in older versions.
                    # Teams should apply this exactly once.
                    stmt = (
                        f"-- Apply manually if column already exists:\n"
                        f"ALTER TABLE {table_name} ADD COLUMN {ddl_fragment};"
                    )
                statements.append(stmt)

        return statements

    def write_migration_file(self, diff: SchemaDiff) -> str | None:
        """
        Write a timestamped .sql migration file for the diff.

        Returns the file path written, or None if there are no changes.
        """
        statements = self.generate_sql(diff)
        if not statements:
            return None

        os.makedirs(self._migration_dir, exist_ok=True)
        timestamp = datetime.datetime.now(tz=datetime.timezone.utc).strftime(
            "%Y%m%d_%H%M%S"
        )
        filename = f"{timestamp}_migration.sql"
        filepath = os.path.join(self._migration_dir, filename)

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(f"-- Migration generated at {timestamp} UTC\n")
            f.write(f"-- Dialect: {self._db.dialect.name}\n\n")
            for stmt in statements:
                f.write(stmt.rstrip(";") + ";\n\n")

        return filepath

    def apply_diff(self, diff: SchemaDiff) -> int:
        """
        Apply the diff directly to the DB without writing a file.

        Returns the number of statements executed.

        This is used by ``migrate`` when ``--no-file`` is passed, or in tests.
        """
        statements = self.generate_sql(diff)
        for stmt in statements:
            # Strip comments before executing
            clean = "\n".join(
                line for line in stmt.splitlines()
                if not line.strip().startswith("--")
            ).strip()
            if clean:
                self._db.execute(clean, fetch="none")
        return len(statements)


class MigrationApplier:
    """
    Applies pending .sql migration files to the database with state tracking.

    Uses `orm_migrations` history table to ensure migrations are executed
    exactly once in order.

    Parameters
    ----------
    db:
        Target database.
    migration_dir:
        Directory containing .sql migration files.
    track_history:
        If True (default), records applied migrations in `orm_migrations`.
    """

    def __init__(
        self,
        db: Database,
        migration_dir: str = "migrations",
        track_history: bool = True,
    ) -> None:
        self._db = db
        self._migration_dir = migration_dir
        self._track_history = track_history
        from orm.migrations.history import MigrationHistory
        self.history = MigrationHistory(db) if track_history else None

    def pending_files(self) -> list[str]:
        """Return sorted list of pending .sql files."""
        if not os.path.isdir(self._migration_dir):
            return []

        if self.history:
            filenames = self.history.get_pending(self._migration_dir)
        else:
            filenames = sorted(
                f for f in os.listdir(self._migration_dir) if f.endswith(".sql")
            )
        return [os.path.join(self._migration_dir, f) for f in filenames]

    def apply_all(self, dry_run: bool = False) -> int:
        """
        Apply all pending migration files in order.

        Parameters
        ----------
        dry_run:
            If True, only simulates without executing DDL or writing to history.

        Returns the total number of SQL statements executed.
        """
        total = 0
        for filepath in self.pending_files():
            basename = os.path.basename(filepath)
            with open(filepath, encoding="utf-8") as f:
                content = f.read()

            raw_statements = content.split(";")
            for raw in raw_statements:
                lines = [
                    line for line in raw.splitlines()
                    if not line.strip().startswith("--")
                ]
                stmt = "\n".join(lines).strip()
                if stmt:
                    if not dry_run:
                        self._db.execute(stmt, fetch="none")
                    total += 1

            if not dry_run and self.history:
                self.history.record_applied(basename)

        return total

    def rollback_last(self) -> str | None:
        """Revert recording of the most recently applied migration."""
        if not self.history:
            return None
        applied = self.history.get_applied()
        if not applied:
            return None
        last_applied = applied[-1]
        self.history.record_unapplied(last_applied)
        return last_applied
