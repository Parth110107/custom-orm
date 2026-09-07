"""
orm/dialects/__init__.py
------------------------
Convenience re-export for both built-in dialects.

Importing from ``orm.dialects`` instead of the individual modules keeps the
public surface area stable: if a dialect is ever moved or refactored,
only this file needs updating, not every caller.
"""

from orm.dialects.postgres import PostgresDialect
from orm.dialects.mysql import MySQLDialect

__all__ = ["PostgresDialect", "MySQLDialect"]
