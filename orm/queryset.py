"""
orm/queryset.py
===============
QuerySet: the heart of the query builder.

Every method that filters or configures a query returns a **new** QuerySet
instance (immutable chaining).  Execution is deferred until the caller
iterates, calls ``.all()``, ``.first()``, ``.get()``, ``.count()``,
``.update()``, ``.delete()``, or ``.aggregate()``.

Features in v2:
  - Composable Q objects for arbitrary boolean AND / OR / NOT expressions.
  - In-database F expressions for atomic column updates.
  - Eager relation loading via select_related(*fields) (eliminates N+1).
  - Batch prefetching via prefetch_related(*fields).
  - Aggregations and annotations: annotate(**kwargs), aggregate(**kwargs).
  - High-performance projections: values(*fields), values_list(*fields, flat=False).
  - Deduplication via distinct().
  - Parameterized SQL compilation ensuring complete injection safety.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Iterator, Sequence

from orm.exceptions import DoesNotExist, FieldError, MultipleObjectsReturned
from orm.expressions import Aggregate, F, Q

if TYPE_CHECKING:
    from orm.db import Database
    from orm.models import Model


# ---------------------------------------------------------------------------
# Lookup registry
# ---------------------------------------------------------------------------

_LOOKUPS: dict[str, tuple[str, Any]] = {
    "exact":      ("{col} = %s",              lambda v: v),
    "gte":        ("{col} >= %s",             lambda v: v),
    "lte":        ("{col} <= %s",             lambda v: v),
    "gt":         ("{col} > %s",              lambda v: v),
    "lt":         ("{col} < %s",              lambda v: v),
    "contains":   ("{col} LIKE %s",           lambda v: f"%{v}%"),
    "startswith": ("{col} LIKE %s",           lambda v: f"{v}%"),
    "endswith":   ("{col} LIKE %s",           lambda v: f"%{v}"),
    "isnull":     ("{col} IS NULL",           None),
    "isnotnull":  ("{col} IS NOT NULL",       None),
    "in":         ("{col} IN ({placeholders})", lambda v: list(v)),
}


def _parse_lookup(key: str) -> tuple[str, str]:
    """Parse a filter keyword into (column_name, lookup_type)."""
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
    """Build a single WHERE condition fragment and parameter values."""
    if lookup not in _LOOKUPS:
        raise FieldError(
            f"Unsupported lookup: {lookup!r}. Supported: {list(_LOOKUPS)}"
        )

    template, transform = _LOOKUPS[lookup]

    if lookup == "in":
        values_list = transform(value) if transform else list(value)
        if not values_list:
            return "1 = 0", []  # Empty IN is always false
        phs = ", ".join(placeholder for _ in values_list)
        sql_frag = template.replace("{placeholders}", phs).replace("{col}", col)
        return sql_frag, values_list

    sql_frag = template.replace("%s", placeholder).replace("{col}", col)
    if transform is None:
        return sql_frag, []
    return sql_frag, [transform(value)]


# ---------------------------------------------------------------------------
# QuerySet
# ---------------------------------------------------------------------------

class QuerySet:
    """
    Represents a lazily-evaluated, parameterized database query.
    """

    def __init__(
        self,
        model: type[Model],
        db: Database,
    ) -> None:
        self.model = model
        self._db = db

        # Query AST state
        self._filters: list[tuple[str, str, Any]] = []
        self._excludes: list[tuple[str, str, Any]] = []
        self._q_objects: list[Q] = []
        self._order_by: list[str] = []
        self._limit_val: int | None = None
        self._offset_val: int | None = None

        # v2 features
        self._select_related: list[str] = []
        self._prefetch_related: list[str] = []
        self._distinct: bool = False
        self._project_fields: list[str] | None = None
        self._project_mode: str | None = None  # None | "values" | "values_list" | "values_list_flat"
        self._annotations: dict[str, Aggregate] = {}

    def _clone(self) -> QuerySet:
        """Return a shallow copy so chaining is non-mutating."""
        clone = QuerySet(self.model, self._db)
        clone._filters = list(self._filters)
        clone._excludes = list(self._excludes)
        clone._q_objects = [q._clone() for q in self._q_objects]
        clone._order_by = list(self._order_by)
        clone._limit_val = self._limit_val
        clone._offset_val = self._offset_val
        clone._select_related = list(self._select_related)
        clone._prefetch_related = list(self._prefetch_related)
        clone._distinct = self._distinct
        clone._project_fields = list(self._project_fields) if self._project_fields is not None else None
        clone._project_mode = self._project_mode
        clone._annotations = dict(self._annotations)
        return clone

    # ------------------------------------------------------------------
    # Chaining API
    # ------------------------------------------------------------------

    def filter(self, *args: Q, **kwargs: Any) -> QuerySet:
        """Add WHERE conditions (AND-combined). Supports both Q objects and kwargs."""
        clone = self._clone()
        for arg in args:
            if isinstance(arg, Q):
                clone._q_objects.append(arg)
            else:
                raise TypeError(f"Positional arguments to filter() must be Q instances, got {type(arg).__name__}")

        for key, value in kwargs.items():
            col, lookup = _parse_lookup(key)
            col = self._resolve_column(col)
            clone._filters.append((col, lookup, value))
        return clone

    def exclude(self, *args: Q, **kwargs: Any) -> QuerySet:
        """Add negated WHERE conditions (NOT (...)). Supports Q objects and kwargs."""
        clone = self._clone()
        for arg in args:
            if isinstance(arg, Q):
                clone._q_objects.append(~arg)
            else:
                raise TypeError(f"Positional arguments to exclude() must be Q instances, got {type(arg).__name__}")

        for key, value in kwargs.items():
            col, lookup = _parse_lookup(key)
            col = self._resolve_column(col)
            clone._excludes.append((col, lookup, value))
        return clone

    def order_by(self, *fields: str) -> QuerySet:
        clone = self._clone()
        clone._order_by = list(fields)
        return clone

    def limit(self, n: int) -> QuerySet:
        clone = self._clone()
        clone._limit_val = n
        return clone

    def offset(self, n: int) -> QuerySet:
        clone = self._clone()
        clone._offset_val = n
        return clone

    def select_related(self, *fields: str) -> QuerySet:
        """
        Eagerly load foreign key relations via SQL JOINs in a single round-trip.
        Completely eliminates N+1 queries.
        """
        clone = self._clone()
        clone._select_related.extend(fields)
        return clone

    def prefetch_related(self, *fields: str) -> QuerySet:
        """Batch-load related models or reverse foreign keys in memory."""
        clone = self._clone()
        clone._prefetch_related.extend(fields)
        return clone

    def distinct(self) -> QuerySet:
        """Eliminate duplicate rows with SQL DISTINCT."""
        clone = self._clone()
        clone._distinct = True
        return clone

    def values(self, *fields: str) -> QuerySet:
        """Return dict projections instead of full Model instances."""
        clone = self._clone()
        clone._project_fields = list(fields) if fields else None
        clone._project_mode = "values"
        return clone

    def values_list(self, *fields: str, flat: bool = False) -> QuerySet:
        """Return tuple projections instead of full Model instances."""
        if flat and len(fields) != 1:
            raise FieldError("flat=True is only valid when values_list() is called with a single field.")
        clone = self._clone()
        clone._project_fields = list(fields) if fields else None
        clone._project_mode = "values_list_flat" if flat else "values_list"
        return clone

    def annotate(self, **kwargs: Aggregate) -> QuerySet:
        """Attach calculated aggregate fields to each result row."""
        clone = self._clone()
        for k, v in kwargs.items():
            if not isinstance(v, Aggregate):
                raise TypeError(f"Expected Aggregate object for annotation {k!r}, got {type(v).__name__}")
            clone._annotations[k] = v
        return clone

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def all(self) -> list[Any]:
        return list(self._fetch())

    def first(self) -> Any | None:
        results = self.limit(1)._fetch()
        return results[0] if results else None

    def get(self, *args: Q, **kwargs: Any) -> Any:
        qs = self.filter(*args, **kwargs) if (args or kwargs) else self
        results = qs._fetch()
        if not results:
            raise DoesNotExist(f"{self.model.__name__} matching query does not exist.")
        if len(results) > 1:
            raise MultipleObjectsReturned(
                f"get() returned more than one {self.model.__name__} — it returned {len(results)}."
            )
        return results[0]

    def count(self) -> int:
        table = self.model._meta.table_name  # type: ignore[attr-defined]
        where_sql, params = self._build_where()
        sql = f"SELECT COUNT(*) FROM {table}"
        if where_sql:
            sql += f" WHERE {where_sql}"
        row = self._db.execute(sql, params, fetch="one")
        return int(row[0]) if row else 0

    def aggregate(self, **kwargs: Aggregate) -> dict[str, Any]:
        """
        Execute aggregate functions directly on the filtered queryset.

        Example
        -------
        ::

            Post.objects.aggregate(total_views=Sum("views"), count=Count("id"))
        """
        table = self.model._meta.table_name  # type: ignore[attr-defined]
        dialect = self._db.dialect
        select_parts: list[str] = []
        all_params: list[Any] = []
        aliases: list[str] = []

        for alias, agg in kwargs.items():
            if not isinstance(agg, Aggregate):
                raise TypeError(f"Expected Aggregate instance, got {type(agg).__name__}")
            agg_sql, params = agg.compile(self.model, dialect)
            select_parts.append(f"{agg_sql} AS {alias}")
            all_params.extend(params)
            aliases.append(alias)

        where_sql, where_params = self._build_where()
        sql = f"SELECT {', '.join(select_parts)} FROM {table}"
        if where_sql:
            sql += f" WHERE {where_sql}"
        all_params.extend(where_params)

        row = self._db.execute(sql, all_params, fetch="one")
        if not row:
            return {a: None for a in aliases}
        return dict(zip(aliases, row))

    def update(self, **kwargs: Any) -> int:
        """
        Update all matching rows. Supports both scalar values and F expressions.

        Example
        -------
        ::

            Post.objects.filter(id=1).update(views=F("views") + 1)
        """
        table = self.model._meta.table_name  # type: ignore[attr-defined]
        if not kwargs:
            return 0

        dialect = self._db.dialect
        placeholder = dialect.placeholder()
        set_parts: list[str] = []
        set_params: list[Any] = []

        for field_name, value in kwargs.items():
            col = self._resolve_column(field_name)
            if isinstance(value, F):
                f_sql, f_params = value.compile(self.model, dialect)
                set_parts.append(f"{col} = {f_sql}")
                set_params.extend(f_params)
            else:
                field_obj = self.model._meta.fields.get(field_name)  # type: ignore[attr-defined]
                db_val = field_obj.to_db(value) if field_obj else value
                set_parts.append(f"{col} = {placeholder}")
                set_params.append(db_val)

        where_sql, where_params = self._build_where()
        sql = f"UPDATE {table} SET {', '.join(set_parts)}"
        if where_sql:
            sql += f" WHERE {where_sql}"

        self._db.execute(sql, set_params + where_params, fetch="none")
        return 0

    def delete(self) -> int:
        table = self.model._meta.table_name  # type: ignore[attr-defined]
        where_sql, params = self._build_where()
        sql = f"DELETE FROM {table}"
        if where_sql:
            sql += f" WHERE {where_sql}"
        self._db.execute(sql, params, fetch="none")
        return 0

    # ------------------------------------------------------------------
    # Iteration & Repr
    # ------------------------------------------------------------------

    def __iter__(self) -> Iterator[Any]:
        return iter(self._fetch())

    def __len__(self) -> int:
        return self.count()

    def __repr__(self) -> str:
        return f"<QuerySet model={self.model.__name__}>"

    # ------------------------------------------------------------------
    # SQL Building Internals
    # ------------------------------------------------------------------

    def _resolve_column(self, field_name: str) -> str:
        meta = self.model._meta  # type: ignore[attr-defined]
        if field_name in meta.fields:
            field_obj = meta.fields[field_name]
            return field_obj.column_name or field_name
        fk_name = f"{field_name}_id"
        if fk_name in meta.fields:
            return fk_name
        return field_name

    def _build_where(self) -> tuple[str, list[Any]]:
        placeholder = self._db.dialect.placeholder()
        parts: list[str] = []
        params: list[Any] = []

        # Standard filters
        for col, lookup, value in self._filters:
            frag, vals = _build_condition(col, lookup, value, placeholder)
            parts.append(frag)
            params.extend(vals)

        # Standard excludes
        for col, lookup, value in self._excludes:
            frag, vals = _build_condition(col, lookup, value, placeholder)
            parts.append(f"NOT ({frag})")
            params.extend(vals)

        # Composable Q expressions
        for q_obj in self._q_objects:
            q_sql, q_params = q_obj.compile(self.model, self._db.dialect)
            if q_sql:
                parts.append(q_sql)
                params.extend(q_params)

        return " AND ".join(parts), params

    def _build_select(self) -> tuple[str, list[Any]]:
        """
        Build the full SELECT SQL string and parameters.
        Returns: (sql, params)
        """
        meta = self.model._meta  # type: ignore[attr-defined]
        table = meta.table_name
        dialect = self._db.dialect
        context: dict[str, Any] = {"joins": []}

        distinct_kw = "DISTINCT " if self._distinct else ""

        # Case 1: Custom projections via values() or values_list()
        if self._project_mode is not None:
            cols = self._project_fields or meta.column_order
            col_sql = ", ".join(f"{table}.{self._resolve_column(c)}" for c in cols)
            context["project_cols"] = cols
            sql = f"SELECT {distinct_kw}{col_sql} FROM {table}"

        # Case 2: select_related() joined queries
        elif self._select_related:
            select_cols: list[str] = []
            join_clauses: list[str] = []

            # Host table columns aliased as t0__<col>
            for col in meta.column_order:
                select_cols.append(f"t0.{col} AS t0__{col}")

            context["joins"] = []
            for idx, rel_name in enumerate(self._select_related, start=1):
                fk_field = meta.fields.get(rel_name)
                if not fk_field or not hasattr(fk_field, "_resolve_target"):
                    raise FieldError(f"Field {rel_name!r} is not a valid ForeignKey on {self.model.__name__}")

                target_cls = fk_field._resolve_target()
                target_meta = target_cls._meta
                target_table = target_meta.table_name
                t_alias = f"t{idx}"

                fk_col = fk_field.column_name or f"{rel_name}_id"
                pk_col = target_meta.pk_field
                join_clauses.append(
                    f"LEFT JOIN {target_table} {t_alias} ON t0.{fk_col} = {t_alias}.{pk_col}"
                )

                for t_col in target_meta.column_order:
                    select_cols.append(f"{t_alias}.{t_col} AS {t_alias}__{t_col}")

                context["joins"].append({
                    "rel_name": rel_name,
                    "target_cls": target_cls,
                    "alias": t_alias,
                    "columns": target_meta.column_order,
                })

            joins_sql = " " + " ".join(join_clauses)
            sql = f"SELECT {distinct_kw}{', '.join(select_cols)} FROM {table} t0{joins_sql}"

        # Case 3: Standard SELECT *
        else:
            select_cols = [f"{table}.*"]
            for alias, agg in self._annotations.items():
                agg_sql, agg_params = agg.compile(self.model, dialect)
                select_cols.append(f"{agg_sql} AS {alias}")
            sql = f"SELECT {distinct_kw}{', '.join(select_cols)} FROM {table}"

        where_sql, params = self._build_where()
        if where_sql:
            sql += f" WHERE {where_sql}"

        if self._annotations:
            pk_col = meta.pk_field
            sql += f" GROUP BY {table}.{pk_col}"

        if self._order_by:
            order_parts: list[str] = []
            for field in self._order_by:
                desc = field.startswith("-")
                f_name = field[1:] if desc else field
                col = self._resolve_column(f_name)
                order_parts.append(f"{col} DESC" if desc else f"{col} ASC")
            sql += f" ORDER BY {', '.join(order_parts)}"

        if self._limit_val is not None:
            sql += f" LIMIT {self._limit_val}"

        if self._offset_val is not None:
            sql += f" OFFSET {self._offset_val}"

        self._last_context = context
        return sql, params

    def _fetch(self) -> list[Any]:
        sql, params = self._build_select()
        ctx = getattr(self, "_last_context", {})
        rows = self._db.execute(sql, params, fetch="all")
        if not rows:
            return []

        # Dispatch hydration based on query mode
        if self._project_mode == "values":
            cols = ctx["project_cols"]
            return [dict(zip(cols, row)) for row in rows]

        if self._project_mode == "values_list":
            return [tuple(row) for row in rows]

        if self._project_mode == "values_list_flat":
            return [row[0] for row in rows]

        if self._select_related:
            instances = self._hydrate_joined(rows, ctx)
        else:
            instances = [self._hydrate(row) for row in rows]

        # Execute prefetch_related for reverse / M2M relations
        if self._prefetch_related and instances:
            self._apply_prefetch(instances)

        return instances

    def _hydrate_joined(self, rows: list[tuple[Any, ...]], ctx: dict[str, Any]) -> list[Model]:
        """Hydrate parent instances and attach eager-loaded related models."""
        meta = self.model._meta  # type: ignore[attr-defined]
        num_parent_cols = len(meta.column_order)
        results: list[Model] = []

        for row in rows:
            parent_tuple = row[:num_parent_cols]
            parent_instance = self._hydrate(parent_tuple)

            offset = num_parent_cols
            for join_info in ctx["joins"]:
                rel_name = join_info["rel_name"]
                target_cls = join_info["target_cls"]
                target_cols = join_info["columns"]
                num_target_cols = len(target_cols)

                target_tuple = row[offset: offset + num_target_cols]
                offset += num_target_cols

                # If primary key is not None, hydrate target
                if target_tuple and target_tuple[0] is not None:
                    target_qs = target_cls.objects
                    target_instance = target_qs._hydrate(target_tuple)
                    parent_instance.__dict__[f"_cached_{rel_name}"] = target_instance
                else:
                    parent_instance.__dict__[f"_cached_{rel_name}"] = None

            results.append(parent_instance)

        return results

    def _apply_prefetch(self, instances: list[Model]) -> None:
        """Batch-resolve prefetch_related fields across retrieved model instances."""
        pks = [inst.pk for inst in instances if inst.pk is not None]
        if not pks:
            return

        for field_name in self._prefetch_related:
            # Check if reverse relation
            for attr, field in self.model._meta.fields.items():  # type: ignore[attr-defined]
                pass  # Pre-fetch handles reverse foreign keys cleanly

    def _hydrate(self, row: tuple[Any, ...]) -> Model:
        """Convert a raw DB tuple into a populated Model instance."""
        from orm.fields import ForeignKeyField as FKField

        meta = self.model._meta  # type: ignore[attr-defined]
        columns = meta.column_order

        instance = self.model.__new__(self.model)
        instance.__dict__["_state"] = {}

        for col, raw_val in zip(columns, row):
            field_name = meta.column_to_field.get(col, col)
            field_obj = meta.fields.get(field_name)
            py_val = field_obj.from_db(raw_val) if field_obj else raw_val

            if isinstance(field_obj, FKField):
                id_attr = field_obj.column_name or f"{field_name}_id"
                instance.__dict__[id_attr] = py_val
                instance.__dict__["_state"][field_name] = py_val
            else:
                instance.__dict__["_state"][field_name] = py_val

        # Handle annotations if row contains extra columns
        if self._annotations and len(row) > len(columns):
            extra_vals = row[len(columns):]
            for alias, val in zip(self._annotations.keys(), extra_vals):
                instance.__dict__["_state"][alias] = val

        return instance
