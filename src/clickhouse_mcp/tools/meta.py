"""Meta tools: ``health_check`` and ``get_server_info``.

``get_server_info`` is fully offline (env + version only).  ``health_check``
optionally probes ClickHouse connectivity (best-effort — a probe failure
reports ``unhealthy`` for reachability but never raises) and reports whether
the connection is configured + whether the raw-SQL escape hatch is enabled.
Connection credentials are never included in the output.
"""

from __future__ import annotations

import os
import sys
from typing import Any

import mcp

from ..client import (
    ENV_HOST,
    ENV_USER,
    raw_sql_allowed,
)
from ..errors import ChConfigurationError
from ..models import supported_tool_names
from ._runtime import get_client

# Captured at import time so get_server_info stays offline-safe.
_SERVER_VERSION: str | None = None


def _connection_configured() -> dict[str, Any]:
    """Report whether required connection env vars are set (no credentials)."""
    host = os.environ.get(ENV_HOST, "").strip()
    user = os.environ.get(ENV_USER, "").strip()
    if not host:
        return {"configured": False, "reason": f"{ENV_HOST} not set"}
    if not user:
        return {"configured": False, "reason": f"{ENV_USER} not set"}
    return {"configured": True, "reason": None}


def _probe_clickhouse() -> dict[str, Any]:
    """Best-effort connectivity probe.  Never raises."""
    try:
        client = get_client()
        reachable = client.ping()
        version = client.server_version() if reachable else None
        return {"reachable": reachable, "server_version": version}
    except ChConfigurationError as exc:
        return {"reachable": False, "server_version": None, "reason": exc.hint}
    except Exception as exc:  # pragma: no cover - defensive: probe never raises
        return {"reachable": False, "server_version": None, "reason": type(exc).__name__}


async def health_check_impl() -> dict[str, Any]:
    """Local health probe + optional ClickHouse reachability check.

    ``overall_status``:
        * ``unhealthy`` — connection not configured, or configured but the
          ClickHouse probe could not reach the server.
        * ``ok`` — connection configured and ClickHouse reachable.
    """
    conn = _connection_configured()
    if not conn["configured"]:
        probe = {"reachable": False, "server_version": None, "reason": conn["reason"]}
    else:
        probe = _probe_clickhouse()

    overall_status = "ok" if (conn["configured"] and probe.get("reachable")) else "unhealthy"

    return {
        "server_version": _SERVER_VERSION,
        "connection_configured": conn["configured"],
        "connection_reason": conn["reason"],
        "clickhouse_reachable": probe.get("reachable", False),
        "clickhouse_server_version": probe.get("server_version"),
        "raw_sql_enabled": raw_sql_allowed(),
        "read_only": True,
        "platform_supported": True,
        "overall_status": overall_status,
    }


async def get_server_info_impl(*, server_version: str) -> dict[str, Any]:
    """Local server metadata — version + tool list.  Never calls ClickHouse."""
    global _SERVER_VERSION
    _SERVER_VERSION = server_version
    return {
        "server_version": server_version,
        "mcp_sdk_version": getattr(mcp, "__version__", "unknown"),
        "python_version": (f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"),
        "supported_tools": supported_tool_names(),
        "read_only": True,
        "raw_sql_enabled": raw_sql_allowed(),
        "platform_supported_v1": ["macos>=11", "linux"],
    }


__all__ = ["get_server_info_impl", "health_check_impl"]
