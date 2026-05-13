from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from prometheus_mcp.chart import ChartSeries, format_series_label, render_chart


@pytest.fixture
def window() -> tuple[datetime, datetime]:
    start = datetime(2026, 5, 13, 12, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(minutes=10)
    return start, end


def _evenly_sampled(
    start: datetime,
    count: int,
    values: list[float | None],
) -> list[tuple[float, float | None]]:
    assert len(values) == count
    step = 600 / max(1, count - 1)
    return [(start.timestamp() + i * step, values[i]) for i in range(count)]


def _sine_points(start: datetime, count: int) -> list[tuple[float, float | None]]:
    values: list[float | None] = [10.0 + 5.0 * math.sin(i / 6) for i in range(count)]
    return _evenly_sampled(start, count, values)


def _ramp_points(
    start: datetime, count: int, *, reverse: bool = False
) -> list[tuple[float, float | None]]:
    values: list[float | None] = [
        float((count - 1 - i) if reverse else i) for i in range(count)
    ]
    return _evenly_sampled(start, count, values)


def test_format_series_label_name_only() -> None:
    assert format_series_label({"__name__": "up"}) == "up"


def test_format_series_label_with_labels() -> None:
    label = format_series_label({"__name__": "up", "job": "api", "instance": "a:8080"})
    assert label == 'up{instance="a:8080", job="api"}'


def test_format_series_label_without_name() -> None:
    assert format_series_label({"job": "api"}) == '{job="api"}'


def test_format_series_label_empty() -> None:
    assert format_series_label({}) == "{}"


def test_render_chart_empty_series(window: tuple[datetime, datetime]) -> None:
    start, end = window
    assert render_chart([], start=start, end=end) == "(no data)"


def test_render_chart_single_series_returns_text(
    window: tuple[datetime, datetime],
) -> None:
    start, end = window
    series = ChartSeries(label='up{job="api"}', points=_sine_points(start, 60))
    chart = render_chart([series], start=start, end=end, width=60, height=10)
    assert 'up{job="api"}' in chart
    assert start.isoformat(timespec="seconds") in chart
    assert end.isoformat(timespec="seconds") in chart
    assert "┤" in chart or "┼" in chart


def test_render_chart_multi_series_has_numbered_legend(
    window: tuple[datetime, datetime],
) -> None:
    start, end = window
    one = ChartSeries(label="a", points=_ramp_points(start, 30))
    two = ChartSeries(label="b", points=_ramp_points(start, 30, reverse=True))
    chart = render_chart([one, two], start=start, end=end, width=40, height=8)
    assert "[1] a" in chart
    assert "[2] b" in chart


def test_render_chart_handles_all_none_values(
    window: tuple[datetime, datetime],
) -> None:
    start, end = window
    series = ChartSeries(label="empty", points=_evenly_sampled(start, 5, [None] * 5))
    chart = render_chart([series], start=start, end=end, width=40, height=6)
    assert "empty" in chart


def test_render_chart_handles_gaps_between_valid_points(
    window: tuple[datetime, datetime],
) -> None:
    start, end = window
    series = ChartSeries(
        label="patchy",
        points=[
            (start.timestamp(), 1.0),
            (start.timestamp() + 60, None),
            (start.timestamp() + 120, 2.0),
            (end.timestamp(), 3.0),
        ],
    )
    chart = render_chart([series], start=start, end=end, width=40, height=6)
    assert "patchy" in chart


def test_render_chart_label_precision_under_one(
    window: tuple[datetime, datetime],
) -> None:
    start, end = window
    values: list[float | None] = [0.001 * i for i in range(20)]
    series = ChartSeries(label="tiny", points=_evenly_sampled(start, 20, values))
    chart = render_chart([series], start=start, end=end, width=40, height=8)
    assert "0.0" in chart


def test_render_chart_label_precision_for_large_values(
    window: tuple[datetime, datetime],
) -> None:
    start, end = window
    values: list[float | None] = [10_000.0 + i * 100 for i in range(20)]
    series = ChartSeries(label="big", points=_evenly_sampled(start, 20, values))
    chart = render_chart([series], start=start, end=end, width=40, height=8)
    assert "10,000" in chart or "10000" in chart


def test_render_chart_handles_duplicate_timestamps(
    window: tuple[datetime, datetime],
) -> None:
    start, end = window
    ts = start.timestamp() + 60
    series = ChartSeries(
        label="dups",
        points=[
            (start.timestamp(), 1.0),
            (ts, 5.0),
            (ts, 10.0),
            (end.timestamp(), 20.0),
        ],
    )
    chart = render_chart([series], start=start, end=end, width=40, height=6)
    assert "dups" in chart


def test_render_chart_label_precision_for_zero_values(
    window: tuple[datetime, datetime],
) -> None:
    start, end = window
    series = ChartSeries(label="zeros", points=_evenly_sampled(start, 10, [0.0] * 10))
    chart = render_chart([series], start=start, end=end, width=40, height=6)
    assert "0.00" in chart
