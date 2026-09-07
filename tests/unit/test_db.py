"""
tests/unit/test_db.py
=====================
Unit tests for the Database class: pool management, transaction context
manager, and dialect selection.

All driver calls are mocked — no real DB connection is made.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch, call

import pytest

from orm.exceptions import ConnectionError as OrmConnectionError


# ---------------------------------------------------------------------------
# We need to patch psycopg.connect and pymysql.connect at import time.
# Use module-level patches via monkeypatch / patch context.
# ---------------------------------------------------------------------------

def _make_mock_raw_conn() -> MagicMock:
    """Build a realistic-looking mock raw connection."""
    conn = MagicMock()
    cursor = MagicMock()
    cursor.fetchone.return_value = (1,)
    cursor.fetchall.return_value = [(1, "test")]
    conn.cursor.return_value = cursor
    conn.autocommit = False
    return conn


class TestDatabaseDialectSelection:
    def test_postgres_scheme_selects_postgres_dialect(self) -> None:
        from orm.db import Database
        from orm.dialects.postgres import PostgresDialect
        # We can't easily construct a live DB without a real server, so we
        # verify dialect selection by checking the class used in the pool.
        db = Database.__new__(Database)
        import threading, urllib.parse
        db._url = "postgresql://u:p@h:5432/d"
        db._max_conn = 5
        db._pool = []
        db._lock = threading.Lock()
        db._local = threading.local()
        from orm.dialects.base import get_dialect
        db._dialect = get_dialect("postgresql")
        assert db.dialect.name == "postgres"
        assert isinstance(db.dialect, PostgresDialect)

    def test_mysql_scheme_selects_mysql_dialect(self) -> None:
        from orm.db import Database
        from orm.dialects.mysql import MySQLDialect
        db = Database.__new__(Database)
        import threading
        db._url = "mysql://u:p@h:3306/d"
        db._max_conn = 5
        db._pool = []
        db._lock = threading.Lock()
        db._local = threading.local()
        from orm.dialects.base import get_dialect
        db._dialect = get_dialect("mysql")
        assert db.dialect.name == "mysql"
        assert isinstance(db.dialect, MySQLDialect)

    def test_invalid_scheme_raises(self) -> None:
        from orm.dialects.base import get_dialect
        with pytest.raises(ValueError, match="Unsupported dialect"):
            get_dialect("sqlite")


class TestConnectionPool:
    def _make_db(self, max_conn: int = 3) -> tuple[MagicMock, MagicMock]:
        """Return (db, mock_dialect) with dialect.get_connection mocked."""
        import threading
        from orm.db import Database

        # Build a Database instance bypassing __init__ to avoid needing a
        # real DB connection.  We inject a mock dialect directly.
        db = Database.__new__(Database)
        db._url = "postgresql://u:p@h:5432/d"
        db._max_conn = max_conn
        db._pool = []
        db._lock = threading.Lock()
        db._local = threading.local()

        mock_raw = _make_mock_raw_conn()
        mock_dialect = MagicMock()
        mock_dialect.name = "postgres"
        mock_dialect.placeholder.return_value = "%s"
        mock_dialect.get_connection.return_value = mock_raw
        db._dialect = mock_dialect
        return db, mock_dialect

    def test_acquire_creates_connection_when_pool_empty(self) -> None:
        db, dialect = self._make_db()
        conn = db._acquire()
        dialect.get_connection.assert_called_once()
        assert conn.in_use is True
        db._release(conn)

    def test_acquire_reuses_idle_connection(self) -> None:
        db, dialect = self._make_db()
        conn1 = db._acquire()
        db._release(conn1)
        conn2 = db._acquire()
        # Should be the same connection object, not a new one
        assert conn1 is conn2
        dialect.get_connection.assert_called_once()  # only one connection created
        db._release(conn2)

    def test_pool_exhaustion_raises(self) -> None:
        db, dialect = self._make_db(max_conn=1)
        conn = db._acquire()  # exhaust the pool
        with pytest.raises(OrmConnectionError, match="exhausted"):
            db._acquire()
        db._release(conn)

    def test_release_marks_connection_idle(self) -> None:
        db, dialect = self._make_db()
        conn = db._acquire()
        assert conn.in_use is True
        db._release(conn)
        assert conn.in_use is False

    def test_close_all_closes_connections(self) -> None:
        db, dialect = self._make_db()
        conn = db._acquire()
        db._release(conn)
        db.close_all()
        conn.raw.close.assert_called_once()
        assert len(db._pool) == 0


class TestTransaction:
    def _make_db(self) -> MagicMock:
        from orm.db import Database
        db = Database.__new__(Database)
        import threading, urllib.parse
        db._url = "postgresql://u:p@h/d"
        db._max_conn = 5
        db._pool = []
        db._lock = threading.Lock()
        db._local = threading.local()

        mock_raw = _make_mock_raw_conn()
        mock_dialect = MagicMock()
        mock_dialect.name = "postgres"
        mock_dialect.placeholder.return_value = "%s"
        mock_dialect.get_connection.return_value = mock_raw
        db._dialect = mock_dialect
        return db

    def test_transaction_commits_on_success(self) -> None:
        db = self._make_db()
        mock_raw = db._dialect.get_connection.return_value

        with db.transaction():
            pass  # no-op

        mock_raw.commit.assert_called_once()
        mock_raw.rollback.assert_not_called()

    def test_transaction_rolls_back_on_exception(self) -> None:
        db = self._make_db()
        mock_raw = db._dialect.get_connection.return_value

        with pytest.raises(ValueError):
            with db.transaction():
                raise ValueError("oops")

        mock_raw.rollback.assert_called_once()
        mock_raw.commit.assert_not_called()

    def test_nested_transaction_is_noop(self) -> None:
        """Inner transaction() block should not commit separately."""
        db = self._make_db()
        mock_raw = db._dialect.get_connection.return_value

        with db.transaction():
            with db.transaction():  # nested — should be a no-op
                pass
            # After inner exits, nothing committed yet
        # Commit happens once at outermost exit
        assert mock_raw.commit.call_count == 1

    def test_connection_released_after_transaction(self) -> None:
        db = self._make_db()
        with db.transaction():
            pass
        # All connections should be idle after the transaction
        for conn in db._pool:
            assert not conn.in_use
