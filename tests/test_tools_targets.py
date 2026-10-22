from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx
from fastmcp import Client
from fastmcp.client import FastMCPTransport

TARGETS_URL = "https://prom.example/api/v1/targets"


def active_target(
    instance: str,
    *,
    job: str = "node",
    scrape_pool: str | None = None,
    health: str = "up",
    last_error: str = "",
    scrape_url: str | None = None,
    last_scrape: str = "2026-05-13T11:59:45.123456789Z",
) -> dict[str, Any]:
    """An entry of `activeTargets`, shaped like Prometheus 3.x returns it."""
    return {
        "discoveredLabels": {
            "__address__": instance,
            "__metrics_path__": "/metrics",
            "__scheme__": "http",
            "job": job,
        },
        "labels": {"instance": instance, "job": job},
        "scrapePool": scrape_pool or job,
        "scrapeUrl": scrape_url or f"http://{instance}/metrics",
        "globalUrl": f"http://{instance}/metrics",
        "lastError": last_error,
        "lastScrape": last_scrape,
        "lastScrapeDuration": 0.050688943,
        "health": health,
        "scrapeInterval": "1m",
        "scrapeTimeout": "10s",
    }


def dropped_target(address: str, *, job: str = "node") -> dict[str, Any]:
    """An entry of `droppedTargets`, which carries only discovered labels."""
    return {
        "discoveredLabels": {"__address__": address, "job": job},
        "scrapePool": job,
    }


def targets_response(
    active: list[dict[str, Any]], dropped: list[dict[str, Any]] | None = None
) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "status": "success",
            "data": {
                "activeTargets": active,
                "droppedTargets": dropped or [],
                "droppedTargetCounts": {"node": len(dropped or [])},
            },
        },
    )


async def test_list_targets_returns_each_active_target(
    mcp_client: Client[FastMCPTransport], mock_router: respx.Router
) -> None:
    mock_router.get(TARGETS_URL).mock(
        return_value=targets_response(
            [
                active_target(
                    "a:9100",
                    health="down",
                    last_error="connection refused",
                )
            ]
        )
    )
    result = await mcp_client.call_tool("list_targets", {"server": "prefect"})
    assert result.structured_content == {
        "server": "prefect",
        "total": 1,
        "limit": 100,
        "offset": 0,
        "targets": [
            {
                "state": "active",
                "scrape_pool": "node",
                "job": "node",
                "instance": "a:9100",
                "labels": {"instance": "a:9100", "job": "node"},
                "discovered_labels": None,
                "health": "down",
                "last_error": "connection refused",
                "last_scrape": "2026-05-13T11:59:45.123456789Z",
                "last_scrape_duration_seconds": 0.050688943,
                "scrape_interval": "1m",
                "scrape_timeout": "10s",
                "scrape_url": "http://a:9100/metrics",
            }
        ],
    }


async def test_list_targets_reports_a_healthy_target_without_an_error(
    mcp_client: Client[FastMCPTransport], mock_router: respx.Router
) -> None:
    mock_router.get(TARGETS_URL).mock(
        return_value=targets_response([active_target("a:9100")])
    )
    result = await mcp_client.call_tool("list_targets", {"server": "prefect"})
    assert result.structured_content is not None
    assert result.structured_content["targets"][0]["last_error"] is None


async def test_list_targets_reports_a_target_never_scraped_without_a_time(
    mcp_client: Client[FastMCPTransport], mock_router: respx.Router
) -> None:
    mock_router.get(TARGETS_URL).mock(
        return_value=targets_response(
            [
                active_target(
                    "a:9100", health="unknown", last_scrape="0001-01-01T00:00:00Z"
                )
            ]
        )
    )
    result = await mcp_client.call_tool("list_targets", {"server": "prefect"})
    assert result.structured_content is not None
    assert result.structured_content["targets"][0]["last_scrape"] is None


async def test_list_targets_reports_no_scrape_interval_when_prometheus_omits_it(
    mcp_client: Client[FastMCPTransport], mock_router: respx.Router
) -> None:
    """Prometheus before 2.30 reports no `scrapeInterval` or `scrapeTimeout`."""
    target = active_target("a:9100")
    del target["scrapeInterval"], target["scrapeTimeout"]
    mock_router.get(TARGETS_URL).mock(return_value=targets_response([target]))
    result = await mcp_client.call_tool("list_targets", {"server": "prefect"})
    assert result.structured_content is not None
    listed = result.structured_content["targets"][0]
    assert (listed["scrape_interval"], listed["scrape_timeout"]) == (None, None)


async def test_list_targets_drops_credentials_from_the_scrape_url(
    mcp_client: Client[FastMCPTransport], mock_router: respx.Router
) -> None:
    mock_router.get(TARGETS_URL).mock(
        return_value=targets_response(
            [
                active_target(
                    "a:9100",
                    scrape_url="https://reader:hunter2@a:9100/probe?module=http_2xx",
                )
            ]
        )
    )
    result = await mcp_client.call_tool("list_targets", {"server": "prefect"})
    assert result.structured_content is not None
    assert result.structured_content["targets"][0]["scrape_url"] == (
        "https://a:9100/probe?module=http_2xx"
    )
    assert "hunter2" not in str(result.content)


async def test_list_targets_returns_dropped_targets_with_their_discovered_labels(
    mcp_client: Client[FastMCPTransport], mock_router: respx.Router
) -> None:
    mock_router.get(TARGETS_URL).mock(
        return_value=targets_response([], [dropped_target("c:9100")])
    )
    result = await mcp_client.call_tool(
        "list_targets", {"server": "prefect", "state": "dropped"}
    )
    assert result.structured_content is not None
    assert result.structured_content["targets"] == [
        {
            "state": "dropped",
            "scrape_pool": "node",
            "job": "node",
            "instance": None,
            "labels": {},
            "discovered_labels": {"__address__": "c:9100", "job": "node"},
            "health": None,
            "last_error": None,
            "last_scrape": None,
            "last_scrape_duration_seconds": None,
            "scrape_interval": None,
            "scrape_timeout": None,
            "scrape_url": None,
        }
    ]


@pytest.mark.parametrize(
    ("arguments", "state"),
    [
        ({}, "active"),
        ({"state": "active"}, "active"),
        ({"state": "dropped"}, "dropped"),
        ({"state": "any"}, "any"),
    ],
)
async def test_list_targets_asks_prometheus_for_one_state(
    mcp_client: Client[FastMCPTransport],
    mock_router: respx.Router,
    arguments: dict[str, str],
    state: str,
) -> None:
    route = mock_router.get(TARGETS_URL).mock(return_value=targets_response([]))
    await mcp_client.call_tool("list_targets", {"server": "prefect", **arguments})
    assert route.calls.last.request.url.params["state"] == state


@pytest.mark.parametrize(
    ("arguments", "params"),
    [
        ({}, [("state", "active")]),
        ({"scrape_pool": "api"}, [("state", "active"), ("scrapePool", "api")]),
    ],
)
async def test_list_targets_asks_prometheus_for_one_scrape_pool(
    mcp_client: Client[FastMCPTransport],
    mock_router: respx.Router,
    arguments: dict[str, str],
    params: list[tuple[str, str]],
) -> None:
    route = mock_router.get(TARGETS_URL).mock(return_value=targets_response([]))
    await mcp_client.call_tool("list_targets", {"server": "prefect", **arguments})
    assert list(route.calls.last.request.url.params.multi_items()) == params


async def test_list_targets_lists_active_targets_before_dropped_ones(
    mcp_client: Client[FastMCPTransport], mock_router: respx.Router
) -> None:
    mock_router.get(TARGETS_URL).mock(
        return_value=targets_response(
            [active_target("a:9100")], [dropped_target("c:9100")]
        )
    )
    result = await mcp_client.call_tool(
        "list_targets", {"server": "prefect", "state": "any"}
    )
    assert result.structured_content is not None
    assert [t["state"] for t in result.structured_content["targets"]] == [
        "active",
        "dropped",
    ]


@pytest.fixture
def mixed_targets(mock_router: respx.Router) -> None:
    """Targets of two jobs in every health, answered for any `state` and
    `scrapePool`, as a Prometheus that ignores those parameters would answer."""
    mock_router.get(TARGETS_URL).mock(
        return_value=targets_response(
            [
                active_target("a:9100", job="node"),
                active_target("b:9100", job="node", health="down"),
                active_target("a:8080", job="api", health="down"),
                active_target("b:8080", job="api", health="unknown"),
                active_target("c:8080", job="api", scrape_pool="api-canary"),
            ],
            [dropped_target("c:9100", job="node")],
        )
    )


@pytest.mark.parametrize(
    ("arguments", "instances"),
    [
        ({"health": "down"}, ["b:9100", "a:8080"]),
        ({"health": "unknown"}, ["b:8080"]),
        ({"job": "api"}, ["a:8080", "b:8080", "c:8080"]),
        ({"job": "node"}, ["a:9100", "b:9100"]),
        ({"job": "node", "state": "dropped"}, [None]),
        ({"job": "node", "health": "up"}, ["a:9100"]),
        ({"job": "node", "state": "any"}, ["a:9100", "b:9100", None]),
        ({"health": "down", "state": "any"}, ["b:9100", "a:8080"]),
        ({"job": "worker"}, []),
        ({"scrape_pool": "api"}, ["a:8080", "b:8080"]),
        ({"scrape_pool": "api-canary"}, ["c:8080"]),
        ({"scrape_pool": "node", "state": "dropped"}, [None]),
        ({"scrape_pool": "node", "health": "down"}, ["b:9100"]),
        ({"scrape_pool": "api", "job": "node"}, []),
        ({"scrape_pool": "worker", "state": "any"}, []),
    ],
)
async def test_list_targets_filters_by_health_job_and_scrape_pool(
    mcp_client: Client[FastMCPTransport],
    mixed_targets: None,
    arguments: dict[str, str],
    instances: list[str | None],
) -> None:
    result = await mcp_client.call_tool(
        "list_targets", {"server": "prefect", **arguments}
    )
    assert result.structured_content is not None
    assert [t["instance"] for t in result.structured_content["targets"]] == instances
    assert result.structured_content["total"] == len(instances)


async def test_list_targets_pages_after_filtering(
    mcp_client: Client[FastMCPTransport], mixed_targets: None
) -> None:
    result = await mcp_client.call_tool(
        "list_targets",
        {"server": "prefect", "health": "down", "limit": 1, "offset": 1},
    )
    assert result.structured_content is not None
    assert result.structured_content["total"] == 2
    assert [t["instance"] for t in result.structured_content["targets"]] == ["a:8080"]
