"""ClickHouse Read-only MCP Server.

A Model Context Protocol (MCP) server exposing 7 read-only tools over a
USA-market ClickHouse warehouse (1.49B-row 1m bars + L1 aggregates + L2
materialised technical indicators), unlocking large-scale / cross-sectional
quant analysis that single-symbol REST MCP servers cannot do.

Public modules:
    - :mod:`clickhouse_mcp.server` — FastMCP entry point (7 tools).
    - :mod:`clickhouse_mcp.client` — read-only ``clickhouse_connect`` wrapper.
    - :mod:`clickhouse_mcp.errors` — structured exception hierarchy + redaction.
    - :mod:`clickhouse_mcp.models` — Pydantic v2 input schemas (strict validation).
    - :mod:`clickhouse_mcp.cache_backend` — pluggable response-cache backend.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
