"""``run_safe_sql`` — the default-DISABLED raw-SQL escape hatch.

Security-critical surface.  Disabled unless ``CLICKHOUSE_MCP_ALLOW_RAW_SQL=true``.
Even when enabled, every query goes through :func:`validate_safe_sql`
(SELECT-only, single statement, no comments, DDL/DML rejected, forced
``LIMIT``) AND is executed with the client's ``readonly=1`` +
execution-time / result-row guardrails.
"""

from __future__ import annotations

from typing import Any

from ..client import raw_sql_allowed, validate_safe_sql
from ..errors import ChNotAllowedError
from ..models import RunSafeSqlInput
from ._runtime import get_client

_DISABLED_HINT = (
    "run_safe_sql is disabled. It is OFF by default for safety. Set "
    "CLICKHOUSE_MCP_ALLOW_RAW_SQL=true to enable it; even then only single "
    "read-only SELECT statements are permitted (readonly=1, forced LIMIT, "
    "execution-time + result-row guardrails). Prefer the structured tools "
    "(get_ohlcv / get_indicators / screen_stocks / get_correlation_matrix)."
)


async def run_safe_sql_impl(args: RunSafeSqlInput) -> dict[str, Any]:
    """Run a single read-only SELECT, if the escape hatch is enabled.

    Raises :class:`ChNotAllowedError` when raw SQL is disabled or the query
    fails the safe-SQL gate.
    """
    if not raw_sql_allowed():
        raise ChNotAllowedError(reason=_DISABLED_HINT)

    safe_query = validate_safe_sql(args.query, default_limit=args.limit)
    client = get_client()
    out = client.query(safe_query)
    columns = out["columns"]
    rows = [dict(zip(columns, row, strict=False)) for row in out["rows"]]
    return {
        "columns": columns,
        "row_count": len(rows),
        "rows": rows,
    }


__all__ = ["run_safe_sql_impl"]
