# prometheus-mcp

An MCP server for querying multiple Prometheus instances, with full
PromQL passthrough and ASCII-art charts so agents can _see_ the shape of
a metric.

Configure one MCP server, point it at any number of Prometheus backends
(dev / staging / prod, per-customer, per-region, etc.), and let an agent
pick the right one by short slug on every tool call.

## Status

Alpha — under active development.

## Install

```bash
uv pip install prometheus-mcp
```

## Configure

Servers are declared with environment variables, all prefixed
`PROMETHEUS_MCP_`. `PROMETHEUS_MCP_SERVERS` lists the slugs, then each
slug has a `_URL` and any number of `_HEADER_<NAME>` entries:

```bash
export PROMETHEUS_MCP_SERVERS=prod,stg,dev
export PROMETHEUS_MCP_PROD_URL=https://prom-prod.example/
export PROMETHEUS_MCP_PROD_HEADER_AUTHORIZATION='Bearer …'
export PROMETHEUS_MCP_PROD_HEADER_X_SCOPE_ORGID=tenant-7
export PROMETHEUS_MCP_STG_URL=https://prom-stg.example/
export PROMETHEUS_MCP_DEV_URL=http://localhost:9090/
```

Header names are SCREAMING_SNAKE_CASE in the env var and translate to
`Header-Case` on the wire (`AUTHORIZATION` → `Authorization`,
`X_SCOPE_ORGID` → `X-Scope-Orgid`). Slugs can include `a-z`, `0-9`,
`_`, and `-`; case-insensitive at the API surface.

## Run

```bash
uv run prometheus-mcp                       # stdio (default)
uv run prometheus-mcp --transport http      # streamable HTTP
```

Or as a module: `python -m prometheus_mcp`.

## Tools

All tools take a `server` slug to select which backend to query. They
are read-only and idempotent.

### Discovery

| Tool             | What it does                                                  |
| ---------------- | ------------------------------------------------------------- |
| `list_servers`   | List the configured slugs and their URLs (headers redacted).  |
| `list_metrics`   | Enumerate metric names (`/api/v1/label/__name__/values`).     |
| `list_labels`    | Enumerate label names.                                        |
| `label_values`   | Values seen for a single label (optionally scoped by match).  |
| `list_series`    | Full label sets of series matching one or more selectors.     |
| `metric_metadata`| Per-metric type/help/unit, flattened for paging.              |

All discovery tools accept `limit` and `offset` for paging on top of
`total`, so an agent can walk through high-cardinality stores in
chunks.

### Query

| Tool          | What it does                                            |
| ------------- | ------------------------------------------------------- |
| `query`       | Instant PromQL query at a single point in time.         |
| `query_range` | Range PromQL query over a time window.                  |

Both tools pass PromQL through unchanged. Time arguments accept
`"now"`, `"now-5h"`, `"now+30m"`, bare `"-5h"` / `"+2h"`, ISO / RFC-3339
timestamps, and Unix epoch numerics (seconds or ms, auto-detected by
magnitude). `query_range` picks a `step` that targets ~360 samples
with a 15s floor when none is given, and returns a `resolved` block
with the actual `start` / `end` / `step` the server saw.

Vector and matrix results page by series (`series_limit` /
`series_offset`); each series's samples pass through untouched.

### Chart

| Tool          | What it does                                            |
| ------------- | ------------------------------------------------------- |
| `chart_range` | Run a range query and render the result as ASCII art.   |

`chart_range` returns a single string — series legend, plot, timestamp
caption — that you can pass straight to another agent or print to a
terminal:

```
go_memstats_heap_alloc_bytes{instance="localhost:9090", job="prometheus"}
312,951,928  ┤
301,493,769  ┤                          ╭╮    ╭╮            ╭╮          ╭╮
290,035,610  ┤ ╭╮              ╭╮       ││    ││            ││          ││     ╭╮
278,577,452  ┤ ││╭╮            ││       ││    ││    ╭╮  ╭╮  ││╭╮  ╭╮    ││   ╭╮││╭╮
267,119,293  ┼╮││││  ╭╮  ╭╮    ││       ││╭╮  ││    ││╭╮││  ││││╭╮││╭╮  ││ ╭╮││││││╭╴
255,661,134  ┤│││││  ││╭╮││    ││ ╭╮    ││││  ││    ││││││╭╮││││││││││  ││ │││││││││
244,202,975  ┤│││││  ││││││╭╮╭╮││ ││╭╮  ││││  ││╭╮ ╭╯│││││││││││││││││  │╰╮│││││││││
232,744,817  ┤│││││╭╮│││││││╰╯│││ ││││  ││││  ││││ │ │││││││││││││││││ ╭╯ ││││││││││
221,286,658  ┤││││││││││╰╯││  ╰╯│ ││││ ╭╯│││ ╭╯╰╯╰╮│ ╰╯││╰╯││││╰╯││││╰╮│  ││╰╯││││││
209,828,499  ┤╰╯╰╯││││││  ││    │ ││││╭╯ ╰╯╰╮│    ││   ││  ╰╯╰╯  ││││ ╰╯  ││  ││╰╯││
198,370,340  ┤    ╰╯╰╯││  ││    │ │││╰╯     ╰╯    ││   ││        ││╰╯     ││  ╰╯  ╰╯
186,912,182  ┤        ╰╯  ││    ╰╮│││             ╰╯   ╰╯        ╰╯       ╰╯
175,454,023  ┤            ╰╯     ││││
163,995,864  ┤                   ╰╯╰╯
           2026-05-13T13:40:20+00:00                      2026-05-13T14:40:20+00:00
```

The y-axis precision adapts to both the magnitude and the spread of the
data, so tightly-clustered metrics still show variation.

`max_series` (default 5) refuses to draw if too many series come back
— an over-plotted chart isn't useful. The tool returns the list of
labels and a hint to narrow with `topk(...)` or label selectors.

## Configure your MCP client

```jsonc
{
  "mcpServers": {
    "prometheus": {
      "command": "uv",
      "args": ["run", "prometheus-mcp"],
      "env": {
        "PROMETHEUS_MCP_SERVERS": "prod,stg,dev",
        "PROMETHEUS_MCP_PROD_URL": "https://prom-prod.example/",
        "PROMETHEUS_MCP_PROD_HEADER_AUTHORIZATION": "Bearer …",
        "PROMETHEUS_MCP_STG_URL": "https://prom-stg.example/",
        "PROMETHEUS_MCP_DEV_URL": "http://localhost:9090/"
      }
    }
  }
}
```

## Development

```bash
uv sync
uv run pytest               # 100% branch coverage
uv run prek run --all-files # lint, type-check, file-size limits
```

## License

MIT.
