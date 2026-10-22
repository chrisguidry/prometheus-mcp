from __future__ import annotations

import pytest

from prometheus_mcp.config import ServerConfig, UnknownServerError
from prometheus_mcp.prometheus import PrometheusServers, prometheus_servers


async def test_servers_reuse_one_client_per_slug(server: ServerConfig) -> None:
    servers = PrometheusServers({"prefect": server})
    first = servers.client("prefect")
    second = servers.client("PREFECT")
    await servers.aclose()
    assert first is second


async def test_servers_open_a_new_client_after_closing(server: ServerConfig) -> None:
    servers = PrometheusServers({"prefect": server})
    first = servers.client("prefect")
    await servers.aclose()
    second = servers.client("prefect")
    await servers.aclose()
    assert first is not second


async def test_servers_reject_an_unknown_slug(server: ServerConfig) -> None:
    servers = PrometheusServers({"prefect": server})
    with pytest.raises(UnknownServerError, match="available: prefect"):
        servers.client("staging")


async def test_prometheus_servers_reads_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROMETHEUS_MCP_SERVERS", "prefect")
    monkeypatch.setenv("PROMETHEUS_MCP_PREFECT_URL", "https://prom.example/")
    async with prometheus_servers() as servers:
        assert list(servers.configs) == ["prefect"]


async def test_prometheus_servers_closes_its_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROMETHEUS_MCP_SERVERS", "prefect")
    monkeypatch.setenv("PROMETHEUS_MCP_PREFECT_URL", "https://prom.example/")
    async with prometheus_servers() as servers:
        client = servers.client("prefect")
    with pytest.raises(RuntimeError, match="client has been closed"):
        await client.labels()
