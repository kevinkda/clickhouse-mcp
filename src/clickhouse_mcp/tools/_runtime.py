"""Process-wide read-only ClickHouse client singleton + test reset hook."""

from __future__ import annotations

import threading

from ..client import ClickHouseReadOnlyClient

_client: ClickHouseReadOnlyClient | None = None
_lock = threading.Lock()


def get_client() -> ClickHouseReadOnlyClient:
    """Return the process-wide read-only client, constructing it on first use.

    Construction reads + validates connection env vars (raising
    :class:`~clickhouse_mcp.errors.ChConfigurationError` if unset), so the
    server fails closed rather than connecting to a wrong host.
    """
    global _client
    if _client is not None:
        return _client
    with _lock:
        if _client is None:  # pragma: no branch - double-checked lock; race side not deterministically testable
            _client = ClickHouseReadOnlyClient()
    return _client


def set_client(client: ClickHouseReadOnlyClient | None) -> None:
    """Test helper — inject (or clear) the singleton."""
    global _client
    with _lock:
        if _client is not None and client is None:
            _client.close()
        _client = client


def reset_client_cache() -> None:
    """Test helper — drop the singleton so the next call re-creates it."""
    set_client(None)


__all__ = ["get_client", "reset_client_cache", "set_client"]
