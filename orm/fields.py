"""
orm/fields.py
-------------
Field system for custom-orm.

Each field class represents a single column in a database table.  Fields
serve three purposes:

1. **Schema generation** – ``db_type()`` returns the SQL type string that the
   dialect uses when building ``CREATE TABLE`` DDL.

2. **Value coercion** – ``to_db()`` / ``from_db()`` convert between Python
   values and the raw values the DB driver expects / returns.  This is the
   correct place for dialect-specific coercion (e.g. MySQL's TINYINT(1) for
   booleans) so that model code stays dialect-agnostic.

3. **Validation** – ``validate()`` runs before any INSERT or UPDATE to catch
   obvious errors (null on non-nullable field, string too long) at the Python
   level rather than waiting for a DB round-trip.

Declaration order is preserved via ``_creation_counter`` (a module-level
monotonic integer).  Each ``Field.__init__`` increments the counter and stores
the current value on the instance.  The model metaclass reads
``_creation_order`` from each field to sort them back into declaration order,
which is required for reproducible ``CREATE TABLE`` output.

``ForeignKeyField`` uses the Python *descriptor protocol* (``__get__`` /
``__set__``) so that attribute access on a model instance transparently loads
the related object, while the underlying raw integer is always available under
the ``<name>_id`` attribute.  In v1 there is no caching — every access fires
a fresh query.  Caching is left for v2 when a proper identity map is added.
"""

from __future__ import annotations

import datetime
import decimal
import json
import uuid
from typing import TYPE_CHECKING, Any, Callable, Sequence

from orm.exceptions import FieldError

if TYPE_CHECKING:
    # Avoid a circular import at runtime; the model module imports fields.
    from orm.models import Model  # noqa: F401

# Sentinel that distinguishes "no default" from "default is None".
_MISSING: object = object()


class Field:
    """
    Abstract base for all ORM field types.

    Subclasses **must** override ``db_type()``.  ``to_db()``, ``from_db()``,
    and ``validate()`` have sensible defaults (identity / null-check) and may
    be overridden as needed.

    Parameters
    ----------
    null:
        If ``True`` the column is declared ``NULL`` and Python ``None`` is a
        valid value.  Defaults to ``False`` (``NOT NULL``).
    default:
        The Python value to use when no value is supplied for this field.
        Use the sentinel ``...`` (Ellipsis, the default) to mean "no default".
        ``None`` is a *valid* explicit default and is distinct from "no
        default".
    primary_key:
        If ``True`` this column is the table's primary key.  Only one field
        per model should have this set.  Defaults to ``False``.
    column_name:
        The actual database column name.  If ``None`` (the default) the model
        metaclass assigns the Python attribute name as the column name.
    """

    # Module-level monotonic counter used to preserve declaration order.
    # Each instantiation increments this *before* the instance reads it so
    # that the very first field gets counter == 1 (not 0).
    _creation_counter: int = 0

    def __init__(
        self,
        null: bool = False,
        default: Any = _MISSING,
        primary_key: bool = False,
        column_name: str | None = None,
        validators: Sequence[Any] = (),
    ) -> None:
        Field._creation_counter += 1
        # Store the snapshot so the metaclass can sort fields.
        self._creation_order: int = Field._creation_counter

        self.null: bool = null
        self.default: Any = default
        self.primary_key: bool = primary_key
        self.column_name: str | None = column_name
        self.validators: list[Any] = list(validators)

        # Set by the model metaclass once the field is attached to a model.
        self.attname: str = ""  # Python attribute name on the model class
        self.model: type | None = None

    # ------------------------------------------------------------------
    # Subclass interface
    # ------------------------------------------------------------------

    def db_type(self, dialect: str) -> str:
        """
        Return the SQL type string for this field, e.g. ``'INTEGER'``.

        Parameters
        ----------
        dialect:
            Either ``'postgres'`` or ``'mysql'``.  Some fields return
            different SQL types per dialect (e.g. ``BooleanField``).

        Raises
        ------
        NotImplementedError
            Always — subclasses must override this method.
        """
        raise NotImplementedError(
            f"{type(self).__name__} must implement db_type()"
        )

    def to_db(self, value: Any) -> Any:
        """
        Convert a Python value to the form the DB driver expects.

        The default implementation is the identity function.  Override when
        the DB representation differs from Python's (e.g. booleans on MySQL).

        Parameters
        ----------
        value:
            The Python value from the model instance.

        Returns
        -------
        Any
            The value to pass in the params tuple of the SQL statement.
        """
        return value

    def from_db(self, value: Any) -> Any:
        """
        Convert a raw DB value to the appropriate Python type.

        The default implementation is the identity function.  Override when
        the driver returns a type that differs from what Python code expects
        (e.g. returning a ``datetime`` from a string timestamp column).

        Parameters
        ----------
        value:
            Raw value as returned by the DB driver cursor.

        Returns
        -------
        Any
            The Python value to set on the model instance.
        """
        return value

    def validate(self, value: Any) -> None:
        """
        Raise ``FieldError`` if *value* is not acceptable for this field.

        The base implementation only checks the null constraint.  Subclasses
        should call ``super().validate(value)`` first and then add their own
        checks.

        Parameters
        ----------
        value:
            The Python value about to be written to this field.

        Raises
        ------
        FieldError
            If ``null=False`` and *value* is ``None``.
        """
        if value is None and not self.null:
            raise FieldError(
                f"Field '{self.attname}' does not allow null values."
            )
        if value is not None:
            for validator in self.validators:
                validator(value)

    def __repr__(self) -> str:
        return (
            f"<{type(self).__name__} attname={self.attname!r} "
            f"null={self.null} pk={self.primary_key}>"
        )


# ---------------------------------------------------------------------------
# Concrete field types
# ---------------------------------------------------------------------------


class IntegerField(Field):
    """
    Maps to ``INTEGER`` on both PostgreSQL and MySQL.

    Suitable for whole-number values including foreign-key surrogates.
    When ``primary_key=True`` the dialect will render it as ``SERIAL PRIMARY
    KEY`` (Postgres) or ``INT AUTO_INCREMENT PRIMARY KEY`` (MySQL) so you
    never need to supply an explicit default — the DB generates it.

    Parameters
    ----------
    null, default, primary_key, column_name:
        Inherited from ``Field``.
    """

    def db_type(self, dialect: str) -> str:
        """Return ``'INTEGER'`` for both dialects."""
        return "INTEGER"

    def validate(self, value: Any) -> None:
        """
        Extend base null check with an integer type check.

        We allow ``None`` through if ``null=True`` so the base class handles
        that case before we try to call ``isinstance``.
        """
        super().validate(value)
        if value is not None and not isinstance(value, int):
            raise FieldError(
                f"Field '{self.attname}' expects an int, got {type(value).__name__}."
            )


class CharField(Field):
    """
    Maps to ``VARCHAR(max_length)``.

    Use ``TextField`` when the maximum length is unknown or very large.

    Parameters
    ----------
    max_length:
        Maximum number of characters.  Required — omitting it raises
        ``FieldError`` immediately at class definition time.
    null, default, primary_key, column_name:
        Inherited from ``Field``.
    """

    def __init__(self, max_length: int, **kwargs: Any) -> None:
        if not isinstance(max_length, int) or max_length <= 0:
            raise FieldError(
                "CharField requires a positive integer 'max_length'."
            )
        super().__init__(**kwargs)
        self.max_length: int = max_length

    def db_type(self, dialect: str) -> str:
        """Return ``'VARCHAR(n)'`` where *n* is ``max_length``."""
        return f"VARCHAR({self.max_length})"

    def validate(self, value: Any) -> None:
        """
        Check null constraint then enforce max_length.

        We intentionally do NOT silently truncate strings; exceeding
        ``max_length`` is always a programming or data-quality error.
        """
        super().validate(value)
        if value is not None:
            if not isinstance(value, str):
                raise FieldError(
                    f"Field '{self.attname}' expects a str, got {type(value).__name__}."
                )
            if len(value) > self.max_length:
                raise FieldError(
                    f"Field '{self.attname}' value exceeds max_length "
                    f"({len(value)} > {self.max_length})."
                )


class TextField(Field):
    """
    Maps to ``TEXT`` on both dialects.

    Prefer this over ``CharField`` when there is no meaningful upper bound on
    the string length (e.g. article body, JSON blobs stored as text).
    ``TEXT`` columns cannot be indexed directly in some DB configurations, so
    use ``CharField`` when the column needs an index.

    Parameters
    ----------
    null, default, primary_key, column_name:
        Inherited from ``Field``.
    """

    def db_type(self, dialect: str) -> str:
        """Return ``'TEXT'`` for both dialects."""
        return "TEXT"

    def validate(self, value: Any) -> None:
        """Check null constraint and that the value is a string."""
        super().validate(value)
        if value is not None and not isinstance(value, str):
            raise FieldError(
                f"Field '{self.attname}' expects a str, got {type(value).__name__}."
            )


class BooleanField(Field):
    """
    Maps to ``BOOLEAN`` on PostgreSQL and ``TINYINT(1)`` on MySQL.

    MySQL historically lacked a native ``BOOLEAN`` type; ``TINYINT(1)`` is
    the universally understood convention (``1`` = True, ``0`` = False).
    ``to_db`` / ``from_db`` handle the int↔bool translation for MySQL
    transparently so model code always works with Python ``bool`` values.

    Parameters
    ----------
    null, default, primary_key, column_name:
        Inherited from ``Field``.
    """

    def db_type(self, dialect: str) -> str:
        """
        Return the dialect-appropriate boolean type.

        - ``'postgres'`` → ``'BOOLEAN'``
        - ``'mysql'``    → ``'TINYINT(1)'``
        """
        if dialect == "mysql":
            return "TINYINT(1)"
        return "BOOLEAN"

    def to_db(self, value: Any) -> Any:
        """
        Convert Python bool to the DB representation.

        For MySQL we store ``1`` / ``0`` because the driver may not accept
        Python ``bool`` directly in all configurations.  For Postgres the
        driver handles Python ``bool`` natively so we pass it through.
        """
        if value is None:
            return None
        # Always store as int so behaviour is consistent across drivers.
        return 1 if value else 0

    def from_db(self, value: Any) -> Any:
        """
        Convert the raw DB value back to Python ``bool``.

        Postgres returns a native Python ``bool``; MySQL returns ``0``/``1``.
        ``bool(value)`` handles both cases correctly.
        """
        if value is None:
            return None
        return bool(value)

    def validate(self, value: Any) -> None:
        """Check null constraint and that the value is bool-like."""
        super().validate(value)
        if value is not None and not isinstance(value, (bool, int)):
            raise FieldError(
                f"Field '{self.attname}' expects a bool, got {type(value).__name__}."
            )


class DateTimeField(Field):
    """
    Maps to ``TIMESTAMP`` (Postgres) or ``DATETIME`` (MySQL).

    The slight naming difference is handled at the dialect layer;
    ``DateTimeField.db_type()`` always returns ``'TIMESTAMP'`` and the
    MySQL dialect's ``column_ddl()`` overrides it to ``DATETIME``.

    ``to_db`` accepts a ``datetime.datetime`` and passes it straight through —
    both psycopg3 and PyMySQL accept Python datetimes natively.  ``from_db``
    ensures the returned value is always a ``datetime.datetime``, converting
    strings if the driver returns one (older MySQL drivers occasionally do).

    Parameters
    ----------
    null, default, primary_key, column_name:
        Inherited from ``Field``.
    """

    def db_type(self, dialect: str) -> str:
        """
        Return the timestamp type for the given dialect.

        - ``'postgres'`` → ``'TIMESTAMP'``
        - ``'mysql'``    → ``'DATETIME'`` (handled here to keep dialect.py
          focused on structural DDL rather than type overrides)
        """
        if dialect == "mysql":
            return "DATETIME"
        return "TIMESTAMP"

    def to_db(self, value: Any) -> Any:
        """
        Validate that *value* is a ``datetime`` and pass it through.

        We do not convert to a string here because both psycopg3 and PyMySQL
        accept Python ``datetime`` objects directly in parameter tuples.
        """
        if value is None:
            return None
        if not isinstance(value, datetime.datetime):
            raise FieldError(
                f"Field '{self.attname}' expects a datetime.datetime, "
                f"got {type(value).__name__}."
            )
        return value

    def from_db(self, value: Any) -> Any:
        """
        Ensure the DB value is returned as ``datetime.datetime``.

        Most drivers return a native ``datetime``; this guard handles the edge
        case where an older driver returns an ISO-8601 string.
        """
        if value is None:
            return None
        if isinstance(value, datetime.datetime):
            return value
        if isinstance(value, str):
            # Fallback: parse ISO-8601 string (e.g. "2024-01-15 10:30:00").
            return datetime.datetime.fromisoformat(value)
        raise FieldError(
            f"Field '{self.attname}' cannot convert {type(value).__name__!r} "
            "to datetime."
        )

    def validate(self, value: Any) -> None:
        """Check null constraint and that the value is a datetime."""
        super().validate(value)
        if value is not None and not isinstance(value, datetime.datetime):
            raise FieldError(
                f"Field '{self.attname}' expects a datetime.datetime, "
                f"got {type(value).__name__}."
            )


class ForeignKeyField(Field):
    """
    Represents a many-to-one relationship stored as an integer FK column.

    The column always stores the referenced row's primary key as an
    ``INTEGER``.  By convention the DB column name is ``<attname>_id`` (e.g.
    the Python attribute ``author`` becomes the column ``author_id``).  This
    suffix is applied automatically by the model metaclass when it reads
    ``self.column_name``.

    Descriptor behaviour
    --------------------
    ``ForeignKeyField`` implements ``__get__`` / ``__set__`` so that:

    - ``instance.author``    — calls ``Author.objects.get(id=instance.author_id)``
                               and returns the ``Author`` instance.  No caching
                               in v1; every access fires a fresh query.
    - ``instance.author = obj`` — sets ``instance.author_id = obj.pk``.
    - ``instance.author_id`` — the raw integer stored in the column.

    Not caching in v1 keeps the implementation simple and avoids subtle bugs
    around stale references.  The identity-map cache planned for v2 will make
    repeated accesses cheap without changing the public API.

    Forward references
    ------------------
    ``to`` can be either the target model class itself *or* its name as a
    string.  The string form is needed for models that reference each other
    (or reference themselves) before both classes are defined.  The actual
    class is resolved lazily on first ``__get__`` access via
    ``_resolve_target()``.

    Parameters
    ----------
    to:
        Target model class or its ``__name__`` as a string.
    on_delete:
        SQL ``ON DELETE`` action — ``'CASCADE'``, ``'SET NULL'``,
        ``'RESTRICT'``, etc.  Defaults to ``'CASCADE'``.
    related_name:
        Name of the reverse accessor on the *target* model
        (e.g. ``'post_set'``).  Reserved for v2 — not yet implemented.
    null, default, primary_key, column_name:
        Inherited from ``Field``.
    """

    def __init__(
        self,
        to: "type | str",
        on_delete: str = "CASCADE",
        related_name: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.to: "type | str" = to
        self.on_delete: str = on_delete.upper()
        self.related_name: str | None = related_name

    # ------------------------------------------------------------------
    # Field interface
    # ------------------------------------------------------------------

    def db_type(self, dialect: str) -> str:
        """
        Always returns ``'INTEGER'``.

        The FK *constraint* (``REFERENCES ...``) is added separately at the
        end of the ``CREATE TABLE`` statement by the dialect, because some
        databases require all column definitions to appear before foreign-key
        declarations.
        """
        return "INTEGER"

    def to_db(self, value: Any) -> Any:
        """Pass the integer FK value through unchanged."""
        return value

    def from_db(self, value: Any) -> Any:
        """Return the raw integer PK from the DB unchanged."""
        return value

    def validate(self, value: Any) -> None:
        """Accept ``None`` (if ``null=True``) or a positive integer."""
        super().validate(value)
        if value is not None and not isinstance(value, int):
            raise FieldError(
                f"Field '{self.attname}_id' expects an int FK value, "
                f"got {type(value).__name__}."
            )

    # ------------------------------------------------------------------
    # Descriptor protocol
    # ------------------------------------------------------------------

    def __set_name__(self, owner: type, name: str) -> None:
        """
        Called by Python when the descriptor is assigned to a class attribute.

        We store the attribute name so ``__get__`` and ``__set__`` know which
        ``_id`` attribute to read/write.  The model metaclass also calls this
        path indirectly through normal attribute assignment.
        """
        self.attname = name
        # The raw integer lives at <name>_id on the instance.
        self._id_attr = f"{name}_id"

    def _resolve_target(self) -> type:
        """
        Return the actual target model class, resolving string references.

        When ``to`` was given as a string we search the global model registry
        (``orm.model.ModelBase._registry``) for the matching class name.  This
        lazy resolution means forward references just work — as long as the
        target model is defined by the time the first query runs.

        Raises
        ------
        FieldError
            If the target class name cannot be found in the registry.
        """
        if isinstance(self.to, str):
            # Lazy import to avoid circular dependency at module load time.
            from orm.models import ModelBase  # type: ignore[attr-defined]
            target_name = self.to
            target = ModelBase._registry.get(target_name)
            if target is None:
                raise FieldError(
                    f"ForeignKeyField on '{self.attname}' references unknown "
                    f"model '{target_name}'. Make sure it is defined before "
                    "the first query."
                )
            return target
        return self.to  # type: ignore[return-value]

    def __get__(
        self,
        instance: "Model | None",
        owner: type,
    ) -> "ForeignKeyField | Model | None":
        """
        Descriptor getter — returns the related object when accessed on an
        instance, or the field itself when accessed on the class.

        Why no caching in v1?
        Every write to ``instance.author_id`` (including mass assignments
        during ``__init__``) would need to invalidate a cached value.
        Without a proper dirty-flag system this is error-prone.  v2 will add
        an identity map that makes re-fetching essentially free for warm data.
        """
        if instance is None:
            # Class-level access: return the descriptor itself so introspection
            # works (e.g. ``Post.author.attname``).
            return self  # type: ignore[return-value]

        # If select_related already eager-loaded the object, return the cached instance
        cached_val = instance.__dict__.get(f"_cached_{self.attname}")
        if cached_val is not None:
            return cached_val

        raw_id: int | None = instance.__dict__.get(self._id_attr)
        if raw_id is None:
            return None

        target_cls = self._resolve_target()
        # Fire query and optionally cache
        res = target_cls.objects.get(id=raw_id)
        instance.__dict__[f"_cached_{self.attname}"] = res
        return res  # type: ignore[return-value]

    def __set__(self, instance: "Model", value: Any) -> None:
        """
        Descriptor setter.

        Accepts either a model instance (extracts its ``pk``) or a raw
        integer.  Setting to ``None`` is allowed when ``null=True``.
        """
        if value is None:
            instance.__dict__[self._id_attr] = None
            return

        # Accept a model instance → extract its PK.
        if hasattr(value, "pk"):
            instance.__dict__[self._id_attr] = value.pk
            return

        # Accept a raw integer.
        if isinstance(value, int):
            instance.__dict__[self._id_attr] = value
            return

        raise FieldError(
            f"Cannot assign {type(value).__name__!r} to '{self.attname}'. "
            "Expected a model instance or integer."
        )


class JSONField(Field):
    """
    Stores arbitrary structured data (dict, list, etc.) serialized as JSON.
    Maps to JSONB on PostgreSQL and JSON on MySQL.
    """

    def db_type(self, dialect: str) -> str:
        return "JSONB" if dialect == "postgres" else "JSON"

    def to_db(self, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        return json.dumps(value)

    def from_db(self, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, (dict, list)):
            return value
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value


class DecimalField(Field):
    """
    Fixed-point decimal field mapping to DECIMAL(max_digits, decimal_places).
    """

    def __init__(self, max_digits: int = 10, decimal_places: int = 2, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.max_digits: int = max_digits
        self.decimal_places: int = decimal_places

    def db_type(self, dialect: str) -> str:
        return f"DECIMAL({self.max_digits}, {self.decimal_places})"

    def to_db(self, value: Any) -> Any:
        if value is None:
            return None
        return str(decimal.Decimal(str(value)))

    def from_db(self, value: Any) -> Any:
        if value is None:
            return None
        return decimal.Decimal(str(value))


class DateField(Field):
    """
    Date field mapping to DATE.
    """

    def db_type(self, dialect: str) -> str:
        return "DATE"

    def to_db(self, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, datetime.date):
            return value.isoformat()
        return str(value)

    def from_db(self, value: Any) -> Any:
        if value is None or isinstance(value, datetime.date):
            return value
        if isinstance(value, str):
            return datetime.date.fromisoformat(value)
        return value


class TimeField(Field):
    """
    Time field mapping to TIME.
    """

    def db_type(self, dialect: str) -> str:
        return "TIME"

    def to_db(self, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, datetime.time):
            return value.isoformat()
        return str(value)

    def from_db(self, value: Any) -> Any:
        if value is None or isinstance(value, datetime.time):
            return value
        if isinstance(value, str):
            return datetime.time.fromisoformat(value)
        return value


class UUIDField(Field):
    """
    UUID field mapping to UUID on PostgreSQL and VARCHAR(36) on MySQL.
    """

    def db_type(self, dialect: str) -> str:
        return "UUID" if dialect == "postgres" else "VARCHAR(36)"

    def to_db(self, value: Any) -> Any:
        if value is None:
            return None
        return str(value)

    def from_db(self, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, uuid.UUID):
            return value
        try:
            return uuid.UUID(str(value))
        except (ValueError, TypeError):
            return str(value)


class ManyRelatedManager:
    """
    Manager for interacting with ManyToMany relationships on a model instance.
    """

    def __init__(self, instance: Any, field: ManyToManyField) -> None:
        self.instance = instance
        self.field = field

    def _get_target_model(self) -> type[Model]:
        return self.field._resolve_target()

    def all(self) -> list[Any]:
        """Fetch all related models linked through the junction table."""
        target_cls = self._get_target_model()
        db = self.instance.__class__._db
        if db is None:
            raise RuntimeError("Database not bound")

        source_pk = self.instance.pk
        if source_pk is None:
            return []

        junction = self.field.get_junction_table_name(self.instance.__class__)
        source_col = f"{self.instance.__class__._meta.table_name}_id"
        target_col = f"{target_cls._meta.table_name}_id"
        target_table = target_cls._meta.table_name

        placeholder = db.dialect.placeholder()
        sql = (
            f"SELECT t.* FROM {target_table} t "
            f"INNER JOIN {junction} j ON t.{target_cls._meta.pk_field} = j.{target_col} "
            f"WHERE j.{source_col} = {placeholder}"
        )
        rows = db.execute(sql, [source_pk], fetch="all")
        if not rows:
            return []
        qs = target_cls.objects
        return [qs._hydrate(row) for row in rows]

    def add(self, *objs: Any) -> None:
        """Link one or more related instances in the junction table."""
        target_cls = self._get_target_model()
        db = self.instance.__class__._db
        if db is None:
            raise RuntimeError("Database not bound")

        source_pk = self.instance.pk
        if source_pk is None:
            raise FieldError("Cannot add relations to an unsaved instance.")

        junction = self.field.get_junction_table_name(self.instance.__class__)
        source_col = f"{self.instance.__class__._meta.table_name}_id"
        target_col = f"{target_cls._meta.table_name}_id"
        placeholder = db.dialect.placeholder()

        # Ensure junction table exists
        self.field.ensure_junction_table(db, self.instance.__class__)

        for obj in objs:
            target_pk = obj.pk if hasattr(obj, "pk") else obj
            sql = f"INSERT INTO {junction} ({source_col}, {target_col}) VALUES ({placeholder}, {placeholder})"
            try:
                db.execute(sql, [source_pk, target_pk], fetch="none")
            except Exception:
                pass  # Ignore duplicate entries

    def remove(self, *objs: Any) -> None:
        """Remove links from the junction table."""
        target_cls = self._get_target_model()
        db = self.instance.__class__._db
        if db is None:
            return

        source_pk = self.instance.pk
        if source_pk is None:
            return

        junction = self.field.get_junction_table_name(self.instance.__class__)
        source_col = f"{self.instance.__class__._meta.table_name}_id"
        target_col = f"{target_cls._meta.table_name}_id"
        placeholder = db.dialect.placeholder()

        for obj in objs:
            target_pk = obj.pk if hasattr(obj, "pk") else obj
            sql = f"DELETE FROM {junction} WHERE {source_col} = {placeholder} AND {target_col} = {placeholder}"
            db.execute(sql, [source_pk, target_pk], fetch="none")

    def clear(self) -> None:
        """Clear all relationships for this instance."""
        db = self.instance.__class__._db
        if db is None or self.instance.pk is None:
            return

        junction = self.field.get_junction_table_name(self.instance.__class__)
        source_col = f"{self.instance.__class__._meta.table_name}_id"
        placeholder = db.dialect.placeholder()
        sql = f"DELETE FROM {junction} WHERE {source_col} = {placeholder}"
        db.execute(sql, [self.instance.pk], fetch="none")


class ManyToManyField(Field):
    """
    Defines a many-to-many relationship via an explicit or auto-managed junction table.
    """

    def __init__(
        self,
        to: type | str,
        through: str | None = None,
        related_name: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.to: type | str = to
        self.through: str | None = through
        self.related_name: str | None = related_name

    def db_type(self, dialect: str) -> str:
        # ManyToManyField does not create a column on the host table
        return ""

    def get_junction_table_name(self, source_cls: type) -> str:
        if self.through:
            return self.through
        target_cls = self._resolve_target()
        names = sorted([source_cls._meta.table_name, target_cls._meta.table_name])
        return f"{names[0]}_{names[1]}"

    def ensure_junction_table(self, db: Any, source_cls: type) -> None:
        target_cls = self._resolve_target()
        junction = self.get_junction_table_name(source_cls)
        source_col = f"{source_cls._meta.table_name}_id"
        target_col = f"{target_cls._meta.table_name}_id"
        sql = (
            f"CREATE TABLE IF NOT EXISTS {junction} ("
            f"{source_col} INTEGER NOT NULL, "
            f"{target_col} INTEGER NOT NULL, "
            f"PRIMARY KEY ({source_col}, {target_col})"
            f")"
        )
        db.execute(sql, fetch="none")

    def _resolve_target(self) -> type:
        if isinstance(self.to, str):
            from orm.models import ModelBase
            target = ModelBase._registry.get(self.to)
            if target is None:
                raise FieldError(f"ManyToManyField references unknown model '{self.to}'")
            return target
        return self.to  # type: ignore[return-value]

    def __get__(self, instance: Any, owner: type) -> Any:
        if instance is None:
            return self
        return ManyRelatedManager(instance, self)

    def __set__(self, instance: Any, value: Any) -> None:
        raise FieldError("Cannot directly assign to ManyToManyField. Use .add() or .remove().")

