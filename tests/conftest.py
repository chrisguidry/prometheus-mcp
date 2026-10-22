from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
import respx
from fastmcp import Client
from fastmcp.client import FastMCPTransport

from prometheus_mcp.config import ServerConfig
from prometheus_mcp.server import mcp


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configure one Prometheus server, with the slug "prefect"."""
    monkeypatch.setenv("PROMETHEUS_MCP_SERVERS", "prefect")
    monkeypatch.setenv("PROMETHEUS_MCP_PREFECT_URL", "https://prom.example/")


@pytest.fixture
def server() -> ServerConfig:
    return ServerConfig(
        slug="prefect",
        url="https://prom.example/",
        headers={"Authorization": "Bearer token", "X-Scope-Orgid": "tenant-7"},
    )


@pytest.fixture
def mock_router() -> Iterator[respx.Router]:
    with respx.mock(assert_all_called=False) as router:
        yield router


@pytest.fixture
async def mcp_client(configured: None) -> AsyncIterator[Client[FastMCPTransport]]:
    """An MCP client connected in memory to the server."""
    async with Client(mcp) as client:
        yield client
