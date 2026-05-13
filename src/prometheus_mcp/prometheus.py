"""Async HTTP client for the Prometheus query API.

A thin wrapper around ``httpx.AsyncClient`` that knows how to talk to
``/api/v1`` on a Prometheus server, including error translation and the
handful of GETs the MCP tools need (``labels``, ``label/<name>/values``,
``series``, ``metadata``, ``query``, ``query_range``).

Higher-level concerns — paging, time-range parsing, chart rendering —
live in other modules. This module deals only in already-resolved
``datetime`` / ``timedelta`` values.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Mapping, Sequence, cast

import httpx
from httpx._types import QueryParamTypes

from prometheus_mcp.config import ServerConfig, get_server

__all__ = [
    "PrometheusAPIError",
    "PrometheusClient",
    "PrometheusError",
    "PrometheusTransportError",
    "aclose_clients",
    "get_client",
]

DEFAULT_TIMEOUT_SECONDS = 30.0


class PrometheusError(Exception):
    """Base class for Prometheus client failures."""


class PrometheusAPIError(PrometheusError):
    """Prometheus returned ``status: "error"`` with details."""

    def __init__(self, error_type: str, error: str) -> None:
        self.error_type = error_type
        self.error = error
        super().__init__(f"{error_type}: {error}")


class PrometheusTransportError(PrometheusError):
    """The HTTP request to Prometheus failed before we got a valid body."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        self.status_code = status_code
        super().__init__(message)


class PrometheusClient:
    """Async client bound to a single Prometheus server."""

    def __init__(
        self,
        config: ServerConfig,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        base_url = config.url.rstrip("/") + "/api/v1/"
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers=dict(config.headers),
            timeout=timeout,
            transport=transport,
        )
        self.config = config

    async def __aenter__(self) -> "PrometheusClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def request(
        self,
        path: str,
        *,
        params: Mapping[str, str] | Sequence[tuple[str, str]] | None = None,
    ) -> Any:
        try:
            response = await self._client.get(
                path, params=cast(QueryParamTypes | None, params)
            )
        except httpx.HTTPError as exc:
            raise PrometheusTransportError(
                f"request to {path!r} failed: {exc}"
            ) from exc

        if response.status_code >= 400:
            raise PrometheusTransportError(
                f"{response.request.url} returned HTTP {response.status_code}",
                status_code=response.status_code,
            )

        try:
            raw = response.json()
        except ValueError as exc:
            raise PrometheusTransportError(
                f"{response.request.url} returned non-JSON body"
            ) from exc

        if not isinstance(raw, dict):
            raise PrometheusTransportError(
                f"{response.request.url} returned a {type(raw).__name__}, expected an object"
            )

        payload = cast(dict[str, Any], raw)
        status = payload.get("status")
        if status == "success":
            return payload.get("data")
        if status == "error":
            raise PrometheusAPIError(
                str(payload.get("errorType", "unknown")),
                str(payload.get("error", "")),
            )
        raise PrometheusTransportError(
            f"{response.request.url} returned unexpected status {status!r}"
        )

    async def labels(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        match: list[str] | None = None,
        limit: int | None = None,
    ) -> list[str]:
        params = _build_params(start=start, end=end, match=match, limit=limit)
        data: list[str] | None = await self.request("labels", params=params)
        return data or []

    async def label_values(
        self,
        name: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        match: list[str] | None = None,
        limit: int | None = None,
    ) -> list[str]:
        params = _build_params(start=start, end=end, match=match, limit=limit)
        data: list[str] | None = await self.request(
            f"label/{name}/values", params=params
        )
        return data or []

    async def series(
        self,
        match: list[str],
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
    ) -> list[dict[str, str]]:
        params = _build_params(start=start, end=end, match=match, limit=limit)
        data: list[dict[str, str]] | None = await self.request("series", params=params)
        return data or []

    async def metadata(
        self,
        *,
        metric: str | None = None,
        limit: int | None = None,
    ) -> dict[str, list[dict[str, str]]]:
        params: list[tuple[str, str]] = []
        if metric is not None:
            params.append(("metric", metric))
        if limit is not None:
            params.append(("limit", str(limit)))
        data: dict[str, list[dict[str, str]]] | None = await self.request(
            "metadata", params=params
        )
        return data or {}

    async def query(
        self,
        expr: str,
        *,
        time: datetime | None = None,
        timeout: timedelta | None = None,
    ) -> dict[str, Any]:
        params: list[tuple[str, str]] = [("query", expr)]
        if time is not None:
            params.append(("time", _format_time(time)))
        if timeout is not None:
            params.append(("timeout", _format_duration(timeout)))
        return await self.request("query", params=params)

    async def query_range(
        self,
        expr: str,
        *,
        start: datetime,
        end: datetime,
        step: timedelta,
        timeout: timedelta | None = None,
    ) -> dict[str, Any]:
        params: list[tuple[str, str]] = [
            ("query", expr),
            ("start", _format_time(start)),
            ("end", _format_time(end)),
            ("step", _format_duration(step)),
        ]
        if timeout is not None:
            params.append(("timeout", _format_duration(timeout)))
        return await self.request("query_range", params=params)


def _build_params(
    *,
    start: datetime | None,
    end: datetime | None,
    match: list[str] | None,
    limit: int | None,
) -> list[tuple[str, str]]:
    params: list[tuple[str, str]] = []
    if start is not None:
        params.append(("start", _format_time(start)))
    if end is not None:
        params.append(("end", _format_time(end)))
    if match:
        params.extend(("match[]", expr) for expr in match)
    if limit is not None:
        params.append(("limit", str(limit)))
    return params


def _format_time(value: datetime) -> str:
    return f"{value.timestamp():.3f}"


def _format_duration(value: timedelta) -> str:
    return f"{value.total_seconds():.3f}s"


_clients: dict[str, PrometheusClient] = {}


def get_client(
    slug: str,
    servers: Mapping[str, ServerConfig],
) -> PrometheusClient:
    """Return a cached ``PrometheusClient`` for ``slug``, creating it on demand."""
    config = get_server(slug, servers)
    client = _clients.get(config.slug)
    if client is None:
        client = PrometheusClient(config)
        _clients[config.slug] = client
    return client


async def aclose_clients() -> None:
    """Close every cached client. Bind to FastMCP shutdown."""
    while _clients:
        _, client = _clients.popitem()
        await client.aclose()
