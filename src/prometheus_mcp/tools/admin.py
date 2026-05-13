"""Admin / introspection tools — useful for an agent figuring out where it is."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from prometheus_mcp import server as server_module
from prometheus_mcp.server import mcp


@mcp.tool(
    annotations={
        "title": "List Prometheus servers",
        "readOnlyHint": True,
        "idempotentHint": True,
    }
)
async def list_servers() -> Annotated[
    list[dict[str, str]],
    Field(
        description=(
            "Every Prometheus backend this MCP server is configured to talk to."
        )
    ),
]:
    """List the Prometheus servers available to this MCP server.

    Each entry has a ``slug`` (the value to pass as ``server`` on every other
    tool) and a ``url`` (the Prometheus base URL). Authentication headers are
    not returned.
    """
    return [
        {"slug": cfg.slug, "url": cfg.url} for cfg in server_module.SERVERS.values()
    ]
