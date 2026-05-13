from __future__ import annotations

from datetime import datetime, timezone
from typing import AsyncIterator, Iterator

import httpx
import pytest
import respx

from prometheus_mcp import server as server_module
from prometheus_mcp.config import ServerConfig
from prometheus_mcp.prometheus import aclose_clients
from prometheus_mcp.tools.query import query, query_range


@pytest.fixture
def servers(monkeypatch: pytest.MonkeyPatch) -> dict[str, ServerConfig]:
    registry = {
        "prefect": ServerConfig(slug="prefect", url="https://prom.example/"),
    }
    monkeypatch.setattr(server_module, "SERVERS", registry)
    return registry


@pytest.fixture
def mock_router() -> Iterator[respx.Router]:
    with respx.mock(assert_all_called=False) as router:
        yield router


@pytest.fixture(autouse=True)
async def reset_clients() -> AsyncIterator[None]:
    yield
    await aclose_clients()


async def test_instant_query_passes_expr_and_time(
    servers: dict[str, ServerConfig], mock_router: respx.Router
) -> None:
    route = mock_router.get("https://prom.example/api/v1/query").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "resultType": "vector",
                    "result": [
                        {"metric": {"job": "api"}, "value": [1_780_000_000, "1"]},
                    ],
                },
            },
        )
    )
    result = await query(server="prefect", expr="up", time="2026-05-13T12:00:00Z")
    sent = route.calls.last.request
    assert sent.url.params["query"] == "up"
    assert (
        sent.url.params["time"]
        == f"{datetime(2026, 5, 13, 12, 0, 0, tzinfo=timezone.utc).timestamp():.3f}"
    )
    assert result["resultType"] == "vector"
    assert result["total_series"] == 1
    assert result["resolved"] == {"time": "2026-05-13T12:00:00+00:00"}


async def test_instant_query_pages_vector_results(
    servers: dict[str, ServerConfig], mock_router: respx.Router
) -> None:
    rows = [
        {"metric": {"i": str(i)}, "value": [1_780_000_000, str(i)]} for i in range(5)
    ]
    mock_router.get("https://prom.example/api/v1/query").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": {"resultType": "vector", "result": rows},
            },
        )
    )
    result = await query(server="prefect", expr="up", series_limit=2, series_offset=1)
    assert [r["metric"]["i"] for r in result["result"]] == ["1", "2"]
    assert result["total_series"] == 5


async def test_instant_query_passes_through_scalar(
    servers: dict[str, ServerConfig], mock_router: respx.Router
) -> None:
    mock_router.get("https://prom.example/api/v1/query").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": {"resultType": "scalar", "result": [1_780_000_000, "42"]},
            },
        )
    )
    result = await query(server="prefect", expr="42")
    assert result["resultType"] == "scalar"
    assert result["result"] == [1_780_000_000, "42"]
    assert result["total_series"] == 0


async def test_instant_query_accepts_timeout(
    servers: dict[str, ServerConfig], mock_router: respx.Router
) -> None:
    route = mock_router.get("https://prom.example/api/v1/query").mock(
        return_value=httpx.Response(
            200,
            json={"status": "success", "data": {"resultType": "vector", "result": []}},
        )
    )
    await query(server="prefect", expr="up", timeout="5s")
    assert route.calls.last.request.url.params["timeout"] == "5s"


async def test_query_range_passes_resolved_window(
    servers: dict[str, ServerConfig], mock_router: respx.Router
) -> None:
    route = mock_router.get("https://prom.example/api/v1/query_range").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": {"resultType": "matrix", "result": []},
            },
        )
    )
    result = await query_range(
        server="prefect",
        expr="rate(up[5m])",
        start="2026-05-13T11:00:00Z",
        end="2026-05-13T12:00:00Z",
        step="30s",
    )
    sent = route.calls.last.request
    assert sent.url.params["query"] == "rate(up[5m])"
    assert sent.url.params["step"] == "30"
    assert result["resolved"] == {
        "start": "2026-05-13T11:00:00+00:00",
        "end": "2026-05-13T12:00:00+00:00",
        "step": "30s",
    }


async def test_query_range_picks_default_step(
    servers: dict[str, ServerConfig], mock_router: respx.Router
) -> None:
    route = mock_router.get("https://prom.example/api/v1/query_range").mock(
        return_value=httpx.Response(
            200,
            json={"status": "success", "data": {"resultType": "matrix", "result": []}},
        )
    )
    await query_range(
        server="prefect",
        expr="up",
        start="2026-05-13T11:00:00Z",
        end="2026-05-13T12:00:00Z",
    )
    assert route.calls.last.request.url.params["step"] == "15"


async def test_query_range_pages_matrix_results(
    servers: dict[str, ServerConfig], mock_router: respx.Router
) -> None:
    rows = [
        {"metric": {"i": str(i)}, "values": [[1_780_000_000, "0"]]} for i in range(4)
    ]
    mock_router.get("https://prom.example/api/v1/query_range").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": {"resultType": "matrix", "result": rows},
            },
        )
    )
    result = await query_range(
        server="prefect",
        expr="up",
        start="2026-05-13T11:00:00Z",
        end="2026-05-13T12:00:00Z",
        series_limit=2,
    )
    assert [r["metric"]["i"] for r in result["result"]] == ["0", "1"]
    assert result["total_series"] == 4
