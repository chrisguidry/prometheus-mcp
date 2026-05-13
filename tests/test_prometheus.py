from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import AsyncIterator, Iterator

import httpx
import pytest
import respx

from prometheus_mcp.config import ServerConfig
from prometheus_mcp.prometheus import (
    PrometheusAPIError,
    PrometheusClient,
    PrometheusTransportError,
    aclose_clients,
    get_client,
)


@pytest.fixture
def server() -> ServerConfig:
    return ServerConfig(
        slug="prefect",
        url="https://prom.example/",
        headers={"Authorization": "Bearer token", "X-Scope-Orgid": "tenant-7"},
    )


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 5, 13, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def mock_router() -> Iterator[respx.Router]:
    with respx.mock(assert_all_called=False) as router:
        yield router


@pytest.fixture
async def reset_client_cache() -> AsyncIterator[None]:
    yield
    await aclose_clients()


async def test_request_returns_data_for_success(
    server: ServerConfig, mock_router: respx.Router
) -> None:
    mock_router.get("https://prom.example/api/v1/labels").mock(
        return_value=httpx.Response(
            200, json={"status": "success", "data": ["__name__", "job"]}
        )
    )
    async with PrometheusClient(server) as client:
        data = await client.request("labels")
    assert data == ["__name__", "job"]


async def test_request_sends_configured_headers(
    server: ServerConfig, mock_router: respx.Router
) -> None:
    route = mock_router.get("https://prom.example/api/v1/labels").mock(
        return_value=httpx.Response(200, json={"status": "success", "data": []})
    )
    async with PrometheusClient(server) as client:
        await client.request("labels")
    sent = route.calls.last.request
    assert sent.headers["authorization"] == "Bearer token"
    assert sent.headers["x-scope-orgid"] == "tenant-7"


async def test_request_translates_api_error(
    server: ServerConfig, mock_router: respx.Router
) -> None:
    mock_router.get("https://prom.example/api/v1/query").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "error",
                "errorType": "bad_data",
                "error": "parse error at char 4",
            },
        )
    )
    async with PrometheusClient(server) as client:
        with pytest.raises(PrometheusAPIError) as excinfo:
            await client.request("query", params=[("query", "+++")])
    assert excinfo.value.error_type == "bad_data"
    assert excinfo.value.error == "parse error at char 4"
    assert "bad_data" in str(excinfo.value)


async def test_request_translates_http_error(
    server: ServerConfig, mock_router: respx.Router
) -> None:
    mock_router.get("https://prom.example/api/v1/labels").mock(
        return_value=httpx.Response(503, text="upstream down")
    )
    async with PrometheusClient(server) as client:
        with pytest.raises(PrometheusTransportError) as excinfo:
            await client.request("labels")
    assert excinfo.value.status_code == 503


async def test_request_translates_transport_error(
    server: ServerConfig, mock_router: respx.Router
) -> None:
    mock_router.get("https://prom.example/api/v1/labels").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    async with PrometheusClient(server) as client:
        with pytest.raises(PrometheusTransportError, match="connection refused"):
            await client.request("labels")


async def test_request_rejects_non_json_body(
    server: ServerConfig, mock_router: respx.Router
) -> None:
    mock_router.get("https://prom.example/api/v1/labels").mock(
        return_value=httpx.Response(200, content=b"<html></html>")
    )
    async with PrometheusClient(server) as client:
        with pytest.raises(PrometheusTransportError, match="non-JSON body"):
            await client.request("labels")


async def test_request_rejects_non_object_body(
    server: ServerConfig, mock_router: respx.Router
) -> None:
    mock_router.get("https://prom.example/api/v1/labels").mock(
        return_value=httpx.Response(200, json=["not", "an", "object"])
    )
    async with PrometheusClient(server) as client:
        with pytest.raises(PrometheusTransportError, match="expected an object"):
            await client.request("labels")


async def test_request_rejects_unexpected_status(
    server: ServerConfig, mock_router: respx.Router
) -> None:
    mock_router.get("https://prom.example/api/v1/labels").mock(
        return_value=httpx.Response(200, json={"status": "weird"})
    )
    async with PrometheusClient(server) as client:
        with pytest.raises(PrometheusTransportError, match="unexpected status"):
            await client.request("labels")


async def test_labels_passes_match_and_limit(
    server: ServerConfig,
    mock_router: respx.Router,
    now: datetime,
) -> None:
    route = mock_router.get("https://prom.example/api/v1/labels").mock(
        return_value=httpx.Response(
            200, json={"status": "success", "data": ["job", "instance"]}
        )
    )
    async with PrometheusClient(server) as client:
        result = await client.labels(
            start=now - timedelta(hours=1),
            end=now,
            match=["up", "process_cpu_seconds_total"],
            limit=100,
        )
    assert result == ["job", "instance"]
    sent = route.calls.last.request
    assert sent.url.params.get_list("match[]") == [
        "up",
        "process_cpu_seconds_total",
    ]
    assert sent.url.params["limit"] == "100"
    assert sent.url.params["start"] == f"{(now - timedelta(hours=1)).timestamp():.3f}"
    assert sent.url.params["end"] == f"{now.timestamp():.3f}"


async def test_labels_returns_empty_list_on_null_data(
    server: ServerConfig, mock_router: respx.Router
) -> None:
    mock_router.get("https://prom.example/api/v1/labels").mock(
        return_value=httpx.Response(200, json={"status": "success", "data": None})
    )
    async with PrometheusClient(server) as client:
        assert await client.labels() == []


async def test_label_values_uses_path_parameter(
    server: ServerConfig, mock_router: respx.Router
) -> None:
    route = mock_router.get("https://prom.example/api/v1/label/job/values").mock(
        return_value=httpx.Response(
            200, json={"status": "success", "data": ["api", "worker"]}
        )
    )
    async with PrometheusClient(server) as client:
        result = await client.label_values("job")
    assert result == ["api", "worker"]
    assert route.called


async def test_label_values_returns_empty_on_null(
    server: ServerConfig, mock_router: respx.Router
) -> None:
    mock_router.get("https://prom.example/api/v1/label/job/values").mock(
        return_value=httpx.Response(200, json={"status": "success", "data": None})
    )
    async with PrometheusClient(server) as client:
        assert await client.label_values("job") == []


async def test_series_requires_match_list(
    server: ServerConfig, mock_router: respx.Router
) -> None:
    route = mock_router.get("https://prom.example/api/v1/series").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": [{"__name__": "up", "job": "api"}],
            },
        )
    )
    async with PrometheusClient(server) as client:
        result = await client.series(match=["up{job='api'}"], limit=10)
    assert result == [{"__name__": "up", "job": "api"}]
    sent = route.calls.last.request
    assert sent.url.params.get_list("match[]") == ["up{job='api'}"]
    assert sent.url.params["limit"] == "10"


async def test_series_returns_empty_on_null(
    server: ServerConfig, mock_router: respx.Router
) -> None:
    mock_router.get("https://prom.example/api/v1/series").mock(
        return_value=httpx.Response(200, json={"status": "success", "data": None})
    )
    async with PrometheusClient(server) as client:
        assert await client.series(match=["up"]) == []


async def test_metadata_passes_metric_and_limit(
    server: ServerConfig, mock_router: respx.Router
) -> None:
    payload = {
        "up": [{"type": "gauge", "help": "Target liveness", "unit": ""}],
    }
    route = mock_router.get("https://prom.example/api/v1/metadata").mock(
        return_value=httpx.Response(200, json={"status": "success", "data": payload})
    )
    async with PrometheusClient(server) as client:
        result = await client.metadata(metric="up", limit=5)
    assert result == payload
    sent = route.calls.last.request
    assert sent.url.params["metric"] == "up"
    assert sent.url.params["limit"] == "5"


async def test_metadata_returns_empty_when_null(
    server: ServerConfig, mock_router: respx.Router
) -> None:
    mock_router.get("https://prom.example/api/v1/metadata").mock(
        return_value=httpx.Response(200, json={"status": "success", "data": None})
    )
    async with PrometheusClient(server) as client:
        assert await client.metadata() == {}


async def test_query_passes_expr_and_time(
    server: ServerConfig,
    mock_router: respx.Router,
    now: datetime,
) -> None:
    route = mock_router.get("https://prom.example/api/v1/query").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": {"resultType": "vector", "result": []},
            },
        )
    )
    async with PrometheusClient(server) as client:
        result = await client.query("up", time=now, timeout=timedelta(seconds=2))
    assert result == {"resultType": "vector", "result": []}
    sent = route.calls.last.request
    assert sent.url.params["query"] == "up"
    assert sent.url.params["time"] == f"{now.timestamp():.3f}"
    assert sent.url.params["timeout"] == "2.000s"


async def test_query_omits_optional_params_when_unset(
    server: ServerConfig, mock_router: respx.Router
) -> None:
    route = mock_router.get("https://prom.example/api/v1/query").mock(
        return_value=httpx.Response(
            200,
            json={"status": "success", "data": {"resultType": "vector", "result": []}},
        )
    )
    async with PrometheusClient(server) as client:
        await client.query("up")
    sent = route.calls.last.request
    assert "time" not in sent.url.params
    assert "timeout" not in sent.url.params


async def test_query_range_omits_timeout_when_unset(
    server: ServerConfig,
    mock_router: respx.Router,
    now: datetime,
) -> None:
    route = mock_router.get("https://prom.example/api/v1/query_range").mock(
        return_value=httpx.Response(
            200,
            json={"status": "success", "data": {"resultType": "matrix", "result": []}},
        )
    )
    async with PrometheusClient(server) as client:
        await client.query_range(
            "up",
            start=now - timedelta(minutes=5),
            end=now,
            step=timedelta(seconds=15),
        )
    assert "timeout" not in route.calls.last.request.url.params


async def test_query_range_passes_all_args(
    server: ServerConfig,
    mock_router: respx.Router,
    now: datetime,
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
    start = now - timedelta(minutes=30)
    async with PrometheusClient(server) as client:
        await client.query_range(
            "rate(up[5m])",
            start=start,
            end=now,
            step=timedelta(seconds=30),
            timeout=timedelta(seconds=5),
        )
    sent = route.calls.last.request
    assert sent.url.params["query"] == "rate(up[5m])"
    assert sent.url.params["start"] == f"{start.timestamp():.3f}"
    assert sent.url.params["end"] == f"{now.timestamp():.3f}"
    assert sent.url.params["step"] == "30.000s"
    assert sent.url.params["timeout"] == "5.000s"


async def test_get_client_caches_per_slug(
    server: ServerConfig, reset_client_cache: None
) -> None:
    servers = {"prefect": server}
    first = get_client("prefect", servers)
    second = get_client("PREFECT", servers)
    assert first is second


async def test_aclose_clients_drops_cached_instances(
    server: ServerConfig, reset_client_cache: None
) -> None:
    servers = {"prefect": server}
    first = get_client("prefect", servers)
    await aclose_clients()
    second = get_client("prefect", servers)
    assert first is not second
