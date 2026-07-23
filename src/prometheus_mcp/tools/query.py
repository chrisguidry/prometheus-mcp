"""Query tools: run PromQL against a server and return the JSON result.

PromQL passes through unchanged — these tools don't try to abstract the
query language. They handle three things on top of the underlying client:
time-range parsing (so an agent can write ``"now-5h"``), series-level
paging for vector/matrix results, and a ``resolved`` block that echoes
the actual timestamps and step used so the agent can sanity-check.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field

from prometheus_mcp import server as server_module
from prometheus_mcp.prometheus import get_client
from prometheus_mcp.server import mcp
from prometheus_mcp.time_range import (
    format_duration_human,
    parse_range,
    parse_step,
    parse_time,
)
from prometheus_mcp.tools.discovery import Limit, Offset, ServerSlug


_READ_ONLY = {
    "readOnlyHint": True,
    "idempotentHint": True,
    "openWorldHint": True,
}

PromQL = Annotated[
    str,
    Field(min_length=1, description="A PromQL expression to evaluate."),
]
TimeArgument = Annotated[
    str,
    Field(
        description=(
            "Evaluation time. Accepts 'now', 'now-5h', '+2h', RFC-3339 / ISO, "
            "or a Unix epoch (seconds or ms)."
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

_QUERY_RANGE_TARGET_SAMPLES = 100

_EMPTY_INSTANT_HINT = (
    "No series matched at this instant. The metric may not exist, the "
    "selectors may not match, or there may be no samples within the "
    "lookback window (default 5m) at this time — `chart_range` over a "
    "wider window shows when data exists."
)
_EMPTY_RANGE_HINT = (
    "No series matched in this window. The metric may not exist, the "
    "selectors may not match, or there may be no samples in this time "
    "range — `chart_range` over a wider window shows when data exists."
)
TimeoutArgument = Annotated[
    str | None,
    Field(
        default=None,
        description="Server-side query timeout as a duration (e.g. '5s').",
    ),
]


def _slice_series(
    result: Any,
    *,
    result_type: str,
    limit: int,
    offset: int,
) -> tuple[int, Any]:
    if result_type in ("vector", "matrix") and isinstance(result, list):
        return len(result), result[offset : offset + limit]
    return 0, result


@mcp.tool(annotations={"title": "Instant PromQL query", **_READ_ONLY})
async def query(
    server: ServerSlug,
    expr: PromQL,
    time: TimeArgument = "now",
    timeout: TimeoutArgument = None,
    series_limit: Limit = 100,
    series_offset: Offset = 0,
) -> dict[str, Any]:
    """Run an instant PromQL query at a single point in time.

    Good for current values and set-style questions: what targets are up,
    top-k by current value, how many series match. For any question about
    how a metric behaves *over time*, start with ``chart_range`` instead —
    it shows the shape of the data at a fraction of the size of raw
    samples.

    Returns Prometheus's native ``{resultType, result}`` shape, plus a
    ``total_series`` count and series-level paging for vector results.
    Scalar and string results pass through unchanged. An empty result
    carries a ``hint``: the metric may not exist, or it may simply have no
    samples within the lookback window at that instant.
    """
    resolved_time = parse_time(time)
    timeout_delta = parse_step(timeout) if timeout is not None else None
    client = get_client(server, server_module.SERVERS)
    payload = await client.query(expr, time=resolved_time, timeout=timeout_delta)

    result_type = payload.get("resultType", "")
    raw_result = payload.get("result")
    total, paged = _slice_series(
        raw_result,
        result_type=result_type,
        limit=series_limit,
        offset=series_offset,
    )
    response = {
        "server": server,
        "resultType": result_type,
        "total_series": total,
        "series_limit": series_limit,
        "series_offset": series_offset,
        "result": paged,
        "resolved": {"time": resolved_time.isoformat()},
    }
    if result_type == "vector" and total == 0:
        response["hint"] = _EMPTY_INSTANT_HINT
    return response


@mcp.tool(annotations={"title": "Range PromQL query", **_READ_ONLY})
async def query_range(
    server: ServerSlug,
    expr: PromQL,
    start: StartArgument = "now-1h",
    end: EndArgument = "now",
    step: StepArgument = None,
    timeout: TimeoutArgument = None,
    series_limit: Limit = 50,
    series_offset: Offset = 0,
) -> dict[str, Any]:
    """Run a range PromQL query and return the raw samples.

    This is the zoom-in tool. Results are big — every series times every
    step, often tens of thousands of tokens. For a first look at how a
    metric behaves over time, use ``chart_range`` instead; come back here
    with a narrow window or a tightly aggregated expression when you need
    the exact values.

    Returns the matrix result with series-level paging. Each series's
    samples pass through untouched — paging trims series count, not the
    points within a series. The ``resolved`` block reports the actual
    start / end / step the server saw. An empty result carries a ``hint``
    about why it might be empty.
    """
    start_dt, end_dt, step_dt = parse_range(
        start, end, step, target_samples=_QUERY_RANGE_TARGET_SAMPLES
    )
    timeout_delta = parse_step(timeout) if timeout is not None else None
    client = get_client(server, server_module.SERVERS)
    payload = await client.query_range(
        expr,
        start=start_dt,
        end=end_dt,
        step=step_dt,
        timeout=timeout_delta,
    )

    result_type = payload.get("resultType", "")
    raw_result = payload.get("result")
    total, paged = _slice_series(
        raw_result,
        result_type=result_type,
        limit=series_limit,
        offset=series_offset,
    )
    response = {
        "server": server,
        "resultType": result_type,
        "total_series": total,
        "series_limit": series_limit,
        "series_offset": series_offset,
        "result": paged,
        "resolved": {
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
            "step": format_duration_human(step_dt),
        },
    }
    if result_type == "matrix" and total == 0:
        response["hint"] = _EMPTY_RANGE_HINT
    return response
