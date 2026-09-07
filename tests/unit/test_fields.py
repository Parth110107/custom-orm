"""
tests/unit/test_fields.py
=========================
Unit tests for field definitions, DDL output, and value conversion.

No real DB is used here — we test fields in isolation by calling
db_type(), to_db(), from_db(), and validate() directly.
"""

from __future__ import annotations

import datetime
import pytest

from orm.exceptions import FieldError
from orm.fields import (
    BooleanField,
    CharField,
    DateTimeField,
    ForeignKeyField,
    IntegerField,
    TextField,
)


class TestIntegerField:
    def test_db_type_postgres(self) -> None:
        f = IntegerField()
        assert f.db_type("postgres") == "INTEGER"

    def test_db_type_mysql(self) -> None:
        f = IntegerField()
        assert f.db_type("mysql") == "INTEGER"

    def test_to_db_passthrough(self) -> None:
        f = IntegerField()
        assert f.to_db(42) == 42

    def test_from_db_passthrough(self) -> None:
        f = IntegerField()
        assert f.from_db(42) == 42

    def test_validate_none_raises_when_not_null(self) -> None:
        f = IntegerField(null=False)
        with pytest.raises(FieldError):
            f.validate(None)

    def test_validate_none_ok_when_nullable(self) -> None:
        f = IntegerField(null=True)
        f.validate(None)  # should not raise

    def test_primary_key_flag(self) -> None:
        f = IntegerField(primary_key=True)
        assert f.primary_key is True


class TestCharField:
    def test_db_type_returns_varchar(self) -> None:
        f = CharField(max_length=50)
        assert f.db_type("postgres") == "VARCHAR(50)"
        assert f.db_type("mysql") == "VARCHAR(50)"

    def test_validate_max_length(self) -> None:
        f = CharField(max_length=5)
        with pytest.raises(FieldError, match="max_length"):
            f.validate("toolong")

    def test_validate_within_limit(self) -> None:
        f = CharField(max_length=10)
        f.validate("ok")  # should not raise

    def test_to_db_str(self) -> None:
        f = CharField(max_length=100)
        assert f.to_db("hello") == "hello"

    def test_from_db_str(self) -> None:
        f = CharField(max_length=100)
        assert f.from_db("hello") == "hello"


class TestTextField:
    def test_db_type(self) -> None:
        f = TextField()
        assert f.db_type("postgres") == "TEXT"
        assert f.db_type("mysql") == "TEXT"

    def test_to_db_from_db(self) -> None:
        f = TextField()
        assert f.to_db("long text") == "long text"
        assert f.from_db("long text") == "long text"


class TestBooleanField:
    def test_db_type_postgres(self) -> None:
        f = BooleanField()
        assert f.db_type("postgres") == "BOOLEAN"

    def test_db_type_mysql(self) -> None:
        f = BooleanField()
        assert f.db_type("mysql") == "TINYINT(1)"

    def test_to_db_true(self) -> None:
        f = BooleanField()
        assert f.to_db(True) == 1

    def test_to_db_false(self) -> None:
        f = BooleanField()
        assert f.to_db(False) == 0

    def test_from_db_1(self) -> None:
        f = BooleanField()
        assert f.from_db(1) is True

    def test_from_db_0(self) -> None:
        f = BooleanField()
        assert f.from_db(0) is False

    def test_from_db_none(self) -> None:
        f = BooleanField(null=True)
        assert f.from_db(None) is None


class TestDateTimeField:
    def test_db_type_postgres(self) -> None:
        f = DateTimeField()
        assert f.db_type("postgres") == "TIMESTAMP"

    def test_db_type_mysql(self) -> None:
        f = DateTimeField()
        assert f.db_type("mysql") == "DATETIME"

    def test_to_db_passthrough_datetime(self) -> None:
        f = DateTimeField()
        dt = datetime.datetime(2024, 1, 15, 12, 0, 0)
        assert f.to_db(dt) == dt

    def test_from_db_datetime(self) -> None:
        f = DateTimeField()
        dt = datetime.datetime(2024, 1, 15, 12, 0, 0)
        assert f.from_db(dt) == dt

    def test_from_db_none_nullable(self) -> None:
        f = DateTimeField(null=True)
        assert f.from_db(None) is None


class TestForeignKeyField:
    def test_column_name_defaults_to_none(self) -> None:
        # column_name is assigned by metaclass; raw field has None
        f = ForeignKeyField("Author", on_delete="CASCADE")
        assert f.column_name is None  # set by metaclass later

    def test_on_delete_stored(self) -> None:
        f = ForeignKeyField("Author", on_delete="SET NULL")
        assert f.on_delete == "SET NULL"

    def test_db_type_is_integer(self) -> None:
        f = ForeignKeyField("Author", on_delete="CASCADE")
        assert f.db_type("postgres") == "INTEGER"
        assert f.db_type("mysql") == "INTEGER"


class TestCreationCounter:
    """Field instances should have monotonically increasing creation counters
    so the metaclass can preserve declaration order."""

    def test_counter_increases(self) -> None:
        a = IntegerField()
        b = CharField(max_length=10)
        c = TextField()
        # _creation_order is the per-instance snapshot of the global counter
        assert a._creation_order < b._creation_order < c._creation_order
