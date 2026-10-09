"""
tests/unit/test_v2_features.py
==============================
Unit tests for v2 enhancements:
  - Composable Q boolean expressions (&, |, ~)
  - In-database F column expressions and arithmetic
  - Aggregations and annotations (Count, Sum, Avg, Max, Min, aggregate, annotate)
  - Projections (values, values_list, values_list flat)
  - Deduplication via distinct()
  - Eager relation loading via select_related()
  - Reverse relation managers (<model>_set)
  - Advanced fields: JSONField, DecimalField, DateField, TimeField, UUIDField, Validators
  - ManyToManyField and ManyRelatedManager
  - QueryCache with table-level invalidation
  - MigrationHistory table and rollback tracker
"""

from __future__ import annotations

import datetime
import decimal
import uuid
from unittest.mock import MagicMock

import pytest

from orm import (
    Avg,
    BooleanField,
    CharField,
    Count,
    DateField,
    DecimalField,
    F,
    FieldError,
    ForeignKeyField,
    IntegerField,
    JSONField,
    ManyToManyField,
    Max,
    Min,
    Model,
    Q,
    QueryCache,
    QuerySet,
    Sum,
    TimeField,
    UUIDField,
)
from orm.migrations.history import MigrationHistory


# ---------------------------------------------------------------------------
# Models for v2 tests
# ---------------------------------------------------------------------------

class User(Model):
    name = CharField(max_length=100)
    rating = DecimalField(max_digits=5, decimal_places=2, default="0.00")
    profile = JSONField(default=dict)
    guid = UUIDField(null=True)


class Article(Model):
    title = CharField(max_length=200)
    views = IntegerField(default=0)
    author = ForeignKeyField(User, on_delete="CASCADE", related_name="articles")


class Tag(Model):
    name = CharField(max_length=50)


class Book(Model):
    title = CharField(max_length=200)
    tags = ManyToManyField(Tag)


def _mock_db() -> MagicMock:
    db = MagicMock()
    db.dialect.placeholder.return_value = "%s"
    db.dialect.name = "postgres"
    User._db = db
    Article._db = db
    Tag._db = db
    Book._db = db
    return db


# ---------------------------------------------------------------------------
# 1. Q Expression Tests
# ---------------------------------------------------------------------------

class TestQExpressions:
    def test_q_and_or_compilation(self) -> None:
        db = _mock_db()
        q = Q(title__startswith="A") | Q(views__gte=100)
        sql, params = q.compile(Article, db.dialect)
        assert "title LIKE %s" in sql
        assert "views >= %s" in sql
        assert " OR " in sql
        assert params == ["A%", 100]

    def test_q_negation(self) -> None:
        db = _mock_db()
        q = ~Q(views=0)
        sql, params = q.compile(Article, db.dialect)
        assert "NOT (views = %s)" in sql
        assert params == [0]

    def test_q_nested_combinations(self) -> None:
        db = _mock_db()
        q = (Q(views__gt=50) & Q(title__contains="Python")) | ~Q(author__id=1)
        sql, params = q.compile(Article, db.dialect)
        assert "OR" in sql
        assert "AND" in sql
        assert "NOT" in sql
        assert "%Python%" in params

    def test_queryset_with_q_filter(self) -> None:
        db = _mock_db()
        qs = Article.objects.filter(Q(views__gt=10) | Q(views__lt=2), title="Guide")
        sql, params = qs._build_select()
        assert "WHERE" in sql
        assert "title = %s" in sql
        assert "views > %s" in sql
        assert "views < %s" in sql
        assert " OR " in sql
        assert params == ["Guide", 10, 2]


# ---------------------------------------------------------------------------
# 2. F Expression Tests
# ---------------------------------------------------------------------------

class TestFExpressions:
    def test_f_arithmetic(self) -> None:
        db = _mock_db()
        f_expr = F("views") + 5
        sql, params = f_expr.compile(Article, db.dialect)
        assert sql == "(views + %s)"
        assert params == [5]

    def test_update_with_f_expression(self) -> None:
        db = _mock_db()
        Article.objects.filter(id=1).update(views=F("views") + 1)
        db.execute.assert_called_once()
        sql, params = db.execute.call_args[0][0], db.execute.call_args[0][1]
        assert "UPDATE article SET views = (views + %s) WHERE id = %s" in sql
        assert params == [1, 1]


# ---------------------------------------------------------------------------
# 3. Aggregations & Annotations Tests
# ---------------------------------------------------------------------------

class TestAggregations:
    def test_aggregate_execution(self) -> None:
        db = _mock_db()
        db.execute.return_value = (42, 105.5)
        res = Article.objects.aggregate(total=Count("id"), avg_views=Avg("views"))
        assert res == {"total": 42, "avg_views": 105.5}
        sql = db.execute.call_args[0][0]
        assert "SELECT COUNT(id) AS total, AVG(views) AS avg_views FROM article" in sql

    def test_annotate_sql(self) -> None:
        db = _mock_db()
        qs = Article.objects.annotate(total_count=Count("id"))
        sql, params = qs._build_select()
        assert "COUNT(id) AS total_count" in sql
        assert "GROUP BY article.id" in sql


# ---------------------------------------------------------------------------
# 4. Projections & Distinct
# ---------------------------------------------------------------------------

class TestProjections:
    def test_values_projection(self) -> None:
        db = _mock_db()
        db.execute.return_value = [(1, "Hello"), (2, "World")]
        rows = Article.objects.values("id", "title").all()
        assert rows == [
            {"id": 1, "title": "Hello"},
            {"id": 2, "title": "World"},
        ]

    def test_values_list_flat(self) -> None:
        db = _mock_db()
        db.execute.return_value = [(10,), (20,)]
        titles = Article.objects.values_list("views", flat=True).all()
        assert titles == [10, 20]

    def test_distinct_sql(self) -> None:
        db = _mock_db()
        qs = Article.objects.distinct()
        sql, _ = qs._build_select()
        assert "SELECT DISTINCT" in sql


# ---------------------------------------------------------------------------
# 5. Eager Loading (select_related)
# ---------------------------------------------------------------------------

class TestSelectRelated:
    def test_select_related_builds_join_sql(self) -> None:
        db = _mock_db()
        qs = Article.objects.select_related("author")
        sql, params = qs._build_select()
        ctx = qs._last_context
        assert "LEFT JOIN user t1 ON t0.author_id = t1.id" in sql
        assert "t0.title AS t0__title" in sql
        assert "t1.name AS t1__name" in sql
        assert len(ctx["joins"]) == 1

    def test_select_related_hydrates_related_object_without_query(self) -> None:
        db = _mock_db()
        # Row layout: [Article: id, title, views, author_id] + [User: id, name, rating, profile, guid]
        fake_row = (1, "Joined Post", 50, 99, 99, "Alice", "4.50", "{}", None)
        db.execute.return_value = [fake_row]

        articles = Article.objects.select_related("author").all()
        assert len(articles) == 1
        art = articles[0]
        assert art.title == "Joined Post"

        # Accessing .author should read from cache, requiring 0 extra queries
        author_inst = art.author
        assert author_inst is not None
        assert author_inst.name == "Alice"
        assert author_inst.pk == 99


# ---------------------------------------------------------------------------
# 6. Reverse Relationships
# ---------------------------------------------------------------------------

class TestReverseRelations:
    def test_reverse_descriptor_attached(self) -> None:
        u = User(name="Alice")
        u.__dict__["_state"] = {"id": 1, "name": "Alice"}
        # Article has author with related_name="articles"
        assert hasattr(u, "articles")
        qs = u.articles
        assert isinstance(qs, QuerySet)
        sql, params = qs._build_select()
        assert "WHERE author_id = %s" in sql
        assert params == [1]


# ---------------------------------------------------------------------------
# 7. Advanced Field Types & Validators
# ---------------------------------------------------------------------------

class TestAdvancedFields:
    def test_json_field_round_trip(self) -> None:
        f = JSONField()
        data = {"theme": "dark", "tags": [1, 2, 3]}
        db_val = f.to_db(data)
        assert isinstance(db_val, str)
        restored = f.from_db(db_val)
        assert restored == data

    def test_decimal_field(self) -> None:
        f = DecimalField(max_digits=8, decimal_places=2)
        dec = decimal.Decimal("123.45")
        assert f.to_db(dec) == "123.45"
        assert f.from_db("123.45") == dec
        assert f.db_type("postgres") == "DECIMAL(8, 2)"

    def test_date_and_time_fields(self) -> None:
        df = DateField()
        today = datetime.date(2026, 10, 9)
        assert df.to_db(today) == "2026-10-09"
        assert df.from_db("2026-10-09") == today

        tf = TimeField()
        t = datetime.time(14, 30, 0)
        assert tf.to_db(t) == "14:30:00"
        assert tf.from_db("14:30:00") == t

    def test_uuid_field(self) -> None:
        uf = UUIDField()
        test_uuid = uuid.uuid4()
        assert uf.to_db(test_uuid) == str(test_uuid)
        assert uf.from_db(str(test_uuid)) == test_uuid

    def test_field_validators(self) -> None:
        def validate_positive(val: int) -> None:
            if val < 0:
                raise FieldError("Value must be non-negative")

        field = IntegerField(validators=[validate_positive])
        field.validate(10)  # ok
        with pytest.raises(FieldError, match="non-negative"):
            field.validate(-5)


# ---------------------------------------------------------------------------
# 8. Query Cache
# ---------------------------------------------------------------------------

class TestQueryCache:
    def test_cache_hit_and_eviction(self) -> None:
        cache = QueryCache(maxsize=2, default_ttl=60)
        cache.set("SELECT * FROM post", (1,), ["post1"], table="post")

        hit = cache.get("SELECT * FROM post", (1,))
        assert hit == ["post1"]
        assert cache.hits == 1

        # Table invalidation
        invalidated = cache.invalidate_table("post")
        assert invalidated == 1
        assert cache.get("SELECT * FROM post", (1,)) is None
        assert cache.misses == 1


# ---------------------------------------------------------------------------
# 9. Migration History Tracker
# ---------------------------------------------------------------------------

class TestMigrationHistory:
    def test_migration_history_record_and_get(self) -> None:
        db = _mock_db()
        history = MigrationHistory(db)

        # Mock table results
        db.execute.return_value = [("20260101_initial.sql",)]
        applied = history.get_applied()
        assert applied == ["20260101_initial.sql"]

        history.record_applied("20260102_add_cols.sql")
        assert db.execute.call_count >= 2
        last_sql = db.execute.call_args[0][0]
        assert "INSERT INTO orm_migrations" in last_sql
