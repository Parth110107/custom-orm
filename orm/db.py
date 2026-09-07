"""
orm/db.py
=========
The central Database class.  It owns:
  - A dialect instance (postgres or mysql) chosen from the DSN scheme.
  - A thread-safe connection pool (lazy: connections created on first use).
  - A context manager for transactions.
  - A low-level ``execute`` helper used by QuerySet.

Design notes
------------
Pool design is intentionally simple for v1:
  * We use a plain list + threading.Lock, not a queue or semaphore.
  * Connections are lazy-created up to max_conn; excess callers block briefly
    then raise ConnectionError.  A proper backoff queue is v2.
  * Thread-local storage is NOT used — connections are returned to the pool
    after each operation so they can be shared across threads.

Transaction design:
  * ``db.transaction()`` is a context manager that pins one connection for the
    duration of the block, turns off autocommit, and commits/rolls back on exit.
  * QuerySet calls ``db.execute()``; inside a transaction the bound connection
    is used so all statements see the same transaction.

Async support is deliberately out of scope for v1; the pool uses only
threading primitives.  When async is added in v2, this class should be
subclassed (AsyncDatabase) rather than retrofitted so sync and async callers
can coexist.
"""

from __future__ import annotations

import threading
import time
import urllib.parse
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Generator, Sequence

from orm.exceptions import ConnectionError as OrmConnectionError

if TYPE_CHECKING:
    # Avoid circular imports at runtime; type-checker sees the Model class.
    from orm.models import Model


class _PooledConnection:
    """Thin wrapper around a raw driver connection tracking availability."""

    def __init__(self, raw: Any) -> None:
        self.raw = raw
        self.in_use: bool = False


class Database:
    """
    Top-level entry point for the ORM.

    Parameters
    ----------
    url:
        DSN in the form ``dialect://user:password@host:port/dbname``.
        Supported schemes: ``postgresql``, ``postgres``, ``mysql``.
    min_conn:
        Minimum pool size (currently unused \u2014 all connections are lazy).
        Reserved for v2 pool warming.
    max_conn:
        Maximum number of simultaneous open connections.

    Example
    -------
    ::

        db = Database("postgresql://alice:secret@localhost:5432/myapp")
        db.register(Author, Post)
        db.create_tables([Author, Post])
    """

    def __init__(
        self,
        url: str,
        min_conn: int = 1,
        max_conn: int = 10,
    ) -> None:
        self._url = url
        self._min_conn = min_conn
        self._max_conn = max_conn

        # Parse scheme to select dialect
        parsed = urllib.parse.urlparse(url)
        scheme = parsed.scheme.lower()

        from orm.dialects.base import get_dialect  # local import avoids circularity
        self._dialect = get_dialect(scheme)

        # Pool state
        self._pool: list[_PooledConnection] = []
        self._lock = threading.Lock()

        # Thread-local storage for transaction-pinned connections.
        # When a thread enters db.transaction(), we store the pinned
        # connection here so nested execute() calls reuse it.
        self._local = threading.local()

    # ------------------------------------------------------------------
    # Pool management
    # ------------------------------------------------------------------

    def _create_connection(self) -> _PooledConnection:
        """Open a new raw connection via the dialect and wrap it."""
        raw = self._dialect.get_connection(self._url)
        return _PooledConnection(raw)

    def _acquire(self) -> _PooledConnection:
        """
        Return an idle connection from the pool, creating one if under the
        max-connection limit.  Raises ``ConnectionError`` if the pool is full.

        Why not block/queue?  For v1 simplicity — a full queue with timeout
        is planned for v2 alongside connection health-checks.
        """
        with self._lock:
            # Prefer an existing idle connection
            for conn in self._pool:
                if not conn.in_use:
                    conn.in_use = True
                    return conn

            # Create a new connection if we have capacity
            if len(self._pool) < self._max_conn:
                conn = self._create_connection()
                conn.in_use = True
                self._pool.append(conn)
                return conn

        raise OrmConnectionError(
            f"Connection pool exhausted (max_conn={self._max_conn}). "
            "Increase max_conn or release connections faster."
        )

    def _release(self, conn: _PooledConnection) -> None:
        """Return a connection to the pool (mark it idle)."""
        with self._lock:
            conn.in_use = False

    # ------------------------------------------------------------------
    # Public execute API (used by QuerySet)
    # ------------------------------------------------------------------

    def execute(
        self,
        sql: str,
        params: Sequence[Any] = (),
        *,
        fetch: str = "none",  # "none" | "one" | "all"
    ) -> Any:
        """
        Execute a parameterized SQL statement.

        Parameters
        ----------
        sql:
            Parameterized SQL string.  Use ``%s`` placeholders (both psycopg3
            and PyMySQL accept this).  NEVER interpolate user-supplied values
            into ``sql`` directly \u2014 always use ``params``.
        params:
            Sequence of values to bind to the placeholders.
        fetch:
            What to return:
            - ``"none"`` \u2014 no rows returned (INSERT, UPDATE, DELETE, DDL)
            - ``"one"``  \u2014 return a single row as a tuple
            - ``"all"``  \u2014 return all rows as a list of tuples

        Returns
        -------
        Depends on ``fetch``:
        - ``"none"`` \u2014 ``None``
        - ``"one"``  \u2014 a single tuple or ``None`` if no row
        - ``"all"``  \u2014 list of tuples (may be empty)
        """
        # If we're inside a transaction, use the pinned connection
        pinned: _PooledConnection | None = getattr(self._local, "txn_conn", None)
        if pinned is not None:
            return self._run(pinned.raw, sql, params, fetch)

        # Otherwise acquire from the pool, execute, release
        conn = self._acquire()
        try:
            return self._run(conn.raw, sql, params, fetch)
        finally:
            self._release(conn)

    def _run(
        self,
        raw_conn: Any,
        sql: str,
        params: Sequence[Any],
        fetch: str,
    ) -> Any:
        """Inner helper that uses a concrete raw connection."""
        cursor = raw_conn.cursor()
        try:
            cursor.execute(sql, params)
            if fetch == "one":
                return cursor.fetchone()
            elif fetch == "all":
                return cursor.fetchall()
            else:
                # Autocommit INSERT/UPDATE/DELETE when outside a transaction.
                # DDL (CREATE TABLE) is also committed here.
                # Inside a transaction the caller commits explicitly.
                if not getattr(self._local, "in_transaction", False):
                    raw_conn.commit()
                return None
        finally:
            cursor.close()

    def execute_lastrowid(
        self,
        sql: str,
        params: Sequence[Any] = (),
        table_name: str = "",
    ) -> int:
        """
        Execute an INSERT and return the PK of the inserted row.

        Postgres uses ``RETURNING id``; MySQL uses ``cursor.lastrowid``.
        The dialect handles the difference transparently.
        """
        pinned: _PooledConnection | None = getattr(self._local, "txn_conn", None)
        conn = pinned if pinned is not None else self._acquire()
        try:
            cursor = conn.raw.cursor()
            try:
                cursor.execute(sql, params)
                pk = self._dialect.last_insert_id(cursor, table_name)
                if not getattr(self._local, "in_transaction", False):
                    conn.raw.commit()
                return pk
            finally:
                cursor.close()
        finally:
            if pinned is None:
                self._release(conn)

    # ------------------------------------------------------------------
    # Transaction context manager
    # ------------------------------------------------------------------

    @contextmanager
    def transaction(self) -> Generator[None, None, None]:
        """
        Context manager for explicit transactions.

        Usage::

            with db.transaction():
                Author.objects.create(name="Alice")
                Post.objects.create(author=alice, title="Hello")

        On successful exit the transaction is committed.
        On any exception the transaction is rolled back and the exception
        is re-raised.

        Nesting note: nested ``with db.transaction()`` blocks reuse the same
        connection (the outer transaction wins).  Full savepoint support is
        planned for v2.
        """
        # Check for existing transaction (re-entrant nesting \u2014 no-op inner)
        if getattr(self._local, "in_transaction", False):
            # Already in a transaction \u2014 join it (no savepoint in v1)
            yield
            return

        conn = self._acquire()
        self._local.txn_conn = conn
        self._local.in_transaction = True

        # Disable autocommit so the driver accumulates statements
        try:
            raw = conn.raw
            raw.autocommit = False
        except AttributeError:
            pass  # some drivers don't have this attribute

        try:
            yield
            raw.commit()
        except Exception:
            raw.rollback()
            raise
        finally:
            self._local.txn_conn = None
            self._local.in_transaction = False
            self._release(conn)

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def create_tables(self, models: list[type[Model]]) -> None:
        """
        Create DB tables for each model in the list if they don't exist.

        Uses ``CREATE TABLE IF NOT EXISTS`` so it's safe to call repeatedly.
        Models are created in the given order \u2014 put parent models (no FK deps)
        before child models to satisfy FK constraints.

        Parameters
        ----------
        models:
            List of Model subclasses whose tables should be created.
        """
        for model in models:
            sql = self._dialect.create_table_sql(model)
            self.execute(sql, fetch="none")

    def register(self, *models: type[Model]) -> None:
        """
        Associate models with this Database instance.

        After registration, ``Model.objects`` queries route to this database.
        This is the recommended way to bind models to a DB rather than
        assigning ``Model._db`` directly.

        Parameters
        ----------
        models:
            One or more Model subclasses to register.
        """
        for model in models:
            model._db = self  # type: ignore[attr-defined]

    @property
    def dialect(self) -> Any:
        """Expose the dialect for use by QuerySet and migrations."""
        return self._dialect

    def close_all(self) -> None:
        """Close all connections in the pool (call on application shutdown)."""
        with self._lock:
            for conn in self._pool:
                try:
                    conn.raw.close()
                except Exception:
                    pass
            self._pool.clear()
