"""Admin and introspection tools, for an agent finding out where it is."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any, cast

from fastmcp.dependencies import Shared
from fastmcp.tools import tool
from pydantic import BaseModel, Field

from prometheus_mcp.config import ServerConfig
from prometheus_mcp.prometheus import (
    PrometheusError,
    PrometheusServers,
    prometheus_servers,
)
from prometheus_mcp.tools.effects import READ, READS

logger: logging.Logger = logging.getLogger(__name__)

# list_servers asks every server for its version at once, so the slowest
# server sets how long the listing takes. A short timeout keeps one server
# that does not answer from holding up the list of all of them.
VERSION_TIMEOUT_SECONDS = 5.0


class PrometheusServer(BaseModel):
    """One Prometheus server this MCP server can query."""

    slug: str = Field(description="The value to pass as `server` to other tools.")
    url: str = Field(
        description="The Prometheus base URL, without any user name or password."
    )
    version: str | None = Field(
        description=(
            "The version the server reports, such as '3.5.0'. Null when the "
            "server did not report one before its "
            f"{VERSION_TIMEOUT_SECONDS:g}-second timeout."
        )
    )


async def _version(servers: PrometheusServers, config: ServerConfig) -> str | None:
    try:
        info = await servers.client(config.slug).build_info(
            timeout=VERSION_TIMEOUT_SECONDS
        )
    except PrometheusError as error:
        logger.info("No version from Prometheus server %r: %s", config.slug, error)
        return None
    if not isinstance(info, dict):
        return None
    version = cast(dict[str, Any], info).get("version")
    return version if isinstance(version, str) and version else None


@tool(title="List Prometheus servers", tags={"servers", READ}, annotations=READS)
async def list_servers(
    servers: PrometheusServers = Shared(prometheus_servers),
) -> list[PrometheusServer]:
    """List the Prometheus servers available to this MCP server.

    Each entry has a ``slug`` (the value to pass as ``server`` on every other
    tool), a ``url`` (the Prometheus base URL), and the ``version`` the server
    reports from ``/api/v1/status/buildinfo``. Authentication headers and
    credentials in the URL are not returned. A server that does not answer
    still appears, with a null ``version``.
    """
    configs = list(servers.configs.values())
    versions = await asyncio.gather(*(_version(servers, c) for c in configs))
    return [
        PrometheusServer(slug=config.slug, url=config.public_url, version=version)
        for config, version in zip(configs, versions)
    ]


TOOLS: list[Callable[..., object]] = [list_servers]
