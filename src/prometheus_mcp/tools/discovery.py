"""Discovery tools: what metrics, labels, and series live in a given Prometheus."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field

from prometheus_mcp import server as server_module
from prometheus_mcp.prometheus import get_client
from prometheus_mcp.server import mcp

_READ_ONLY = {
    "readOnlyHint": True,
    "idempotentHint": True,
    "openWorldHint": True,
}

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
            "e.g. ['up', '{job=\"api\"}']."
        ),
    ),
]
Limit = Annotated[
    int,
    Field(
        ge=1,
        le=10_000,
        description="Maximum entries to return after slicing.",
    ),
]
Offset = Annotated[
    int,
    Field(ge=0, description="Zero-based offset into the full result set."),
]


def _page(items: list[Any], *, limit: int, offset: int) -> tuple[int, list[Any]]:
    return len(items), items[offset : offset + limit]


@mcp.tool(annotations={"title": "List metric names", **_READ_ONLY})
async def list_metrics(
    server: ServerSlug,
    match: MatchSelectors = None,
    limit: Limit = 500,
    offset: Offset = 0,
) -> dict[str, Any]:
    """Return the metric names known to a Prometheus server.

    Uses ``/api/v1/label/__name__/values`` so this is exactly the list of
    metrics that have at least one series. Pass ``match`` to scope the
    search (cheaper on high-cardinality stores) and use ``limit`` /
    ``offset`` for paging.
    """
    client = get_client(server, server_module.SERVERS)
    names = await client.label_values("__name__", match=match)
    total, page = _page(names, limit=limit, offset=offset)
    return {
        "server": server,
        "total": total,
        "limit": limit,
        "offset": offset,
        "metrics": page,
    }


@mcp.tool(annotations={"title": "List label names", **_READ_ONLY})
async def list_labels(
    server: ServerSlug,
    match: MatchSelectors = None,
    limit: Limit = 500,
    offset: Offset = 0,
) -> dict[str, Any]:
    """Return every label name that appears in this Prometheus.

    Pair with ``label_values`` to enumerate what each label can take.
    """
    client = get_client(server, server_module.SERVERS)
    names = await client.labels(match=match)
    total, page = _page(names, limit=limit, offset=offset)
    return {
        "server": server,
        "total": total,
        "limit": limit,
        "offset": offset,
        "labels": page,
    }


@mcp.tool(annotations={"title": "List label values", **_READ_ONLY})
async def label_values(
    server: ServerSlug,
    label: Annotated[str, Field(description="The label whose values to enumerate.")],
    match: MatchSelectors = None,
    limit: Limit = 500,
    offset: Offset = 0,
) -> dict[str, Any]:
    """Return every value seen for a single label.

    Useful for finding the set of jobs, instances, status codes, etc.
    """
    client = get_client(server, server_module.SERVERS)
    values = await client.label_values(label, match=match)
    total, page = _page(values, limit=limit, offset=offset)
    return {
        "server": server,
        "label": label,
        "total": total,
        "limit": limit,
        "offset": offset,
        "values": page,
    }


@mcp.tool(annotations={"title": "List series", **_READ_ONLY})
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
    limit: Limit = 200,
    offset: Offset = 0,
) -> dict[str, Any]:
    """Return the full label set of every series matching ``match``.

    Helpful for inspecting label cardinality before building a query.
    """
    client = get_client(server, server_module.SERVERS)
    series = await client.series(match=match)
    total, page = _page(series, limit=limit, offset=offset)
    return {
        "server": server,
        "total": total,
        "limit": limit,
        "offset": offset,
        "series": page,
    }


@mcp.tool(annotations={"title": "Metric metadata", **_READ_ONLY})
async def metric_metadata(
    server: ServerSlug,
    metric: Annotated[
        str | None,
        Field(
            default=None,
            description="If set, return metadata only for this metric.",
        ),
    ] = None,
    limit: Limit = 200,
    offset: Offset = 0,
) -> dict[str, Any]:
    """Return the per-metric metadata Prometheus has scraped (type, help, unit).

    Prometheus returns one record per metric per scrape source. The output
    is flattened to ``[{metric, type, help, unit}, ...]`` so paging is
    predictable.
    """
    client = get_client(server, server_module.SERVERS)
    raw = await client.metadata(metric=metric)
    rows: list[dict[str, str]] = []
    for name, entries in raw.items():
        for entry in entries:
            rows.append(
                {
                    "metric": name,
                    "type": entry.get("type", ""),
                    "help": entry.get("help", ""),
                    "unit": entry.get("unit", ""),
                }
            )
    rows.sort(key=lambda row: (row["metric"], row["type"]))
    total, page = _page(rows, limit=limit, offset=offset)
    return {
        "server": server,
        "total": total,
        "limit": limit,
        "offset": offset,
        "metadata": page,
    }
