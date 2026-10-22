from __future__ import annotations

import httpx
import pytest
import respx
from fastmcp import Client
from fastmcp.client import FastMCPTransport
from fastmcp.exceptions import ToolError


async def test_unknown_servers_reach_the_client_with_the_available_slugs(
    mcp_client: Client[FastMCPTransport],
) -> None:
    with pytest.raises(
        ToolError,
        match="^unknown Prometheus server 'staging' \\(available: prefect\\)$",
    ):
        await mcp_client.call_tool("list_labels", {"server": "staging"})


async def test_prometheus_errors_reach_the_client_with_prometheus_detail(
    mcp_client: Client[FastMCPTransport], mock_router: respx.Router
) -> None:
    mock_router.get("https://prom.example/api/v1/query").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "error",
                "errorType": "execution",
                "error": "query timed out in expression evaluation",
            },
        )
    )
    with pytest.raises(
        ToolError,
        match="^Prometheus returned execution: query timed out in expression",
    ):
        await mcp_client.call_tool("query", {"server": "prefect", "expr": "up"})


async def test_timeouts_reach_the_client_with_the_server_and_path(
    mcp_client: Client[FastMCPTransport], mock_router: respx.Router
) -> None:
    mock_router.get("https://prom.example/api/v1/query").mock(
        side_effect=httpx.ReadTimeout("timed out")
    )
    with pytest.raises(
        ToolError, match="^Prometheus server 'prefect' did not answer query within"
    ):
        await mcp_client.call_tool("query", {"server": "prefect", "expr": "up"})


async def test_unexpected_errors_reach_the_client_without_detail(
    mcp_client: Client[FastMCPTransport], mock_router: respx.Router
) -> None:
    mock_router.get("https://prom.example/api/v1/query").mock(
        return_value=httpx.Response(200, json={"status": "success", "data": [1]})
    )
    with pytest.raises(ToolError) as raised:
        await mcp_client.call_tool("query", {"server": "prefect", "expr": "up"})
    assert str(raised.value) == "Error calling tool 'query'"
