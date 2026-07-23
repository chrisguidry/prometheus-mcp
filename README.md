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

The tools are designed around a workflow that keeps an agent's context
small: discover what a server contains, look at the *shape* of a metric
with a cheap ASCII chart, then zoom into a narrow window with a raw
query only when exact values matter. The server publishes this workflow
as MCP instructions so agents pick it up automatically.

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
magnitude). `query_range` picks a `step` that targets ~100 samples
with a 15s floor when none is given, and returns a `resolved` block
with the actual `start` / `end` / `step` the server saw.

Vector and matrix results page by series (`series_limit` /
`series_offset`); each series's samples pass through untouched. An
empty result carries a `hint` explaining the likely reasons — the
metric may not exist, or it may simply have no samples in that window —
so an agent doesn't mistake an outage for a metric that never was.

### Chart

| Tool          | What it does                                            |
| ------------- | ------------------------------------------------------- |
| `chart_range` | Run a range query and render the result as ASCII art.   |

`chart_range` is the recommended first step for any question about how
a metric behaves over time: it compresses a range query into a few
hundred tokens where the raw samples would cost tens of thousands. The
chart arrives as plain text — expression title, per-series legend,
plot, timestamp caption — ready to read or pass along:

```
sum(go_memstats_heap_alloc_bytes)
{} — min 682,170,624, avg 722,347,844, max 779,258,192, last 735,480,312
778,101,047  ┤
772,891,833  ┤               ╭╮
767,682,619  ┤               ││                                ╭╮
762,473,405  ┤              ╭╯│                                ││
757,264,192  ┤              │ │                ╭╮              ││
752,054,978  ┤              │ │            ╭╮  ││              ││
746,845,764  ┤              │ │           ╭╯│╭─╯│             ╭╯│               ╭╮
741,636,550  ┤╭╮            │ │           │ ││  │             │ │ ╭╮            ││
736,427,337  ┤││      ╭╮    │ │      ╭╮   │ ││  │    ╭╮╭╮     │ │ ││╭╮          ││    ╭─╮
731,218,123  ┤││      ││    │ │╭╮  ╭╮││╭╮ │ ╰╯  │    ││││     │ │ ││││         ╭╯│ ╭╮ │ │
726,008,909  ┼╯│      ││╭╮ ╭╯ │││  ││││││ │     │    ││││     │ │ ││││ ╭─╮     │ ╰╮││╭╯ │
720,799,695  ┤ │╭╮ ╭╮ ││││ │  ╰╯│  ││││││ │     │    ││││╭╮ ╭╮│ ╰╮││││ │ │     │  ╰╯││  │
715,590,482  ┤ │││╭╯│╭╯│││╭╯    ╰╮ │││╰╯╰─╯     ╰╮ ╭╮│╰╯│││╭╯╰╯  │││││╭╯ │╭────╯    ╰╯  ╰───╴
710,381,268  ┤ ││││ ││ ││╰╯      ╰─╯╰╯           │╭╯││  ││╰╯     ││││╰╯  ╰╯
705,172,054  ┤ ╰╯╰╯ ││ ╰╯                        ╰╯ ╰╯  ││       ╰╯╰╯
699,962,840  ┤      ╰╯                                  ╰╯
           2026-07-23T09:55:36+00:00  2026-07-23T10:25:36+00:00   2026-07-23T10:55:36+00:00
```

The legend's min / avg / max / last often answers the question without
another call, and the y-axis precision adapts to both the magnitude and
the spread of the data, so tightly-clustered metrics still show
variation.

Absent data stays absent: stretches with no samples render as blank
space instead of an interpolated line, and the legend reports what
fraction of the window had no data — so an outage looks like an outage,
not a smooth ramp between the samples on either side.

`max_series` (default 5) refuses to draw if too many series come back
— an over-plotted chart isn't useful. Instead of a series dump, the
refusal summarizes label cardinalities (e.g. `instance: 5, mode: 8`)
with a few example series, so the next call can aggregate with
`sum by (...)`, filter with label selectors, or wrap in `topk(...)`.

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
