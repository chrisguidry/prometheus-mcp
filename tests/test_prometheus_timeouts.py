from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from prometheus_mcp.config import ServerConfig
from prometheus_mcp.prometheus import PrometheusClient, PrometheusTransportError


async def test_query_waits_for_the_query_timeout(
    server: ServerConfig, mock_router: respx.Router
) -> None:
    route = mock_router.get("https://prom.example/api/v1/query").mock(
        return_value=httpx.Response(
            200, json={"status": "success", "data": {"resultType": "vector"}}
        )
    )
    async with PrometheusClient(server, timeout=30) as client:
        await client.query("up", timeout=timedelta(minutes=2))
    assert route.calls.last.request.extensions["timeout"]["read"] == 150


async def test_query_range_waits_for_the_query_timeout(
    server: ServerConfig, mock_router: respx.Router
) -> None:
    route = mock_router.get("https://prom.example/api/v1/query_range").mock(
        return_value=httpx.Response(
            200, json={"status": "success", "data": {"resultType": "matrix"}}
        )
    )
    async with PrometheusClient(server, timeout=30) as client:
        await client.query_range(
            "up",
            start=datetime(2026, 5, 13, 11, tzinfo=UTC),
            end=datetime(2026, 5, 13, 12, tzinfo=UTC),
            step=timedelta(seconds=30),
            timeout=timedelta(seconds=90),
        )
    assert route.calls.last.request.extensions["timeout"]["read"] == 120


async def test_query_without_a_timeout_uses_the_client_timeout(
    server: ServerConfig, mock_router: respx.Router
) -> None:
    route = mock_router.get("https://prom.example/api/v1/query").mock(
        return_value=httpx.Response(
            200, json={"status": "success", "data": {"resultType": "vector"}}
        )
    )
    async with PrometheusClient(server, timeout=30) as client:
        await client.query("up")
    assert route.calls.last.request.extensions["timeout"]["read"] == 30


async def test_request_reports_a_timeout(
    server: ServerConfig, mock_router: respx.Router
) -> None:
    mock_router.get("https://prom.example/api/v1/labels").mock(
        side_effect=httpx.ReadTimeout("timed out")
    )
    async with PrometheusClient(server, timeout=30) as client:
        with pytest.raises(
            PrometheusTransportError,
            match="Prometheus server 'prefect' did not answer labels within 30 seconds",
        ):
            await client.labels()
