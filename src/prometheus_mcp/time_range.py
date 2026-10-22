"""Parsing for the friendly time-range arguments the query / chart tools take.

Accepts ``"now"``, ``"now-5h"``, ``"now+30m"``, bare ``"-5h"`` / ``"+2h"``,
ISO-8601 / RFC-3339 timestamps, and Unix epoch numerics (seconds, or
milliseconds when the magnitude is too large to be seconds). Durations
support combinations like ``"1h30m"`` with ``s/m/h/d/w`` units.

Step values additionally accept bare durations (``"15s"``, ``"1m"``,
``"1h30m"``) or numeric seconds.

Everything returns timezone-aware UTC datetimes / positive ``timedelta`` s
so the Prometheus client can serialize them straight to the wire.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

__all__ = [
    "TimeRangeError",
    "event_time",
    "format_duration",
    "parse_duration",
    "parse_range",
    "parse_step",
    "parse_time",
]


def format_duration(value: timedelta) -> str:
    """Render a duration as Prometheus writes one (e.g. ``"30s"`` or ``"500ms"``).

    Whole seconds render as ``Ns``. Anything else renders as ``Nms``, because
    Prometheus's duration grammar rejects a fractional number of seconds.
    """
    seconds = value.total_seconds()
    if seconds.is_integer():
        return f"{int(seconds)}s"
    return f"{round(seconds * 1_000)}ms"


# Prometheus is written in Go, and it reports the zero value of Go's time.Time
# for an event that never happened, such as the last scrape of a new target.
_GO_ZERO_TIME = "0001-01-01T00:00:00Z"


def event_time(value: object) -> str | None:
    """The RFC-3339 time Prometheus reports for an event, or None for never."""
    if not isinstance(value, str) or not value or value == _GO_ZERO_TIME:
        return None
    return value


class TimeRangeError(ValueError):
    """Raised when a time / duration value can't be parsed."""


_DURATION_UNITS = {"s": 1, "m": 60, "h": 3_600, "d": 86_400, "w": 604_800}
_DURATION_PART = re.compile(r"(\d+(?:\.\d+)?)([smhdw])")
_RELATIVE = re.compile(r"^(?:now)?([+-])(.+)$")
_DEFAULT_STEP_SECONDS = 15.0
_TARGET_RANGE_POINTS = 360
_EPOCH_MS_THRESHOLD = 1e12


def parse_duration(value: str) -> timedelta:
    """Parse durations like ``"5m"``, ``"1h30m"``, ``"0.5s"``."""
    text = value.strip()
    if not text:
        raise TimeRangeError("empty duration")
    parts = _DURATION_PART.findall(text)
    if not parts or "".join(amount + unit for amount, unit in parts) != text:
        raise TimeRangeError(f"invalid duration: {value!r}")
    total = sum(float(amount) * _DURATION_UNITS[unit] for amount, unit in parts)
    return timedelta(seconds=total)


def parse_step(value: str | float) -> timedelta:
    """Parse a query step. Accepts numeric seconds or a duration string."""
    if isinstance(value, bool):
        raise TimeRangeError(f"invalid step: {value!r}")
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds <= 0:
            raise TimeRangeError(f"step must be positive, got {value!r}")
        return timedelta(seconds=seconds)
    text = value.strip()
    try:
        seconds = float(text)
    except ValueError:
        result = parse_duration(text)
    else:
        if seconds <= 0:
            raise TimeRangeError(f"step must be positive, got {value!r}")
        return timedelta(seconds=seconds)
    if result.total_seconds() <= 0:
        raise TimeRangeError(f"step must be positive, got {value!r}")
    return result


def parse_time(
    value: str | float,
    *,
    now: datetime | None = None,
) -> datetime:
    """Resolve a friendly time value to a timezone-aware UTC datetime."""
    if isinstance(value, bool):
        raise TimeRangeError(f"unrecognized time {value!r}")
    if isinstance(value, (int, float)):
        return _epoch_to_datetime(float(value))

    text = value.strip()
    if not text:
        raise TimeRangeError("empty time value")

    reference = now if now is not None else datetime.now(UTC)

    if text == "now":
        return reference

    match = _RELATIVE.match(text)
    if match:
        sign = -1 if match.group(1) == "-" else 1
        try:
            delta = parse_duration(match.group(2))
        except TimeRangeError as exc:
            raise TimeRangeError(f"invalid relative time {value!r}: {exc}") from exc
        return reference + sign * delta

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        pass
    else:
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    try:
        return _epoch_to_datetime(float(text))
    except ValueError as exc:
        raise TimeRangeError(f"unrecognized time {value!r}") from exc


def parse_range(
    start: str | float | None = None,
    end: str | float | None = None,
    step: str | float | None = None,
    *,
    now: datetime | None = None,
    target_samples: int = _TARGET_RANGE_POINTS,
) -> tuple[datetime, datetime, timedelta]:
    """Resolve ``start`` / ``end`` / ``step`` for a range query.

    ``end`` defaults to ``"now"`` and ``start`` to ``"now-1h"``. When
    ``step`` is unset, it is picked so that the range yields roughly
    ``target_samples`` samples with a 15s floor.
    """
    reference = now if now is not None else datetime.now(UTC)
    end_dt = parse_time(end if end is not None else "now", now=reference)
    start_dt = parse_time(start if start is not None else "now-1h", now=reference)
    if start_dt >= end_dt:
        raise TimeRangeError(
            f"start {start_dt.isoformat()} must be before end {end_dt.isoformat()}"
        )

    if step is None:
        duration_seconds = (end_dt - start_dt).total_seconds()
        seconds = max(_DEFAULT_STEP_SECONDS, duration_seconds / target_samples)
        step_dt = timedelta(seconds=seconds)
    else:
        step_dt = parse_step(step)

    return start_dt, end_dt, step_dt


def _epoch_to_datetime(value: float) -> datetime:
    seconds = value / 1_000 if abs(value) > _EPOCH_MS_THRESHOLD else value
    return datetime.fromtimestamp(seconds, tz=UTC)
