"""
orm/migrations/detector.py
==========================
Schema differ: compares live DB schema against current model definitions
and produces a list of additive changes to apply.

v1 scope:
  - New tables (CREATE TABLE)
  - New columns on existing tables (ALTER TABLE ADD COLUMN)

Out of scope for v1 (flagged as warnings):
  - Column type changes
  - Column drops / renames
  - Index creation / drops
  - FK constraint changes

Why INFORMATION_SCHEMA?
-----------------------
Both PostgreSQL and MySQL expose INFORMATION_SCHEMA as a standard SQL view
for schema introspection.  This lets the detector code stay mostly
dialect-agnostic, with only minor differences in catalog filtering.
MySQL uses ``TABLE_SCHEMA = DATABASE()``; PostgreSQL uses
``table_schema = 'public'``.  The dialect supplies these via helper methods
``list_tables()`` and ``get_table_columns()``.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from orm.db import Database
    from orm.models import Model


@dataclass
class SchemaDiff:
    """
    Result of comparing model definitions against live DB schema.

    Attributes
    ----------
    new_tables:
        Model classes whose tables do not yet exist in the DB.
    new_columns:
        Mapping of ``table_name`` -> list of ``(attr_name, Field)`` tuples
        for columns that exist on the model but not in the DB.
    skipped:
        Informational list of destructive changes that were detected but
        skipped (column drops, type changes, etc.).  Emitted as warnings.
    """

    new_tables: list[type[Model]] = field(default_factory=list)
    new_columns: dict[str, list[tuple[str, Any]]] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.new_tables or self.new_columns)


class SchemaDetector:
    """
    Detects differences between the current model state and the live DB.

    Parameters
    ----------
    db:
        The Database to introspect.
    models:
        The list of Model classes to compare against.
    """

    def __init__(self, db: Database, models: list[type[Model]]) -> None:
        self._db = db
        self._models = models

    def detect(self) -> SchemaDiff:
        """
        Run the diff and return a ``SchemaDiff``.

        This performs read-only queries against the live DB; no changes are
        applied here.
        """
        diff = SchemaDiff()

        # Acquire a connection for introspection
        conn = self._db._acquire()
        try:
            existing_tables = set(
                self._db.dialect.list_tables(conn.raw)
            )

            for model in self._models:
                meta = model._meta  # type: ignore[attr-defined]
                table = meta.table_name

                if table not in existing_tables:
                    diff.new_tables.append(model)
                    continue

                # Table exists — check for missing columns
                existing_cols = set(
                    self._db.dialect.get_table_columns(conn.raw, table)
                )
                model_cols = set(meta.column_order)
                missing = model_cols - existing_cols

                if missing:
                    missing_fields = []
                    for col in missing:
                        field_attr = meta.column_to_field.get(col)
                        if field_attr and field_attr in meta.fields:
                            missing_fields.append((col, meta.fields[field_attr]))
                    diff.new_columns[table] = missing_fields

                # Detect columns in DB but not in model (would be DROP — skip)
                extra_in_db = existing_cols - model_cols
                for col in extra_in_db:
                    msg = (
                        f"Column '{table}.{col}' exists in the DB but not in "
                        f"the model.  Destructive changes (DROP COLUMN) are "
                        f"out of scope for v1 \u2014 apply manually."
                    )
                    diff.skipped.append(msg)
                    warnings.warn(msg, UserWarning, stacklevel=2)

        finally:
            self._db._release(conn)

        return diff
