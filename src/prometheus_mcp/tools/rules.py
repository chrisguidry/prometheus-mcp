"""Rule tools: the alerting and recording rules a Prometheus server evaluates,
and the alerts its alerting rules raise.

The shapes follow ``GET /api/v1/rules`` and ``GET /api/v1/alerts`` in the
Prometheus HTTP API
(https://prometheus.io/docs/prometheus/latest/querying/api/#rules).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastmcp.dependencies import Shared
from fastmcp.tools import tool
from pydantic import BaseModel, Field

from prometheus_mcp.prometheus import PrometheusServers, prometheus_servers
from prometheus_mcp.time_range import event_time
from prometheus_mcp.tools.discovery import Limit, Offset, Page, ServerSlug
from prometheus_mcp.tools.effects import READ, READS


class Alert(BaseModel):
    """One active alert: an alerting rule's expression returned a series."""

    labels: dict[str, str] = Field(
        description="The alert's labels, including `alertname`."
    )
    annotations: dict[str, str] = Field(
        description="The alert's annotations, such as `summary` or `description`."
    )
    state: str = Field(
        description=(
            "'pending' while the alert waits out its rule's `for` duration, "
            "then 'firing'."
        )
    )
    active_at: str | None = Field(
        description="When the alert became pending, as RFC-3339."
    )
    keep_firing_since: str | None = Field(
        description=(
            "When the alert's expression stopped returning a series while the "
            "rule's `keep_firing_for` keeps the alert firing, as RFC-3339. Null "
            "otherwise."
        )
    )
    value: str = Field(
        description=(
            "The value of the expression at the last evaluation, in "
            "Prometheus's notation, e.g. '1e+00'."
        )
    )


def _alert(raw: dict[str, Any]) -> Alert:
    return Alert(
        labels=raw.get("labels") or {},
        annotations=raw.get("annotations") or {},
        state=raw.get("state", ""),
        active_at=event_time(raw.get("activeAt")),
        keep_firing_since=event_time(raw.get("keepFiringSince")),
        value=raw.get("value", ""),
    )


class Rule(BaseModel):
    """One alerting or recording rule."""

    group: str = Field(description="The name of the rule's group.")
    file: str = Field(description="The rule file that defines the group.")
    name: str = Field(
        description="The alert name, or the metric a recording rule writes."
    )
    type: str = Field(description="'alerting' or 'recording'.")
    query: str = Field(description="The rule's PromQL expression.")
    labels: dict[str, str] = Field(description="The labels the rule adds.")
    health: str = Field(
        description=(
            "'ok' when the last evaluation worked, 'err' when it failed, and "
            "'unknown' before the first evaluation."
        )
    )
    last_error: str | None = Field(
        description="Why the last evaluation failed. Null when it worked."
    )
    last_evaluation: str | None = Field(
        description="When the rule last ran, as RFC-3339. Null before the first run."
    )
    evaluation_seconds: float | None = Field(
        description="How long the last evaluation took."
    )
    state: str | None = Field(
        description=(
            "For an alerting rule: 'firing' when any alert fires, else "
            "'pending' when any alert is pending, else 'inactive'. 'unknown' "
            "before the first evaluation. Null for a recording rule."
        )
    )
    duration_seconds: float | None = Field(
        description=(
            "For an alerting rule: its `for` duration, how long an alert stays "
            "pending before it fires. Null for a recording rule."
        )
    )
    annotations: dict[str, str] | None = Field(
        description=(
            "For an alerting rule: its annotation templates. Null for a recording rule."
        )
    )
    alerts: list[Alert] | None = Field(
        description=(
            "For an alerting rule: its active alerts. Null for a recording rule."
        )
    )


def _rule(group: dict[str, Any], raw: dict[str, Any]) -> Rule:
    alerting = raw.get("type") == "alerting"
    alerts: list[dict[str, Any]] = raw.get("alerts") or []
    return Rule(
        group=group.get("name", ""),
        file=group.get("file", ""),
        name=raw.get("name", ""),
        type=raw.get("type", ""),
        query=raw.get("query", ""),
        labels=raw.get("labels") or {},
        health=raw.get("health", ""),
        last_error=raw.get("lastError") or None,
        last_evaluation=event_time(raw.get("lastEvaluation")),
        evaluation_seconds=raw.get("evaluationTime"),
        state=raw.get("state") if alerting else None,
        duration_seconds=raw.get("duration") if alerting else None,
        annotations=(raw.get("annotations") or {}) if alerting else None,
        alerts=[_alert(alert) for alert in alerts] if alerting else None,
    )


def _rules_of(group: dict[str, Any]) -> list[dict[str, Any]]:
    return group.get("rules") or []


class RulesPage(Page):
    rules: list[Rule] = Field(
        description="Rules on this page, in the order of their groups and files."
    )


class AlertsPage(Page):
    alerts: list[Alert] = Field(description="Active alerts on this page.")


@tool(title="List rules", tags={"rules", READ}, annotations=READS)
async def list_rules(
    server: ServerSlug,
    type: Annotated[
        Literal["alert", "record", "any"],
        Field(
            description=(
                "Which rules to list: 'alert' for alerting rules, 'record' "
                "for recording rules, or 'any'. Default: 'any'."
            )
        ),
    ] = "any",
    group: Annotated[
        str | None,
        Field(
            description=(
                "Only list the rules of the group with exactly this name. "
                "Default: every group."
            )
        ),
    ] = None,
    limit: Limit = 100,
    offset: Offset = 0,
    servers: PrometheusServers = Shared(prometheus_servers),
) -> RulesPage:
    """List the alerting and recording rules a Prometheus server evaluates.

    Each rule reports its ``health`` and ``last_error``, so a rule that fails
    to evaluate is easy to find. Each alerting rule reports its ``state`` and
    its active ``alerts``. A rule whose ``state`` is 'inactive' has no
    active alerts, and ``list_alerts`` does not list it. Paging counts rules
    across all groups.
    """
    groups = await servers.client(server).rules(
        rule_type=None if type == "any" else type, group=group
    )
    rules = [_rule(g, raw) for g in groups for raw in _rules_of(g)]
    return RulesPage(
        server=server,
        total=len(rules),
        limit=limit,
        offset=offset,
        rules=rules[offset : offset + limit],
    )


@tool(title="List active alerts", tags={"rules", READ}, annotations=READS)
async def list_alerts(
    server: ServerSlug,
    state: Annotated[
        Literal["firing", "pending"] | None,
        Field(
            description=(
                "Only list alerts in this state: 'firing', or 'pending' for an "
                "alert that waits out its rule's `for` duration. Default: both."
            )
        ),
    ] = None,
    limit: Limit = 100,
    offset: Offset = 0,
    servers: PrometheusServers = Shared(prometheus_servers),
) -> AlertsPage:
    """List the active alerts of every alerting rule on a Prometheus server.

    An active alert is pending or firing. Prometheus does not list inactive
    alerts here: ``list_rules`` with ``type='alert'`` shows every alerting
    rule, including those in the 'inactive' state. This lists the alerts
    Prometheus evaluates, which can differ from what Alertmanager shows after
    its silences and inhibitions.
    """
    raw = await servers.client(server).alerts()
    alerts = [_alert(entry) for entry in raw]
    matching = [alert for alert in alerts if state is None or alert.state == state]
    return AlertsPage(
        server=server,
        total=len(matching),
        limit=limit,
        offset=offset,
        alerts=matching[offset : offset + limit],
    )


TOOLS: list[Callable[..., object]] = [list_rules, list_alerts]
