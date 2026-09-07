"""
orm/dialects/base.py
--------------------
Abstract base class for all database dialects plus a factory function.

WHY an abstract base class?
---------------------------
Both PostgreSQL and MySQL share the same logical interface (open a connection,
build DDL, execute queries) but differ in driver API, SQL syntax, and
type names.  Formalising that interface with ``abc.ABC`` + ``@abstractmethod``
means:

1. New dialects (SQLite, CockroachDB, …) can be added without touching any
   other ORM code — only the dialect module + the factory need updating.
2. The type checker (mypy/pyright) catches missing method implementations at
   development time rather than at runtime.
3. ``get_dialect()`` returns ``DialectBase`` so all call sites are typed
   against the interface, not a concrete class.

Circular import avoidance
--------------------------
``DialectBase`` references ``Field`` only in type annotations, which are
strings (``'Field'``) inside ``TYPE_CHECKING`` blocks.  The concrete dialect
modules import ``Field`` directly and are themselves imported lazily inside
``get_dialect()`` rather than at module load time.  This prevents the chain:

    orm.fields → orm.dialects.base → orm.dialects.postgres → orm.fields
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from orm.fields import Field


class DialectBase(ABC):
    """
    Interface that every database dialect must implement.

    A *dialect* encapsulates everything that differs between database backends:
    - How to open a connection (driver API, DSN format)
    - SQL syntax quirks (placeholders, auto-increment syntax, DDL details)
    - How to introspect an existing schema (``information_schema`` vs
      ``SHOW COLUMNS``)

    All methods that interact with the DB receive either a *connection* or a
    *cursor* from the same driver that ``get_connection`` returned, ensuring
    that dialect-specific driver quirks are contained within a single module.

    Attributes
    ----------
    name:
        Short lowercase identifier for the dialect (``'postgres'`` or
        ``'mysql'``).  Used by ``Field.db_type()`` to select the right SQL
        type string.
    """

    name: str  # must be set on each concrete subclass

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    @abstractmethod
    def get_connection(self, dsn: str) -> Any:
        """
        Open and return a raw DB-API 2.0 connection.

        Parameters
        ----------
        dsn:
            A connection string in the format understood by the underlying
            driver (e.g. ``postgresql://user:pass@host:5432/db`` for psycopg3,
            ``mysql://user:pass@host:3306/db`` for PyMySQL).

        Returns
        -------
        Any
            A live DB-API 2.0 connection object.

        Raises
        ------
        orm.exceptions.ConnectionError
            If the connection cannot be established.  The original driver
            exception is chained as ``__cause__``.
        """

    # ------------------------------------------------------------------
    # Query building helpers
    # ------------------------------------------------------------------

    @abstractmethod
    def placeholder(self) -> str:
        """
        Return the parameter placeholder string for this dialect.

        Both psycopg3 and PyMySQL use ``%s``-style placeholders, unlike
        sqlite3 which uses ``?``.  Having this as a method (rather than a
        constant) lets future dialects (e.g. sqlite) return ``'?'`` without
        any changes to the query-builder layer.

        Returns
        -------
        str
            The placeholder string, e.g. ``'%s'``.
        """

    # ------------------------------------------------------------------
    # DDL helpers
    # ------------------------------------------------------------------

    @abstractmethod
    def column_ddl(self, field: "Field", column_name: str) -> str:
        """
        Return the DDL fragment for a single column.

        This is the per-column piece of a ``CREATE TABLE`` statement, for
        example ``'age INTEGER NOT NULL'`` or
        ``'id SERIAL PRIMARY KEY'``.

        Parameters
        ----------
        field:
            The ``Field`` instance describing the column.
        column_name:
            The actual database column name (may differ from the Python
            attribute name, e.g. ``'author_id'`` for a ``ForeignKeyField``
            named ``'author'``).

        Returns
        -------
        str
            A DDL fragment suitable for inclusion inside a ``CREATE TABLE``
            column list.
        """

    @abstractmethod
    def create_table_sql(self, model: type) -> str:
        """
        Return the full ``CREATE TABLE IF NOT EXISTS`` SQL for a model.

        Parameters
        ----------
        model:
            The model class (a subclass of ``orm.model.Model``).

        Returns
        -------
        str
            Complete DDL string ready to execute without parameters.
        """

    # ------------------------------------------------------------------
    # Insert helper
    # ------------------------------------------------------------------

    @abstractmethod
    def last_insert_id(self, cursor: Any, table_name: str) -> int:
        """
        Return the primary key of the most recently inserted row.

        The mechanism differs per dialect:
        - PostgreSQL uses ``RETURNING id`` in the INSERT statement; this
          method then calls ``cursor.fetchone()[0]``.
        - MySQL uses ``cursor.lastrowid``.

        Parameters
        ----------
        cursor:
            The cursor that executed the INSERT.
        table_name:
            The table that was inserted into (needed for the Postgres
            ``RETURNING`` approach in some driver configurations).

        Returns
        -------
        int
            The auto-generated primary key value.
        """

    # ------------------------------------------------------------------
    # Schema introspection
    # ------------------------------------------------------------------

    @abstractmethod
    def get_table_columns(self, connection: Any, table_name: str) -> list[str]:
        """
        Return the current column names for *table_name*.

        Used by the migration engine to detect schema drift: compare the
        columns the model declares against the columns that currently exist in
        the DB and generate ``ALTER TABLE`` statements for the difference.

        Parameters
        ----------
        connection:
            An open DB connection (same driver as ``get_connection`` returns).
        table_name:
            The database table to inspect.

        Returns
        -------
        list[str]
            Column names in the order the database reports them.
        """

    @abstractmethod
    def list_tables(self, connection: Any) -> list[str]:
        """
        Return all table names in the current database / schema.

        Used by ``orm.management`` commands (``inspect``, ``migrate``) to
        discover which tables already exist so they can be skipped or
        compared.

        Parameters
        ----------
        connection:
            An open DB connection.

        Returns
        -------
        list[str]
            Table names in whatever order the database returns them.
        """


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_dialect(scheme: str) -> DialectBase:
    """
    Return the appropriate ``DialectBase`` instance for *scheme*.

    The concrete dialect classes are imported **inside** this function to
    avoid a circular import: the dialect modules import from ``orm.fields``,
    which in turn may import from ``orm.dialects.base`` at type-check time.
    Lazy imports here break that cycle at module-load time.

    Parameters
    ----------
    scheme:
        The URL scheme portion of the DSN, e.g. ``'postgresql'``,
        ``'postgres'``, or ``'mysql'``.

    Returns
    -------
    DialectBase
        A fresh dialect instance.  Dialects are stateless so it is safe to
        create a new instance per connection.

    Raises
    ------
    ValueError
        If *scheme* is not a recognised dialect identifier.

    Examples
    --------
    >>> dialect = get_dialect('postgresql')
    >>> conn = dialect.get_connection('postgresql://user:pass@localhost/mydb')
    """
    if scheme in ("postgresql", "postgres"):
        from orm.dialects.postgres import PostgresDialect  # noqa: PLC0415
        return PostgresDialect()
    elif scheme in ("mysql",):
        from orm.dialects.mysql import MySQLDialect  # noqa: PLC0415
        return MySQLDialect()
    raise ValueError(f"Unsupported dialect: {scheme!r}")
