"""Async HTTP client for the Prometheus query API.

A thin wrapper around ``httpx.AsyncClient`` that knows how to talk to
``/api/v1`` on a Prometheus server, including error translation and the
handful of GETs the MCP tools need (``labels``, ``label/<name>/values``,
``series``, ``metadata``, ``query``, ``query_range``, ``targets``,
``alerts``, ``rules``, ``status/buildinfo``).

Higher-level concerns, such as paging, time-range parsing, and chart
rendering, live in other modules. This module deals only in
already-resolved ``datetime`` / ``timedelta`` values.

Error messages name the server by its slug and the API path, never by its
URL, because the URL can hold a user name and password.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any, Self, cast
from urllib.parse import quote

import httpx
from httpx._types import QueryParamTypes

from prometheus_mcp.config import ServerConfig, get_server, load_servers
from prometheus_mcp.time_range import format_duration

__all__ = [
    "PrometheusAPIError",
    "PrometheusClient",
    "PrometheusError",
    "PrometheusServers",
    "PrometheusTransportError",
    "prometheus_servers",
]

DEFAULT_TIMEOUT_SECONDS = 30.0


class PrometheusError(Exception):
    """Base class for Prometheus client failures."""


class PrometheusAPIError(PrometheusError):
    """Prometheus returned ``status: "error"`` with details."""

    def __init__(self, error_type: str, error: str) -> None:
        self.error_type = error_type
        self.error = error
        super().__init__(f"Prometheus returned {error_type}: {error}")


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
        self.timeout = timeout
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers=dict(config.headers),
            timeout=timeout,
            transport=transport,
        )
        self.config = config

    async def __aenter__(self) -> Self:
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
        timeout: float | None = None,
    ) -> Any:
        """GET ``path`` and return the ``data`` of a successful response.

        ``timeout`` is in seconds and replaces the client's timeout for this
        request.
        """
        server = f"Prometheus server {self.config.slug!r}"
        seconds = self.timeout if timeout is None else timeout
        try:
            response = await self._client.get(
                path, params=cast(QueryParamTypes | None, params), timeout=seconds
            )
        except httpx.TimeoutException as exc:
            raise PrometheusTransportError(
                f"{server} did not answer {path} within {seconds:g} seconds"
            ) from exc
        except httpx.HTTPError as exc:
            raise PrometheusTransportError(
                f"request to {server} for {path} failed: {exc}"
            ) from exc

        if response.status_code >= 400:
            detail = _try_extract_error_detail(response)
            raise PrometheusTransportError(
                f"{server} answered {path} with HTTP {response.status_code}{detail}",
                status_code=response.status_code,
            )

        try:
            raw = response.json()
        except ValueError as exc:
            raise PrometheusTransportError(
                f"{server} returned a non-JSON body for {path}"
            ) from exc

        if not isinstance(raw, dict):
            raise PrometheusTransportError(
                f"{server} returned a {type(raw).__name__} for {path}, "
                "expected an object"
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
            f"{server} returned unexpected status {status!r} for {path}"
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
        # Quote the name so that a "/" or ".." in it cannot reach another path.
        data: list[str] | None = await self.request(
            f"label/{quote(name, safe='')}/values", params=params
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
            params.append(("timeout", format_duration(timeout)))
        return await self.request(
            "query", params=params, timeout=self._query_wait(timeout)
        )

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
            ("step", _format_step(step)),
        ]
        if timeout is not None:
            params.append(("timeout", format_duration(timeout)))
        return await self.request(
            "query_range", params=params, timeout=self._query_wait(timeout)
        )

    async def targets(self, *, state: str) -> dict[str, Any]:
        """``state`` is ``active``, ``dropped``, or ``any``."""
        data: dict[str, Any] | None = await self.request(
            "targets", params=[("state", state)]
        )
        return data or {}

    async def alerts(self) -> list[dict[str, Any]]:
        data: dict[str, Any] | None = await self.request("alerts")
        return (data or {}).get("alerts") or []

    async def rules(
        self, *, rule_type: str | None = None, group: str | None = None
    ) -> list[dict[str, Any]]:
        """Return the rule groups. ``rule_type`` is ``alert`` or ``record``."""
        params: list[tuple[str, str]] = []
        if rule_type is not None:
            params.append(("type", rule_type))
        if group is not None:
            params.append(("rule_group[]", group))
        data: dict[str, Any] | None = await self.request("rules", params=params)
        return (data or {}).get("groups") or []

    async def build_info(self, *, timeout: float | None = None) -> Any:
        """Return the build properties, such as ``version``, the server reports.

        ``timeout`` is in seconds and replaces the client's timeout.
        """
        return await self.request("status/buildinfo", timeout=timeout)

    def _query_wait(self, timeout: timedelta | None) -> float | None:
        """How long to wait for a query that Prometheus may run for ``timeout``.

        Without this, the client's own timeout would end a long query before
        Prometheus does, and the caller would never see Prometheus's answer.
        """
        if timeout is None:
            return None
        return self.timeout + timeout.total_seconds()


def _try_extract_error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        text = response.text.strip()
        return f": {text[:200]}" if text else ""
    if isinstance(body, dict):
        payload = cast(dict[str, Any], body)
        error = payload.get("error") or payload.get("message")
        if isinstance(error, str) and error:
            error_type = payload.get("errorType")
            prefix = f" ({error_type})" if isinstance(error_type, str) else ""
            return f"{prefix}: {error}"
    return ""


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


def _format_step(value: timedelta) -> str:
    """Prometheus accepts numeric seconds (int or float) for ``step``."""
    seconds = value.total_seconds()
    return str(int(seconds)) if seconds.is_integer() else f"{seconds:g}"


class PrometheusServers:
    """The configured Prometheus servers, with one client for each server in use."""

    def __init__(self, configs: Mapping[str, ServerConfig]) -> None:
        self.configs = configs
        self._clients: dict[str, PrometheusClient] = {}

    def client(self, slug: str) -> PrometheusClient:
        """Return the client for ``slug``, and open it on first use.

        Raises ``UnknownServerError`` when no server has that slug.
        """
        config = get_server(slug, self.configs)
        client = self._clients.get(config.slug)
        if client is None:
            client = PrometheusClient(config)
            self._clients[config.slug] = client
        return client

    async def aclose(self) -> None:
        while self._clients:
            _, client = self._clients.popitem()
            await client.aclose()


@asynccontextmanager
async def prometheus_servers() -> AsyncGenerator[PrometheusServers]:
    """Open the servers configured in the environment, and close their clients.

    Tools receive this through FastMCP's ``Shared`` dependency, so the MCP
    server keeps one connection pool per Prometheus server for its whole life.
    """
    servers = PrometheusServers(load_servers())
    try:
        yield servers
    finally:
        await servers.aclose()
