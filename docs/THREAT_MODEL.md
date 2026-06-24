# Threat Model

Scope: `clickhouse-mcp`, a read-only MCP server that queries a USA-market
ClickHouse warehouse. STRIDE-style, mapped to the OWASP Top 10 suites in
`tests/`.

## Assets

- **ClickHouse credentials** (`CLICKHOUSE_MCP_USER` / `_PASSWORD`) — the crown
  jewels. Compromise enables direct warehouse access.
- **Warehouse integrity** — the 1.49B-row historical dataset. Must be
  read-only from this server's perspective.
- **Warehouse availability** — a runaway query must not exhaust the cluster.

## Trust boundaries

```
MCP client (LLM/agent)  ──stdio JSON-RPC──>  clickhouse-mcp  ──HTTP──>  ClickHouse
        (untrusted tool inputs)            (validation +              (read-only
                                            parameterisation)          account)
```

Tool inputs are **untrusted**. The ClickHouse host/credentials come from the
operator's env (trusted) and are never influenced by tool inputs.

## Threats & mitigations

| STRIDE | Threat | Mitigation |
| --- | --- | --- |
| **Spoofing** | N/A — server does not authenticate end users | The single identity is the operator-configured CH account (env) |
| **Tampering** | A tool mutates warehouse data | No write tool exists; `readonly=1` on every query; dedicated read-only account; `run_safe_sql` rejects DDL/DML and is off by default |
| **Repudiation** | No audit of who ran what | Structured JSON server log (stderr + rotating file); read-only so blast radius is bounded |
| **Information disclosure** | Credentials leak via error/log | `redact_secrets` on every exception; `ConnectionSettings.__repr__` excludes user/password; credentials env-only |
| **Information disclosure** | SQL injection exfiltrates data | Anchored-regex + allow-list validation; ClickHouse parameter binding; table names from fixed allow-list; `run_safe_sql` SELECT-only gate |
| **Denial of service** | Runaway / unbounded query exhausts CH | `max_execution_time` + `max_result_rows` + `result_overflow_mode=throw`; forced `LIMIT` on raw SQL; structured tools cap `limit` via Pydantic bounds |
| **Elevation of privilege** | Reach an internal host via SSRF | Outbound host fixed from env at startup; no tool argument feeds the connection target; URL/host-shaped symbols rejected by `SYMBOL_RE` |

## OWASP Top 10 applicability (test coverage map)

- **A01 Broken Access Control / A05 Broken Access Control (2017 A5)** — read-only
  tool surface; no mutating verb in source.
- **A02 Cryptographic Failures / Sensitive Data Exposure** — credential
  redaction; no plaintext credential in logs/repr.
- **A03 Injection** — parameterised queries; strict input validation;
  `run_safe_sql` keyword/statement gate.
- **A04 Insecure Design** — fail-closed config (missing host/user raises before
  connect); `run_safe_sql` off by default; resource guardrails.
- **A05 Security Misconfiguration** — explicit defaults; `readonly=1` always.
- **A06 Vulnerable Components** — pinned deps; pip-audit CI gate.
- **A07 Identification & Authentication** — N/A (operator-configured CH account
  is the only identity).
- **A08 Software & Data Integrity** — result shape validation; read-only.
- **A09 Logging & Monitoring** — structured JSON logs; health_check surface.
- **A10 SSRF** — fixed outbound host; injection-shaped inputs rejected.
