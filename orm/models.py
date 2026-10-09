"""
orm/models.py
=============
Model base class and metaclass.

How it works
------------
``ModelBase`` is a metaclass that runs when Python processes a class
definition.  It scans the class body for ``Field`` instances, builds a
``ModelMeta`` object capturing the table name and field map, then
attaches an ``objects`` manager (a QuerySet factory) to the class.

``Model`` is the base class for user-defined models.  Users never
instantiate QuerySet directly — they use ``Model.objects.filter(...)``.

Table naming convention:
  By default the table name is the lowercase class name.  Override by
  setting ``class Meta: table_name = "custom_name"`` in the model body.

Descriptor strategy:
  ForeignKeyField implements ``__get__`` / ``__set__`` directly (it IS a
  Python descriptor), so the metaclass installs the field object itself
  directly on the class.  All other fields get wrapped in _FieldDescriptor
  which routes get/set through _state.

Model registry:
  ModelBase maintains a ``_registry`` mapping class names to classes.  This
  is used by ForeignKeyField to resolve forward references (string FK targets).
"""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, Any

from orm.exceptions import DoesNotExist, FieldError, IntegrityError as OrmIntegrityError
from orm.fields import Field, ForeignKeyField, IntegerField, ManyToManyField

if TYPE_CHECKING:
    from orm.db import Database
    from orm.queryset import QuerySet


class _ReverseForeignKeyDescriptor:
    """
    Descriptor installed on parent model allowing reverse relation queries,
    e.g. author.post_set.all() or author.posts.all().
    """

    def __init__(self, child_model: type[Model], fk_field_name: str) -> None:
        self.child_model = child_model
        self.fk_field_name = fk_field_name

    def __get__(self, instance: Any, owner: type) -> Any:
        if instance is None:
            return self
        col = f"{self.fk_field_name}_id"
        return self.child_model.objects.filter(**{col: instance.pk})


# ---------------------------------------------------------------------------
# ModelMeta: metadata container built by the metaclass
# ---------------------------------------------------------------------------

class ModelMeta:
    """
    Holds introspected metadata for a Model class.

    Attributes
    ----------
    table_name:
        The DB table name (lowercased class name by default).
    fields:
        Mapping of Python attribute name -> Field instance.
        This is the *logical* name (e.g. ``author``, not ``author_id``).
    column_order:
        Ordered list of DB column names matching ``SELECT *`` output.
        Built by iterating fields in declaration order (preserved via
        ``Field._creation_counter``).
    column_to_field:
        Mapping of DB column name -> Python attribute name.
    pk_field:
        Name of the primary-key attribute.
    """

    def __init__(
        self,
        table_name: str,
        fields: dict[str, Field],
        pk_field: str,
    ) -> None:
        self.table_name = table_name
        self.fields = fields
        self.pk_field = pk_field

        # Build column_order and column_to_field from fields in order.
        # ForeignKeyField stores data under <attr>_id column; other fields
        # use attr name as column name (unless column_name overridden).
        self.column_order: list[str] = []
        self.column_to_field: dict[str, str] = {}

        for attr_name, field in fields.items():
            if isinstance(field, ManyToManyField) or (hasattr(field, "db_type") and field.db_type("postgres") == ""):
                continue
            col = field.column_name or attr_name
            self.column_order.append(col)
            self.column_to_field[col] = attr_name

    def __repr__(self) -> str:
        return f"<ModelMeta table={self.table_name!r} pk={self.pk_field!r}>"


# ---------------------------------------------------------------------------
# Manager: queryset factory attached to Model.objects
# ---------------------------------------------------------------------------

class Manager:
    """
    Descriptor that provides ``Model.objects`` as a QuerySet factory.

    Accessing ``Post.objects`` returns a fresh QuerySet bound to the model
    and its database.  Accessing ``instance.objects`` raises AttributeError
    because managers are class-level, not instance-level.
    """

    def __init__(self, model: type[Model]) -> None:
        self._model = model

    def __get__(self, instance: Any, owner: type) -> QuerySet:
        if instance is not None:
            raise AttributeError(
                "Manager is not accessible via model instances. "
                "Use the class: Post.objects.filter(...)"
            )
        from orm.queryset import QuerySet  # local import avoids circularity

        db = self._model._db
        if db is None:
            raise RuntimeError(
                f"No database bound to {self._model.__name__}. "
                "Call db.register(Model) or set Model._db = db."
            )
        return QuerySet(self._model, db)

    # Convenience passthroughs so Manager itself acts like a QuerySet root.
    # This lets users write Model.objects.create(...) directly.
    def create(self, **kwargs: Any) -> Model:
        return self._model._create(**kwargs)  # type: ignore[attr-defined]

    def filter(self, **kwargs: Any) -> QuerySet:
        return self.__get__(None, self._model).filter(**kwargs)

    def exclude(self, **kwargs: Any) -> QuerySet:
        return self.__get__(None, self._model).exclude(**kwargs)

    def all(self) -> list[Model]:
        return self.__get__(None, self._model).all()

    def get(self, **kwargs: Any) -> Model:
        return self.__get__(None, self._model).get(**kwargs)

    def count(self) -> int:
        return self.__get__(None, self._model).count()

    def order_by(self, *fields: str) -> QuerySet:
        return self.__get__(None, self._model).order_by(*fields)

    def first(self) -> Model | None:
        return self.__get__(None, self._model).first()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.__get__(None, self._model), name)


# ---------------------------------------------------------------------------
# _FieldDescriptor  (used for non-FK fields only)
# ---------------------------------------------------------------------------

class _FieldDescriptor:
    """
    Data descriptor installed by ModelBase for non-ForeignKey fields.

    Routes __get__ through field.from_db and __set__ through field.validate
    + field.to_db, with values stored in instance.__dict__['_state'].

    ForeignKeyField is NOT wrapped here — it implements its own full
    descriptor protocol with __get__ and __set__ and is installed directly.
    """

    def __init__(self, attr_name: str, field: Field) -> None:
        self._attr_name = attr_name
        self._field = field

    def __get__(self, instance: Any, owner: type) -> Any:
        if instance is None:
            # Class-level access — return the field itself (useful for introspection)
            return self._field
        state: dict[str, Any] = instance.__dict__.setdefault("_state", {})
        return state.get(self._attr_name)

    def __set__(self, instance: Any, value: Any) -> None:
        state: dict[str, Any] = instance.__dict__.setdefault("_state", {})
        self._field.validate(value)
        state[self._attr_name] = self._field.to_db(value)


# ---------------------------------------------------------------------------
# ModelBase metaclass
# ---------------------------------------------------------------------------

class ModelBase(type):
    """
    Metaclass for all ORM models.

    Runs at class-definition time to:
    1. Collect all ``Field`` instances from the class body (in declaration
       order using ``Field._creation_counter``).
    2. Inject an implicit ``IntegerField(primary_key=True)`` named ``id``
       unless the user has declared their own pk field.
    3. Build the ``_meta`` attribute (a ``ModelMeta`` instance).
    4. Attach a ``Manager`` to ``Model.objects``.
    5. Install descriptors: ForeignKeyField objects are installed directly
       (they are descriptors themselves); other Fields get _FieldDescriptor.
    6. Register the class by name in ModelBase._registry for FK forward refs.
    """

    # Global registry mapping class name -> Model class.
    # Used by ForeignKeyField._resolve_target() for string-based FK targets.
    _registry: dict[str, type] = {}

    def __new__(
        mcs,
        name: str,
        bases: tuple[type, ...],
        namespace: dict[str, Any],
    ) -> type:
        # Don't process the Model base class itself
        if name == "Model" and not any(isinstance(b, ModelBase) for b in bases):
            return super().__new__(mcs, name, bases, namespace)

        # Extract inner Meta class for overrides
        inner_meta = namespace.pop("Meta", None)
        table_name = (
            getattr(inner_meta, "table_name", None)
            or name.lower()
        )

        # Collect fields declared on this class (not inherited ones)
        raw_fields: dict[str, Field] = {
            k: v
            for k, v in namespace.items()
            if isinstance(v, Field)
        }

        # Also collect inherited fields (from parent models)
        inherited_fields: dict[str, Field] = {}
        for base in bases:
            if hasattr(base, "_meta"):
                inherited_fields.update(base._meta.fields)  # type: ignore[attr-defined]

        # Sort by creation order to preserve declaration order.
        # _creation_order is the per-instance snapshot set by Field.__init__.
        sorted_own = sorted(raw_fields.items(), key=lambda kv: kv[1]._creation_order)

        # Remove raw fields from namespace (we install descriptors below)
        for k in raw_fields:
            namespace.pop(k, None)

        # Auto-inject ``id`` PK if no explicit primary_key field
        has_pk = any(f.primary_key for f in raw_fields.values())
        if not has_pk and not any(f.primary_key for f in inherited_fields.values()):
            pk_field = IntegerField(primary_key=True)
            # Give it a counter lower than any user field so it sorts first
            pk_field._creation_order = -1  # sorts before all user-declared fields
            sorted_own.insert(0, ("id", pk_field))
            pk_attr = "id"
        else:
            pk_attr = next(k for k, f in sorted_own if f.primary_key)

        # Merge inherited + own fields (own takes precedence)
        all_fields: dict[str, Field] = {**inherited_fields, **dict(sorted_own)}

        # Finalize ForeignKeyField column names (add _id suffix).
        # The column_name on the field is the DB column; the attr name is
        # the Python name.  __set_name__ on ForeignKeyField also fires
        # during setattr below, but we set it here to guarantee it's correct
        # in _meta before the class is created.
        for attr, field in all_fields.items():
            if isinstance(field, ForeignKeyField):
                if field.column_name is None:
                    field.column_name = f"{attr}_id"
                # Always set attname and _id_attr so the descriptor can locate
                # the stored value.  Field.__init__ initialises attname to "",
                # so we cannot rely on hasattr() to detect "not yet set".
                field.attname = attr  # type: ignore[attr-defined]
                field._id_attr = field.column_name  # type: ignore[attr-defined]

        # Build _meta
        meta = ModelMeta(
            table_name=table_name,
            fields=all_fields,
            pk_field=pk_attr,
        )

        namespace["_meta"] = meta
        namespace["_db"] = None  # set by db.register()

        cls = super().__new__(mcs, name, bases, namespace)

        # Install descriptors on the new class.
        # ForeignKeyField and ManyToManyField: install field object directly.
        # All other fields: wrap in _FieldDescriptor.
        for attr_name, field in all_fields.items():
            if isinstance(field, (ForeignKeyField, ManyToManyField)):
                setattr(cls, attr_name, field)
            else:
                descriptor = _FieldDescriptor(attr_name, field)
                setattr(cls, attr_name, descriptor)

        # Attach Manager
        cls.objects = Manager(cls)  # type: ignore[attr-defined]

        # Register by class name for FK forward-reference resolution
        ModelBase._registry[name] = cls

        # Wire reverse foreign key descriptors on target models
        for attr_name, field in all_fields.items():
            if isinstance(field, ForeignKeyField):
                target_cls = None
                if isinstance(field.to, str):
                    target_cls = ModelBase._registry.get(field.to)
                elif isinstance(field.to, type):
                    target_cls = field.to

                if target_cls and isinstance(target_cls, type):
                    rel_name = field.related_name or f"{name.lower()}_set"
                    if not hasattr(target_cls, rel_name):
                        setattr(target_cls, rel_name, _ReverseForeignKeyDescriptor(cls, attr_name))

        return cls


# ---------------------------------------------------------------------------
# Model base class
# ---------------------------------------------------------------------------

class Model(metaclass=ModelBase):
    """
    Base class for all ORM models.

    Define your model like this::

        class Author(Model):
            name  = CharField(max_length=100)
            email = CharField(max_length=200)

        class Post(Model):
            title   = CharField(max_length=200)
            body    = TextField()
            author  = ForeignKeyField(Author, on_delete="CASCADE")
            created = DateTimeField(null=True)

    CRUD
    ----
    ::

        # Create
        alice = Author.create(name="Alice", email="alice@example.com")

        # Read
        author = Author.objects.get(id=1)
        posts  = Post.objects.filter(author=alice).order_by("-created")

        # Update (instance)
        alice.name = "Alicia"
        alice.save()

        # Update (bulk)
        Post.objects.filter(author=alice).update(title="Updated")

        # Delete
        alice.delete()
    """

    # _db and _meta are set by ModelBase; type annotations here for IDE support
    _db: Database | None
    _meta: ModelMeta

    def __init__(self, **kwargs: Any) -> None:
        # Initialize _state before setattr so descriptors have somewhere to write
        self.__dict__["_state"] = {}
        for key, value in kwargs.items():
            setattr(self, key, value)

    @classmethod
    def _create(cls, **kwargs: Any) -> Model:
        """Internal create method called by Manager.create()."""
        instance = cls(**kwargs)
        instance.save(force_insert=True)
        return instance

    @classmethod
    def create(cls, **kwargs: Any) -> Model:
        """
        Create, INSERT, and return a new model instance.

        Parameters
        ----------
        **kwargs:
            Field name/value pairs.  Unknown fields raise ``FieldError``.

        Returns
        -------
        Model:
            The newly created instance with its ``id`` (pk) populated.
        """
        return cls._create(**kwargs)

    def save(self, force_insert: bool = False) -> None:
        """
        Persist this instance to the database (INSERT or UPDATE).

        If the instance has no pk, an INSERT is performed and the new pk
        is written back to ``self.id``.  If it has a pk and ``force_insert``
        is False, an UPDATE is performed.

        Parameters
        ----------
        force_insert:
            If True, always INSERT (used by ``create()``).
        """
        db = self.__class__._db
        if db is None:
            raise RuntimeError(
                f"No database bound to {self.__class__.__name__}. "
                "Call db.register(Model)."
            )

        meta = self.__class__._meta
        state = self.__dict__.get("_state", {})
        pk_val = state.get(meta.pk_field)

        if pk_val is None or force_insert:
            self._do_insert(db, meta, state)
        else:
            self._do_update(db, meta, state, pk_val)

    def _do_insert(
        self,
        db: Database,
        meta: ModelMeta,
        state: dict[str, Any],
    ) -> None:
        """Build and execute an INSERT statement."""
        placeholder = db.dialect.placeholder()

        cols: list[str] = []
        vals: list[Any] = []

        for attr_name, field in meta.fields.items():
            if field.primary_key:
                continue  # Let DB auto-assign the PK

            # ForeignKeyField stores its value at <attr>_id in instance.__dict__
            if isinstance(field, ForeignKeyField):
                id_attr = f"{attr_name}_id"
                raw = self.__dict__.get(id_attr)
                col = field.column_name or id_attr
            else:
                raw = state.get(attr_name)
                col = field.column_name or attr_name

            db_val = field.to_db(raw)
            cols.append(col)
            vals.append(db_val)

        if not cols:
            raise FieldError("Cannot INSERT a model with no non-PK fields.")

        ph_list = ", ".join(placeholder for _ in cols)
        col_list = ", ".join(cols)

        if db.dialect.name == "postgres":
            sql = (
                f"INSERT INTO {meta.table_name} ({col_list}) "
                f"VALUES ({ph_list}) RETURNING id"
            )
        else:
            sql = (
                f"INSERT INTO {meta.table_name} ({col_list}) "
                f"VALUES ({ph_list})"
            )

        new_pk = db.execute_lastrowid(sql, vals, table_name=meta.table_name)
        state[meta.pk_field] = new_pk

    def _do_update(
        self,
        db: Database,
        meta: ModelMeta,
        state: dict[str, Any],
        pk_val: Any,
    ) -> None:
        """Build and execute an UPDATE statement."""
        placeholder = db.dialect.placeholder()
        set_parts: list[str] = []
        set_vals: list[Any] = []

        for attr_name, field in meta.fields.items():
            if field.primary_key:
                continue

            if isinstance(field, ForeignKeyField):
                id_attr = f"{attr_name}_id"
                raw = self.__dict__.get(id_attr)
                col = field.column_name or id_attr
            else:
                raw = state.get(attr_name)
                col = field.column_name or attr_name

            db_val = field.to_db(raw)
            set_parts.append(f"{col} = {placeholder}")
            set_vals.append(db_val)

        pk_col = meta.fields[meta.pk_field].column_name or meta.pk_field
        sql = (
            f"UPDATE {meta.table_name} "
            f"SET {', '.join(set_parts)} "
            f"WHERE {pk_col} = {placeholder}"
        )
        db.execute(sql, set_vals + [pk_val], fetch="none")

    def delete(self) -> None:
        """DELETE this instance from the database."""
        db = self.__class__._db
        if db is None:
            raise RuntimeError("No database bound.")

        meta = self.__class__._meta
        state = self.__dict__.get("_state", {})
        pk_val = state.get(meta.pk_field)

        if pk_val is None:
            raise DoesNotExist(
                f"Cannot delete an unsaved {self.__class__.__name__} instance."
            )

        pk_col = meta.fields[meta.pk_field].column_name or meta.pk_field
        placeholder = db.dialect.placeholder()
        sql = f"DELETE FROM {meta.table_name} WHERE {pk_col} = {placeholder}"
        db.execute(sql, [pk_val], fetch="none")
        state.pop(meta.pk_field, None)

    @property
    def pk(self) -> Any:
        """Return the primary key value of this instance."""
        state = self.__dict__.get("_state", {})
        return state.get(self.__class__._meta.pk_field)

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} pk={self.pk!r}>"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, self.__class__):
            return NotImplemented
        return self.pk == other.pk and self.pk is not None

    def __hash__(self) -> int:
        return hash((self.__class__, self.pk))
