"""
tests/unit/test_models.py
=========================
Unit tests for the ModelBase metaclass and Model class.

We test metaclass behavior in isolation: field collection, _meta construction,
pk auto-injection, table naming, and descriptor installation.

No real DB is used.  We use a mock Database to satisfy the Manager constraint.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from orm.fields import BooleanField, CharField, ForeignKeyField, IntegerField, TextField
from orm.models import Model, ModelMeta


# ---------------------------------------------------------------------------
# Helper: a minimal mock DB so Manager doesn't complain
# ---------------------------------------------------------------------------

def _mock_db() -> MagicMock:
    db = MagicMock()
    db.dialect.placeholder.return_value = "%s"
    db.dialect.name = "postgres"
    return db


# ---------------------------------------------------------------------------
# Test models (defined at module level, not inside tests, so metaclass runs)
# ---------------------------------------------------------------------------

class SimpleModel(Model):
    name = CharField(max_length=100)
    age  = IntegerField()


class CustomTableModel(Model):
    title = CharField(max_length=200)

    class Meta:
        table_name = "custom_posts"


class ParentModel(Model):
    value = IntegerField()


class ChildModel(Model):
    label  = CharField(max_length=50)
    parent = ForeignKeyField(ParentModel, on_delete="CASCADE")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestModelMeta:
    def test_table_name_defaults_to_lowercase_class_name(self) -> None:
        assert SimpleModel._meta.table_name == "simplemodel"

    def test_custom_table_name_from_inner_meta(self) -> None:
        assert CustomTableModel._meta.table_name == "custom_posts"

    def test_auto_pk_injected(self) -> None:
        # 'id' should be auto-added when no explicit pk field is declared
        assert "id" in SimpleModel._meta.fields
        assert SimpleModel._meta.pk_field == "id"
        assert SimpleModel._meta.fields["id"].primary_key is True

    def test_declared_fields_present(self) -> None:
        assert "name" in SimpleModel._meta.fields
        assert "age" in SimpleModel._meta.fields

    def test_column_order_includes_pk_first(self) -> None:
        # Auto-pk should come before declared fields
        cols = SimpleModel._meta.column_order
        assert cols[0] == "id"

    def test_column_to_field_mapping(self) -> None:
        mapping = SimpleModel._meta.column_to_field
        assert mapping["name"] == "name"
        assert mapping["age"] == "age"

    def test_fk_column_name_gets_id_suffix(self) -> None:
        # ForeignKeyField named 'parent' should have column 'parent_id'
        fk_field = ChildModel._meta.fields["parent"]
        assert fk_field.column_name == "parent_id"

    def test_fk_column_in_column_order(self) -> None:
        assert "parent_id" in ChildModel._meta.column_order

    def test_meta_repr(self) -> None:
        r = repr(SimpleModel._meta)
        assert "simplemodel" in r
        assert "id" in r


class TestModelInstantiation:
    def test_create_instance_with_kwargs(self) -> None:
        obj = SimpleModel(name="Alice", age=30)
        assert obj._state["name"] == "Alice"
        assert obj._state["age"] == 30

    def test_pk_is_none_before_save(self) -> None:
        obj = SimpleModel(name="Bob", age=25)
        assert obj.pk is None

    def test_repr(self) -> None:
        obj = SimpleModel(name="Carol", age=20)
        assert "SimpleModel" in repr(obj)

    def test_equality_by_pk(self) -> None:
        db = _mock_db()
        SimpleModel._db = db
        a = SimpleModel.__new__(SimpleModel)
        a._state = {"id": 1, "name": "A", "age": 1}
        b = SimpleModel.__new__(SimpleModel)
        b._state = {"id": 1, "name": "B", "age": 2}
        assert a == b  # same pk

    def test_inequality_different_pk(self) -> None:
        a = SimpleModel.__new__(SimpleModel)
        a._state = {"id": 1, "name": "A", "age": 1}
        b = SimpleModel.__new__(SimpleModel)
        b._state = {"id": 2, "name": "B", "age": 2}
        assert a != b


class TestModelDescriptors:
    def test_get_field_via_class_returns_field_object(self) -> None:
        field_obj = SimpleModel.__dict__["name"].__get__(None, SimpleModel)
        # Class-level access returns the Field
        from orm.fields import Field
        assert isinstance(field_obj, Field)

    def test_set_and_get_on_instance(self) -> None:
        obj = SimpleModel(name="Alice", age=30)
        obj.name = "Alicia"
        assert obj._state["name"] == "Alicia"

    def test_fk_set_with_instance(self) -> None:
        parent = ParentModel.__new__(ParentModel)
        parent.__dict__["_state"] = {"id": 5, "value": 99}
        child = ChildModel(label="x")
        child.parent = parent
        # ForeignKeyField stores the raw id in instance.__dict__[_id_attr],
        # not in _state — so check __dict__ directly.
        assert child.__dict__.get("parent_id") == 5

    def test_fk_set_with_raw_id(self) -> None:
        child = ChildModel(label="y")
        child.parent = 7
        assert child.__dict__.get("parent_id") == 7


class TestManagerAccess:
    def test_manager_raises_on_instance_access(self) -> None:
        db = _mock_db()
        SimpleModel._db = db
        obj = SimpleModel(name="Test", age=1)
        with pytest.raises(AttributeError):
            _ = obj.objects  # type: ignore[attr-defined]

    def test_manager_returns_queryset_on_class_access(self) -> None:
        from orm.queryset import QuerySet
        db = _mock_db()
        SimpleModel._db = db
        qs = SimpleModel.objects.filter()
        assert isinstance(qs, QuerySet)
