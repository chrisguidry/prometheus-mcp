"""ASCII chart rendering for range query results.

Pure rendering — no Prometheus or MCP imports — so the renderer can be
exercised with hand-built data in tests. The tool layer adapts a range
query response into ``ChartSeries`` and calls :func:`render_chart`.

Multiple series render as stacked per-series panels that share an
x-axis, each autoscaled to its own value range (an ``overlay`` mode
draws them into one shared plot instead). Each series' header line
reports min / avg / max / last, plus how much of the window had no data
when gap detection is on. The y-axis labels come from ``asciichartpy``'s
auto-scaling, and a short caption below the chart shows ``start``,
midpoint, and ``end`` timestamps so a reader has anchors for the x-axis.
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
    title: str | None = None,
    max_gap_seconds: float | None = None,
    overlay: bool = False,
) -> str:
    """Render one or more series as an ASCII chart with a caption.

    Multiple series render as stacked per-series panels, each autoscaled
    to its own range so every series keeps its shape and its identity;
    ``height`` is the total plot-row budget split across the panels. Pass
    ``overlay=True`` to draw every series into one shared plot instead.

    When ``max_gap_seconds`` is set, stretches between consecutive samples
    further apart than that render as blank space instead of an
    interpolated line, and the legend reports how much of the window had
    no data.
    """
    if not series:
        return "(no data)"

    resampled = [
        _resample(
            s.points,
            start=start,
            end=end,
            width=width,
            max_gap_seconds=max_gap_seconds,
        )
        for s in series
    ]
    column_seconds = (end - start).total_seconds() / max(1, width - 1)
    edge_tolerance = (
        math.ceil(max_gap_seconds / column_seconds) if max_gap_seconds else 0
    )
    legends = _legend(
        series, resampled, edge_tolerance, numbered=overlay and len(series) > 1
    )

    parts: list[str] = []
    if title:
        parts.append(title)
    if len(series) == 1 or overlay:
        config: dict[str, Any] = {
            "height": height,
            "format": _label_format_string(resampled),
        }
        plot_input: Any = resampled[0] if len(resampled) == 1 else resampled
        has_data = any(
            not math.isnan(value) for columns in resampled for value in columns
        )
        parts.extend(legends)
        parts.append(asciichartpy.plot(plot_input, config) if has_data else "")
    else:
        panel_height = max(4, height // len(series))
        for legend_line, columns in zip(legends, resampled):
            config = {
                "height": panel_height,
                "format": _label_format_string([columns]),
            }
            parts.append(legend_line)
            parts.append(asciichartpy.plot(columns, config))
    parts.append(_render_x_caption(start, end, width))
    return "\n".join(parts)


def _legend(
    series: list[ChartSeries],
    resampled: list[list[float]],
    edge_tolerance: int,
    *,
    numbered: bool,
) -> list[str]:
    stats = [_series_stats(s.points) for s in series]
    finite = [value for stat in stats if stat is not None for value in stat]
    precision = _label_precision(finite)
    lines: list[str] = []
    for index, (entry, stat, columns) in enumerate(zip(series, stats, resampled)):
        prefix = f"[{index + 1}] " if numbered else ""
        missing = _missing_columns(columns, edge_tolerance)
        lines.append(
            f"{prefix}{entry.label} — {_describe(stat, missing, len(columns), precision)}"
        )
    return lines


def _missing_columns(columns: list[float], edge_tolerance: int) -> int:
    """Count gap columns, forgiving short runs at either edge.

    A brief empty stretch at the window's edges is scrape lag or window
    alignment, not absent data; runs longer than ``edge_tolerance``
    columns are real gaps and stay counted.
    """
    missing = sum(1 for value in columns if math.isnan(value))
    if not missing or missing == len(columns):
        return missing
    leading = next(i for i, value in enumerate(columns) if not math.isnan(value))
    trailing = next(
        i for i, value in enumerate(reversed(columns)) if not math.isnan(value)
    )
    if leading <= edge_tolerance:
        missing -= leading
    if trailing <= edge_tolerance:
        missing -= trailing
    return missing


def _series_stats(
    points: list[tuple[float, float | None]],
) -> tuple[float, float, float, float] | None:
    valid = sorted((ts, value) for ts, value in points if value is not None)
    if not valid:
        return None
    values = [value for _, value in valid if value is not None]
    return (min(values), sum(values) / len(values), max(values), values[-1])


def _describe(
    stat: tuple[float, float, float, float] | None,
    missing: int,
    total: int,
    precision: int,
) -> str:
    if stat is None:
        return "no data"
    minimum, mean, maximum, last = stat
    text = (
        f"min {minimum:,.{precision}f}, avg {mean:,.{precision}f}, "
        f"max {maximum:,.{precision}f}, last {last:,.{precision}f}"
    )
    if missing:
        percent = max(1, round(100 * missing / total))
        text += f", no data {percent}% of window"
    return text


def _resample(
    points: list[tuple[float, float | None]],
    *,
    start: datetime,
    end: datetime,
    width: int,
    max_gap_seconds: float | None = None,
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
        _interpolate(
            start_ts + (end_ts - start_ts) * col / span,
            times,
            values,
            max_gap_seconds,
        )
        for col in range(width)
    ]


def _interpolate(
    target: float,
    times: list[float],
    values: list[float],
    max_gap_seconds: float | None,
) -> float:
    if target < times[0] or target > times[-1]:
        return math.nan
    idx = bisect.bisect_left(times, target)
    if idx == 0 or times[idx] == target:
        return values[idx]
    t0, t1 = times[idx - 1], times[idx]
    if max_gap_seconds is not None and t1 - t0 > max_gap_seconds:
        return math.nan
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


_X_CAPTION_INDENT = 11
_MIN_GAP = 2


def _render_x_caption(start: datetime, end: datetime, width: int) -> str:
    left = start.isoformat(timespec="seconds")
    right = end.isoformat(timespec="seconds")
    middle = (start + (end - start) / 2).isoformat(timespec="seconds")
    if width >= len(left) + len(middle) + len(right) + 2 * _MIN_GAP:
        inner_width = width - len(left) - len(right)
        middle_pos = (inner_width - len(middle)) // 2
        return (
            " " * _X_CAPTION_INDENT
            + left
            + " " * middle_pos
            + middle
            + " " * (inner_width - middle_pos - len(middle))
            + right
        )
    gap = max(_MIN_GAP, width - len(left) - len(right))
    return " " * _X_CAPTION_INDENT + left + " " * gap + right
