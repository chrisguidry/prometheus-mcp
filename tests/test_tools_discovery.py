from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import respx
from fastmcp import Client
from fastmcp.client import FastMCPTransport
from fastmcp.exceptions import ToolError


async def test_list_metrics_pages_response(
    mcp_client: Client[FastMCPTransport], mock_router: respx.Router
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
    result = await mcp_client.call_tool(
        "list_metrics", {"server": "prefect", "limit": 3, "offset": 2}
    )
    assert result.structured_content == {
        "server": "prefect",
        "total": 7,
        "limit": 3,
        "offset": 2,
        "metrics": ["metric_2", "metric_3", "metric_4"],
    }


async def test_list_metrics_passes_match(
    mcp_client: Client[FastMCPTransport], mock_router: respx.Router
) -> None:
    route = mock_router.get("https://prom.example/api/v1/label/__name__/values").mock(
        return_value=httpx.Response(
            200, json={"status": "success", "data": ["up", "process_cpu_seconds"]}
        )
    )
    await mcp_client.call_tool(
        "list_metrics", {"server": "prefect", "match": ['up{job="api"}']}
    )
    assert route.calls.last.request.url.params.get_list("match[]") == ['up{job="api"}']


async def test_list_metrics_unknown_server_raises(
    mcp_client: Client[FastMCPTransport],
) -> None:
    with pytest.raises(ToolError, match="unknown Prometheus server 'staging'"):
        await mcp_client.call_tool("list_metrics", {"server": "staging"})


async def test_list_labels_returns_paged_labels(
    mcp_client: Client[FastMCPTransport], mock_router: respx.Router
) -> None:
    mock_router.get("https://prom.example/api/v1/labels").mock(
        return_value=httpx.Response(
            200,
            json={"status": "success", "data": ["__name__", "instance", "job"]},
        )
    )
    result = await mcp_client.call_tool(
        "list_labels", {"server": "prefect", "limit": 2, "offset": 1}
    )
    assert result.structured_content == {
        "server": "prefect",
        "total": 3,
        "limit": 2,
        "offset": 1,
        "labels": ["instance", "job"],
    }


async def test_label_values_returns_paged_values(
    mcp_client: Client[FastMCPTransport], mock_router: respx.Router
) -> None:
    mock_router.get("https://prom.example/api/v1/label/job/values").mock(
        return_value=httpx.Response(
            200, json={"status": "success", "data": ["api", "worker", "ingest"]}
        )
    )
    result = await mcp_client.call_tool(
        "label_values", {"server": "prefect", "label": "job", "limit": 2}
    )
    assert result.structured_content == {
        "server": "prefect",
        "label": "job",
        "total": 3,
        "limit": 2,
        "offset": 0,
        "values": ["api", "worker"],
    }


async def test_list_series_passes_match(
    mcp_client: Client[FastMCPTransport], mock_router: respx.Router
) -> None:
    payload = [
        {"__name__": "up", "job": "api", "instance": "a:8080"},
        {"__name__": "up", "job": "api", "instance": "b:8080"},
    ]
    route = mock_router.get("https://prom.example/api/v1/series").mock(
        return_value=httpx.Response(200, json={"status": "success", "data": payload})
    )
    result = await mcp_client.call_tool(
        "list_series", {"server": "prefect", "match": ['up{job="api"}']}
    )
    assert result.structured_content == {
        "server": "prefect",
        "total": 2,
        "limit": 200,
        "offset": 0,
        "series": payload,
    }
    assert route.calls.last.request.url.params.get_list("match[]") == ['up{job="api"}']


async def test_metric_metadata_flattens_and_sorts(
    mcp_client: Client[FastMCPTransport], mock_router: respx.Router
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
    result = await mcp_client.call_tool("metric_metadata", {"server": "prefect"})
    assert result.structured_content == {
        "server": "prefect",
        "total": 3,
        "limit": 200,
        "offset": 0,
        "metadata": [
            {
                "metric": "http_requests_total",
                "type": "counter",
                "help": "Total requests",
                "unit": "",
            },
            {
                "metric": "http_requests_total",
                "type": "counter",
                "help": "Total requests",
                "unit": "",
            },
            {"metric": "up", "type": "gauge", "help": "Target liveness", "unit": ""},
        ],
    }


async def test_metric_metadata_passes_metric_filter(
    mcp_client: Client[FastMCPTransport], mock_router: respx.Router
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
    await mcp_client.call_tool("metric_metadata", {"server": "prefect", "metric": "up"})
    assert route.calls.last.request.url.params["metric"] == "up"


# Each discovery tool that takes a time window, the API path it calls, and the
# arguments it needs besides the server and the window.
TOOLS_WITH_A_WINDOW: list[tuple[str, str, dict[str, object]]] = [
    ("list_metrics", "label/__name__/values", {}),
    ("list_labels", "labels", {}),
    ("label_values", "label/job/values", {"label": "job"}),
    ("list_series", "series", {"match": ["up"]}),
]


@pytest.mark.parametrize(("tool", "path", "arguments"), TOOLS_WITH_A_WINDOW)
async def test_discovery_passes_time_window(
    mcp_client: Client[FastMCPTransport],
    mock_router: respx.Router,
    tool: str,
    path: str,
    arguments: dict[str, object],
) -> None:
    route = mock_router.get(f"https://prom.example/api/v1/{path}").mock(
        return_value=httpx.Response(200, json={"status": "success", "data": []})
    )
    await mcp_client.call_tool(
        tool,
        {
            "server": "prefect",
            "start": "2026-05-13T11:00:00Z",
            "end": "2026-05-13T12:00:00Z",
            **arguments,
        },
    )
    sent = route.calls.last.request.url.params
    start = datetime(2026, 5, 13, 11, tzinfo=UTC).timestamp()
    end = datetime(2026, 5, 13, 12, tzinfo=UTC).timestamp()
    assert (sent["start"], sent["end"]) == (f"{start:.3f}", f"{end:.3f}")


@pytest.mark.parametrize(("tool", "path", "arguments"), TOOLS_WITH_A_WINDOW)
async def test_discovery_searches_all_time_by_default(
    mcp_client: Client[FastMCPTransport],
    mock_router: respx.Router,
    tool: str,
    path: str,
    arguments: dict[str, object],
) -> None:
    route = mock_router.get(f"https://prom.example/api/v1/{path}").mock(
        return_value=httpx.Response(200, json={"status": "success", "data": []})
    )
    await mcp_client.call_tool(tool, {"server": "prefect", **arguments})
    sent = route.calls.last.request.url.params
    assert "start" not in sent
    assert "end" not in sent


async def test_discovery_reports_a_bad_time_window(
    mcp_client: Client[FastMCPTransport],
) -> None:
    with pytest.raises(ToolError, match="unrecognized time 'yesterday'"):
        await mcp_client.call_tool(
            "list_labels", {"server": "prefect", "start": "yesterday"}
        )
