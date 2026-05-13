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


mcp: FastMCP[None] = FastMCP("prometheus-mcp", lifespan=lifespan)


from prometheus_mcp import tools  # noqa: E402, F401  -- imported for side effects
