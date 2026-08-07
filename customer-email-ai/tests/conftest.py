"""Test path setup for project-local modules."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture(autouse=True)
def isolate_cached_database_engine():
    """Prevent a SQLite engine from leaking state into a later test."""
    yield
    from database.connection import reset_engine

    reset_engine()
