from __future__ import annotations

from typing import AsyncIterator, Iterator

import httpx
import pytest
import respx
from fastmcp.tools import ToolResult
from mcp.types import TextContent

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


def _text(result: ToolResult) -> str:
    assert result.structured_content is None
    return "\n".join(
        block.text for block in result.content if isinstance(block, TextContent)
    )


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
    text = _text(result)
    assert text.splitlines()[0] == "up"
    assert 'up{job="api"}' in text
    assert "2026-05-13T11:00:00+00:00" in text


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
    text = _text(result)
    assert text.startswith("(no data)")
    assert "2026-05-13T11:00:00+00:00" in text
    assert "wider window" in text


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
    assert "expects a matrix result" in _text(result)


async def test_chart_range_too_many_series_summarizes_labels(
    servers: dict[str, ServerConfig], mock_router: respx.Router
) -> None:
    series = [
        {
            "metric": {
                "__name__": "up",
                "instance": f"host{i}:8080",
                "mode": ["user", "system"][i % 2],
            },
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
    text = _text(result)
    assert "10 series" in text
    assert "__name__: 1" in text
    assert "mode: 2" in text
    assert "instance: 10" in text
    assert 'up{instance="host2:8080", mode="user"}' in text
    assert "sum by" in text
    assert "topk(3" in text


async def test_chart_range_marks_missing_samples_as_gaps(
    servers: dict[str, ServerConfig], mock_router: respx.Router
) -> None:
    base = 1_778_407_200  # 2026-05-13T11:00:00Z
    values: list[list[object]] = [[base + i * 15, "1.0"] for i in range(40)]
    values.extend([base + 3_000 + i * 15, "2.0"] for i in range(40))
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
        step="15s",
    )
    assert "no data" in _text(result).splitlines()[1]


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
    assert "x" in _text(result)


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
    assert "(no data)" not in _text(result)
