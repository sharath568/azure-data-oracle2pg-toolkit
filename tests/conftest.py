"""Shared pytest configuration.

Tests marked ``integration`` need a real PostgreSQL server. They run only when
``O2PG_TEST_DSN`` is set, for example::

    O2PG_TEST_DSN=postgresql://postgres:postgres@localhost:5432/postgres pytest -m integration
"""

from __future__ import annotations

import os

import pytest


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("O2PG_TEST_DSN"):
        return
    skip = pytest.mark.skip(reason="set O2PG_TEST_DSN to run the PostgreSQL integration tests")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)
