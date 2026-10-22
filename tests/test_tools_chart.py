from __future__ import annotations

from typing import Any

import httpx
import respx
from fastmcp import Client
from fastmcp.client import FastMCPTransport
from fastmcp.client.client import CallToolResult
from mcp.types import TextContent


async def chart(client: Client[FastMCPTransport], **arguments: Any) -> CallToolResult:
    return await client.call_tool(
        "chart_range",
        {
            "server": "prefect",
            "start": "2026-05-13T11:00:00Z",
            "end": "2026-05-13T12:00:00Z",
            **arguments,
        },
    )


def _matrix_payload(series: object) -> dict[str, object]:
    return {
        "status": "success",
        "data": {"resultType": "matrix", "result": series},
    }


def _text(result: CallToolResult) -> str:
    assert result.structured_content is None
    return "\n".join(
        block.text for block in result.content if isinstance(block, TextContent)
    )


async def test_chart_range_renders_matrix(
    mcp_client: Client[FastMCPTransport], mock_router: respx.Router
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
    result = await chart(mcp_client, expr="up")
    text = _text(result)
    assert text.splitlines()[0] == "up"
    assert 'up{job="api"}' in text
    assert "2026-05-13T11:00:00+00:00" in text


async def test_chart_range_handles_empty_result(
    mcp_client: Client[FastMCPTransport], mock_router: respx.Router
) -> None:
    mock_router.get("https://prom.example/api/v1/query_range").mock(
        return_value=httpx.Response(200, json=_matrix_payload([]))
    )
    result = await chart(mcp_client, expr="up")
    text = _text(result)
    assert text.startswith("(no data)")
    assert "2026-05-13T11:00:00+00:00" in text
    assert "wider window" in text


async def test_chart_range_rejects_non_matrix(
    mcp_client: Client[FastMCPTransport], mock_router: respx.Router
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
    result = await chart(mcp_client, expr="42")
    assert "expects a matrix result" in _text(result)


async def test_chart_range_too_many_series_summarizes_labels(
    mcp_client: Client[FastMCPTransport], mock_router: respx.Router
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
    result = await chart(mcp_client, expr="up", max_series=3)
    text = _text(result)
    assert "10 series" in text
    assert "__name__: 1" in text
    assert "mode: 2" in text
    assert "instance: 10" in text
    assert 'up{instance="host2:8080", mode="user"}' in text
    assert "sum by" in text
    assert "topk(3" in text


def _two_series_payload() -> dict[str, object]:
    values: list[list[object]] = [[1_778_670_000 + i * 15, "1.0"] for i in range(240)]
    return _matrix_payload(
        [
            {"metric": {"__name__": "up", "job": "api"}, "values": values},
            {"metric": {"__name__": "up", "job": "web"}, "values": values},
        ]
    )


async def test_chart_range_multi_series_renders_panels(
    mcp_client: Client[FastMCPTransport], mock_router: respx.Router
) -> None:
    mock_router.get("https://prom.example/api/v1/query_range").mock(
        return_value=httpx.Response(200, json=_two_series_payload())
    )
    result = await chart(mcp_client, expr="up")
    text = _text(result)
    assert 'up{job="api"} — ' in text
    assert 'up{job="web"} — ' in text
    assert "[1]" not in text


async def test_chart_range_overlay_flag_draws_one_plot(
    mcp_client: Client[FastMCPTransport], mock_router: respx.Router
) -> None:
    mock_router.get("https://prom.example/api/v1/query_range").mock(
        return_value=httpx.Response(200, json=_two_series_payload())
    )
    result = await chart(mcp_client, expr="up", overlay=True)
    text = _text(result)
    assert '[1] up{job="api"}' in text
    assert '[2] up{job="web"}' in text


async def test_chart_range_marks_missing_samples_as_gaps(
    mcp_client: Client[FastMCPTransport], mock_router: respx.Router
) -> None:
    base = 1_778_670_000  # 2026-05-13T11:00:00Z
    values: list[list[object]] = [[base + i * 15, "1.0"] for i in range(40)]
    values.extend([base + 3_000 + i * 15, "2.0"] for i in range(40))
    mock_router.get("https://prom.example/api/v1/query_range").mock(
        return_value=httpx.Response(
            200,
            json=_matrix_payload([{"metric": {"__name__": "x"}, "values": values}]),
        )
    )
    result = await chart(mcp_client, expr="x", step="15s")
    legend = _text(result).splitlines()[1]
    assert "min 1.00" in legend
    assert "no data 6" in legend


async def test_chart_range_skips_nan_values(
    mcp_client: Client[FastMCPTransport], mock_router: respx.Router
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
    result = await chart(mcp_client, expr="x")
    assert "x" in _text(result)


async def test_chart_range_skips_non_numeric_values(
    mcp_client: Client[FastMCPTransport], mock_router: respx.Router
) -> None:
    values: list[list[object]] = [[1_780_000_000, "not-a-number"]]
    mock_router.get("https://prom.example/api/v1/query_range").mock(
        return_value=httpx.Response(
            200,
            json=_matrix_payload([{"metric": {"__name__": "x"}, "values": values}]),
        )
    )
    result = await chart(mcp_client, expr="x")
    assert "(no data)" not in _text(result)


async def test_chart_range_passes_timeout(
    mcp_client: Client[FastMCPTransport], mock_router: respx.Router
) -> None:
    route = mock_router.get("https://prom.example/api/v1/query_range").mock(
        return_value=httpx.Response(200, json=_matrix_payload([]))
    )
    await chart(mcp_client, expr="up", timeout="2m")
    assert route.calls.last.request.url.params["timeout"] == "120s"
