"""ASCII chart rendering for range query results.

Pure rendering — no Prometheus or MCP imports — so the renderer can be
exercised with hand-built data in tests. The tool layer adapts a range
query response into ``ChartSeries`` and calls :func:`render_chart`.

Multiple series are stacked into one chart with a numbered legend; the
y-axis labels come from ``asciichartpy``'s auto-scaling, and a short
caption below the chart shows ``start``, midpoint, and ``end``
timestamps so a reader has anchors for the x-axis.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import asciichartpy

__all__ = [
    "ChartSeries",
    "format_series_label",
    "render_chart",
]


@dataclass(frozen=True)
class ChartSeries:
    """One labelled time series for the renderer.

    ``points`` is a list of ``(unix_timestamp, value)`` pairs. Use
    ``None`` for the value to mark a gap; the renderer will leave a
    visible break in the curve.
    """

    label: str
    points: list[tuple[float, float | None]]


def format_series_label(metric: dict[str, str]) -> str:
    """Render a Prometheus metric dict as a PromQL-style series label."""
    name = metric.get("__name__", "")
    rest = sorted((k, v) for k, v in metric.items() if k != "__name__")
    if not rest:
        return name or "{}"
    body = ", ".join(f'{k}="{v}"' for k, v in rest)
    return f"{name}{{{body}}}" if name else f"{{{body}}}"


def render_chart(
    series: list[ChartSeries],
    *,
    start: datetime,
    end: datetime,
    width: int = 80,
    height: int = 18,
) -> str:
    """Render one or more series as a single ASCII chart with a caption."""
    if not series:
        return "(no data)"

    resampled = [_resample(s.points, start=start, end=end, width=width) for s in series]
    config: dict[str, Any] = {
        "height": height,
        "format": _label_format_string(resampled),
    }
    plot_input: Any = resampled[0] if len(resampled) == 1 else resampled
    chart_str: str = asciichartpy.plot(plot_input, config)

    parts: list[str] = []
    if len(series) > 1:
        parts.extend(f"[{i + 1}] {s.label}" for i, s in enumerate(series))
    else:
        parts.append(series[0].label)
    parts.append(chart_str)
    parts.append(_render_x_caption(start, end, width))
    return "\n".join(parts)


def _resample(
    points: list[tuple[float, float | None]],
    *,
    start: datetime,
    end: datetime,
    width: int,
) -> list[float]:
    start_ts = start.timestamp()
    end_ts = end.timestamp()
    valid = sorted((ts, value) for ts, value in points if value is not None)
    if not valid:
        return [math.nan] * width

    times = [t for t, _ in valid]
    values = [v for _, v in valid if v is not None]
    span = max(1, width - 1)
    return [
        _interpolate(start_ts + (end_ts - start_ts) * col / span, times, values)
        for col in range(width)
    ]


def _interpolate(target: float, times: list[float], values: list[float]) -> float:
    if target < times[0] or target > times[-1]:
        return math.nan
    idx = bisect.bisect_left(times, target)
    if idx == 0 or times[idx] == target:
        return values[idx]
    t0, t1 = times[idx - 1], times[idx]
    v0, v1 = values[idx - 1], values[idx]
    return v0 + (v1 - v0) * (target - t0) / (t1 - t0)


def _label_format_string(resampled: list[list[float]]) -> str:
    finite = [v for series in resampled for v in series if not math.isnan(v)]
    return f"{{:>10,.{_label_precision(finite)}f}} "


def _label_precision(values: list[float]) -> int:
    """Pick decimal precision so labels distinguish each y-axis row.

    Considers both magnitude (large numbers need fewer decimals) and
    spread (tightly-clustered values need more decimals to differentiate).
    """
    if not values:
        return 2
    spread = max(values) - min(values)
    magnitude = max(abs(v) for v in values)
    if spread > 0:
        spread_precision = max(0, 2 - math.floor(math.log10(spread)))
    else:
        spread_precision = 0
    if magnitude < 1:
        magnitude_precision = 4
    elif magnitude < 100:
        magnitude_precision = 2
    else:
        magnitude_precision = 0
    return min(8, max(magnitude_precision, spread_precision))


def _render_x_caption(start: datetime, end: datetime, width: int) -> str:
    midpoint = start + (end - start) / 2
    labels = [
        start.isoformat(timespec="seconds"),
        midpoint.isoformat(timespec="seconds"),
        end.isoformat(timespec="seconds"),
    ]
    return _three_column_caption(labels, width=width)


def _three_column_caption(labels: list[str], *, width: int) -> str:
    left, middle, right = labels
    pad_left = " " * 11
    inner_width = max(0, width - len(left) - len(right))
    middle_pos = max(0, (inner_width - len(middle)) // 2)
    spaces_after_left = " " * middle_pos
    spaces_after_middle = " " * max(0, inner_width - middle_pos - len(middle))
    return f"{pad_left}{left}{spaces_after_left}{middle}{spaces_after_middle}{right}"
