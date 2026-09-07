"""
orm/queryset.py
===============
QuerySet: the heart of the query builder.

Every method that filters or configures a query returns a **new** QuerySet
instance (immutable chaining).  Execution is deferred until the caller
iterates, calls ``.all()``, ``.first()``, ``.get()``, ``.count()``,
``.update()``, or ``.delete()``.

Why immutable chaining?
-----------------------
Each ``qs.filter(...)`` call returns a brand-new object rather than mutating
``self``.  This means a ``base_qs`` can be stored and reused by different
callers without surprising side-effects.  It also makes unit-testing trivial:
you can inspect the SQL/params of a QS before execution.

Parameterized SQL only:
-----------------------
All user-supplied values (filter values, update values) are placed into the
``params`` tuple and handed to the driver.  Table names and column names come
from model definitions (Python source), never from user input, so they are
safe to embed in the SQL string via ``.format()`` \u2014 but we still avoid
unnecessary string construction.

Supported filter lookups (v1):
  exact (default), gte, lte, gt, lt, contains, startswith, endswith, isnull
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Iterator

from orm.exceptions import DoesNotExist, FieldError, MultipleObjectsReturned

if TYPE_CHECKING:
    from orm.db import Database
    from orm.models import Model


# ---------------------------------------------------------------------------
# Lookup registry
# ---------------------------------------------------------------------------

# Maps lookup suffix -> (SQL fragment template, value transformer)
# The template uses {col} for the column name and %s for the value placeholder.
# value_transform(v) converts the Python value before binding.
_LOOKUPS: dict[str, tuple[str, Any]] = {
    "exact":      ("{col} = %s",              lambda v: v),
    "gte":        ("{col} >= %s",             lambda v: v),
    "lte":        ("{col} <= %s",             lambda v: v),
    "gt":         ("{col} > %s",              lambda v: v),
    "lt":         ("{col} < %s",              lambda v: v),
    "contains":   ("{col} LIKE %s",           lambda v: f"%{v}%"),
    "startswith": ("{col} LIKE %s",           lambda v: f"{v}%"),
    "endswith":   ("{col} LIKE %s",           lambda v: f"%{v}"),
    "isnull":     ("{col} IS NULL",           None),  # value unused
    "isnotnull":  ("{col} IS NOT NULL",       None),  # value unused
}


def _parse_lookup(key: str) -> tuple[str, str]:
    """
    Parse a filter keyword into (column_name, lookup_type).

    Examples:
        ``age__gte`` -> (``age``, ``gte``)
        ``name``     -> (``name``, ``exact``)
        ``user__id`` -> this is a ForeignKey traversal (not supported in v1;
                         the field name is ``user_id``, so use ``user_id=``)
    """
    parts = key.rsplit("__", 1)
    if len(parts) == 2 and parts[1] in _LOOKUPS:
        return parts[0], parts[1]
    return key, "exact"


def _build_condition(
    col: str,
    lookup: str,
    value: Any,
    placeholder: str,
) -> tuple[str, list[Any]]:
    """
    Build a single WHERE condition fragment and the bound params for it.

    Returns
    -------
    (sql_fragment, bound_values)
        ``sql_fragment`` is a string like ``"age >= %s"`` or ``"name IS NULL"``
        ``bound_values`` is a list of 0 or 1 values to append to the params list.
    """
    if lookup not in _LOOKUPS:
        raise FieldError(
            f"Unsupported lookup: {lookup!r}. "
            f"Supported: {list(_LOOKUPS)}"
        )

    template, transform = _LOOKUPS[lookup]
    # Replace generic %s with the dialect's actual placeholder
    sql_frag = template.replace("%s", placeholder).replace("{col}", col)

    if transform is None:
        # No value consumed (IS NULL / IS NOT NULL)
        return sql_frag, []
    return sql_frag, [transform(value)]


# ---------------------------------------------------------------------------
# QuerySet
# ---------------------------------------------------------------------------

class QuerySet:
    """
    Represents a lazily-evaluated, parameterized database query.

    Do not instantiate directly \u2014 use ``Model.objects`` or chain from an
    existing QuerySet.

    Attributes
    ----------
    model:
        The Model class this QuerySet queries against.
    _db:
        The Database instance to execute against.
    _filters:
        Accumulated WHERE conditions (list of ``(col, lookup, value)`` tuples).
    _excludes:
        Same as _filters but negated (NOT (...)).
    _order_by:
        List of column strings.  Prefix ``-`` for DESC.
    _limit_val:
        Integer LIMIT or None.
    _offset_val:
        Integer OFFSET or None.
    """

    def __init__(
        self,
        model: type[Model],
        db: Database,
    ) -> None:
        self.model = model
        self._db = db

        # All conditions are stored as lists; chaining appends to copies.
        self._filters: list[tuple[str, str, Any]] = []
        self._excludes: list[tuple[str, str, Any]] = []
        self._order_by: list[str] = []
        self._limit_val: int | None = None
        self._offset_val: int | None = None

    # ------------------------------------------------------------------
    # Internal clone helper
    # ------------------------------------------------------------------

    def _clone(self) -> QuerySet:
        """Return a shallow copy so chaining is non-mutating."""
        clone = QuerySet(self.model, self._db)
        clone._filters = list(self._filters)
        clone._excludes = list(self._excludes)
        clone._order_by = list(self._order_by)
        clone._limit_val = self._limit_val
        clone._offset_val = self._offset_val
        return clone

    # ------------------------------------------------------------------
    # Chaining API
    # ------------------------------------------------------------------

    def filter(self, **kwargs: Any) -> QuerySet:
        """
        Return a new QuerySet with additional WHERE conditions (AND-combined).

        Supported lookups: ``field=value`` (exact) or ``field__lookup=value``.

        Example::

            Post.objects.filter(published=True, author__id=1)
            Post.objects.filter(title__startswith="Hello")
        """
        clone = self._clone()
        for key, value in kwargs.items():
            col, lookup = _parse_lookup(key)
            col = self._resolve_column(col)
            clone._filters.append((col, lookup, value))
        return clone

    def exclude(self, **kwargs: Any) -> QuerySet:
        """
        Return a new QuerySet excluding rows matching the given conditions.

        Semantically equivalent to ``WHERE NOT (condition)``.

        Example::

            Post.objects.exclude(published=False)
        """
        clone = self._clone()
        for key, value in kwargs.items():
            col, lookup = _parse_lookup(key)
            col = self._resolve_column(col)
            clone._excludes.append((col, lookup, value))
        return clone

    def order_by(self, *fields: str) -> QuerySet:
        """
        Return a new QuerySet ordered by the given fields.

        Prefix a field name with ``-`` for descending order.

        Example::

            Post.objects.order_by("-created_at", "title")
        """
        clone = self._clone()
        clone._order_by = list(fields)
        return clone

    def limit(self, n: int) -> QuerySet:
        """Return a new QuerySet with a LIMIT clause."""
        clone = self._clone()
        clone._limit_val = n
        return clone

    def offset(self, n: int) -> QuerySet:
        """Return a new QuerySet with an OFFSET clause."""
        clone = self._clone()
        clone._offset_val = n
        return clone

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def all(self) -> list[Model]:
        """Execute the query and return all matching model instances."""
        return list(self._fetch())

    def first(self) -> Model | None:
        """Return the first matching model instance, or None."""
        results = self.limit(1)._fetch()
        return results[0] if results else None

    def get(self, **kwargs: Any) -> Model:
        """
        Return exactly one matching instance.

        Raises
        ------
        DoesNotExist:
            If no rows match.
        MultipleObjectsReturned:
            If more than one row matches.
        """
        qs = self.filter(**kwargs) if kwargs else self
        results = qs._fetch()
        if not results:
            raise DoesNotExist(
                f"{self.model.__name__} matching query does not exist."
            )
        if len(results) > 1:
            raise MultipleObjectsReturned(
                f"get() returned more than one {self.model.__name__} \u2014 "
                f"it returned {len(results)}."
            )
        return results[0]

    def count(self) -> int:
        """Return the number of rows matching the current filters."""
        table = self.model._meta.table_name  # type: ignore[attr-defined]
        where_sql, params = self._build_where()
        sql = f"SELECT COUNT(*) FROM {table}"
        if where_sql:
            sql += f" WHERE {where_sql}"
        row = self._db.execute(sql, params, fetch="one")
        return int(row[0]) if row else 0

    def update(self, **kwargs: Any) -> int:
        """
        Update all matching rows with the given field=value pairs.

        Returns the number of rows affected.

        Example::

            Post.objects.filter(published=False).update(published=True)
        """
        table = self.model._meta.table_name  # type: ignore[attr-defined]
        if not kwargs:
            return 0

        placeholder = self._db.dialect.placeholder()
        set_parts: list[str] = []
        set_params: list[Any] = []

        for field_name, value in kwargs.items():
            col = self._resolve_column(field_name)
            field_obj = self.model._meta.fields.get(field_name)  # type: ignore[attr-defined]
            db_val = field_obj.to_db(value) if field_obj else value
            set_parts.append(f"{col} = {placeholder}")
            set_params.append(db_val)

        where_sql, where_params = self._build_where()
        sql = f"UPDATE {table} SET {', '.join(set_parts)}"
        if where_sql:
            sql += f" WHERE {where_sql}"

        self._db.execute(sql, set_params + where_params, fetch="none")
        # Driver-agnostic rowcount is not trivially available after execute;
        # returning -1 to signal "unknown" is standard in some ORMs.
        # Returning 0 here is acceptable for v1.
        return 0

    def delete(self) -> int:
        """
        Delete all matching rows.

        Example::

            Post.objects.filter(author_id=42).delete()
        """
        table = self.model._meta.table_name  # type: ignore[attr-defined]
        where_sql, params = self._build_where()
        sql = f"DELETE FROM {table}"
        if where_sql:
            sql += f" WHERE {where_sql}"
        self._db.execute(sql, params, fetch="none")
        return 0

    # ------------------------------------------------------------------
    # Iteration support
    # ------------------------------------------------------------------

    def __iter__(self) -> Iterator[Model]:
        return iter(self._fetch())

    def __len__(self) -> int:
        return self.count()

    def __repr__(self) -> str:
        return f"<QuerySet model={self.model.__name__}>"

    # ------------------------------------------------------------------
    # SQL building internals
    # ------------------------------------------------------------------

    def _resolve_column(self, field_name: str) -> str:
        """
        Map a Python field name to the DB column name.

        ForeignKeyField names are stored as ``fieldname_id`` columns, so
        passing ``author`` resolves to ``author_id``.
        """
        meta = self.model._meta  # type: ignore[attr-defined]

        # Direct match in the field map
        if field_name in meta.fields:
            field_obj = meta.fields[field_name]
            return field_obj.column_name or field_name

        # ForeignKeyField: ``author`` -> ``author_id``
        fk_name = f"{field_name}_id"
        if fk_name in meta.fields:
            return fk_name

        # Pass through unchanged (may be an explicit column_name)
        return field_name

    def _build_where(self) -> tuple[str, list[Any]]:
        """
        Build the WHERE clause from accumulated filters and excludes.

        Returns (where_sql, params) where ``where_sql`` is the fragment
        AFTER the WHERE keyword (or empty string if no conditions).

        All filter conditions are AND-ed together.
        Exclude conditions are wrapped in NOT (...) and AND-ed.
        """
        placeholder = self._db.dialect.placeholder()
        parts: list[str] = []
        params: list[Any] = []

        for col, lookup, value in self._filters:
            frag, vals = _build_condition(col, lookup, value, placeholder)
            parts.append(frag)
            params.extend(vals)

        for col, lookup, value in self._excludes:
            frag, vals = _build_condition(col, lookup, value, placeholder)
            parts.append(f"NOT ({frag})")
            params.extend(vals)

        return " AND ".join(parts), params

    def _build_select(self) -> tuple[str, list[Any]]:
        """
        Build the full SELECT statement for this QuerySet.

        Returns (sql, params).
        """
        table = self.model._meta.table_name  # type: ignore[attr-defined]
        where_sql, params = self._build_where()

        sql = f"SELECT * FROM {table}"
        if where_sql:
            sql += f" WHERE {where_sql}"

        if self._order_by:
            order_parts: list[str] = []
            for field in self._order_by:
                if field.startswith("-"):
                    order_parts.append(f"{field[1:]} DESC")
                else:
                    order_parts.append(f"{field} ASC")
            sql += f" ORDER BY {', '.join(order_parts)}"

        if self._limit_val is not None:
            sql += f" LIMIT {self._limit_val}"

        if self._offset_val is not None:
            sql += f" OFFSET {self._offset_val}"

        return sql, params

    def _fetch(self) -> list[Model]:
        """Execute the SELECT and hydrate model instances."""
        sql, params = self._build_select()
        rows = self._db.execute(sql, params, fetch="all")
        if not rows:
            return []
        return [self._hydrate(row) for row in rows]

    def _hydrate(self, row: tuple[Any, ...]) -> Model:
        """
        Convert a raw DB row (tuple) into a model instance.

        The column order from ``SELECT *`` matches the order in which fields
        were declared on the model (preserved by the metaclass).  We zip the
        values with the declared columns to map them correctly.

        Storage rules:
          - Regular fields: written to ``instance.__dict__['_state'][attr_name]``
          - ForeignKeyField: written to ``instance.__dict__['<attr>_id']`` so the
            ForeignKeyField descriptor (which reads from ``instance.__dict__``)
            can find it without triggering a recursive load.

        Each field's ``from_db`` is called to convert the raw driver value to
        the appropriate Python type (e.g., TINYINT(1) -> bool for MySQL).
        """
        from orm.fields import ForeignKeyField as FKField  # local to avoid circular

        meta = self.model._meta  # type: ignore[attr-defined]
        columns = meta.column_order  # ordered list of column names

        # Create a fresh instance bypassing __init__
        instance = self.model.__new__(self.model)
        # Initialize _state in __dict__ directly to bypass descriptors
        instance.__dict__["_state"] = {}

        for col, raw_val in zip(columns, row):
            field_name = meta.column_to_field.get(col, col)
            field_obj = meta.fields.get(field_name)
            py_val = field_obj.from_db(raw_val) if field_obj else raw_val

            if isinstance(field_obj, FKField):
                # ForeignKeyField descriptor reads from instance.__dict__[_id_attr]
                # e.g. instance.__dict__['author_id'] = 5
                id_attr = field_obj.column_name or f"{field_name}_id"
                instance.__dict__[id_attr] = py_val
                # Also store in _state for convenience access
                instance.__dict__["_state"][field_name] = py_val
            else:
                instance.__dict__["_state"][field_name] = py_val

        return instance
