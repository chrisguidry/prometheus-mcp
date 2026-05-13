"""MCP tool implementations for prometheus-mcp.

Importing this package registers every tool on ``prometheus_mcp.server.mcp``.
"""

from prometheus_mcp.tools import admin, chart, discovery, query

__all__ = ["admin", "chart", "discovery", "query"]
