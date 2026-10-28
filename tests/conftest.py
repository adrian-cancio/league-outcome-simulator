from __future__ import annotations

import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, cast

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
project_root_str = str(PROJECT_ROOT)
if project_root_str not in sys.path:
    sys.path.insert(0, project_root_str)

from league_outcome_simulator.data import SofaScoreClient
from tests.support import Clock, FakeSession


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "live: talks to the real SofaScore API; needs LOS_LIVE_TESTS=1"
    )


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    return tmp_path / "cache"


@pytest.fixture
def make_client(
    cache_dir: Path, clock: Clock
) -> Iterator[Callable[..., SofaScoreClient]]:
    """Factory for clients wired to a FakeSession with no real sleeping."""
    created: list[SofaScoreClient] = []

    def factory(
        routes: dict[str, Any] | None = None, **kwargs: Any
    ) -> SofaScoreClient:
        kwargs.setdefault("cache_dir", cache_dir)
        kwargs.setdefault("clock", clock)
        kwargs.setdefault("retry_delay", 0.0)
        client = SofaScoreClient(**kwargs)
        client.session = cast(Any, FakeSession(routes))
        created.append(client)
        return client

    yield factory
    for client in created:
        client.close()
