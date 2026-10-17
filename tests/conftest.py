from pathlib import Path

import pytest

from whitespace.ingest.base import RateLimiter
from whitespace.storage import Database, RawStore

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def db():
    return Database(":memory:")


@pytest.fixture
def raw(tmp_path):
    return RawStore(tmp_path / "raw")


@pytest.fixture
def limiter():
    return RateLimiter(0.0)  # no waiting in tests


@pytest.fixture
def forum_html():
    return (FIXTURES / "forum.html").read_text()
