"""
custom-orm
==========
A lightweight, from-scratch ORM for PostgreSQL and MySQL.

Typical usage::

    from orm import Database, Model, CharField, IntegerField, ForeignKeyField

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
    posts = Post.objects.filter(author=alice).order_by("-title").all()
"""

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
from orm.fields import (
    BooleanField,
    CharField,
    DateTimeField,
    Field,
    ForeignKeyField,
    IntegerField,
    TextField,
)
from orm.models import Model
from orm.queryset import QuerySet

__version__ = "0.1.0"

__all__ = [
    # Core
    "Database",
    "Model",
    "QuerySet",
    # Fields
    "Field",
    "IntegerField",
    "CharField",
    "TextField",
    "BooleanField",
    "DateTimeField",
    "ForeignKeyField",
    # Exceptions
    "OrmError",
    "DoesNotExist",
    "MultipleObjectsReturned",
    "IntegrityError",
    "ConnectionError",
    "MigrationError",
    "FieldError",
]
