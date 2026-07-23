"""Chart tool: render a range query as ASCII art for an agent to read."""

from __future__ import annotations

import math
from typing import Annotated

from fastmcp.tools import ToolResult
from pydantic import Field

from prometheus_mcp import server as server_module
from prometheus_mcp.chart import ChartSeries, format_series_label, render_chart
from prometheus_mcp.prometheus import get_client
from prometheus_mcp.server import mcp
from prometheus_mcp.time_range import format_duration_human, parse_range
from prometheus_mcp.tools.discovery import ServerSlug
from prometheus_mcp.tools.query import EndArgument, PromQL, StartArgument

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
ChartStepArgument = Annotated[
    str | None,
    Field(
        default=None,
        description=(
            "Sample step as a duration ('15s', '1m', '1h30m') or numeric "
            "seconds. When omitted, picks a step targeting ~360 samples with "
            "a 15s floor; the renderer resamples to the chart width either "
            "way."
        ),
    ),
]

# Stretches longer than this many steps between samples render as blank
# space rather than an interpolated line.
_GAP_STEPS = 1.5

_EMPTY_HINT = (
    "No series matched in this window. The metric may not exist, the label "
    "selectors may not match, or there may be no samples in this time range "
    "— try a wider window, or `list_series` to check what exists."
)


def _parse_value(raw: object) -> float | None:
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if math.isnan(value) else value


def _label_cardinalities(metrics: list[dict[str, str]]) -> dict[str, int]:
    seen: dict[str, set[str]] = {}
    for metric in metrics:
        for key, value in metric.items():
            seen.setdefault(key, set()).add(value)
    return {
        key: len(values)
        for key, values in sorted(seen.items(), key=lambda kv: (len(kv[1]), kv[0]))
    }


def _too_many_series_text(
    total: int,
    max_series: int,
    cardinalities: dict[str, int],
    examples: list[str],
    hint: str,
) -> str:
    lines = [f"Too many series to chart: {total} series (max_series={max_series})."]
    lines.append("")
    lines.append("Label cardinalities:")
    lines.extend(f"  {key}: {count}" for key, count in cardinalities.items())
    lines.append("")
    lines.append("Example series:")
    lines.extend(f"  {label}" for label in examples)
    lines.append("")
    lines.append(hint)
    return "\n".join(lines)


@mcp.tool(annotations={"title": "Chart a range query", **_READ_ONLY})
async def chart_range(
    server: ServerSlug,
    expr: PromQL,
    start: StartArgument = "now-1h",
    end: EndArgument = "now",
    step: ChartStepArgument = None,
    width: ChartWidth = 80,
    height: ChartHeight = 18,
    max_series: MaxSeries = 5,
) -> ToolResult:
    """Run a range query and render it as an ASCII chart — the recommended
    first step for any question about how a metric behaves over time.

    The chart compresses a range query into a few hundred tokens: the plot
    shows trend, spikes, dips, gaps, and periodicity, and the legend
    reports min / avg / max / last for every series, which often answers
    the question without another call. ``query_range`` returns the same
    window as raw samples at many times the size — reach for it only when
    you need exact values, and narrow the window first using what the
    chart shows.

    Missing data renders as blank space, and the legend notes what
    fraction of the window had no samples.

    Refuses to draw when more than ``max_series`` series come back;
    the response then summarizes label cardinalities so you can pick a
    label to aggregate by (``sum by (...)``), filter with selectors, or
    wrap the expression in ``topk(...)`` and retry.
    """
    start_dt, end_dt, step_dt = parse_range(start, end, step)
    window = (
        f"Queried {server} from {start_dt.isoformat(timespec='seconds')} "
        f"to {end_dt.isoformat(timespec='seconds')} "
        f"(step {format_duration_human(step_dt)})."
    )
    client = get_client(server, server_module.SERVERS)
    payload = await client.query_range(expr, start=start_dt, end=end_dt, step=step_dt)

    result_type = payload.get("resultType")
    raw_series = payload.get("result") or []
    if result_type != "matrix":
        return ToolResult(
            content=(
                f"chart_range expects a matrix result, got {result_type!r}. "
                "Use `query_range` with an expression that returns a matrix."
            )
        )

    if not raw_series:
        return ToolResult(content=f"(no data)\n\n{window}\n\n{_EMPTY_HINT}")

    labels = [format_series_label(s.get("metric", {})) for s in raw_series]
    if len(raw_series) > max_series:
        cardinalities = _label_cardinalities([s.get("metric", {}) for s in raw_series])
        hint = (
            "Aggregate with `sum by (<label>) (...)` using a low-cardinality "
            "label, narrow with label selectors, or wrap the expression in "
            f"`topk({max_series}, ...)`, then retry."
        )
        return ToolResult(
            content=_too_many_series_text(
                len(raw_series), max_series, cardinalities, labels[:3], hint
            )
        )

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
        title=expr,
        max_gap_seconds=step_dt.total_seconds() * _GAP_STEPS,
    )
    return ToolResult(content=chart_string)
