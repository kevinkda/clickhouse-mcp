# Changelog

All notable changes to this project are documented here. The format is based
on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-06-16

Initial release — a read-only MCP server over a USA-market ClickHouse
warehouse (1.49B-row 1m bars + L1 aggregates + L2 materialised indicators).

### Added

- 7 read-only tools:
  - `get_ohlcv` — OHLCV bars for one symbol (1m/5m/15m/1h/1d/1w).
  - `get_indicators` — one materialised / runtime indicator series.
  - `screen_stocks` — full-market technical-indicator scan.
  - `get_correlation_matrix` — pairwise return-correlation matrix (Pearson / Spearman).
  - `run_safe_sql` — **disabled by default** raw-SQL escape hatch.
  - `health_check` — local probe + optional ClickHouse reachability check.
  - `get_server_info` — version + tool list (offline).
- Read-only `clickhouse_connect` wrapper with lazy connect, connection reuse,
  `readonly=1` + `max_execution_time` + `max_result_rows` guardrails on every
  query, and parameterised (never concatenated) SQL.
- Security: dedicated read-only account guidance, SSRF-safe fixed host,
  credential redaction in every exception, and the `run_safe_sql` SELECT-only /
  single-statement / DDL-DML-reject / forced-LIMIT gate.
- Pluggable in-process memory LRU response cache (`cache_backend.py`).
- 100% test coverage including OWASP Top 10 (2017 / 2021 / 2025), penetration,
  exception, and boundary suites — `clickhouse_connect` fully mocked, no live
  ClickHouse.
- Reusable CI (kevinkda/mcp-ci-templates), pre-commit, ruff/mypy/bandit/pip-audit
  gates, mirroring the sibling MCP servers.

[0.1.0]: https://github.com/kevinkda/clickhouse-mcp/releases/tag/v0.1.0
