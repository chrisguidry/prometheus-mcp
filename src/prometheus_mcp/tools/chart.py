"""Chart tool: render a range query as ASCII art for an agent to read."""

from __future__ import annotations

import math
from typing import Annotated, Any

from pydantic import Field

from prometheus_mcp import server as server_module
from prometheus_mcp.chart import ChartSeries, format_series_label, render_chart
from prometheus_mcp.prometheus import get_client
from prometheus_mcp.server import mcp
from prometheus_mcp.time_range import format_duration_human, parse_range
from prometheus_mcp.tools.discovery import ServerSlug
from prometheus_mcp.tools.query import (
    EndArgument,
    PromQL,
    StartArgument,
    StepArgument,
)

_READ_ONLY = {
    "readOnlyHint": True,
    "idempotentHint": True,
    "openWorldHint": True,
}

ChartWidth = Annotated[
    int,
    Field(
        ge=40,
        le=200,
        description="Chart width in characters. Default 80 fits most terminals.",
    ),
]
ChartHeight = Annotated[
    int,
    Field(
        ge=6,
        le=60,
        description="Chart height in rows. Default 18 is a comfortable read.",
    ),
]
MaxSeries = Annotated[
    int,
    Field(
        ge=1,
        le=20,
        description=(
            "Refuse to draw if the result has more than this many series — an "
            "overplotted chart isn't useful. Use PromQL `topk` or label "
            "selectors to narrow further."
        ),
    ),
]


def _parse_value(raw: object) -> float | None:
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if math.isnan(value) else value


@mcp.tool(annotations={"title": "Chart a range query", **_READ_ONLY})
async def chart_range(
    server: ServerSlug,
    expr: PromQL,
    start: StartArgument = "now-1h",
    end: EndArgument = "now",
    step: StepArgument = None,
    width: ChartWidth = 80,
    height: ChartHeight = 18,
    max_series: MaxSeries = 5,
) -> dict[str, Any]:
    """Run a range query and render the result as an ASCII chart.

    Useful when an agent wants to *see* the shape of a metric rather than
    digest raw points. The returned ``chart`` field is a single string —
    legend, plot, and timestamp caption — that can be passed straight to
    another agent or printed to a terminal.

    Refuses to draw when more than ``max_series`` series come back: an
    over-plotted chart is unreadable. Wrap the expression in ``topk(...)``
    or add label selectors and retry.
    """
    start_dt, end_dt, step_dt = parse_range(start, end, step)
    resolved = {
        "start": start_dt.isoformat(),
        "end": end_dt.isoformat(),
        "step": format_duration_human(step_dt),
    }
    client = get_client(server, server_module.SERVERS)
    payload = await client.query_range(expr, start=start_dt, end=end_dt, step=step_dt)

    result_type = payload.get("resultType")
    raw_series = payload.get("result") or []
    if result_type != "matrix":
        return {
            "server": server,
            "error": (
                f"chart_range expects a matrix result, got {result_type!r}. "
                "Use `query_range` with an expression that returns a matrix."
            ),
            "resolved": resolved,
        }

    labels = [format_series_label(s.get("metric", {})) for s in raw_series]
    if not raw_series:
        return {
            "server": server,
            "chart": "(no data)",
            "series": [],
            "resolved": resolved,
        }
    if len(raw_series) > max_series:
        return {
            "server": server,
            "too_many_series": True,
            "total_series": len(raw_series),
            "max_series": max_series,
            "series": labels,
            "hint": (
                "Narrow the result with a `topk(N, ...)` wrapper or by adding "
                "label selectors, then retry."
            ),
            "resolved": resolved,
        }

    chart_series = [
        ChartSeries(
            label=label,
            points=[
                (float(ts), _parse_value(value))
                for ts, value in entry.get("values", [])
            ],
        )
        for label, entry in zip(labels, raw_series)
    ]
    chart_string = render_chart(
        chart_series,
        start=start_dt,
        end=end_dt,
        width=width,
        height=height,
    )
    return {
        "server": server,
        "chart": chart_string,
        "series": labels,
        "resolved": resolved,
    }
