"""The FastMCP application instance for prometheus-mcp.

Loads the configured Prometheus servers from the environment, builds the
``FastMCP`` app, and registers every tool by importing the ``tools``
package for its side effects.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastmcp import FastMCP

from prometheus_mcp.config import ServerConfig, load_servers
from prometheus_mcp.prometheus import aclose_clients

SERVERS: dict[str, ServerConfig] = load_servers()


@asynccontextmanager
async def lifespan(_: FastMCP[None]) -> AsyncIterator[None]:
    try:
        yield
    finally:
        await aclose_clients()


INSTRUCTIONS = """\
Tools for exploring the metrics in one or more Prometheus servers.

The intended workflow:

1. Discover: `list_servers` names the configured servers; `list_metrics`,
   `list_labels`, `label_values`, `list_series`, and `metric_metadata`
   map what a server contains.
2. Look at the shape: `chart_range` is the first tool to reach for on any
   question about a metric over time. It renders a range query as an
   ASCII chart with per-series min / avg / max / last — a few hundred
   tokens instead of the tens of thousands that raw samples cost.
3. Zoom in: `query` and `query_range` return exact values. Use them on
   narrow windows or aggregated expressions once a chart has shown where
   to look.
"""

mcp: FastMCP[None] = FastMCP(
    "prometheus-mcp",
    instructions=INSTRUCTIONS,
    lifespan=lifespan,
)


from prometheus_mcp import tools  # noqa: E402, F401  -- imported for side effects
