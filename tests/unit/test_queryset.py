"""
tests/unit/test_queryset.py
===========================
Unit tests for the QuerySet SQL builder.

We test that the correct parameterized SQL and params are produced for
each chaining operation, WITHOUT executing against a real DB.

Strategy: mock Database.execute() and inspect what SQL+params it received.
"""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from orm.exceptions import DoesNotExist, FieldError, MultipleObjectsReturned
from orm.fields import BooleanField, CharField, IntegerField
from orm.models import Model
from orm.queryset import QuerySet, _parse_lookup


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

class Article(Model):
    title     = CharField(max_length=200)
    published = BooleanField(default=False)
    views     = IntegerField(default=0)


def _make_qs() -> tuple[QuerySet, MagicMock]:
    """Return a QuerySet and the mock DB it's attached to."""
    db = MagicMock()
    db.dialect.placeholder.return_value = "%s"
    db.dialect.name = "postgres"
    Article._db = db
    qs = QuerySet(Article, db)
    return qs, db


# ---------------------------------------------------------------------------
# Tests: _parse_lookup helper
# ---------------------------------------------------------------------------

class TestParseLookup:
    def test_bare_field_is_exact(self) -> None:
        assert _parse_lookup("name") == ("name", "exact")

    def test_gte_suffix(self) -> None:
        assert _parse_lookup("age__gte") == ("age", "gte")

    def test_contains_suffix(self) -> None:
        assert _parse_lookup("title__contains") == ("title", "contains")

    def test_double_underscore_in_name_not_a_lookup(self) -> None:
        # 'foo__bar' where 'bar' is not a registered lookup -> treat as exact
        col, lookup = _parse_lookup("foo__bar")
        assert col == "foo__bar"
        assert lookup == "exact"


# ---------------------------------------------------------------------------
# Tests: filter / exclude SQL generation
# ---------------------------------------------------------------------------

class TestFilterSQL:
    def test_empty_filter_no_where(self) -> None:
        qs, db = _make_qs()
        sql, params = qs._build_select()
        assert "WHERE" not in sql
        assert params == []

    def test_single_exact_filter(self) -> None:
        qs, db = _make_qs()
        qs2 = qs.filter(title="Hello")
        sql, params = qs2._build_select()
        assert "WHERE" in sql
        assert "title = %s" in sql
        assert params == ["Hello"]

    def test_multiple_filters_and_combined(self) -> None:
        qs, db = _make_qs()
        qs2 = qs.filter(published=True, views__gte=100)
        sql, params = qs2._build_select()
        assert "published = %s" in sql
        assert "views >= %s" in sql
        assert "AND" in sql

    def test_exclude_wraps_in_not(self) -> None:
        qs, db = _make_qs()
        qs2 = qs.exclude(published=True)
        sql, params = qs2._build_select()
        assert "NOT (" in sql
        assert "published = %s" in sql

    def test_contains_lookup(self) -> None:
        qs, db = _make_qs()
        qs2 = qs.filter(title__contains="orm")
        sql, params = qs2._build_select()
        assert "LIKE %s" in sql
        assert params == ["%orm%"]

    def test_startswith_lookup(self) -> None:
        qs, db = _make_qs()
        qs2 = qs.filter(title__startswith="Intro")
        _, params = qs2._build_select()
        assert params == ["Intro%"]

    def test_endswith_lookup(self) -> None:
        qs, db = _make_qs()
        qs2 = qs.filter(title__endswith="ORM")
        _, params = qs2._build_select()
        assert params == ["%ORM"]

    def test_isnull_lookup_no_param(self) -> None:
        qs, db = _make_qs()
        qs2 = qs.filter(title__isnull=None)
        sql, params = qs2._build_select()
        assert "IS NULL" in sql
        assert params == []

    def test_unsupported_lookup_raises(self) -> None:
        from orm.queryset import _build_condition
        # _parse_lookup only recognizes registered lookups as suffix.
        # To test FieldError on bad lookup, call _build_condition directly.
        with pytest.raises(FieldError, match="Unsupported lookup"):
            _build_condition("title", "badlookup", "x", "%s")


class TestOrderBySQL:
    def test_asc_order(self) -> None:
        qs, _ = _make_qs()
        sql, _ = qs.order_by("title")._build_select()
        assert "ORDER BY title ASC" in sql

    def test_desc_order(self) -> None:
        qs, _ = _make_qs()
        sql, _ = qs.order_by("-views")._build_select()
        assert "ORDER BY views DESC" in sql

    def test_multiple_order_fields(self) -> None:
        qs, _ = _make_qs()
        sql, _ = qs.order_by("-views", "title")._build_select()
        assert "views DESC, title ASC" in sql


class TestLimitOffset:
    def test_limit_appended(self) -> None:
        qs, _ = _make_qs()
        sql, _ = qs.limit(10)._build_select()
        assert "LIMIT 10" in sql

    def test_offset_appended(self) -> None:
        qs, _ = _make_qs()
        sql, _ = qs.limit(10).offset(20)._build_select()
        assert "OFFSET 20" in sql


class TestImmutability:
    def test_filter_returns_new_instance(self) -> None:
        qs, _ = _make_qs()
        qs2 = qs.filter(title="x")
        assert qs is not qs2
        assert len(qs._filters) == 0
        assert len(qs2._filters) == 1

    def test_order_by_returns_new_instance(self) -> None:
        qs, _ = _make_qs()
        qs2 = qs.order_by("title")
        assert qs._order_by == []
        assert qs2._order_by == ["title"]


# ---------------------------------------------------------------------------
# Tests: execution delegation
# ---------------------------------------------------------------------------

class TestQuerySetExecution:
    def test_all_calls_execute(self) -> None:
        qs, db = _make_qs()
        db.execute.return_value = []
        result = qs.all()
        db.execute.assert_called_once()
        assert result == []

    def test_count_calls_count_sql(self) -> None:
        qs, db = _make_qs()
        db.execute.return_value = (5,)
        count = qs.count()
        sql = db.execute.call_args[0][0]
        assert "COUNT(*)" in sql
        assert count == 5

    def test_delete_calls_delete_sql(self) -> None:
        qs, db = _make_qs()
        db.execute.return_value = None
        qs.filter(published=True).delete()
        sql = db.execute.call_args[0][0]
        assert sql.startswith("DELETE FROM")
        assert "published = %s" in sql

    def test_update_calls_update_sql(self) -> None:
        qs, db = _make_qs()
        db.execute.return_value = None
        qs.filter(views__lt=10).update(published=True)
        sql = db.execute.call_args[0][0]
        assert sql.startswith("UPDATE")
        assert "WHERE" in sql

    def test_get_raises_does_not_exist(self) -> None:
        qs, db = _make_qs()
        db.execute.return_value = []
        with pytest.raises(DoesNotExist):
            qs.get(title="missing")

    def test_get_raises_multiple_objects_returned(self) -> None:
        qs, db = _make_qs()
        # Return two fake rows (same number of columns as Article: id, title, published, views)
        db.execute.return_value = [
            (1, "A", True, 0),
            (2, "B", False, 0),
        ]
        with pytest.raises(MultipleObjectsReturned):
            qs.get(published=True)

    def test_first_calls_limit_1(self) -> None:
        qs, db = _make_qs()
        db.execute.return_value = []
        result = qs.first()
        sql = db.execute.call_args[0][0]
        assert "LIMIT 1" in sql
        assert result is None
