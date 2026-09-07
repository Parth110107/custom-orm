"""
tests/unit/test_migrations.py
==============================
Unit tests for the migration detector and writer.

We mock the dialect's list_tables / get_table_columns so no real DB is
needed.  We test:
  - Detecting new tables
  - Detecting new columns
  - Skipping destructive changes (columns in DB but not in model)
  - SQL statement generation for new tables and new columns
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from orm.fields import CharField, IntegerField
from orm.migrations.detector import SchemaDetector, SchemaDiff
from orm.migrations.writer import MigrationApplier, MigrationWriter
from orm.models import Model


# ---------------------------------------------------------------------------
# Test models
# ---------------------------------------------------------------------------

class Category(Model):
    name = CharField(max_length=100)


class Article(Model):
    title    = CharField(max_length=200)
    category = MagicMock()  # simplified FK for migration tests


# ---------------------------------------------------------------------------
# Mock DB factory
# ---------------------------------------------------------------------------

def _make_mock_db(
    tables: list[str],
    columns: dict[str, list[str]],
) -> MagicMock:
    """
    Build a mock Database whose dialect returns the given tables and columns.

    Parameters
    ----------
    tables:
        List of table names the mock DB reports as existing.
    columns:
        Mapping of table_name -> list of column names.
    """
    raw_conn = MagicMock()
    pool_conn = MagicMock()
    pool_conn.raw = raw_conn

    dialect = MagicMock()
    dialect.name = "postgres"
    dialect.placeholder.return_value = "%s"
    dialect.list_tables.return_value = tables
    dialect.get_table_columns.side_effect = lambda conn, table: columns.get(table, [])

    db = MagicMock()
    db.dialect = dialect
    db._acquire.return_value = pool_conn
    db._release.return_value = None
    db.execute.return_value = None

    return db


# ---------------------------------------------------------------------------
# Tests: SchemaDetector
# ---------------------------------------------------------------------------

class TestSchemaDetector:
    def test_detects_new_table(self) -> None:
        db = _make_mock_db(tables=[], columns={})
        detector = SchemaDetector(db, [Category])
        diff = detector.detect()
        assert Category in diff.new_tables
        assert diff.has_changes

    def test_no_changes_when_table_exists_with_all_columns(self) -> None:
        # Category has: id, name
        db = _make_mock_db(
            tables=["category"],
            columns={"category": ["id", "name"]},
        )
        detector = SchemaDetector(db, [Category])
        diff = detector.detect()
        assert not diff.has_changes
        assert not diff.new_tables
        assert not diff.new_columns

    def test_detects_missing_column(self) -> None:
        # category table exists but missing 'name' column
        db = _make_mock_db(
            tables=["category"],
            columns={"category": ["id"]},
        )
        detector = SchemaDetector(db, [Category])
        diff = detector.detect()
        assert "category" in diff.new_columns
        col_names = [col for col, _ in diff.new_columns["category"]]
        assert "name" in col_names

    def test_skips_extra_db_column_with_warning(self) -> None:
        # DB has a column that model doesn't (would be DROP)
        db = _make_mock_db(
            tables=["category"],
            columns={"category": ["id", "name", "deprecated_col"]},
        )
        detector = SchemaDetector(db, [Category])
        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            diff = detector.detect()
        assert not diff.has_changes
        assert len(w) >= 1
        assert "deprecated_col" in str(w[0].message)
        assert "deprecated_col" in diff.skipped[0]


# ---------------------------------------------------------------------------
# Tests: MigrationWriter
# ---------------------------------------------------------------------------

class TestMigrationWriter:
    def _make_writer(self, migration_dir: str) -> tuple[MigrationWriter, MagicMock]:
        db = _make_mock_db(tables=[], columns={})
        db.dialect.create_table_sql.return_value = (
            "CREATE TABLE IF NOT EXISTS category (id SERIAL PRIMARY KEY, name VARCHAR(100) NOT NULL)"
        )
        db.dialect.column_ddl.return_value = "name VARCHAR(100) NOT NULL"
        writer = MigrationWriter(db, migration_dir=migration_dir)
        return writer, db

    def test_generate_sql_for_new_table(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            writer, db = self._make_writer(tmpdir)
            diff = SchemaDiff(new_tables=[Category])
            stmts = writer.generate_sql(diff)
            assert len(stmts) == 1
            assert "CREATE TABLE" in stmts[0]

    def test_generate_sql_for_new_column_postgres(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            writer, db = self._make_writer(tmpdir)
            name_field = Category._meta.fields["name"]
            diff = SchemaDiff(new_columns={"category": [("name", name_field)]})
            stmts = writer.generate_sql(diff)
            assert len(stmts) == 1
            assert "ALTER TABLE" in stmts[0]
            assert "ADD COLUMN IF NOT EXISTS" in stmts[0]

    def test_generate_sql_empty_diff(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            writer, db = self._make_writer(tmpdir)
            diff = SchemaDiff()
            stmts = writer.generate_sql(diff)
            assert stmts == []

    def test_write_migration_file_creates_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            writer, db = self._make_writer(tmpdir)
            diff = SchemaDiff(new_tables=[Category])
            filepath = writer.write_migration_file(diff)
            assert filepath is not None
            assert os.path.exists(filepath)
            assert filepath.endswith(".sql")

    def test_write_migration_file_none_when_no_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            writer, db = self._make_writer(tmpdir)
            diff = SchemaDiff()
            filepath = writer.write_migration_file(diff)
            assert filepath is None

    def test_apply_diff_executes_statements(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            writer, db = self._make_writer(tmpdir)
            diff = SchemaDiff(new_tables=[Category])
            n = writer.apply_diff(diff)
            assert n >= 1
            db.execute.assert_called()


# ---------------------------------------------------------------------------
# Tests: MigrationApplier
# ---------------------------------------------------------------------------

class TestMigrationApplier:
    def test_pending_files_empty_when_no_dir(self) -> None:
        db = _make_mock_db(tables=[], columns={})
        applier = MigrationApplier(db, migration_dir="/nonexistent/path")
        assert applier.pending_files() == []

    def test_pending_files_sorted(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create out-of-order files
            for name in ["20240201_migration.sql", "20240101_migration.sql"]:
                open(os.path.join(tmpdir, name), "w").close()
            db = _make_mock_db(tables=[], columns={})
            applier = MigrationApplier(db, migration_dir=tmpdir)
            files = applier.pending_files()
            assert files[0].endswith("20240101_migration.sql")
            assert files[1].endswith("20240201_migration.sql")

    def test_apply_all_executes_sql(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            sql_content = "CREATE TABLE test (id INT PRIMARY KEY);\n"
            filepath = os.path.join(tmpdir, "20240101_migration.sql")
            with open(filepath, "w") as f:
                f.write(sql_content)

            db = _make_mock_db(tables=[], columns={})
            applier = MigrationApplier(db, migration_dir=tmpdir)
            count = applier.apply_all()
            assert count >= 1
            db.execute.assert_called()
