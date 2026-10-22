"""Discovery tools: what metrics, labels, and series live in a given Prometheus."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any

from fastmcp.dependencies import Shared
from fastmcp.tools import tool
from pydantic import BaseModel, Field

from prometheus_mcp.prometheus import PrometheusServers, prometheus_servers
from prometheus_mcp.time_range import parse_time
from prometheus_mcp.tools.effects import READ, READS

ServerSlug = Annotated[
    str,
    Field(
        description=(
            "Short slug naming the Prometheus server to query "
            "(as returned by `list_servers`)."
        )
    ),
]
MatchSelectors = Annotated[
    list[str] | None,
    Field(
        default=None,
        description=(
            "Optional list of PromQL series selectors to narrow the search, "
            "e.g. ['up', '{job=\"api\"}']. Default: no selector."
        ),
    ),
]
WindowStart = Annotated[
    str | None,
    Field(
        default=None,
        description=(
            "Only search series with samples at or after this time. Accepts "
            "'now', 'now-5h', '+2h', RFC-3339, or a Unix epoch (seconds or ms). "
            "Default: no lower bound."
        ),
    ),
]
WindowEnd = Annotated[
    str | None,
    Field(
        default=None,
        description=(
            "Only search series with samples at or before this time. Same "
            "formats as `start`. Default: no upper bound."
        ),
    ),
]
Limit = Annotated[
    int,
    Field(
        ge=1,
        le=10_000,
        description="Maximum number of entries to return, from 1 to 10,000.",
    ),
]
Offset = Annotated[
    int,
    Field(ge=0, description="Zero-based offset into the full result set."),
]


def _page(items: list[Any], *, limit: int, offset: int) -> tuple[int, list[Any]]:
    return len(items), items[offset : offset + limit]


def _time_or_none(value: str | None) -> datetime | None:
    return None if value is None else parse_time(value)


class Page(BaseModel):
    """The paging fields every discovery tool returns."""

    server: str = Field(description="The server slug that was queried.")
    total: int = Field(description="Number of entries before paging.")
    limit: int = Field(description="The `limit` used for this page.")
    offset: int = Field(description="The `offset` used for this page.")


class MetricsPage(Page):
    metrics: list[str] = Field(description="Metric names on this page.")


class LabelsPage(Page):
    labels: list[str] = Field(description="Label names on this page.")


class LabelValuesPage(Page):
    label: str = Field(description="The label whose values these are.")
    values: list[str] = Field(description="Label values on this page.")


class SeriesPage(Page):
    series: list[dict[str, str]] = Field(
        description="The full label set of each series on this page."
    )


class MetricMetadata(BaseModel):
    metric: str = Field(description="Metric name.")
    type: str = Field(
        description=(
            "Metric type: counter, gauge, histogram, gaugehistogram, summary, "
            "info, stateset, or unknown."
        )
    )
    help: str = Field(description="The metric's help text.")
    unit: str = Field(description="The metric's unit, often empty.")


class MetadataPage(Page):
    metadata: list[MetricMetadata] = Field(
        description="Metadata records on this page, sorted by metric and type."
    )


@tool(title="List metric names", tags={"discovery", READ}, annotations=READS)
async def list_metrics(
    server: ServerSlug,
    match: MatchSelectors = None,
    start: WindowStart = None,
    end: WindowEnd = None,
    limit: Limit = 500,
    offset: Offset = 0,
    servers: PrometheusServers = Shared(prometheus_servers),
) -> MetricsPage:
    """Return the metric names known to a Prometheus server.

    Uses ``/api/v1/label/__name__/values``, so this is exactly the list of
    metrics that have at least one series. Pass ``match``, ``start``, or
    ``end`` to scope the search (cheaper on high-cardinality stores), and use
    ``limit`` / ``offset`` for paging.
    """
    names = await servers.client(server).label_values(
        "__name__",
        match=match,
        start=_time_or_none(start),
        end=_time_or_none(end),
    )
    total, page = _page(names, limit=limit, offset=offset)
    return MetricsPage(
        server=server, total=total, limit=limit, offset=offset, metrics=page
    )


@tool(title="List label names", tags={"discovery", READ}, annotations=READS)
async def list_labels(
    server: ServerSlug,
    match: MatchSelectors = None,
    start: WindowStart = None,
    end: WindowEnd = None,
    limit: Limit = 500,
    offset: Offset = 0,
    servers: PrometheusServers = Shared(prometheus_servers),
) -> LabelsPage:
    """Return every label name that appears in this Prometheus.

    Pair with ``label_values`` to enumerate what each label can take.
    """
    names = await servers.client(server).labels(
        match=match, start=_time_or_none(start), end=_time_or_none(end)
    )
    total, page = _page(names, limit=limit, offset=offset)
    return LabelsPage(
        server=server, total=total, limit=limit, offset=offset, labels=page
    )


@tool(title="List label values", tags={"discovery", READ}, annotations=READS)
async def label_values(
    server: ServerSlug,
    label: Annotated[
        str, Field(description="The label whose values to enumerate, e.g. 'job'.")
    ],
    match: MatchSelectors = None,
    start: WindowStart = None,
    end: WindowEnd = None,
    limit: Limit = 500,
    offset: Offset = 0,
    servers: PrometheusServers = Shared(prometheus_servers),
) -> LabelValuesPage:
    """Return every value seen for a single label.

    Useful for finding the set of jobs, instances, status codes, etc.
    """
    values = await servers.client(server).label_values(
        label, match=match, start=_time_or_none(start), end=_time_or_none(end)
    )
    total, page = _page(values, limit=limit, offset=offset)
    return LabelValuesPage(
        server=server,
        label=label,
        total=total,
        limit=limit,
        offset=offset,
        values=page,
    )


@tool(title="List series", tags={"discovery", READ}, annotations=READS)
async def list_series(
    server: ServerSlug,
    match: Annotated[
        list[str],
        Field(
            min_length=1,
            description=(
                "Required series selectors. Prometheus rejects calls without "
                "at least one selector."
            ),
        ),
    ],
    start: WindowStart = None,
    end: WindowEnd = None,
    limit: Limit = 200,
    offset: Offset = 0,
    servers: PrometheusServers = Shared(prometheus_servers),
) -> SeriesPage:
    """Return the full label set of every series matching ``match``.

    Helpful for inspecting label cardinality before building a query.
    """
    series = await servers.client(server).series(
        match=match, start=_time_or_none(start), end=_time_or_none(end)
    )
    total, page = _page(series, limit=limit, offset=offset)
    return SeriesPage(
        server=server, total=total, limit=limit, offset=offset, series=page
    )


@tool(title="Metric metadata", tags={"discovery", READ}, annotations=READS)
async def metric_metadata(
    server: ServerSlug,
    metric: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "If set, return metadata only for this metric. Default: every metric."
            ),
        ),
    ] = None,
    limit: Limit = 200,
    offset: Offset = 0,
    servers: PrometheusServers = Shared(prometheus_servers),
) -> MetadataPage:
    """Return the per-metric metadata Prometheus has scraped (type, help, unit).

    Prometheus returns each distinct set of metadata it scraped for a metric,
    so a metric can have more than one record. The output is flattened to ``[{metric, type, help, unit}, ...]`` so paging is
    predictable.
    """
    raw = await servers.client(server).metadata(metric=metric)
    rows = [
        MetricMetadata(
            metric=name,
            type=entry.get("type", ""),
            help=entry.get("help", ""),
            unit=entry.get("unit", ""),
        )
        for name, entries in raw.items()
        for entry in entries
    ]
    rows.sort(key=lambda row: (row.metric, row.type))
    total, page = _page(rows, limit=limit, offset=offset)
    return MetadataPage(
        server=server, total=total, limit=limit, offset=offset, metadata=page
    )


TOOLS: list[Callable[..., object]] = [
    list_metrics,
    list_labels,
    label_values,
    list_series,
    metric_metadata,
]
