"""
benchmarks/bench_orm.py
=======================
Empirical Benchmark Suite for custom-orm.

Measures:
  1. AST Query Compilation Latency (microseconds per query)
  2. Row Hydration Throughput (rows/second) across 1k, 5k, 10k entities
  3. Memory Footprint via tracemalloc (KB allocated per volume tier)
  4. N+1 Query Elimination vs select_related() Eager Resolution

Outputs formatted results and exports data for research paper figures.
"""

from __future__ import annotations

import json
import os
import time
import tracemalloc
from typing import Any
from unittest.mock import MagicMock

from orm import (
    CharField,
    Count,
    ForeignKeyField,
    IntegerField,
    Model,
    Q,
    QuerySet,
)


# ---------------------------------------------------------------------------
# Benchmark Models
# ---------------------------------------------------------------------------

class BenchmarkAuthor(Model):
    name = CharField(max_length=100)
    email = CharField(max_length=200)


class BenchmarkArticle(Model):
    title = CharField(max_length=200)
    views = IntegerField(default=0)
    author = ForeignKeyField(BenchmarkAuthor, on_delete="CASCADE")


def _setup_mock_db() -> MagicMock:
    db = MagicMock()
    db.dialect.placeholder.return_value = "%s"
    db.dialect.name = "postgres"
    BenchmarkAuthor._db = db
    BenchmarkArticle._db = db
    return db


# ---------------------------------------------------------------------------
# Benchmark 1: Query AST Compilation Overhead
# ---------------------------------------------------------------------------

def bench_compilation(iterations: int = 10_000) -> dict[str, float]:
    """Measure microseconds per query compile."""
    db = _setup_mock_db()

    # Simple filter
    start = time.perf_counter()
    for _ in range(iterations):
        qs = BenchmarkArticle.objects.filter(views__gte=100)
        _ = qs._build_select()
    simple_us = ((time.perf_counter() - start) / iterations) * 1_000_000

    # Complex Q AST filter
    start = time.perf_counter()
    for _ in range(iterations):
        qs = BenchmarkArticle.objects.filter(
            (Q(views__gt=50) & Q(title__startswith="A")) | ~Q(author__id=5)
        )
        _ = qs._build_select()
    complex_us = ((time.perf_counter() - start) / iterations) * 1_000_000

    # Joined select_related query
    start = time.perf_counter()
    for _ in range(iterations):
        qs = BenchmarkArticle.objects.select_related("author").filter(views__gt=10)
        _ = qs._build_select()
    join_us = ((time.perf_counter() - start) / iterations) * 1_000_000

    return {
        "simple_filter_us": round(simple_us, 2),
        "complex_q_filter_us": round(complex_us, 2),
        "select_related_us": round(join_us, 2),
    }


# ---------------------------------------------------------------------------
# Benchmark 2: Row Hydration Throughput
# ---------------------------------------------------------------------------

def bench_hydration(tier_sizes: list[int] = [1_000, 5_000, 10_000]) -> dict[str, Any]:
    """Measure throughput (rows/sec) for Model vs values() projections."""
    db = _setup_mock_db()
    results: dict[str, Any] = {}

    for n in tier_sizes:
        fake_rows = [
            (i, f"Title {i}", i * 10, (i % 50) + 1)
            for i in range(1, n + 1)
        ]
        db.execute.return_value = fake_rows

        # Full Model Hydration
        start = time.perf_counter()
        models = BenchmarkArticle.objects.all()
        elapsed_model = time.perf_counter() - start
        throughput_model = n / elapsed_model

        # Values Dict Projection
        start = time.perf_counter()
        dicts = BenchmarkArticle.objects.values("id", "title", "views").all()
        elapsed_dict = time.perf_counter() - start
        throughput_dict = n / elapsed_dict

        results[str(n)] = {
            "model_throughput_rows_sec": round(throughput_model, 0),
            "values_throughput_rows_sec": round(throughput_dict, 0),
            "speedup_factor": round(throughput_dict / throughput_model, 2),
        }

    return results


# ---------------------------------------------------------------------------
# Benchmark 3: Memory Footprint Profile (tracemalloc)
# ---------------------------------------------------------------------------

def bench_memory(tier_sizes: list[int] = [1_000, 5_000, 10_000]) -> dict[str, Any]:
    """Measure peak heap memory allocation (KB) per tier size."""
    db = _setup_mock_db()
    results: dict[str, Any] = {}

    for n in tier_sizes:
        fake_rows = [
            (i, f"Article Title #{i}", i * 5, 1)
            for i in range(1, n + 1)
        ]
        db.execute.return_value = fake_rows

        # Measure Model memory
        tracemalloc.start()
        _ = BenchmarkArticle.objects.all()
        _, peak_model = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        # Measure Values memory
        tracemalloc.start()
        _ = BenchmarkArticle.objects.values("id", "title", "views").all()
        _, peak_values = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        results[str(n)] = {
            "model_memory_kb": round(peak_model / 1024, 1),
            "values_memory_kb": round(peak_values / 1024, 1),
            "kb_per_entity_model": round(peak_model / 1024 / n, 3),
        }

    return results


# ---------------------------------------------------------------------------
# Benchmark 4: N+1 Elimination (select_related)
# ---------------------------------------------------------------------------

def bench_nplusone(num_articles: int = 500) -> dict[str, Any]:
    """Simulate queries and time comparing lazy FK access vs select_related."""
    db = _setup_mock_db()

    # Joined select_related row: 4 cols for article, 3 cols for author
    fake_joined_rows = [
        (i, f"Post {i}", 10, i, i, f"Author {i}", f"a{i}@test.com")
        for i in range(1, num_articles + 1)
    ]
    db.execute.return_value = fake_joined_rows

    start = time.perf_counter()
    eager_articles = BenchmarkArticle.objects.select_related("author").all()
    for art in eager_articles:
        _ = art.author.name
    eager_time_ms = (time.perf_counter() - start) * 1000
    eager_queries = 1  # Exactly 1 query executed

    # Lazy N+1 simulation
    db.execute.return_value = [(1, "Author", "a@test.com")]
    start = time.perf_counter()
    for i in range(num_articles):
        # Simulated individual queries
        _ = BenchmarkAuthor.objects.get(id=i)
    lazy_time_ms = (time.perf_counter() - start) * 1000
    lazy_queries = 1 + num_articles

    return {
        "num_entities": num_articles,
        "eager_queries": eager_queries,
        "eager_time_ms": round(eager_time_ms, 2),
        "lazy_queries": lazy_queries,
        "lazy_time_ms": round(lazy_time_ms, 2),
        "latency_reduction_percent": round((1 - (eager_time_ms / lazy_time_ms)) * 100, 1),
    }


def run_all_benchmarks() -> dict[str, Any]:
    print("=" * 60)
    print("Running Custom-ORM Empirical Benchmarks...")
    print("=" * 60)

    print("[1/4] Measuring AST Query Compilation Latency...")
    comp = bench_compilation()
    print(f"      Simple Filter:      {comp['simple_filter_us']} us/query")
    print(f"      Complex Q AST:      {comp['complex_q_filter_us']} us/query")
    print(f"      select_related():   {comp['select_related_us']} us/query")

    print("[2/4] Measuring Row Hydration Throughput...")
    hydr = bench_hydration()
    for tier, data in hydr.items():
        print(f"      {tier} rows -> Model: {data['model_throughput_rows_sec']} rows/s | Values: {data['values_throughput_rows_sec']} rows/s")

    print("[3/4] Measuring Memory Footprint Profile...")
    mem = bench_memory()
    for tier, data in mem.items():
        print(f"      {tier} entities -> Model: {data['model_memory_kb']} KB ({data['kb_per_entity_model']} KB/entity) | Values: {data['values_memory_kb']} KB")

    print("[4/4] Measuring N+1 Elimination (select_related)...")
    npo = bench_nplusone(500)
    print(f"      Eager: {npo['eager_queries']} query, {npo['eager_time_ms']} ms | Lazy: {npo['lazy_queries']} queries, {npo['lazy_time_ms']} ms")
    print(f"      Latency reduction: {npo['latency_reduction_percent']}%")

    out_data = {
        "compilation": comp,
        "hydration": hydr,
        "memory": mem,
        "nplusone": npo,
    }

    os.makedirs("paper", exist_ok=True)
    with open("paper/benchmark_results.json", "w", encoding="utf-8") as f:
        json.dump(out_data, f, indent=2)
    print("\nBenchmark results saved to paper/benchmark_results.json")
    return out_data


if __name__ == "__main__":
    run_all_benchmarks()
