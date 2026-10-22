"""Target tools: what a Prometheus server scrapes, and whether the scrapes work.

The shapes follow ``GET /api/v1/targets`` in the Prometheus HTTP API
(https://prometheus.io/docs/prometheus/latest/querying/api/#targets).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastmcp.dependencies import Shared
from fastmcp.tools import tool
from pydantic import BaseModel, Field

from prometheus_mcp.config import without_credentials
from prometheus_mcp.prometheus import PrometheusServers, prometheus_servers
from prometheus_mcp.time_range import event_time
from prometheus_mcp.tools.discovery import Limit, Offset, Page, ServerSlug
from prometheus_mcp.tools.effects import READ, READS

TargetState = Literal["active", "dropped"]
TargetHealth = Literal["up", "down", "unknown"]


class Target(BaseModel):
    """One scrape target, active or dropped."""

    state: TargetState = Field(
        description=(
            "'active' for a target Prometheus scrapes. 'dropped' for a target "
            "that service discovery found and relabeling removed."
        )
    )
    scrape_pool: str = Field(
        description="The scrape pool, named for the `job_name` in the scrape config."
    )
    job: str | None = Field(description="The target's `job` label.")
    instance: str | None = Field(
        description="The target's `instance` label. Null for a dropped target."
    )
    labels: dict[str, str] = Field(
        description=(
            "The labels after relabeling, which Prometheus adds to every series "
            "it scrapes from the target. Empty for a dropped target."
        )
    )
    discovered_labels: dict[str, str] | None = Field(
        description=(
            "The labels service discovery found, before relabeling dropped the "
            "target. Null for an active target."
        )
    )
    health: TargetHealth | None = Field(
        description=(
            "'up' when the last scrape worked, 'down' when it failed, and "
            "'unknown' before the first scrape. Null for a dropped target."
        )
    )
    last_error: str | None = Field(
        description="Why the last scrape failed. Null when it worked."
    )
    last_scrape: str | None = Field(
        description="When the last scrape started, as RFC-3339. Null before the first."
    )
    last_scrape_duration_seconds: float | None = Field(
        description="How long the last scrape took. Null for a dropped target."
    )
    scrape_interval: str | None = Field(
        description=(
            "How often Prometheus scrapes the target, as a Prometheus duration "
            "such as '1m'. Null for a dropped target, and on Prometheus "
            "before 2.30."
        )
    )
    scrape_timeout: str | None = Field(
        description=(
            "How long Prometheus waits for one scrape before it fails, as a "
            "Prometheus duration such as '10s'. Null for a dropped target, "
            "and on Prometheus before 2.30."
        )
    )
    scrape_url: str | None = Field(
        description=(
            "The URL Prometheus scrapes, without any user name or password. "
            "Null for a dropped target."
        )
    )


class TargetsPage(Page):
    targets: list[Target] = Field(
        description="Active targets, then dropped targets, each by scrape pool."
    )


def _active(raw: dict[str, Any]) -> Target:
    labels: dict[str, str] = raw.get("labels") or {}
    scrape_url: str | None = raw.get("scrapeUrl")
    return Target(
        state="active",
        scrape_pool=raw.get("scrapePool", ""),
        job=labels.get("job"),
        instance=labels.get("instance"),
        labels=labels,
        discovered_labels=None,
        health=raw.get("health"),
        last_error=raw.get("lastError") or None,
        last_scrape=event_time(raw.get("lastScrape")),
        last_scrape_duration_seconds=raw.get("lastScrapeDuration"),
        scrape_interval=raw.get("scrapeInterval") or None,
        scrape_timeout=raw.get("scrapeTimeout") or None,
        scrape_url=without_credentials(scrape_url) if scrape_url else None,
    )


def _dropped(raw: dict[str, Any]) -> Target:
    discovered: dict[str, str] = raw.get("discoveredLabels") or {}
    return Target(
        state="dropped",
        scrape_pool=raw.get("scrapePool", ""),
        job=discovered.get("job"),
        instance=None,
        labels={},
        discovered_labels=discovered,
        health=None,
        last_error=None,
        last_scrape=None,
        last_scrape_duration_seconds=None,
        scrape_interval=None,
        scrape_timeout=None,
        scrape_url=None,
    )


@tool(title="List scrape targets", tags={"targets", READ}, annotations=READS)
async def list_targets(
    server: ServerSlug,
    state: Annotated[
        Literal["active", "dropped", "any"],
        Field(
            description=(
                "Which targets to list: 'active' (the ones Prometheus "
                "scrapes), 'dropped' (the ones relabeling removed), or 'any'. "
                "Default: 'active'."
            )
        ),
    ] = "active",
    health: Annotated[
        TargetHealth | None,
        Field(
            description=(
                "Only list active targets with this health: 'up', 'down', or "
                "'unknown'. Dropped targets have no health, so this excludes "
                "them. Default: any health."
            )
        ),
    ] = None,
    job: Annotated[
        str | None,
        Field(
            description=(
                "Only list targets whose `job` label is exactly this value, "
                "e.g. 'node'. Default: every job."
            )
        ),
    ] = None,
    scrape_pool: Annotated[
        str | None,
        Field(
            description=(
                "Only list targets of the scrape pool with exactly this name, "
                "which is a `job_name` in the scrape config. Prometheus 2.42 and "
                "later filter by scrape pool themselves, so this is cheaper "
                "than `job` on a server with many targets. Default: every "
                "scrape pool."
            )
        ),
    ] = None,
    limit: Limit = 100,
    offset: Offset = 0,
    servers: PrometheusServers = Shared(prometheus_servers),
) -> TargetsPage:
    """List the targets a Prometheus server scrapes, and how their scrapes went.

    Start here when a metric is missing or stale: a target that is 'down'
    reports why in ``last_error``. Pass ``health='down'`` to list only the
    failing targets. ``total`` counts the targets after the filters and
    before paging.
    """
    raw = await servers.client(server).targets(state=state, scrape_pool=scrape_pool)
    # Prometheus filters by state, and from 2.42 by scrape pool. Checking again
    # here keeps a server that ignores either parameter from mixing in the
    # targets that the caller left out.
    active: list[dict[str, Any]] = raw.get("activeTargets") or []
    dropped: list[dict[str, Any]] = raw.get("droppedTargets") or []
    targets: list[Target] = []
    if state != "dropped":
        targets.extend(_active(entry) for entry in active)
    if state != "active":
        targets.extend(_dropped(entry) for entry in dropped)
    matching = [
        target
        for target in targets
        if (health is None or target.health == health)
        and (job is None or target.job == job)
        and (scrape_pool is None or target.scrape_pool == scrape_pool)
    ]
    return TargetsPage(
        server=server,
        total=len(matching),
        limit=limit,
        offset=offset,
        targets=matching[offset : offset + limit],
    )


TOOLS: list[Callable[..., object]] = [list_targets]
