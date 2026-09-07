"""
tests/integration/test_mysql.py
=================================
Full-stack integration tests against a live MySQL database.

Run with:
    docker compose up -d
    pytest tests/integration/test_mysql.py --integration -v

MySQL-specific notes:
  - TINYINT(1) is used for BooleanField (from_db returns Python bool)
  - CASCADE delete requires InnoDB engine (set by dialect)
  - No RETURNING id \u2014 uses cursor.lastrowid instead
  - Transactions require InnoDB tables
"""

from __future__ import annotations

import pytest

from orm.db import Database
from orm.exceptions import DoesNotExist
from tests.integration.conftest import Author, Post


pytestmark = pytest.mark.integration


class TestMySQLCRUD:
    def test_create_and_get(self, mysql_db: Database) -> None:
        alice = Author.create(name="Alice", email="alice@example.com")
        assert alice.pk is not None
        fetched = Author.objects.get(id=alice.pk)
        assert fetched._state["name"] == "Alice"

    def test_get_raises_does_not_exist(self, mysql_db: Database) -> None:
        with pytest.raises(DoesNotExist):
            Author.objects.get(id=999999)

    def test_filter(self, mysql_db: Database) -> None:
        Author.create(name="Bob", email="bob@example.com")
        Author.create(name="Carol", email="carol@example.com")
        results = Author.objects.filter(name="Bob").all()
        assert len(results) == 1

    def test_exclude(self, mysql_db: Database) -> None:
        Author.create(name="Dave", email="dave@example.com")
        Author.create(name="Eve", email="eve@example.com")
        results = Author.objects.exclude(name="Dave").all()
        names = [r._state["name"] for r in results]
        assert "Dave" not in names

    def test_order_by_asc(self, mysql_db: Database) -> None:
        Author.create(name="Zara", email="z@example.com")
        Author.create(name="Anna", email="an@example.com")
        results = Author.objects.order_by("name").all()
        names = [r._state["name"] for r in results]
        assert names == sorted(names)

    def test_limit(self, mysql_db: Database) -> None:
        for i in range(5):
            Author.create(name=f"User{i}", email=f"u{i}@example.com")
        results = Author.objects.limit(3).all()
        assert len(results) == 3

    def test_update(self, mysql_db: Database) -> None:
        alice = Author.create(name="Alice", email="old@example.com")
        Author.objects.filter(id=alice.pk).update(email="new@example.com")
        fetched = Author.objects.get(id=alice.pk)
        assert fetched._state["email"] == "new@example.com"

    def test_delete(self, mysql_db: Database) -> None:
        author = Author.create(name="DeleteMe", email="del@example.com")
        pk = author.pk
        Author.objects.filter(id=pk).delete()
        with pytest.raises(DoesNotExist):
            Author.objects.get(id=pk)

    def test_boolean_field_round_trip(self, mysql_db: Database) -> None:
        """
        MySQL stores BOOLEAN as TINYINT(1); verify Python bool is preserved.
        """
        alice = Author.create(name="Alice", email="a@example.com")
        post_true = Post.create(
            title="Published", body=".", published=True, author=alice
        )
        post_false = Post.create(
            title="Draft", body=".", published=False, author=alice
        )
        fetched_true = Post.objects.get(id=post_true.pk)
        fetched_false = Post.objects.get(id=post_false.pk)
        assert fetched_true._state["published"] is True
        assert fetched_false._state["published"] is False


class TestMySQLForeignKey:
    def test_fk_lazy_load(self, mysql_db: Database) -> None:
        alice = Author.create(name="Alice", email="a@example.com")
        post = Post.create(title="Post", body=".", published=False, author=alice)
        fetched = Post.objects.get(id=post.pk)
        related = fetched.author
        assert related is not None
        assert related.pk == alice.pk

    def test_filter_by_fk_id(self, mysql_db: Database) -> None:
        alice = Author.create(name="Alice", email="a@example.com")
        bob = Author.create(name="Bob", email="b@example.com")
        Post.create(title="AP", body=".", published=True, author=alice)
        Post.create(title="BP", body=".", published=True, author=bob)
        alice_posts = Post.objects.filter(author_id=alice.pk).all()
        assert len(alice_posts) == 1

    def test_cascade_delete(self, mysql_db: Database) -> None:
        alice = Author.create(name="Alice", email="a@example.com")
        Post.create(title="Post", body=".", published=True, author=alice)
        pk = alice.pk
        alice.delete()
        # InnoDB CASCADE should remove dependent posts
        remaining = Post.objects.filter(author_id=pk).all()
        assert remaining == []


class TestMySQLTransactions:
    def test_transaction_commit(self, mysql_db: Database) -> None:
        with mysql_db.transaction():
            Author.create(name="TxAuthor", email="tx@example.com")
        assert Author.objects.filter(name="TxAuthor").count() == 1

    def test_transaction_rollback(self, mysql_db: Database) -> None:
        try:
            with mysql_db.transaction():
                Author.create(name="RollbackMe", email="rb@example.com")
                raise ValueError("abort")
        except ValueError:
            pass
        assert Author.objects.filter(name="RollbackMe").count() == 0


class TestMySQLMigrations:
    def test_detect_new_table_and_apply(self, mysql_db: Database) -> None:
        from orm.fields import CharField
        from orm.migrations.detector import SchemaDetector
        from orm.migrations.writer import MigrationWriter

        class Tag(Model):
            label = CharField(max_length=50)  # type: ignore[name-defined]

        mysql_db.register(Tag)

        detector = SchemaDetector(mysql_db, [Tag])
        diff = detector.detect()
        assert Tag in diff.new_tables

        writer = MigrationWriter(mysql_db)
        writer.apply_diff(diff)

        detector2 = SchemaDetector(mysql_db, [Tag])
        diff2 = detector2.detect()
        assert Tag not in diff2.new_tables

        # Cleanup
        mysql_db.execute("DROP TABLE IF EXISTS tag", fetch="none")
