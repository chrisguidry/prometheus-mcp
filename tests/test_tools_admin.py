from __future__ import annotations

import pytest
from fastmcp import Client

from prometheus_mcp.server import mcp


@pytest.fixture
def two_servers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROMETHEUS_MCP_SERVERS", "prod,stg")
    monkeypatch.setenv("PROMETHEUS_MCP_PROD_URL", "https://prom-prod/")
    monkeypatch.setenv("PROMETHEUS_MCP_PROD_HEADER_AUTHORIZATION", "Bearer secret")
    monkeypatch.setenv("PROMETHEUS_MCP_STG_URL", "https://reader:hunter2@prom-stg/")


async def test_list_servers_returns_slug_and_url(two_servers: None) -> None:
    async with Client(mcp) as client:
        result = await client.call_tool("list_servers", {})
    assert result.structured_content == {
        "result": [
            {"slug": "prod", "url": "https://prom-prod/"},
            {"slug": "stg", "url": "https://prom-stg/"},
        ]
    }


async def test_list_servers_does_not_leak_credentials(two_servers: None) -> None:
    async with Client(mcp) as client:
        result = await client.call_tool("list_servers", {})
    text = str(result.content) + str(result.structured_content)
    assert "secret" not in text
    assert "Authorization" not in text
    assert "hunter2" not in text


async def test_list_servers_empty_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PROMETHEUS_MCP_SERVERS", raising=False)
    async with Client(mcp) as client:
        result = await client.call_tool("list_servers", {})
    assert result.structured_content == {"result": []}
