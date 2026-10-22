from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx
from fastmcp import Client
from fastmcp.client import FastMCPTransport

RULES_URL = "https://prom.example/api/v1/rules"
ALERTS_URL = "https://prom.example/api/v1/alerts"


def alert(
    name: str, *, state: str = "firing", instance: str = "a:9100"
) -> dict[str, Any]:
    """One active alert, shaped like Prometheus 3.x returns it."""
    return {
        "labels": {"alertname": name, "instance": instance, "severity": "page"},
        "annotations": {"summary": f"{name} on {instance}"},
        "state": state,
        "activeAt": "2026-05-13T11:50:12.60602144Z",
        "value": "1e+00",
    }


def alerting_rule(
    name: str,
    *,
    state: str = "firing",
    alerts: list[dict[str, Any]] | None = None,
    health: str = "ok",
) -> dict[str, Any]:
    return {
        "state": state,
        "name": name,
        "query": "up == 0",
        "duration": 300,
        "keepFiringFor": 0,
        "labels": {"severity": "page"},
        "annotations": {"summary": "{{ $labels.instance }} is down"},
        "alerts": alerts if alerts is not None else [alert(name)],
        "health": health,
        "evaluationTime": 0.000312,
        "lastEvaluation": "2026-05-13T11:59:50.1Z",
        "type": "alerting",
    }


def recording_rule(name: str, *, last_error: str | None = None) -> dict[str, Any]:
    rule: dict[str, Any] = {
        "name": name,
        "query": "sum by (job) (rate(http_requests_total[5m]))",
        "labels": {},
        "health": "ok" if last_error is None else "err",
        "evaluationTime": 0.0021,
        "lastEvaluation": "2026-05-13T11:59:50.1Z",
        "type": "recording",
    }
    if last_error is not None:
        rule["lastError"] = last_error
    return rule


def group(name: str, rules: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "name": name,
        "file": f"/etc/prometheus/rules/{name}.yaml",
        "rules": rules,
        "interval": 60,
        "limit": 0,
        "evaluationTime": 0.0025,
        "lastEvaluation": "2026-05-13T11:59:50.1Z",
    }


def success(data: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json={"status": "success", "data": data})


async def test_list_rules_returns_an_alerting_rule_with_its_alerts(
    mcp_client: Client[FastMCPTransport], mock_router: respx.Router
) -> None:
    mock_router.get(RULES_URL).mock(
        return_value=success({"groups": [group("nodes", [alerting_rule("NodeDown")])]})
    )
    result = await mcp_client.call_tool("list_rules", {"server": "prefect"})
    assert result.structured_content == {
        "server": "prefect",
        "total": 1,
        "limit": 100,
        "offset": 0,
        "rules": [
            {
                "group": "nodes",
                "file": "/etc/prometheus/rules/nodes.yaml",
                "name": "NodeDown",
                "type": "alerting",
                "query": "up == 0",
                "labels": {"severity": "page"},
                "health": "ok",
                "last_error": None,
                "last_evaluation": "2026-05-13T11:59:50.1Z",
                "evaluation_seconds": 0.000312,
                "state": "firing",
                "duration_seconds": 300,
                "annotations": {"summary": "{{ $labels.instance }} is down"},
                "alerts": [
                    {
                        "labels": {
                            "alertname": "NodeDown",
                            "instance": "a:9100",
                            "severity": "page",
                        },
                        "annotations": {"summary": "NodeDown on a:9100"},
                        "state": "firing",
                        "active_at": "2026-05-13T11:50:12.60602144Z",
                        "keep_firing_since": None,
                        "value": "1e+00",
                    }
                ],
            }
        ],
    }


async def test_list_rules_returns_a_recording_rule_with_its_error(
    mcp_client: Client[FastMCPTransport], mock_router: respx.Router
) -> None:
    mock_router.get(RULES_URL).mock(
        return_value=success(
            {
                "groups": [
                    group(
                        "http",
                        [recording_rule("job:rate5m", last_error="many-to-many")],
                    )
                ]
            }
        )
    )
    result = await mcp_client.call_tool("list_rules", {"server": "prefect"})
    assert result.structured_content is not None
    assert result.structured_content["rules"] == [
        {
            "group": "http",
            "file": "/etc/prometheus/rules/http.yaml",
            "name": "job:rate5m",
            "type": "recording",
            "query": "sum by (job) (rate(http_requests_total[5m]))",
            "labels": {},
            "health": "err",
            "last_error": "many-to-many",
            "last_evaluation": "2026-05-13T11:59:50.1Z",
            "evaluation_seconds": 0.0021,
            "state": None,
            "duration_seconds": None,
            "annotations": None,
            "alerts": None,
        }
    ]


async def test_list_rules_reports_a_rule_never_evaluated_without_a_time(
    mcp_client: Client[FastMCPTransport], mock_router: respx.Router
) -> None:
    rule = alerting_rule("NodeDown", state="inactive", alerts=[], health="unknown")
    rule["lastEvaluation"] = "0001-01-01T00:00:00Z"
    mock_router.get(RULES_URL).mock(
        return_value=success({"groups": [group("nodes", [rule])]})
    )
    result = await mcp_client.call_tool("list_rules", {"server": "prefect"})
    assert result.structured_content is not None
    assert result.structured_content["rules"][0]["last_evaluation"] is None


@pytest.mark.parametrize(
    ("arguments", "params"),
    [
        ({}, []),
        ({"type": "any"}, []),
        ({"type": "alert"}, [("type", "alert")]),
        ({"type": "record"}, [("type", "record")]),
        ({"group": "nodes"}, [("rule_group[]", "nodes")]),
        (
            {"type": "alert", "group": "nodes"},
            [("type", "alert"), ("rule_group[]", "nodes")],
        ),
    ],
)
async def test_list_rules_asks_prometheus_to_filter(
    mcp_client: Client[FastMCPTransport],
    mock_router: respx.Router,
    arguments: dict[str, str],
    params: list[tuple[str, str]],
) -> None:
    route = mock_router.get(RULES_URL).mock(return_value=success({"groups": []}))
    await mcp_client.call_tool("list_rules", {"server": "prefect", **arguments})
    assert list(route.calls.last.request.url.params.multi_items()) == params


async def test_list_rules_pages_across_groups(
    mcp_client: Client[FastMCPTransport], mock_router: respx.Router
) -> None:
    mock_router.get(RULES_URL).mock(
        return_value=success(
            {
                "groups": [
                    group("nodes", [alerting_rule("NodeDown"), recording_rule("a")]),
                    group("http", [recording_rule("b"), recording_rule("c")]),
                ]
            }
        )
    )
    result = await mcp_client.call_tool(
        "list_rules", {"server": "prefect", "limit": 2, "offset": 1}
    )
    assert result.structured_content is not None
    assert result.structured_content["total"] == 4
    assert [
        (rule["group"], rule["name"]) for rule in result.structured_content["rules"]
    ] == [("nodes", "a"), ("http", "b")]


async def test_list_alerts_returns_each_active_alert(
    mcp_client: Client[FastMCPTransport], mock_router: respx.Router
) -> None:
    mock_router.get(ALERTS_URL).mock(
        return_value=success({"alerts": [alert("NodeDown")]})
    )
    result = await mcp_client.call_tool("list_alerts", {"server": "prefect"})
    assert result.structured_content == {
        "server": "prefect",
        "total": 1,
        "limit": 100,
        "offset": 0,
        "alerts": [
            {
                "labels": {
                    "alertname": "NodeDown",
                    "instance": "a:9100",
                    "severity": "page",
                },
                "annotations": {"summary": "NodeDown on a:9100"},
                "state": "firing",
                "active_at": "2026-05-13T11:50:12.60602144Z",
                "keep_firing_since": None,
                "value": "1e+00",
            }
        ],
    }


@pytest.fixture
def mixed_alerts(mock_router: respx.Router) -> None:
    mock_router.get(ALERTS_URL).mock(
        return_value=success(
            {
                "alerts": [
                    alert("NodeDown", instance="a:9100"),
                    alert("NodeDown", state="pending", instance="b:9100"),
                    alert("DiskFull", instance="a:9100"),
                ]
            }
        )
    )


@pytest.mark.parametrize(
    ("arguments", "alerts"),
    [
        ({}, [("NodeDown", "a:9100"), ("NodeDown", "b:9100"), ("DiskFull", "a:9100")]),
        ({"state": "firing"}, [("NodeDown", "a:9100"), ("DiskFull", "a:9100")]),
        ({"state": "pending"}, [("NodeDown", "b:9100")]),
        ({"limit": 1, "offset": 2}, [("DiskFull", "a:9100")]),
    ],
)
async def test_list_alerts_filters_by_state_and_pages(
    mcp_client: Client[FastMCPTransport],
    mixed_alerts: None,
    arguments: dict[str, object],
    alerts: list[tuple[str, str]],
) -> None:
    result = await mcp_client.call_tool(
        "list_alerts", {"server": "prefect", **arguments}
    )
    assert result.structured_content is not None
    assert [
        (alert["labels"]["alertname"], alert["labels"]["instance"])
        for alert in result.structured_content["alerts"]
    ] == alerts


async def test_list_alerts_counts_alerts_before_paging(
    mcp_client: Client[FastMCPTransport], mixed_alerts: None
) -> None:
    result = await mcp_client.call_tool(
        "list_alerts", {"server": "prefect", "state": "firing", "limit": 1}
    )
    assert result.structured_content is not None
    assert result.structured_content["total"] == 2


async def test_list_alerts_reports_when_an_alert_keeps_firing(
    mcp_client: Client[FastMCPTransport], mock_router: respx.Router
) -> None:
    held = alert("NodeDown")
    held["keepFiringSince"] = "2026-05-13T11:58:00Z"
    mock_router.get(ALERTS_URL).mock(return_value=success({"alerts": [held]}))
    result = await mcp_client.call_tool("list_alerts", {"server": "prefect"})
    assert result.structured_content is not None
    assert result.structured_content["alerts"][0]["keep_firing_since"] == (
        "2026-05-13T11:58:00Z"
    )
