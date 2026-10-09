"""
custom-orm
==========
A lightweight, from-scratch ORM for PostgreSQL and MySQL.

Typical usage::

    from orm import Database, Model, CharField, IntegerField, ForeignKeyField, Q, F

    db = Database("postgresql://user:pass@localhost/mydb")

    class Author(Model):
        name = CharField(max_length=100)

    class Post(Model):
        title  = CharField(max_length=200)
        author = ForeignKeyField(Author, on_delete="CASCADE")

    db.register(Author, Post)
    db.create_tables([Author, Post])

    alice = Author.create(name="Alice")
    post  = Post.create(title="Hello", author=alice)
    posts = Post.objects.select_related("author").filter(Q(title__startswith="H") | Q(title__startswith="W")).all()
"""

from orm.cache import QueryCache
from orm.db import Database
from orm.exceptions import (
    ConnectionError,
    DoesNotExist,
    FieldError,
    IntegrityError,
    MigrationError,
    MultipleObjectsReturned,
    OrmError,
)
from orm.expressions import (
    Aggregate,
    Avg,
    Count,
    F,
    Max,
    Min,
    Q,
    Sum,
)
from orm.fields import (
    BooleanField,
    CharField,
    DateField,
    DateTimeField,
    DecimalField,
    Field,
    ForeignKeyField,
    IntegerField,
    JSONField,
    ManyToManyField,
    TextField,
    TimeField,
    UUIDField,
)
from orm.models import Model
from orm.queryset import QuerySet

__version__ = "0.2.0"

__all__ = [
    # Core
    "Database",
    "Model",
    "QuerySet",
    "QueryCache",
    # Expressions
    "Q",
    "F",
    "Aggregate",
    "Count",
    "Sum",
    "Avg",
    "Max",
    "Min",
    # Fields
    "Field",
    "IntegerField",
    "CharField",
    "TextField",
    "BooleanField",
    "DateTimeField",
    "DateField",
    "TimeField",
    "DecimalField",
    "JSONField",
    "UUIDField",
    "ForeignKeyField",
    "ManyToManyField",
    # Exceptions
    "OrmError",
    "DoesNotExist",
    "MultipleObjectsReturned",
    "IntegrityError",
    "ConnectionError",
    "MigrationError",
    "FieldError",
]
