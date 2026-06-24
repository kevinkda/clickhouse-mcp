# clickhouse-mcp

[![test](https://github.com/kevinkda/clickhouse-mcp/actions/workflows/test.yml/badge.svg)](https://github.com/kevinkda/clickhouse-mcp/actions/workflows/test.yml)

> [中文 README](./README_zh.md)

A **read-only** [Model Context Protocol](https://modelcontextprotocol.io) (MCP)
server that queries a USA-market **ClickHouse** warehouse — **1.49 billion** rows
of 1-minute bars across **1,388 symbols** (1991–2026) plus L1 aggregates and L2
materialised technical indicators — and exposes it to MCP-aware agents for
**large-scale, cross-sectional quant analysis** that single-symbol REST MCP
servers cannot do.

It is the 6th read-only server in the kevinkda MCP ecosystem
(schwab-marketdata / schwab-positions / sec-edgar / polygon-news / yfinance),
and deliberately stays independent because its authentication (ClickHouse
host/user/password) and data shape (columnar deep history) differ from the
REST-based siblings.

## Tools (7, all read-only)

| Tool | Purpose |
| --- | --- |
| `get_ohlcv` | OHLCV bars for one symbol over a date range at 1m/5m/15m/1h/1d/1w |
| `get_indicators` | One materialised / runtime indicator series (ma20, rsi14, macd_hist, …) |
| `screen_stocks` | Full-market technical-indicator scan (e.g. "today's RSI<30 oversold list") |
| `get_correlation_matrix` | Pairwise return-correlation matrix for 2–50 symbols (Pearson / Spearman) |
| `run_safe_sql` | **Disabled by default** raw-SQL escape hatch (single read-only SELECT only) |
| `health_check` | Local probe + optional ClickHouse reachability check |
| `get_server_info` | Version + tool list (offline) |

## Quickstart

```bash
git clone https://github.com/kevinkda/clickhouse-mcp
cd clickhouse-mcp
uv sync --extra dev
cp .env.example .env   # then fill in your read-only ClickHouse connection
```

Configure the connection in `.env` (see [Security](#security)):

```bash
CLICKHOUSE_MCP_HOST=your-clickhouse-host
CLICKHOUSE_MCP_HTTP_PORT=8123
CLICKHOUSE_MCP_USER=mcp_readonly
CLICKHOUSE_MCP_PASSWORD=...
CLICKHOUSE_MCP_DATABASE=usa
```

Run it:

```bash
uv run clickhouse-mcp
```

### Register with an MCP client

```jsonc
{
  "mcpServers": {
    "clickhouse": {
      "command": "uv",
      "args": ["run", "clickhouse-mcp"],
      "cwd": "/absolute/path/to/clickhouse-mcp",
      "env": {
        "CLICKHOUSE_MCP_HOST": "your-clickhouse-host",
        "CLICKHOUSE_MCP_HTTP_PORT": "8123",
        "CLICKHOUSE_MCP_USER": "mcp_readonly",
        "CLICKHOUSE_MCP_PASSWORD": "...",
        "CLICKHOUSE_MCP_DATABASE": "usa"
      }
    }
  }
}
```

## Security

This server touches **financial data**, so it is hardened accordingly. See
[`docs/SECURITY.md`](./docs/SECURITY.md) and
[`docs/THREAT_MODEL.md`](./docs/THREAT_MODEL.md).

- **Dedicated read-only ClickHouse account** — point `CLICKHOUSE_MCP_USER` at an
  account created with `readonly = 1` and `GRANT SELECT` only, never an admin
  user. Defence in depth: every query is *also* issued with the ClickHouse
  session setting `readonly=1` plus `max_execution_time` / `max_result_rows`
  guardrails.
- **Parameterised, never concatenated** — `get_ohlcv` / `get_indicators` /
  `screen_stocks` / `get_correlation_matrix` validate every symbol / indicator /
  date through anchored regexes + allow-lists and bind them as ClickHouse query
  parameters. User input never lands in the SQL string.
- **`run_safe_sql` disabled by default** — gated behind
  `CLICKHOUSE_MCP_ALLOW_RAW_SQL=true`; even when enabled it enforces SELECT-only
  single statements, rejects DDL/DML + comments, forces a `LIMIT`, and inherits
  `readonly=1` + the resource guardrails.
- **SSRF-safe** — the ClickHouse host is read from env at startup and is never
  derived from tool input; no argument can redirect the outbound connection.
- **Credentials redacted** — every exception runs its text through
  `redact_secrets`, so a password / DSN can never leak via `repr(exc)` or logs.

## Quant use cases unlocked

CH is the "full-market + deep-history + batch" engine; the REST MCP siblings are
"real-time + fundamentals + news". Together they close the loop:

- **Full-market technical scan** — RSI/MACD/Bollinger/MA signals across all 1,388
  symbols (single-symbol MCP cannot scan the market).
- **Cross-sectional factor ranking** — value/momentum/quality scoring across the
  universe on a columnar store.
- **Multi-symbol correlation matrix** — portfolio / pairs prerequisite; CH batch
  read beats fetching symbols one at a time.
- **Cross-source quant playbook** — CH historical signal → schwab real-time price
  → sec-edgar fundamentals → polygon news.

## Development

```bash
bash scripts/local-ci.sh   # ruff + mypy + bandit + pip-audit + pytest (100% cov)
```

Tests **never** touch a live ClickHouse — `clickhouse_connect` is mocked.

## License

MIT — see [LICENSE](./LICENSE).
