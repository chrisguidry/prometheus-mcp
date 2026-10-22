"""What each tool does, declared the same way for every tool.

MCP clients read tool annotations to decide when to ask the user before a
call, and a client can run a read without asking. Every tool also carries a
``read`` tag, so that a client or a transform can select tools by effect.

Every tool only reads, through Prometheus's query API, from the Prometheus
servers the operator configured. None of them reaches any other system, so
none of them is "open world" in the MCP sense.
"""

from __future__ import annotations

from mcp.types import ToolAnnotations

READ = "read"

READS = ToolAnnotations(read_only_hint=True, open_world_hint=False)
