# Security Policy

## Reporting a vulnerability

Open a private security advisory on the GitHub repository, or use the
[security report issue template](../.github/ISSUE_TEMPLATE/security_report.md).
Do **not** open a public issue for an exploitable vulnerability.

## Posture

clickhouse-mcp is a **read-only** MCP server over a financial-data ClickHouse
warehouse. The controls below are enforced and covered by the test suite (OWASP
2017 / 2021 / 2025, penetration, exception, boundary).

### 1. Dedicated read-only ClickHouse account

Operators MUST point `CLICKHOUSE_MCP_USER` at a ClickHouse account created with
read-only grants, **never** an admin account:

```sql
CREATE USER mcp_readonly IDENTIFIED BY '<password>' SETTINGS readonly = 1;
GRANT SELECT ON usa.* TO mcp_readonly;
```

Defence in depth: every query issued by this server **also** carries the
ClickHouse session setting `readonly = 1`, so even a mis-granted account cannot
mutate data through this server.

### 2. Parameterised queries — no string concatenation

The structured tools (`get_ohlcv`, `get_indicators`, `screen_stocks`,
`get_correlation_matrix`) validate every `symbol` / `indicator` / `date` through
anchored regexes and allow-lists (`src/clickhouse_mcp/models.py`), then bind them
as ClickHouse query parameters (`{x:String}` / `{x:DateTime}` / `{x:UInt32}`).
User input never becomes part of the SQL text. Table names come from a fixed
frequency → table allow-list, never from user input.

### 3. `run_safe_sql` is disabled by default

The raw-SQL escape hatch is off unless `CLICKHOUSE_MCP_ALLOW_RAW_SQL=true`. When
enabled it still enforces:

- single statement only (any extra `;` is rejected),
- `SELECT` / `WITH … SELECT` only,
- DDL / DML / administrative keywords rejected (`INSERT`, `ALTER`, `DROP`,
  `SYSTEM`, `GRANT`, …),
- SQL comments rejected,
- a `LIMIT` is forced if absent,
- and the `readonly = 1` + `max_execution_time` + `max_result_rows` guardrails.

### 4. Resource guardrails

Every query carries `max_execution_time` (default 30s), `max_result_rows`
(default 100k), and `result_overflow_mode = throw`, so a runaway full-market
query cannot exhaust the warehouse.

### 5. SSRF safety

The ClickHouse host/port are read from env **at startup** and are never derived
from tool input. No tool argument can redirect the outbound connection to an
attacker-controlled host or a cloud metadata endpoint.

### 6. Credential hygiene

Connection credentials live only in env, are never logged, and are excluded from
`ConnectionSettings.__repr__`. Every exception runs its free-text fields through
`redact_secrets`, which strips `scheme://user:pass@host` userinfo and inline
`password=` / `secret=` / `token=` assignments, so a credential can never leak
through `repr(exc)` or a log line.
