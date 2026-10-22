from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx
from fastmcp import Client
from fastmcp.client import FastMCPTransport
from fastmcp.client.client import CallToolResult
from fastmcp.exceptions import ToolError


def structured(result: CallToolResult) -> dict[str, Any]:
    assert result.structured_content is not None
    return result.structured_content


async def test_instant_query_passes_expr_and_time(
    mcp_client: Client[FastMCPTransport], mock_router: respx.Router
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
    result = structured(
        await mcp_client.call_tool(
            "query", {"server": "prefect", "expr": "up", "time": "2026-05-13T12:00:00Z"}
        )
    )
    sent = route.calls.last.request
    assert sent.url.params["query"] == "up"
    assert (
        sent.url.params["time"]
        == f"{datetime(2026, 5, 13, 12, 0, 0, tzinfo=UTC).timestamp():.3f}"
    )
    assert result["resultType"] == "vector"
    assert result["total_series"] == 1
    assert result["resolved"] == {"time": "2026-05-13T12:00:00+00:00"}


async def test_instant_query_pages_vector_results(
    mcp_client: Client[FastMCPTransport], mock_router: respx.Router
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
    result = structured(
        await mcp_client.call_tool(
            "query",
            {"server": "prefect", "expr": "up", "series_limit": 2, "series_offset": 1},
        )
    )
    assert [r["metric"]["i"] for r in result["result"]] == ["1", "2"]
    assert result["total_series"] == 5


async def test_instant_query_hints_on_empty_result(
    mcp_client: Client[FastMCPTransport], mock_router: respx.Router
) -> None:
    mock_router.get("https://prom.example/api/v1/query").mock(
        return_value=httpx.Response(
            200,
            json={"status": "success", "data": {"resultType": "vector", "result": []}},
        )
    )
    result = structured(
        await mcp_client.call_tool("query", {"server": "prefect", "expr": "up"})
    )
    assert result["total_series"] == 0
    assert "lookback" in result["hint"]
    assert "chart_range" in result["hint"]


async def test_instant_query_no_hint_when_series_match(
    mcp_client: Client[FastMCPTransport], mock_router: respx.Router
) -> None:
    mock_router.get("https://prom.example/api/v1/query").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "resultType": "vector",
                    "result": [{"metric": {}, "value": [1_780_000_000, "1"]}],
                },
            },
        )
    )
    result = structured(
        await mcp_client.call_tool("query", {"server": "prefect", "expr": "up"})
    )
    assert result["hint"] is None


async def test_query_range_hints_on_empty_result(
    mcp_client: Client[FastMCPTransport], mock_router: respx.Router
) -> None:
    mock_router.get("https://prom.example/api/v1/query_range").mock(
        return_value=httpx.Response(
            200,
            json={"status": "success", "data": {"resultType": "matrix", "result": []}},
        )
    )
    result = structured(
        await mcp_client.call_tool("query_range", {"server": "prefect", "expr": "up"})
    )
    assert result["total_series"] == 0
    assert "chart_range" in result["hint"]


async def test_instant_query_passes_through_scalar(
    mcp_client: Client[FastMCPTransport], mock_router: respx.Router
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
    result = structured(
        await mcp_client.call_tool("query", {"server": "prefect", "expr": "42"})
    )
    assert result["resultType"] == "scalar"
    assert result["result"] == [1_780_000_000, "42"]
    assert result["total_series"] == 0


async def test_instant_query_accepts_timeout(
    mcp_client: Client[FastMCPTransport], mock_router: respx.Router
) -> None:
    route = mock_router.get("https://prom.example/api/v1/query").mock(
        return_value=httpx.Response(
            200,
            json={"status": "success", "data": {"resultType": "vector", "result": []}},
        )
    )
    await mcp_client.call_tool(
        "query", {"server": "prefect", "expr": "up", "timeout": "5s"}
    )
    assert route.calls.last.request.url.params["timeout"] == "5s"


async def test_query_range_passes_resolved_window(
    mcp_client: Client[FastMCPTransport], mock_router: respx.Router
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
    result = structured(
        await mcp_client.call_tool(
            "query_range",
            {
                "server": "prefect",
                "expr": "rate(up[5m])",
                "start": "2026-05-13T11:00:00Z",
                "end": "2026-05-13T12:00:00Z",
                "step": "30s",
            },
        )
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
    mcp_client: Client[FastMCPTransport], mock_router: respx.Router
) -> None:
    route = mock_router.get("https://prom.example/api/v1/query_range").mock(
        return_value=httpx.Response(
            200,
            json={"status": "success", "data": {"resultType": "matrix", "result": []}},
        )
    )
    await mcp_client.call_tool(
        "query_range",
        {
            "server": "prefect",
            "expr": "up",
            "start": "2026-05-13T11:00:00Z",
            "end": "2026-05-13T12:00:00Z",
        },
    )
    assert route.calls.last.request.url.params["step"] == "36"


async def test_query_range_pages_matrix_results(
    mcp_client: Client[FastMCPTransport], mock_router: respx.Router
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
    result = structured(
        await mcp_client.call_tool(
            "query_range",
            {
                "server": "prefect",
                "expr": "up",
                "start": "2026-05-13T11:00:00Z",
                "end": "2026-05-13T12:00:00Z",
                "series_limit": 2,
            },
        )
    )
    assert [r["metric"]["i"] for r in result["result"]] == ["0", "1"]
    assert result["total_series"] == 4


async def test_query_reports_prometheus_errors(
    mcp_client: Client[FastMCPTransport], mock_router: respx.Router
) -> None:
    mock_router.get("https://prom.example/api/v1/query").mock(
        return_value=httpx.Response(
            400,
            json={
                "status": "error",
                "errorType": "bad_data",
                "error": "1:4: parse error: unexpected end of input",
            },
        )
    )
    with pytest.raises(ToolError, match="HTTP 400 \\(bad_data\\): 1:4: parse error"):
        await mcp_client.call_tool("query", {"server": "prefect", "expr": "up{"})


async def test_query_range_reports_a_backwards_window(
    mcp_client: Client[FastMCPTransport],
) -> None:
    with pytest.raises(ToolError, match="must be before end"):
        await mcp_client.call_tool(
            "query_range",
            {"server": "prefect", "expr": "up", "start": "now", "end": "now-1h"},
        )
