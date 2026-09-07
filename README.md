# custom-orm

> A lightweight, from-scratch ORM for PostgreSQL and MySQL. No SQLAlchemy. No magic.

![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
![MIT License](https://img.shields.io/badge/license-MIT-green)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-336791?logo=postgresql&logoColor=white)
![MySQL](https://img.shields.io/badge/MySQL-8-4479A1?logo=mysql&logoColor=white)

---

## Install

```bash
pip install custom-orm
```

Or from source:

```bash
git clone https://github.com/your-org/custom-orm.git
cd custom-orm
pip install -e ".[dev]"
```

---

## Quickstart

```python
from orm import Database, Model
from orm.fields import CharField, TextField, BooleanField, ForeignKeyField

db = Database("postgresql://testuser:testpassword@localhost:5432/mydb")

class Author(Model):
    class Meta:
        db = db

    name  = CharField(max_length=100)
    email = CharField(max_length=255, unique=True)

class Post(Model):
    class Meta:
        db = db

    title     = CharField(max_length=200)
    body      = TextField()
    published = BooleanField(default=False)
    author    = ForeignKeyField(Author, on_delete="CASCADE")

# Create tables
db.create_tables([Author, Post])

# Create
alice = Author.objects.create(name="Alice", email="alice@example.com")

# Get
author = Author.objects.get(id=1)

# Filter, order, limit
posts = Post.objects.filter(published=True).order_by("-title").limit(10)

# Bulk update
Post.objects.filter(author=alice).update(published=True)

# Delete
Post.objects.filter(published=False).delete()

# ForeignKey lazy-load
post = Post.objects.get(id=1)
print(post.author.name)   # Issues a SELECT for the related Author on first access

# Transactions
with db.transaction():
    alice = Author.objects.create(name="Alice", email="alice@example.com")
    Post.objects.create(
        title="Hello World",
        body="First post.",
        published=True,
        author=alice,
    )
```

---

## Configuration

Provide a DSN string to `Database`. Both dialects are supported:

```
# PostgreSQL
postgresql://user:pass@localhost:5432/mydb

# MySQL
mysql://user:pass@localhost:3306/mydb
```

---

## Running Migrations

```bash
# Generate a new migration from model changes
orm-migrate makemigrations

# Apply pending migrations
orm-migrate migrate
```

> **Note:** v1 supports additive-only migrations (new tables, new columns). Destructive operations (DROP, RENAME) are deferred to v2.

---

## Connection Pooling

Pass `min_conn` and `max_conn` to enable a thread-safe connection pool:

```python
db = Database("postgresql://user:pass@localhost:5432/mydb", min_conn=2, max_conn=20)
```

---

## What's Not Supported Yet (v1)

| Feature | Status |
|---|---|
| Many-to-many relationships | ❌ Deferred to v2 |
| `async`/`await` API | ❌ Deferred to v3 |
| `select_related` / `prefetch_related` (N+1 prevention) | ❌ Deferred to v2 |
| Migration rollback / history tracking | ❌ Deferred to v2 |
| Query result caching | ❌ Deferred to v2 |
| DB sharding / replication | 🚫 Out of scope |
| Admin UI | 🚫 Out of scope |

See [`ROADMAP.md`](ROADMAP.md) for the full list and planned versions.

---

## Running Tests

```bash
# Unit tests (no Docker required)
pytest tests/unit/ -v

# Integration tests (requires Docker)
docker compose up -d
pytest tests/ --integration -v
```

---

## Development

```bash
# Install with dev extras
pip install -e ".[dev]"

# Type checking
mypy orm/

# Unit tests with coverage
pytest tests/unit/ -v --cov=orm
```
