"""Rule tools: the alerting and recording rules a Prometheus server evaluates,
and the alerts its alerting rules raise.

The shapes follow ``GET /api/v1/rules`` and ``GET /api/v1/alerts`` in the
Prometheus HTTP API
(https://prometheus.io/docs/prometheus/latest/querying/api/#rules).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
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


_NEVER = datetime.max.replace(tzinfo=UTC)


def _firing_then_oldest(
    alert: Alert,
) -> tuple[bool, datetime, list[tuple[str, str]]]:
    """Order alerts the same way on every call: firing before pending, then
    oldest first, then by labels.

    Prometheus returns a rule's alerts in a different order on each call.
    Without a fixed order, ``max_alerts_per_rule`` would keep different alerts
    each time, and paging through ``list_alerts`` could repeat or skip alerts.
    Alerts from one evaluation share ``active_at``, so their labels break the
    tie.
    """
    # Compare parsed times: Prometheus trims trailing zeros from the fraction
    # of a second, so the strings of two times do not sort in time order.
    since = (
        _NEVER if alert.active_at is None else datetime.fromisoformat(alert.active_at)
    )
    return alert.state != "firing", since, sorted(alert.labels.items())


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
            "For an alerting rule: its active alerts, firing before pending and "
            "oldest first, up to `max_alerts_per_rule`. Null for a recording "
            "rule, and when `include_alerts` is false."
        )
    )
    omitted_alerts: int | None = Field(
        description=(
            "For an alerting rule: how many of its active alerts "
            "`max_alerts_per_rule` left out of `alerts`. Null for a recording "
            "rule, and when `include_alerts` is false."
        )
    )


def _rule(
    group: dict[str, Any],
    raw: dict[str, Any],
    *,
    include_alerts: bool,
    max_alerts: int | None,
) -> Rule:
    alerting = raw.get("type") == "alerting"
    alerts: list[Alert] | None = None
    omitted: int | None = None
    if alerting and include_alerts:
        active = sorted(map(_alert, raw.get("alerts") or []), key=_firing_then_oldest)
        alerts = active[:max_alerts]
        omitted = len(active) - len(alerts)
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
        alerts=alerts,
        omitted_alerts=omitted,
    )


def _rules_of(group: dict[str, Any]) -> list[dict[str, Any]]:
    return group.get("rules") or []


class RulesPage(Page):
    rules: list[Rule] = Field(
        description="Rules on this page, in the order of their groups and files."
    )


class AlertsPage(Page):
    alerts: list[Alert] = Field(
        description=(
            "Active alerts on this page, firing before pending and oldest first."
        )
    )


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
    include_alerts: Annotated[
        bool,
        Field(
            description=(
                "Whether each alerting rule lists its active alerts in "
                "`alerts`. Pass false to list the rules alone, which keeps the "
                "answer small when rules have many alerts. Default: true."
            )
        ),
    ] = True,
    max_alerts_per_rule: Annotated[
        int | None,
        Field(
            ge=0,
            description=(
                "The most active alerts to list for each alerting rule, firing "
                "before pending and oldest first. `omitted_alerts` counts the "
                "rest. Ignored when `include_alerts` is false. Default: no limit."
            ),
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
    active alerts, and ``list_alerts`` does not list it. One rule can have
    thousands of active alerts: pass ``include_alerts=false`` or
    ``max_alerts_per_rule`` to keep the answer small. Paging counts rules
    across all groups.
    """
    groups = await servers.client(server).rules(
        rule_type=None if type == "any" else type,
        group=group,
        exclude_alerts=not include_alerts,
    )
    # Prometheus before 2.49 ignores `exclude_alerts` and returns the alerts
    # anyway, so `_rule` leaves them out itself.
    rules = [
        _rule(g, raw, include_alerts=include_alerts, max_alerts=max_alerts_per_rule)
        for g in groups
        for raw in _rules_of(g)
    ]
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
    alertname: Annotated[
        str | None,
        Field(
            description=(
                "Only list alerts whose `alertname` label is exactly this "
                "value, e.g. 'NodeDown'. Default: every alert name."
            )
        ),
    ] = None,
    labels: Annotated[
        dict[str, str] | None,
        Field(
            description=(
                "Only list alerts that have each of these labels with exactly "
                "this value, e.g. {'severity': 'page'}. As in PromQL, the "
                "value '' matches an alert that does not have the label. "
                "Default: no label filter."
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
    its silences and inhibitions. Alerts are listed firing before pending and
    oldest first, in the same order on every call, so paging with ``offset``
    neither repeats nor skips an alert.
    """
    raw = await servers.client(server).alerts()
    alerts = sorted(map(_alert, raw), key=_firing_then_oldest)
    # The alerts API takes no filters, so every filter applies here.
    matching = [
        alert
        for alert in alerts
        if (state is None or alert.state == state)
        and (alertname is None or alert.labels.get("alertname") == alertname)
        and all(
            alert.labels.get(name, "") == value
            for name, value in (labels or {}).items()
        )
    ]
    return AlertsPage(
        server=server,
        total=len(matching),
        limit=limit,
        offset=offset,
        alerts=matching[offset : offset + limit],
    )


TOOLS: list[Callable[..., object]] = [list_rules, list_alerts]
