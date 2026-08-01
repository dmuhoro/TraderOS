from __future__ import annotations

import pytest

from traderos.interfaces.api import server


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> None:
    """Clear per-IP request buckets before every test so randomized-order
    suite runs are deterministic (avoids transient 429s from shared state)."""
    server.reset_rate_limiter()
