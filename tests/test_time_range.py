from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from prometheus_mcp.time_range import (
    TimeRangeError,
    format_duration_human,
    parse_duration,
    parse_range,
    parse_step,
    parse_time,
)


def test_format_duration_human_whole_seconds() -> None:
    assert format_duration_human(timedelta(seconds=30)) == "30s"


def test_format_duration_human_fractional_seconds_renders_as_ms() -> None:
    assert format_duration_human(timedelta(milliseconds=500)) == "500ms"


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 5, 13, 12, 0, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "text,expected_seconds",
    [
        ("5s", 5),
        ("30m", 30 * 60),
        ("2h", 2 * 3_600),
        ("1d", 86_400),
        ("1w", 604_800),
        ("1h30m", 5_400),
        ("0.5s", 0.5),
        ("90s", 90),
    ],
)
def test_parse_duration_happy_path(text: str, expected_seconds: float) -> None:
    assert parse_duration(text).total_seconds() == expected_seconds


@pytest.mark.parametrize("bad", ["", "5", "h", "5x", "1h 30m", "five-minutes"])
def test_parse_duration_rejects_bad(bad: str) -> None:
    with pytest.raises(TimeRangeError):
        parse_duration(bad)


def test_parse_time_now(now: datetime) -> None:
    assert parse_time("now", now=now) == now


@pytest.mark.parametrize(
    "text,delta",
    [
        ("now-5h", timedelta(hours=-5)),
        ("now+30m", timedelta(minutes=30)),
        ("-5h", timedelta(hours=-5)),
        ("+2h", timedelta(hours=2)),
        ("-90s", timedelta(seconds=-90)),
        ("now-1h30m", timedelta(hours=-1, minutes=-30)),
    ],
)
def test_parse_time_relative(text: str, delta: timedelta, now: datetime) -> None:
    assert parse_time(text, now=now) == now + delta


def test_parse_time_iso_with_offset() -> None:
    parsed = parse_time("2026-05-13T07:00:00-05:00")
    assert parsed == datetime(2026, 5, 13, 12, 0, 0, tzinfo=timezone.utc)


def test_parse_time_iso_without_offset_assumed_utc() -> None:
    parsed = parse_time("2026-05-13T12:00:00")
    assert parsed == datetime(2026, 5, 13, 12, 0, 0, tzinfo=timezone.utc)


def test_parse_time_iso_with_z_suffix() -> None:
    parsed = parse_time("2026-05-13T12:00:00Z")
    assert parsed == datetime(2026, 5, 13, 12, 0, 0, tzinfo=timezone.utc)


def test_parse_time_unix_epoch_seconds_int() -> None:
    parsed = parse_time(1_780_000_000)
    assert parsed.year == 2026


def test_parse_time_unix_epoch_seconds_string() -> None:
    assert parse_time("1780000000") == datetime.fromtimestamp(
        1_780_000_000, tz=timezone.utc
    )


def test_parse_time_unix_epoch_ms_auto_detected() -> None:
    ms = 1_780_000_000_000
    parsed = parse_time(ms)
    assert parsed == datetime.fromtimestamp(ms / 1_000, tz=timezone.utc)


def test_parse_time_rejects_bool() -> None:
    with pytest.raises(TimeRangeError):
        parse_time(True)


def test_parse_time_rejects_empty() -> None:
    with pytest.raises(TimeRangeError):
        parse_time("   ")


def test_parse_time_rejects_garbage() -> None:
    with pytest.raises(TimeRangeError, match="unrecognized"):
        parse_time("yesterday")


def test_parse_time_rejects_bad_relative(now: datetime) -> None:
    with pytest.raises(TimeRangeError, match="invalid relative"):
        parse_time("-fortnight", now=now)


def test_parse_time_defaults_now_when_unset() -> None:
    before = datetime.now(timezone.utc)
    parsed = parse_time("now")
    after = datetime.now(timezone.utc)
    assert before <= parsed <= after


@pytest.mark.parametrize(
    "value,seconds",
    [
        ("15s", 15),
        ("1m", 60),
        ("1h30m", 5_400),
        (30, 30),
        (30.5, 30.5),
        ("30", 30),
    ],
)
def test_parse_step_accepts_durations_and_numerics(
    value: str | int | float, seconds: float
) -> None:
    assert parse_step(value).total_seconds() == seconds


@pytest.mark.parametrize("bad", [0, -1, "0", "-5", "0s", "-15s", True])
def test_parse_step_rejects_nonpositive(bad: object) -> None:
    with pytest.raises(TimeRangeError):
        parse_step(bad)  # type: ignore[arg-type]


def test_parse_range_defaults(now: datetime) -> None:
    start, end, step = parse_range(now=now)
    assert end == now
    assert start == now - timedelta(hours=1)
    assert step == timedelta(seconds=15)


def test_parse_range_picks_step_for_long_window(now: datetime) -> None:
    start, end, step = parse_range(start="now-24h", end="now", now=now)
    assert end == now
    assert start == now - timedelta(hours=24)
    assert step.total_seconds() == 24 * 3600 / 360


def test_parse_range_honors_target_samples(now: datetime) -> None:
    _, _, step = parse_range(start="now-1h", end="now", now=now, target_samples=100)
    assert step == timedelta(seconds=36)


def test_parse_range_target_samples_keeps_step_floor(now: datetime) -> None:
    _, _, step = parse_range(start="now-5m", end="now", now=now, target_samples=100)
    assert step == timedelta(seconds=15)


def test_parse_range_respects_explicit_step(now: datetime) -> None:
    _, _, step = parse_range(start="now-1h", step="5m", now=now)
    assert step == timedelta(minutes=5)


def test_parse_range_rejects_inverted(now: datetime) -> None:
    with pytest.raises(TimeRangeError, match="must be before"):
        parse_range(start="now", end="now-1h", now=now)
