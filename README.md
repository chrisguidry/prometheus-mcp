# prometheus-mcp

An MCP server for querying multiple Prometheus instances, with PromQL
passthrough and ASCII-art charts so agents can _see_ the shape of a
metric.

Configure one MCP server, point it at any number of Prometheus backends
(dev / staging / prod, per-customer, etc.), and let an agent pick the
right one by short slug for every tool call.

## Status

Alpha — under active development.

## Quickstart

```bash
uv sync
export PROMETHEUS_MCP_SERVERS=local
export PROMETHEUS_MCP_LOCAL_URL=http://localhost:9090/
uv run prometheus-mcp
```

See the full configuration and tool catalog below once the buildout
lands.
