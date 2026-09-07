"""
tests/conftest.py
=================
Shared pytest fixtures and configuration.

Marks:
    integration — tests that require live Docker Postgres/MySQL.
    Run with: pytest --integration

Unit tests use mock databases and never touch a real DB.
"""

from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--integration",
        action="store_true",
        default=False,
        help="Run integration tests (requires Docker Postgres + MySQL)",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "integration: mark test as requiring a live database (Docker)",
    )


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    if not config.getoption("--integration"):
        skip_integration = pytest.mark.skip(
            reason="Pass --integration to run integration tests"
        )
        for item in items:
            if "integration" in item.keywords:
                item.add_marker(skip_integration)
