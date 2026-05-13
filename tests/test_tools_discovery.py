from __future__ import annotations

from typing import AsyncIterator, Iterator

import httpx
import pytest
import respx

from prometheus_mcp import server as server_module
from prometheus_mcp.config import ServerConfig, UnknownServerError
from prometheus_mcp.prometheus import aclose_clients
from prometheus_mcp.tools.discovery import (
    label_values,
    list_labels,
    list_metrics,
    list_series,
    metric_metadata,
)


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


async def test_list_metrics_pages_response(
    servers: dict[str, ServerConfig], mock_router: respx.Router
) -> None:
    mock_router.get("https://prom.example/api/v1/label/__name__/values").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": [f"metric_{i}" for i in range(7)],
            },
        )
    )
    result = await list_metrics(server="prefect", limit=3, offset=2)
    assert result == {
        "server": "prefect",
        "total": 7,
        "limit": 3,
        "offset": 2,
        "metrics": ["metric_2", "metric_3", "metric_4"],
    }


async def test_list_metrics_passes_match(
    servers: dict[str, ServerConfig], mock_router: respx.Router
) -> None:
    route = mock_router.get("https://prom.example/api/v1/label/__name__/values").mock(
        return_value=httpx.Response(
            200, json={"status": "success", "data": ["up", "process_cpu_seconds"]}
        )
    )
    await list_metrics(server="prefect", match=['up{job="api"}'])
    assert route.calls.last.request.url.params.get_list("match[]") == ['up{job="api"}']


async def test_list_metrics_unknown_server_raises(
    servers: dict[str, ServerConfig],
) -> None:
    with pytest.raises(UnknownServerError):
        await list_metrics(server="staging")


async def test_list_labels_returns_paged_labels(
    servers: dict[str, ServerConfig], mock_router: respx.Router
) -> None:
    mock_router.get("https://prom.example/api/v1/labels").mock(
        return_value=httpx.Response(
            200,
            json={"status": "success", "data": ["__name__", "instance", "job"]},
        )
    )
    result = await list_labels(server="prefect", limit=2, offset=1)
    assert result == {
        "server": "prefect",
        "total": 3,
        "limit": 2,
        "offset": 1,
        "labels": ["instance", "job"],
    }


async def test_label_values_returns_paged_values(
    servers: dict[str, ServerConfig], mock_router: respx.Router
) -> None:
    mock_router.get("https://prom.example/api/v1/label/job/values").mock(
        return_value=httpx.Response(
            200, json={"status": "success", "data": ["api", "worker", "ingest"]}
        )
    )
    result = await label_values(server="prefect", label="job", limit=2)
    assert result == {
        "server": "prefect",
        "label": "job",
        "total": 3,
        "limit": 2,
        "offset": 0,
        "values": ["api", "worker"],
    }


async def test_list_series_passes_match(
    servers: dict[str, ServerConfig], mock_router: respx.Router
) -> None:
    payload = [
        {"__name__": "up", "job": "api", "instance": "a:8080"},
        {"__name__": "up", "job": "api", "instance": "b:8080"},
    ]
    route = mock_router.get("https://prom.example/api/v1/series").mock(
        return_value=httpx.Response(200, json={"status": "success", "data": payload})
    )
    result = await list_series(server="prefect", match=['up{job="api"}'])
    assert result["series"] == payload
    assert result["total"] == 2
    assert route.calls.last.request.url.params.get_list("match[]") == ['up{job="api"}']


async def test_metric_metadata_flattens_and_sorts(
    servers: dict[str, ServerConfig], mock_router: respx.Router
) -> None:
    mock_router.get("https://prom.example/api/v1/metadata").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "up": [{"type": "gauge", "help": "Target liveness", "unit": ""}],
                    "http_requests_total": [
                        {
                            "type": "counter",
                            "help": "Total requests",
                            "unit": "",
                        },
                        {"type": "counter", "help": "Total requests", "unit": ""},
                    ],
                },
            },
        )
    )
    result = await metric_metadata(server="prefect")
    assert [(row["metric"], row["type"]) for row in result["metadata"]] == [
        ("http_requests_total", "counter"),
        ("http_requests_total", "counter"),
        ("up", "gauge"),
    ]
    assert result["total"] == 3


async def test_metric_metadata_passes_metric_filter(
    servers: dict[str, ServerConfig], mock_router: respx.Router
) -> None:
    route = mock_router.get("https://prom.example/api/v1/metadata").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "up": [{"type": "gauge", "help": "Target liveness", "unit": ""}]
                },
            },
        )
    )
    await metric_metadata(server="prefect", metric="up")
    assert route.calls.last.request.url.params["metric"] == "up"
