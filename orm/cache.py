"""
orm/cache.py
============
Thread-safe in-memory LRU Query Result Cache with automated invalidation.

Provides configurable TTL, LRU eviction, and automatic invalidation on
table modifications (INSERT, UPDATE, DELETE).
"""

from __future__ import annotations

import collections
import threading
import time
from typing import Any


class QueryCache:
    """
    LRU Cache for parameterized SQL query results.
    """

    def __init__(self, maxsize: int = 256, default_ttl: float = 60.0) -> None:
        self.maxsize = maxsize
        self.default_ttl = default_ttl
        self._cache: collections.OrderedDict[str, tuple[Any, float]] = collections.OrderedDict()
        self._table_keys: dict[str, set[str]] = collections.defaultdict(set)
        self._lock = threading.Lock()
        self.hits: int = 0
        self.misses: int = 0

    def _make_key(self, sql: str, params: tuple[Any, ...]) -> str:
        return f"{sql}::{params}"

    def get(self, sql: str, params: tuple[Any, ...]) -> Any:
        """Retrieve cached result or return None if expired / missing."""
        key = self._make_key(sql, params)
        with self._lock:
            if key not in self._cache:
                self.misses += 1
                return None

            result, expiry = self._cache[key]
            if time.time() > expiry:
                del self._cache[key]
                self.misses += 1
                return None

            self._cache.move_to_end(key)
            self.hits += 1
            return result

    def set(
        self,
        sql: str,
        params: tuple[Any, ...],
        value: Any,
        table: str | None = None,
        ttl: float | None = None,
    ) -> None:
        """Store query results in cache with TTL and table index."""
        key = self._make_key(sql, params)
        expiry = time.time() + (ttl if ttl is not None else self.default_ttl)

        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            self._cache[key] = (value, expiry)

            if table:
                self._table_keys[table.lower()].add(key)

            if len(self._cache) > self.maxsize:
                oldest_key, _ = self._cache.popitem(last=False)
                for tbl_keys in self._table_keys.values():
                    tbl_keys.discard(oldest_key)

    def invalidate_table(self, table: str) -> int:
        """Evict all cached entries referencing the given table."""
        with self._lock:
            table_lower = table.lower()
            keys = self._table_keys.pop(table_lower, set())
            for key in keys:
                self._cache.pop(key, None)
            return len(keys)

    def clear(self) -> None:
        """Evict all cached entries and reset statistics."""
        with self._lock:
            self._cache.clear()
            self._table_keys.clear()
            self.hits = 0
            self.misses = 0
