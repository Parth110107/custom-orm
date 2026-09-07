"""
tests/integration/test_postgres.py
====================================
Full-stack integration tests against a live PostgreSQL database.

Run with:
    docker compose up -d
    pytest tests/integration/test_postgres.py --integration -v

Tests cover:
  - Table creation
  - Full CRUD (create, get, filter, update, delete)
  - ForeignKey lazy-load
  - Queryset chaining (order_by, limit, exclude)
  - Transaction commit
  - Transaction rollback
  - Migration: detect new table + apply
"""

from __future__ import annotations

import pytest

from orm.db import Database
from orm.exceptions import DoesNotExist
from tests.integration.conftest import Author, Post


pytestmark = pytest.mark.integration


class TestPostgresCRUD:
    def test_create_and_get(self, pg_db: Database) -> None:
        alice = Author.create(name="Alice", email="alice@example.com")
        assert alice.pk is not None

        fetched = Author.objects.get(id=alice.pk)
        assert fetched.pk == alice.pk
        assert fetched._state["name"] == "Alice"

    def test_get_raises_does_not_exist(self, pg_db: Database) -> None:
        with pytest.raises(DoesNotExist):
            Author.objects.get(id=999999)

    def test_filter_returns_matching_rows(self, pg_db: Database) -> None:
        Author.create(name="Bob", email="bob@example.com")
        Author.create(name="Carol", email="carol@example.com")
        results = Author.objects.filter(name="Bob").all()
        assert len(results) == 1
        assert results[0]._state["name"] == "Bob"

    def test_exclude(self, pg_db: Database) -> None:
        Author.create(name="Dave", email="dave@example.com")
        Author.create(name="Eve", email="eve@example.com")
        results = Author.objects.exclude(name="Dave").all()
        names = [r._state["name"] for r in results]
        assert "Dave" not in names
        assert "Eve" in names

    def test_order_by_desc(self, pg_db: Database) -> None:
        Author.create(name="Alice", email="a@example.com")
        Author.create(name="Bob", email="b@example.com")
        Author.create(name="Carol", email="c@example.com")
        results = Author.objects.order_by("-name").all()
        names = [r._state["name"] for r in results]
        assert names == sorted(names, reverse=True)

    def test_limit(self, pg_db: Database) -> None:
        for i in range(5):
            Author.create(name=f"User{i}", email=f"u{i}@example.com")
        results = Author.objects.order_by("name").limit(2).all()
        assert len(results) == 2

    def test_count(self, pg_db: Database) -> None:
        Author.create(name="X", email="x@example.com")
        Author.create(name="Y", email="y@example.com")
        assert Author.objects.count() == 2

    def test_update(self, pg_db: Database) -> None:
        alice = Author.create(name="Alice", email="old@example.com")
        Author.objects.filter(id=alice.pk).update(email="new@example.com")
        fetched = Author.objects.get(id=alice.pk)
        assert fetched._state["email"] == "new@example.com"

    def test_delete(self, pg_db: Database) -> None:
        author = Author.create(name="DeleteMe", email="del@example.com")
        pk = author.pk
        Author.objects.filter(id=pk).delete()
        with pytest.raises(DoesNotExist):
            Author.objects.get(id=pk)

    def test_instance_save_update(self, pg_db: Database) -> None:
        alice = Author.create(name="Alice", email="a@example.com")
        alice._state["email"] = "updated@example.com"
        alice.save()
        fetched = Author.objects.get(id=alice.pk)
        assert fetched._state["email"] == "updated@example.com"

    def test_instance_delete(self, pg_db: Database) -> None:
        author = Author.create(name="Ghost", email="ghost@example.com")
        pk = author.pk
        author.delete()
        with pytest.raises(DoesNotExist):
            Author.objects.get(id=pk)


class TestPostgresForeignKey:
    def test_create_post_with_author(self, pg_db: Database) -> None:
        alice = Author.create(name="Alice", email="a@example.com")
        post = Post.create(
            title="Hello World",
            body="Content here.",
            published=True,
            author=alice,
        )
        assert post.pk is not None
        assert post._state["author_id"] == alice.pk

    def test_fk_lazy_load(self, pg_db: Database) -> None:
        alice = Author.create(name="Alice", email="a@example.com")
        post = Post.create(title="Post", body=".", published=False, author=alice)

        fetched_post = Post.objects.get(id=post.pk)
        # Access the FK descriptor — should fire a SELECT
        related_author = fetched_post.author
        assert related_author is not None
        assert related_author.pk == alice.pk

    def test_filter_by_fk_id(self, pg_db: Database) -> None:
        alice = Author.create(name="Alice", email="a@example.com")
        bob = Author.create(name="Bob", email="b@example.com")
        Post.create(title="Alice Post", body=".", published=True, author=alice)
        Post.create(title="Bob Post", body=".", published=True, author=bob)
        posts = Post.objects.filter(author_id=alice.pk).all()
        assert len(posts) == 1
        assert posts[0]._state["title"] == "Alice Post"

    def test_cascade_delete(self, pg_db: Database) -> None:
        alice = Author.create(name="Alice", email="a@example.com")
        Post.create(title="Post", body=".", published=True, author=alice)
        # Deleting author should cascade to posts
        author_pk = alice.pk
        alice.delete()
        remaining = Post.objects.filter(author_id=author_pk).all()
        assert remaining == []


class TestPostgresTransactions:
    def test_transaction_commit(self, pg_db: Database) -> None:
        with pg_db.transaction():
            Author.create(name="TxAuthor", email="tx@example.com")
        # Verify it persisted
        assert Author.objects.filter(name="TxAuthor").count() == 1

    def test_transaction_rollback_on_exception(self, pg_db: Database) -> None:
        try:
            with pg_db.transaction():
                Author.create(name="Rollback", email="rb@example.com")
                raise ValueError("deliberate error")
        except ValueError:
            pass
        # Row should NOT be present
        assert Author.objects.filter(name="Rollback").count() == 0

    def test_multiple_inserts_in_transaction(self, pg_db: Database) -> None:
        with pg_db.transaction():
            alice = Author.create(name="Alice", email="a@example.com")
            Post.create(title="P1", body=".", published=True, author=alice)
            Post.create(title="P2", body=".", published=False, author=alice)
        assert Post.objects.filter(author_id=alice.pk).count() == 2


class TestPostgresMigrations:
    def test_detect_and_apply_new_table(self, pg_db: Database) -> None:
        from orm.fields import IntegerField
        from orm.migrations.detector import SchemaDetector
        from orm.migrations.writer import MigrationWriter

        class Tag(Model):
            label = CharField(max_length=50)  # type: ignore[name-defined]

        pg_db.register(Tag)

        detector = SchemaDetector(pg_db, [Tag])
        diff = detector.detect()
        assert Tag in diff.new_tables

        writer = MigrationWriter(pg_db)
        writer.apply_diff(diff)

        # Table should now exist
        detector2 = SchemaDetector(pg_db, [Tag])
        diff2 = detector2.detect()
        assert Tag not in diff2.new_tables

        # Cleanup
        pg_db.execute("DROP TABLE IF EXISTS tag CASCADE", fetch="none")
