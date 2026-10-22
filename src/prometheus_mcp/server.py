"""The FastMCP application instance for prometheus-mcp.

Builds the ``FastMCP`` app from every domain's tools. The tools receive the
configured Prometheus servers through FastMCP's ``Shared`` dependency.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastmcp import FastMCP

from prometheus_mcp.config import load_servers
from prometheus_mcp.errors import PrometheusErrorMiddleware
from prometheus_mcp.tools import admin, chart, discovery, query, rules, targets


@asynccontextmanager
async def lifespan(_: FastMCP[None]) -> AsyncGenerator[None]:
    # Read the configuration at startup, so that a server with a bad
    # configuration fails to start instead of failing every tool call.
    load_servers()
    yield


INSTRUCTIONS = """\
Tools for exploring the metrics in one or more Prometheus servers. Every tool
only reads.

Every tool except `list_servers` takes `server`, the slug of one configured
Prometheus server. `list_servers` names them and reports each one's version.

The intended workflow:

1. Discover: `list_metrics`, `list_labels`, `label_values`, `list_series`,
   and `metric_metadata` map what a server contains.
2. Look at the shape: `chart_range` is the first tool to reach for on any
   question about a metric over time. It renders a range query as an
   ASCII chart with per-series min / avg / max / last. The chart costs a few
   hundred tokens, and the raw samples cost tens of thousands.
3. Zoom in: `query` and `query_range` return exact values. Use them on
   narrow windows or aggregated expressions once a chart has shown where
   to look.

To find out why data is missing or what is wrong, check what Prometheus
itself reports. `list_targets` shows each scrape target's health and last
error. `list_alerts` shows the alerts that are pending or firing, and
`list_rules` shows every alerting and recording rule with its health and
last error.

Time arguments accept 'now', 'now-5h', '+2h', RFC-3339 timestamps, and Unix
epoch seconds or milliseconds. Durations accept units s, m, h, d, and w, such
as '1h30m'.
"""

mcp: FastMCP[None] = FastMCP(
    "prometheus-mcp",
    instructions=INSTRUCTIONS,
    lifespan=lifespan,
    middleware=[PrometheusErrorMiddleware()],
    mask_error_details=True,
    tools=[
        *admin.TOOLS,
        *discovery.TOOLS,
        *query.TOOLS,
        *chart.TOOLS,
        *targets.TOOLS,
        *rules.TOOLS,
    ],
)
