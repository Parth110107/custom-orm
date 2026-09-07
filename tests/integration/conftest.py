"""
tests/integration/conftest.py
==============================
Fixtures for integration tests.

These tests require a live database.  Run with:
    docker compose up -d
    pytest tests/ --integration -v

Environment variables (override defaults):
    PG_URL   \u2014 PostgreSQL DSN (default: postgresql://testuser:testpassword@localhost:5432/test_orm)
    MYSQL_URL \u2014 MySQL DSN (default: mysql://testuser:testpassword@localhost:3306/test_orm)
"""

from __future__ import annotations

import os

import pytest

from orm.db import Database
from orm.fields import BooleanField, CharField, DateTimeField, ForeignKeyField, IntegerField, TextField
from orm.models import Model

# ---------------------------------------------------------------------------
# DSN defaults (match docker-compose.yml)
# ---------------------------------------------------------------------------

DEFAULT_PG_URL = "postgresql://testuser:testpassword@localhost:5432/test_orm"
DEFAULT_MYSQL_URL = "mysql://testuser:testpassword@localhost:3306/test_orm"


# ---------------------------------------------------------------------------
# Model definitions shared by both integration test modules
# ---------------------------------------------------------------------------

class Author(Model):
    name  = CharField(max_length=100)
    email = CharField(max_length=200)


class Post(Model):
    title     = CharField(max_length=200)
    body      = TextField()
    published = BooleanField(default=False)
    author    = ForeignKeyField(Author, on_delete="CASCADE")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="function")
def pg_db() -> Database:  # type: ignore[return]
    """
    Provide a PostgreSQL Database, create tables, yield, then drop tables.
    Skips if PG_URL not reachable.
    """
    url = os.environ.get("PG_URL", DEFAULT_PG_URL)
    try:
        db = Database(url, max_conn=5)
        db.register(Author, Post)
        # Drop and recreate tables for a clean slate
        db.execute("DROP TABLE IF EXISTS post CASCADE", fetch="none")
        db.execute("DROP TABLE IF EXISTS author CASCADE", fetch="none")
        db.create_tables([Author, Post])
        yield db
        db.execute("DROP TABLE IF EXISTS post CASCADE", fetch="none")
        db.execute("DROP TABLE IF EXISTS author CASCADE", fetch="none")
        db.close_all()
    except Exception as exc:
        pytest.skip(f"PostgreSQL not available: {exc}")


@pytest.fixture(scope="function")
def mysql_db() -> Database:  # type: ignore[return]
    """
    Provide a MySQL Database, create tables, yield, then drop tables.
    Skips if MYSQL_URL not reachable.
    """
    url = os.environ.get("MYSQL_URL", DEFAULT_MYSQL_URL)
    try:
        db = Database(url, max_conn=5)
        db.register(Author, Post)
        db.execute("DROP TABLE IF EXISTS post", fetch="none")
        db.execute("DROP TABLE IF EXISTS author", fetch="none")
        db.create_tables([Author, Post])
        yield db
        db.execute("DROP TABLE IF EXISTS post", fetch="none")
        db.execute("DROP TABLE IF EXISTS author", fetch="none")
        db.close_all()
    except Exception as exc:
        pytest.skip(f"MySQL not available: {exc}")
