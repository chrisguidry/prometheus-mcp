"""The FastMCP application instance for prometheus-mcp."""

from __future__ import annotations

from fastmcp import FastMCP

mcp: FastMCP[None] = FastMCP("prometheus-mcp")
