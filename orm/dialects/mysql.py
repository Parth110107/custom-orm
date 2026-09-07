"""
orm/dialects/mysql.py
---------------------
MySQL dialect implementation using **PyMySQL**.

WHY PyMySQL instead of mysqlclient?
-------------------------------------
``mysqlclient`` is a C extension that wraps the native MySQL client library.
While fast, it requires the ``libmysqlclient`` development headers at build
time, which adds a non-trivial system-level dependency in CI environments
(Docker images, GitHub Actions runners) and complicates cross-platform
packaging.

PyMySQL is a pure-Python implementation of the MySQL protocol.  It installs
with a plain ``pip install pymysql`` — no C compiler, no system packages.
The performance difference is negligible for typical OLTP workloads (the
bottleneck is almost always network I/O or the DB engine, not the driver
overhead).  If raw throughput ever becomes a concern, the dialect can be
swapped for mysqlclient without changing any other ORM code.

WHY ``TINYINT(1)`` for booleans?
----------------------------------
MySQL's ``BOOLEAN`` type is an alias for ``TINYINT(1)`` in all supported
versions, so the two are semantically identical.  We use ``TINYINT(1)``
explicitly because:
1. It's unambiguous in older MySQL 5.x versions that pre-date the alias.
2. It matches the widely adopted convention used by Django, SQLAlchemy, and
   most MySQL ORMs, so DBAs immediately understand the intent.
3. It avoids driver-level surprises: some older PyMySQL versions return
   integer values (0/1) for ``BOOLEAN`` columns, and our ``BooleanField``
   ``from_db`` handles that correctly.

WHY ``ENGINE=InnoDB``?
------------------------
InnoDB is the only MySQL storage engine that fully supports:
- ACID transactions
- Foreign-key constraints (which the ORM generates)
- Row-level locking (important for concurrent writes)
MyISAM, MEMORY, and other engines do NOT support FK constraints and would
silently accept (and ignore) the ``REFERENCES`` clauses we emit.
``utf8mb4`` is the only charset that correctly stores the full Unicode range
(including emoji); legacy ``utf8`` in MySQL only covers the BMP.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from orm.dialects.base import DialectBase
from orm.exceptions import ConnectionError as OrmConnectionError

if TYPE_CHECKING:
    from orm.fields import Field


class MySQLDialect(DialectBase):
    """
    Dialect that targets MySQL 5.7+ / MariaDB 10.3+ via PyMySQL.

    DSN format expected by ``get_connection``:
    ``mysql://user:pass@host:port/dbname``

    All DDL uses ``ENGINE=InnoDB DEFAULT CHARSET=utf8mb4`` to ensure FK
    support and full Unicode coverage.
    """

    name: str = "mysql"

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def get_connection(self, dsn: str) -> Any:
        """
        Parse *dsn* and open a PyMySQL connection.

        PyMySQL does not natively accept a URL-style DSN (unlike psycopg3
        which passes the DSN directly to libpq).  We parse the URL with
        ``urllib.parse.urlparse`` and call ``pymysql.connect`` with keyword
        arguments.

        Parameters
        ----------
        dsn:
            URL of the form ``mysql://user:pass@host:port/dbname``.
            The port defaults to ``3306`` if omitted.

        Returns
        -------
        pymysql.connections.Connection
            An open connection with ``autocommit=False``.

        Raises
        ------
        orm.exceptions.ConnectionError
            Wraps ``pymysql.err.OperationalError`` (and any other exception
            raised during connect) so callers never import PyMySQL directly.
        """
        try:
            import pymysql  # noqa: PLC0415

            parsed = urlparse(dsn)
            host = parsed.hostname or "localhost"
            port = parsed.port or 3306
            user = parsed.username or ""
            password = parsed.password or ""
            # Strip the leading '/' from the path component.
            database = parsed.path.lstrip("/")

            return pymysql.connect(
                host=host,
                port=port,
                user=user,
                password=password,
                database=database,
                # Return rows as tuples (DB-API 2.0 default).
                cursorclass=pymysql.cursors.Cursor,
                # Autocommit off: let the ORM manage transactions explicitly.
                autocommit=False,
                # Raise exceptions on warnings so data-truncation issues are
                # visible rather than silently swallowed.
                sql_mode="STRICT_TRANS_TABLES",
            )
        except Exception as exc:
            raise OrmConnectionError(
                f"Could not connect to MySQL: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Placeholder
    # ------------------------------------------------------------------

    def placeholder(self) -> str:
        """
        Return ``'%s'``.

        PyMySQL uses ``%s``-style positional parameters, identical to psycopg3,
        so the same query-builder logic works for both dialects.
        """
        return "%s"

    # ------------------------------------------------------------------
    # DDL helpers
    # ------------------------------------------------------------------

    def column_ddl(self, field: "Field", column_name: str) -> str:
        """
        Build the DDL fragment for one column, MySQL-flavoured.

        Differences from Postgres:
        - Primary-key ``IntegerField`` → ``INT AUTO_INCREMENT PRIMARY KEY``
          (MySQL does not have ``SERIAL``).
        - ``BooleanField`` → ``TINYINT(1)`` (see module docstring).
        - ``DateTimeField`` → ``DATETIME`` (Postgres uses ``TIMESTAMP``).

        Parameters
        ----------
        field:
            The ORM field descriptor.
        column_name:
            The DB-level column name.

        Returns
        -------
        str
            A column DDL fragment ready for inclusion in ``CREATE TABLE``.
        """
        from orm.fields import IntegerField  # noqa: PLC0415

        # Special case: auto-increment primary key.
        if isinstance(field, IntegerField) and field.primary_key:
            return f"{column_name} INT AUTO_INCREMENT PRIMARY KEY"

        sql_type = field.db_type(self.name)
        parts = [column_name, sql_type]

        if not field.null:
            parts.append("NOT NULL")

        from orm.fields import _MISSING  # noqa: PLC0415
        if field.default is not _MISSING:
            default_val = field.default
            if isinstance(default_val, str):
                escaped = default_val.replace("'", "''")
                parts.append(f"DEFAULT '{escaped}'")
            elif isinstance(default_val, bool):
                # MySQL stores booleans as 1/0.
                parts.append(f"DEFAULT {1 if default_val else 0}")
            else:
                parts.append(f"DEFAULT {default_val}")

        return " ".join(parts)

    def create_table_sql(self, model: type) -> str:
        """
        Build the full ``CREATE TABLE IF NOT EXISTS`` statement for MySQL.

        Identical structure to the Postgres version but appended with
        ``ENGINE=InnoDB DEFAULT CHARSET=utf8mb4`` which is required for:
        - Foreign-key constraint support (InnoDB only).
        - Full Unicode including supplementary characters (utf8mb4 vs utf8).

        Parameters
        ----------
        model:
            A ``Model`` subclass.

        Returns
        -------
        str
            Complete DDL string ready to execute.
        """
        from orm.fields import ForeignKeyField  # noqa: PLC0415

        meta = model._meta  # type: ignore[attr-defined]
        table = meta.table_name
        col_parts: list[str] = []
        fk_parts: list[str] = []

        for attname, field in meta.fields.items():
            col_name = field.column_name or attname
            col_parts.append(f"    {self.column_ddl(field, col_name)}")

            if isinstance(field, ForeignKeyField):
                target_cls = field._resolve_target()
                target_table = target_cls._meta.table_name  # type: ignore[attr-defined]
                fk_parts.append(
                    f"    FOREIGN KEY ({col_name}) "
                    f"REFERENCES {target_table}(id) "
                    f"ON DELETE {field.on_delete}"
                )

        all_parts = col_parts + fk_parts
        columns_sql = ",\n".join(all_parts)
        return (
            f"CREATE TABLE IF NOT EXISTS {table} (\n{columns_sql}\n) "
            "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;"
        )

    # ------------------------------------------------------------------
    # Insert helper
    # ------------------------------------------------------------------

    def last_insert_id(self, cursor: Any, table_name: str) -> int:
        """
        Return the auto-generated PK using ``cursor.lastrowid``.

        PyMySQL sets ``cursor.lastrowid`` after every ``INSERT`` statement
        that targets an ``AUTO_INCREMENT`` column, so no ``RETURNING`` clause
        is needed (MySQL does not support ``RETURNING`` in older versions).

        Parameters
        ----------
        cursor:
            The cursor that executed the INSERT.
        table_name:
            Unused for MySQL; kept for interface compatibility.

        Returns
        -------
        int
            The generated primary key.
        """
        return int(cursor.lastrowid)

    # ------------------------------------------------------------------
    # Schema introspection
    # ------------------------------------------------------------------

    def get_table_columns(self, connection: Any, table_name: str) -> list[str]:
        """
        Return column names for *table_name* using ``SHOW COLUMNS``.

        SAFETY NOTE — why the table name is formatted in rather than
        parameterised
        ---------------------------------------------------------------
        MySQL's ``SHOW COLUMNS FROM`` statement treats its argument as an
        *identifier*, not a *value*, so it cannot be bound via ``%s``.
        Attempting to do so causes a syntax error.

        This is safe because **table names in the ORM come exclusively from
        model class definitions** — they are never derived from user input.
        The model metaclass assigns the table name at class-definition time
        from the class name or the explicit ``Meta.table_name`` attribute.
        An attacker cannot inject an arbitrary table name through normal ORM
        usage (``filter()``, ``get()``, etc.) because those APIs only accept
        field values, which are always passed as ``%s`` parameters.

        If you extend the ORM to accept table names from user input in any
        future feature, add an allowlist validation step before calling this
        method.

        Parameters
        ----------
        connection:
            An open PyMySQL connection.
        table_name:
            Name of the table to inspect.

        Returns
        -------
        list[str]
            Column names in the order MySQL reports them (ordinal position).
        """
        # table_name is an identifier, not a value — format it in.
        # Backtick-quote to handle reserved words and mixed-case names.
        sql = f"SHOW COLUMNS FROM `{table_name}`"
        with connection.cursor() as cur:
            cur.execute(sql)
            # SHOW COLUMNS returns rows: (Field, Type, Null, Key, Default, Extra)
            return [row[0] for row in cur.fetchall()]

    def list_tables(self, connection: Any) -> list[str]:
        """
        Return all table names in the current database using ``SHOW TABLES``.

        Unlike Postgres, MySQL's concept of "schema" maps to "database", so
        there is no need to filter by schema — ``SHOW TABLES`` already scopes
        to the currently selected database set in the DSN.

        Parameters
        ----------
        connection:
            An open PyMySQL connection.

        Returns
        -------
        list[str]
            Table names in the order MySQL returns them.
        """
        with connection.cursor() as cur:
            cur.execute("SHOW TABLES")
            return [row[0] for row in cur.fetchall()]
