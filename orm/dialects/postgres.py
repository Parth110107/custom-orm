"""
orm/dialects/postgres.py
------------------------
PostgreSQL dialect implementation using **psycopg3** (the ``psycopg`` package).

WHY psycopg3 instead of psycopg2?
----------------------------------
psycopg2 is in pure maintenance mode — no new features, security patches only.
psycopg3 is the actively developed successor with:
- A modern async-first architecture (sync and async share the same codebase).
- Native support for Python type adaptors (no manual ``register_adapter``).
- ``%s``-style placeholders that work identically to psycopg2, making
  migration of existing code straightforward.
- First-class support for ``COPY`` and pipeline mode (useful for bulk inserts
  in v2).

WHY ``SERIAL`` instead of ``IDENTITY``?
----------------------------------------
``IDENTITY`` columns (SQL-standard, PostgreSQL ≥ 10) are more spec-compliant
but require slightly different DDL and have subtly different behaviour with
``OVERRIDING SYSTEM VALUE``.  ``SERIAL`` is syntactic sugar that has been in
PostgreSQL for decades, is universally understood by tooling (pg_dump,
pgAdmin, DBAs), and maps cleanly to the pattern used in v1 (single integer
PK per table).  We can migrate to ``IDENTITY`` transparently in a future
version by changing this file only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from orm.dialects.base import DialectBase
from orm.exceptions import ConnectionError as OrmConnectionError
from orm.exceptions import FieldError

if TYPE_CHECKING:
    from orm.fields import Field


class PostgresDialect(DialectBase):
    """
    Dialect that targets PostgreSQL via the ``psycopg`` (v3) driver.

    All SQL generated here is standard PostgreSQL DDL/DML.  psycopg3 uses
    ``%s`` placeholders (same as psycopg2) so no changes are needed at the
    query-builder layer when switching between them.
    """

    name: str = "postgres"

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def get_connection(self, dsn: str) -> Any:
        """
        Open a psycopg3 connection and return it.

        psycopg3 accepts the same DSN formats as libpq:
        ``postgresql://user:pass@host:port/dbname``
        or keyword-value strings like ``host=localhost dbname=mydb``.

        Parameters
        ----------
        dsn:
            A libpq-compatible connection string.

        Returns
        -------
        psycopg.Connection
            An open, autocommit-disabled connection.

        Raises
        ------
        orm.exceptions.ConnectionError
            Wraps ``psycopg.OperationalError`` so callers never have to import
            the driver directly.
        """
        try:
            import psycopg  # noqa: PLC0415
            return psycopg.connect(dsn)
        except Exception as exc:
            # Catch broadly so we also wrap network errors, auth failures, etc.
            raise OrmConnectionError(
                f"Could not connect to PostgreSQL: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Placeholder
    # ------------------------------------------------------------------

    def placeholder(self) -> str:
        """
        Return ``'%s'``.

        psycopg3 uses ``%s``-style positional parameters by default (same as
        psycopg2), so a single placeholder string covers all query types.
        """
        return "%s"

    # ------------------------------------------------------------------
    # DDL helpers
    # ------------------------------------------------------------------

    def column_ddl(self, field: "Field", column_name: str) -> str:
        """
        Build the DDL fragment for one column.

        Rules applied in order:
        1. Primary-key ``IntegerField`` → ``SERIAL PRIMARY KEY`` (no NOT NULL
           needed; SERIAL implies NOT NULL).
        2. All other fields: ``<column_name> <SQL_TYPE> [NOT NULL] [DEFAULT v]``

        Foreign-key constraints are NOT included here — they are appended as
        separate ``REFERENCES`` clauses by ``create_table_sql`` so that the
        column list is valid SQL before the constraint list is processed.

        Parameters
        ----------
        field:
            The ORM field descriptor.
        column_name:
            The DB-level column name (may differ from the Python attribute
            name, e.g. ``'author_id'``).

        Returns
        -------
        str
            A column DDL fragment, e.g. ``'email VARCHAR(255) NOT NULL'``.
        """
        from orm.fields import IntegerField  # noqa: PLC0415

        # Special case: auto-increment primary key.
        if isinstance(field, IntegerField) and field.primary_key:
            return f"{column_name} SERIAL PRIMARY KEY"

        sql_type = field.db_type(self.name)
        parts = [column_name, sql_type]

        if not field.null:
            parts.append("NOT NULL")

        # Append DEFAULT only when an explicit default was provided.
        # We compare against the _MISSING sentinel imported from fields.
        from orm.fields import _MISSING  # noqa: PLC0415
        if field.default is not _MISSING:
            default_val = field.default
            if isinstance(default_val, str):
                # Quote string defaults to produce valid SQL literals.
                escaped = default_val.replace("'", "''")
                parts.append(f"DEFAULT '{escaped}'")
            elif isinstance(default_val, bool):
                parts.append(f"DEFAULT {'TRUE' if default_val else 'FALSE'}")
            else:
                parts.append(f"DEFAULT {default_val}")

        return " ".join(parts)

    def create_table_sql(self, model: type) -> str:
        """
        Build the full ``CREATE TABLE IF NOT EXISTS`` statement for *model*.

        Structure:
        1. Column DDL fragments for all non-FK fields.
        2. Column DDL fragments for FK fields (as plain ``INTEGER`` columns).
        3. ``REFERENCES`` constraints for each FK field, placed after all
           columns so the column list is syntactically complete before
           constraints reference it.

        Parameters
        ----------
        model:
            A ``Model`` subclass.  Must have a ``_meta`` attribute with
            ``table_name`` and ``fields`` (an ``{attname: Field}`` dict).

        Returns
        -------
        str
            Complete DDL with trailing newline.

        Example
        -------
        ::

            CREATE TABLE IF NOT EXISTS posts (
                id SERIAL PRIMARY KEY,
                title VARCHAR(255) NOT NULL,
                author_id INTEGER NOT NULL,
                REFERENCES authors(id) ON DELETE CASCADE
            );
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
        return f"CREATE TABLE IF NOT EXISTS {table} (\n{columns_sql}\n);"

    # ------------------------------------------------------------------
    # Insert helper
    # ------------------------------------------------------------------

    def last_insert_id(self, cursor: Any, table_name: str) -> int:
        """
        Return the auto-generated PK from the most recent INSERT.

        For PostgreSQL the INSERT statement must include ``RETURNING id`` at
        the end.  The ORM's ``QuerySet.create()`` adds this clause
        automatically when using the Postgres dialect.  This method then
        calls ``cursor.fetchone()[0]`` to retrieve the value.

        Note: ``cursor.lastrowid`` is NOT reliable in psycopg3 for tables
        with ``SERIAL`` columns; ``RETURNING`` is the idiomatic PostgreSQL
        approach.

        Parameters
        ----------
        cursor:
            The cursor that executed the ``INSERT … RETURNING id``.
        table_name:
            Unused for Postgres but kept for interface compatibility.

        Returns
        -------
        int
            The generated primary key.
        """
        row = cursor.fetchone()
        if row is None:
            raise FieldError(
                "last_insert_id: cursor returned no row. "
                "Ensure the INSERT uses RETURNING id."
            )
        return int(row[0])

    # ------------------------------------------------------------------
    # Schema introspection
    # ------------------------------------------------------------------

    def get_table_columns(self, connection: Any, table_name: str) -> list[str]:
        """
        Return the column names of *table_name* from ``information_schema``.

        We query ``information_schema.columns`` with a parameterised
        ``table_name`` so that the table name is treated as a value (safe from
        injection), and also filter by ``table_schema = 'public'`` to avoid
        returning columns from system schemas when the DB name happens to
        match an internal table name.

        Parameters
        ----------
        connection:
            An open psycopg3 connection.
        table_name:
            The table to inspect.

        Returns
        -------
        list[str]
            Column names in ordinal position order.
        """
        sql = (
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s "
            "ORDER BY ordinal_position;"
        )
        with connection.cursor() as cur:
            cur.execute(sql, (table_name,))
            return [row[0] for row in cur.fetchall()]

    def list_tables(self, connection: Any) -> list[str]:
        """
        Return all user table names in the ``public`` schema.

        Filtering to ``table_schema = 'public'`` excludes PostgreSQL's
        internal ``pg_catalog`` and ``information_schema`` tables, which would
        otherwise appear when the search_path includes them.

        Parameters
        ----------
        connection:
            An open psycopg3 connection.

        Returns
        -------
        list[str]
            Table names.
        """
        sql = (
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' "
            "ORDER BY table_name;"
        )
        with connection.cursor() as cur:
            cur.execute(sql)
            return [row[0] for row in cur.fetchall()]
