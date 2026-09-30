import pytest

from app.main import write_limiter


@pytest.fixture(autouse=True)
def fresh_write_limit():
    """Every test starts with no writes counted against any client."""
    write_limiter.reset()
    yield
    write_limiter.reset()
