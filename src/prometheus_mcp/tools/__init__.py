"""MCP tool implementations for prometheus-mcp.

Importing this package registers every tool on ``prometheus_mcp.server.mcp``.
"""

from prometheus_mcp.tools import admin, discovery

__all__ = ["admin", "discovery"]
