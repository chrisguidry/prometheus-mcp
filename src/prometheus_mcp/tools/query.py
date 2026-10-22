"""Query tools: run PromQL against a server and return the JSON result.

PromQL passes through unchanged. These tools don't try to abstract the
query language. They handle three things on top of the underlying client:
time-range parsing (so an agent can write ``"now-5h"``), series-level
paging for vector/matrix results, and a ``resolved`` block that echoes
the actual timestamps and step used so the agent can sanity-check.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, cast

from fastmcp.dependencies import Shared
from fastmcp.tools import tool
from pydantic import BaseModel, Field

from prometheus_mcp.prometheus import PrometheusServers, prometheus_servers
from prometheus_mcp.time_range import (
    format_duration_human,
    parse_range,
    parse_step,
    parse_time,
)
from prometheus_mcp.tools.discovery import Limit, Offset, ServerSlug
from prometheus_mcp.tools.effects import READ, READS

PromQL = Annotated[
    str,
    Field(min_length=1, description="A PromQL expression to evaluate."),
]
TimeArgument = Annotated[
    str,
    Field(
        description=(
            "Evaluation time. Accepts 'now', 'now-5h', '+2h', RFC-3339 / ISO, "
            "or a Unix epoch (seconds or ms). Default: 'now'."
        )
    ),
]
StartArgument = Annotated[
    str,
    Field(
        description=(
            "Start of the range. Same formats as `time`; defaults to 'now-1h'."
        )
    ),
]
EndArgument = Annotated[
    str,
    Field(description="End of the range. Same formats as `time`; defaults to 'now'."),
]
StepArgument = Annotated[
    str | None,
    Field(
        default=None,
        description=(
            "Sample step as a duration ('15s', '1m', '1h30m') or numeric "
            "seconds. When omitted, picks a step targeting ~100 samples with a "
            "15s floor."
        ),
    ),
]
TimeoutArgument = Annotated[
    str | None,
    Field(
        default=None,
        description=(
            "Server-side query timeout as a duration (e.g. '5s', '2m'). This "
            "MCP server waits that long plus 30 seconds for the answer. When "
            "omitted, it waits 30 seconds."
        ),
    ),
]

_QUERY_RANGE_TARGET_SAMPLES = 100

_EMPTY_INSTANT_HINT = (
    "No series matched at this instant. The metric may not exist, the "
    "selectors may not match, or there may be no samples within the "
    "lookback window (default 5m) at this time. `chart_range` over a "
    "wider window shows when data exists."
)
_EMPTY_RANGE_HINT = (
    "No series matched in this window. The metric may not exist, the "
    "selectors may not match, or there may be no samples in this time "
    "range. `chart_range` over a wider window shows when data exists."
)


class InstantWindow(BaseModel):
    time: str = Field(description="The evaluation time, as RFC-3339 in UTC.")


class RangeWindow(BaseModel):
    start: str = Field(description="The start of the range, as RFC-3339 in UTC.")
    end: str = Field(description="The end of the range, as RFC-3339 in UTC.")
    step: str = Field(description="The step between samples, such as '36s'.")


class QueryResult(BaseModel):
    """Prometheus's native ``{resultType, result}``, paged by series."""

    server: str = Field(description="The server slug that was queried.")
    resultType: str = Field(
        description="Prometheus's result type: vector, matrix, scalar, or string."
    )
    total_series: int = Field(
        description="Number of series before paging; 0 for scalar and string."
    )
    series_limit: int = Field(description="The `series_limit` used for this page.")
    series_offset: int = Field(description="The `series_offset` used for this page.")
    result: Any = Field(
        description=(
            "Prometheus's result. For vector and matrix, the series on this "
            "page; for scalar and string, the [time, value] pair."
        )
    )
    hint: str | None = Field(
        default=None,
        description="Why the result may be empty. Null when series matched.",
    )


class InstantQueryResult(QueryResult):
    resolved: InstantWindow = Field(description="The time Prometheus evaluated.")


class RangeQueryResult(QueryResult):
    resolved: RangeWindow = Field(description="The range Prometheus evaluated.")


def _slice_series(
    result: Any,
    *,
    result_type: str,
    limit: int,
    offset: int,
) -> tuple[int, Any]:
    if result_type in ("vector", "matrix") and isinstance(result, list):
        series = cast(list[Any], result)
        return len(series), series[offset : offset + limit]
    return 0, result


@tool(title="Instant PromQL query", tags={"query", READ}, annotations=READS)
async def query(
    server: ServerSlug,
    expr: PromQL,
    time: TimeArgument = "now",
    timeout: TimeoutArgument = None,
    series_limit: Limit = 100,
    series_offset: Offset = 0,
    servers: PrometheusServers = Shared(prometheus_servers),
) -> InstantQueryResult:
    """Run an instant PromQL query at a single point in time.

    Good for current values and set-style questions: what targets are up,
    top-k by current value, how many series match. For any question about
    how a metric behaves *over time*, start with ``chart_range`` instead. It
    shows the shape of the data at a fraction of the size of raw samples.

    Returns Prometheus's native ``{resultType, result}`` shape, plus a
    ``total_series`` count and series-level paging for vector results.
    Scalar and string results pass through unchanged. An empty result
    carries a ``hint``: the metric may not exist, or it may simply have no
    samples within the lookback window at that instant.
    """
    resolved_time = parse_time(time)
    timeout_delta = parse_step(timeout) if timeout is not None else None
    payload = await servers.client(server).query(
        expr, time=resolved_time, timeout=timeout_delta
    )

    result_type = payload.get("resultType", "")
    total, paged = _slice_series(
        payload.get("result"),
        result_type=result_type,
        limit=series_limit,
        offset=series_offset,
    )
    return InstantQueryResult(
        server=server,
        resultType=result_type,
        total_series=total,
        series_limit=series_limit,
        series_offset=series_offset,
        result=paged,
        resolved=InstantWindow(time=resolved_time.isoformat()),
        hint=_EMPTY_INSTANT_HINT if result_type == "vector" and total == 0 else None,
    )


@tool(title="Range PromQL query", tags={"query", READ}, annotations=READS)
async def query_range(
    server: ServerSlug,
    expr: PromQL,
    start: StartArgument = "now-1h",
    end: EndArgument = "now",
    step: StepArgument = None,
    timeout: TimeoutArgument = None,
    series_limit: Limit = 50,
    series_offset: Offset = 0,
    servers: PrometheusServers = Shared(prometheus_servers),
) -> RangeQueryResult:
    """Run a range PromQL query and return the raw samples.

    This is the zoom-in tool. Results are big: every series times every
    step, often tens of thousands of tokens. For a first look at how a
    metric behaves over time, use ``chart_range`` instead. Come back here
    with a narrow window or a tightly aggregated expression when you need
    the exact values.

    Returns the matrix result with series-level paging. Each series's
    samples pass through untouched: paging trims series count, not the
    points within a series. The ``resolved`` block reports the actual
    start / end / step the server saw. An empty result carries a ``hint``
    about why it might be empty.
    """
    start_dt, end_dt, step_dt = parse_range(
        start, end, step, target_samples=_QUERY_RANGE_TARGET_SAMPLES
    )
    timeout_delta = parse_step(timeout) if timeout is not None else None
    payload = await servers.client(server).query_range(
        expr,
        start=start_dt,
        end=end_dt,
        step=step_dt,
        timeout=timeout_delta,
    )

    result_type = payload.get("resultType", "")
    total, paged = _slice_series(
        payload.get("result"),
        result_type=result_type,
        limit=series_limit,
        offset=series_offset,
    )
    return RangeQueryResult(
        server=server,
        resultType=result_type,
        total_series=total,
        series_limit=series_limit,
        series_offset=series_offset,
        result=paged,
        resolved=RangeWindow(
            start=start_dt.isoformat(),
            end=end_dt.isoformat(),
            step=format_duration_human(step_dt),
        ),
        hint=_EMPTY_RANGE_HINT if result_type == "matrix" and total == 0 else None,
    )


TOOLS: list[Callable[..., object]] = [query, query_range]
