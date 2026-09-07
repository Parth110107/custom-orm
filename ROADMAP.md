# Roadmap

## v1 (current) — Foundation

> Solid core: single-table CRUD, basic querying, two dialects, additive migrations, connection pooling, and one-to-many relationships.

- ✅ Core field types (`CharField`, `TextField`, `IntegerField`, `BooleanField`, `ForeignKeyField`, …)
- ✅ Single-table CRUD via `Model.objects` queryset API
- ✅ PostgreSQL 16 and MySQL 8 support
- ✅ Additive-only migration generation (`makemigrations` / `migrate`)
- ✅ Thread-safe connection pool (`min_conn` / `max_conn`)
- ✅ One-to-many relationships via `ForeignKeyField` (lazy-load on attribute access)

---

## Deferred to v2 — Relationship Improvements

- `select_related(*fields)` — eliminates N+1 queries for `ForeignKeyField` by JOINing in a single query
- `prefetch_related(*fields)` — efficient batch-loading for reverse relations
- Many-to-many relationships via explicit through-models
- Reverse relation managers (`author.post_set.all()`, `author.post_set.filter(...)`)

---

## Deferred to v2 — Query Enhancements

- `annotate()` / `aggregate()` — `COUNT`, `SUM`, `AVG`, `MAX`, `MIN` with grouping
- `values()` / `values_list()` — return `dict` or `tuple` projections instead of model instances
- `distinct()` — deduplicate results at the SQL level
- `Q` objects — OR conditions and arbitrarily nested boolean expressions
- `F` expressions — reference column values in filter expressions and updates (e.g. `F("views") + 1`)

---

## Deferred to v2 — Migration System

- Migration history table — track which migrations have been applied, prevent double-runs
- `orm-migrate migrate --rollback` — revert the last applied migration
- Column rename and drop with an explicit `--confirm` flag to prevent accidental data loss
- Data migrations — Python callable steps alongside SQL DDL, not just schema changes

---

## Deferred to v2 — Performance

- `mysqlclient` as an optional C-extension alternative to PyMySQL for higher throughput
- Advanced connection pool — minimum warm connections, periodic health-check pings, configurable acquisition timeout
- Query result caching with pluggable backends (in-process dict, Redis, memcached)

---

## Deferred to v3 — Async Support

- Full `async`/`await` API using `psycopg[async]` (PostgreSQL) and `aiomysql` (MySQL)
- Async connection pool with `async with db.pool.acquire() as conn:`
- Async context manager for transactions (`async with db.transaction():`)

---

## Out of Scope (by design)

These features are explicitly **not planned** for any version of `custom-orm`:

| Feature | Reason |
|---|---|
| DB sharding / replication | Significant operational complexity; use a dedicated proxy (e.g. PgBouncer, ProxySQL) |
| Admin UI / code scaffolding | Out of the ORM's responsibility — use a separate tool |
| Multi-tenancy | Schema-level isolation is an application concern |
| SQLite, MSSQL, Oracle | Focused on PostgreSQL and MySQL; additional dialects not planned |
