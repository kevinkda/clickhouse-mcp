# MCP Client Registration

Add clickhouse-mcp to your MCP client config (Cursor / Claude Desktop / etc.).
Replace the connection values with your **read-only** ClickHouse account.

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
        "CLICKHOUSE_MCP_PASSWORD": "your-readonly-password",
        "CLICKHOUSE_MCP_DATABASE": "usa",
        "CLICKHOUSE_MCP_SECURE": "false",
        "CLICKHOUSE_MCP_MAX_EXECUTION_TIME": "30",
        "CLICKHOUSE_MCP_MAX_RESULT_ROWS": "100000",
        "CLICKHOUSE_MCP_ALLOW_RAW_SQL": "false"
      }
    }
  }
}
```

Notes:

- `CLICKHOUSE_MCP_ALLOW_RAW_SQL` defaults to `false`; leave it off unless you
  explicitly need `run_safe_sql`.
- Use a dedicated read-only account (see [SECURITY.md](./SECURITY.md)).
- The server is read-only — there is no trade/write surface.
