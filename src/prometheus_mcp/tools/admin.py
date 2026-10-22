"""Admin and introspection tools, for an agent finding out where it is."""

from __future__ import annotations

from collections.abc import Callable

from fastmcp.dependencies import Shared
from fastmcp.tools import tool
from pydantic import BaseModel, Field

from prometheus_mcp.prometheus import PrometheusServers, prometheus_servers
from prometheus_mcp.tools.effects import READ, READS


class PrometheusServer(BaseModel):
    """One Prometheus server this MCP server can query."""

    slug: str = Field(description="The value to pass as `server` to other tools.")
    url: str = Field(
        description="The Prometheus base URL, without any user name or password."
    )


@tool(title="List Prometheus servers", tags={"servers", READ}, annotations=READS)
async def list_servers(
    servers: PrometheusServers = Shared(prometheus_servers),
) -> list[PrometheusServer]:
    """List the Prometheus servers available to this MCP server.

    Each entry has a ``slug`` (the value to pass as ``server`` on every other
    tool) and a ``url`` (the Prometheus base URL). Authentication headers and
    credentials in the URL are not returned.
    """
    return [
        PrometheusServer(slug=config.slug, url=config.public_url)
        for config in servers.configs.values()
    ]


TOOLS: list[Callable[..., object]] = [list_servers]
