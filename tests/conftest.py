"""
Shared pytest fixtures.

The database is a build artifact and is gitignored, so it may not exist
when tests run on a fresh clone. This fixture rebuilds it once per test
session from the committed processed CSVs.
"""

import pytest

from src.database import build_database


@pytest.fixture(scope="session", autouse=True)
def ensure_database():
    """Build the SQLite database once before any tests run."""
    build_database(verbose=False)