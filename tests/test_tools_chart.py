from __future__ import annotations

from typing import AsyncIterator, Iterator

import httpx
import pytest
import respx

from prometheus_mcp import server as server_module
from prometheus_mcp.config import ServerConfig
from prometheus_mcp.prometheus import aclose_clients
from prometheus_mcp.tools.chart import chart_range


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


def _matrix_payload(series: object) -> dict[str, object]:
    return {
        "status": "success",
        "data": {"resultType": "matrix", "result": series},
    }


async def test_chart_range_renders_matrix(
    servers: dict[str, ServerConfig], mock_router: respx.Router
) -> None:
    values: list[list[object]] = [
        [1_780_000_000 + i * 15, str(float(i % 5))] for i in range(40)
    ]
    mock_router.get("https://prom.example/api/v1/query_range").mock(
        return_value=httpx.Response(
            200,
            json=_matrix_payload(
                [{"metric": {"__name__": "up", "job": "api"}, "values": values}]
            ),
        )
    )
    result = await chart_range(
        server="prefect",
        expr="up",
        start="2026-05-13T11:00:00Z",
        end="2026-05-13T12:00:00Z",
    )
    assert 'up{job="api"}' in result["chart"]
    assert result["series"] == ['up{job="api"}']
    assert result["resolved"]["start"] == "2026-05-13T11:00:00+00:00"


async def test_chart_range_handles_empty_result(
    servers: dict[str, ServerConfig], mock_router: respx.Router
) -> None:
    mock_router.get("https://prom.example/api/v1/query_range").mock(
        return_value=httpx.Response(200, json=_matrix_payload([]))
    )
    result = await chart_range(
        server="prefect",
        expr="up",
        start="2026-05-13T11:00:00Z",
        end="2026-05-13T12:00:00Z",
    )
    assert result["chart"] == "(no data)"
    assert result["series"] == []


async def test_chart_range_rejects_non_matrix(
    servers: dict[str, ServerConfig], mock_router: respx.Router
) -> None:
    mock_router.get("https://prom.example/api/v1/query_range").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": {"resultType": "scalar", "result": [1, "1"]},
            },
        )
    )
    result = await chart_range(
        server="prefect",
        expr="42",
        start="2026-05-13T11:00:00Z",
        end="2026-05-13T12:00:00Z",
    )
    assert "expects a matrix result" in result["error"]


async def test_chart_range_too_many_series_returns_hint(
    servers: dict[str, ServerConfig], mock_router: respx.Router
) -> None:
    series = [
        {
            "metric": {"__name__": "up", "instance": f"host{i}:8080"},
            "values": [[1_780_000_000, "1"]],
        }
        for i in range(10)
    ]
    mock_router.get("https://prom.example/api/v1/query_range").mock(
        return_value=httpx.Response(200, json=_matrix_payload(series))
    )
    result = await chart_range(
        server="prefect",
        expr="up",
        start="2026-05-13T11:00:00Z",
        end="2026-05-13T12:00:00Z",
        max_series=3,
    )
    assert result["too_many_series"] is True
    assert result["total_series"] == 10
    assert len(result["series"]) == 10
    assert "topk" in result["hint"]


async def test_chart_range_skips_nan_values(
    servers: dict[str, ServerConfig], mock_router: respx.Router
) -> None:
    values: list[list[object]] = [
        [1_780_000_000, "NaN"],
        [1_780_000_015, "1.0"],
        [1_780_000_030, "2.0"],
    ]
    mock_router.get("https://prom.example/api/v1/query_range").mock(
        return_value=httpx.Response(
            200,
            json=_matrix_payload([{"metric": {"__name__": "x"}, "values": values}]),
        )
    )
    result = await chart_range(
        server="prefect",
        expr="x",
        start="2026-05-13T11:00:00Z",
        end="2026-05-13T12:00:00Z",
    )
    assert "x" in result["chart"]


async def test_chart_range_skips_non_numeric_values(
    servers: dict[str, ServerConfig], mock_router: respx.Router
) -> None:
    values: list[list[object]] = [[1_780_000_000, "not-a-number"]]
    mock_router.get("https://prom.example/api/v1/query_range").mock(
        return_value=httpx.Response(
            200,
            json=_matrix_payload([{"metric": {"__name__": "x"}, "values": values}]),
        )
    )
    result = await chart_range(
        server="prefect",
        expr="x",
        start="2026-05-13T11:00:00Z",
        end="2026-05-13T12:00:00Z",
    )
    assert "(no data)" not in result["chart"]
