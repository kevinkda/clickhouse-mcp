"""Pluggable response-cache backend for clickhouse-mcp.

This mirrors the sibling MCP servers' pluggable cache contract so the
ecosystem stays homogeneous, but is intentionally lean: clickhouse-mcp's
*source of truth is already ClickHouse*, so the only caching concern is a
short-lived in-process LRU + TTL that deduplicates identical structured
queries within a session.  There is no derived-analysis-history concern
here (the warehouse already holds the history), so only the response-cache
half of the contract is implemented.

* :class:`CacheBackend` — runtime-checkable Protocol (``get`` / ``set`` /
  ``clear`` / ``size``).
* :class:`MemoryBackend` — the **default**, zero-external-dependency LRU +
  per-entry TTL store on stdlib ``OrderedDict`` guarded by a short-held
  ``threading.Lock`` (microsecond critical section, never wraps I/O, never
  serialises an asyncio event loop).
* :func:`get_cache_backend` — factory; selects from
  ``CLICKHOUSE_MCP_CACHE_BACKEND`` (only ``memory`` is supported today;
  any other value falls back to ``memory``).
"""

from __future__ import annotations

import copy
import logging
import os
import threading
import time
from collections import OrderedDict
from typing import Any, Final, Protocol, runtime_checkable

log = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_MEMORY_MAXSIZE",
    "ENV_CACHE_BACKEND",
    "CacheBackend",
    "MemoryBackend",
    "get_cache_backend",
]

ENV_CACHE_BACKEND: Final[str] = "CLICKHOUSE_MCP_CACHE_BACKEND"
DEFAULT_MEMORY_MAXSIZE: Final[int] = 2048


@runtime_checkable
class CacheBackend(Protocol):
    """Response-cache storage abstraction the cache layer delegates to."""

    name: str

    def get(self, table: str, key: str) -> dict[str, Any] | None:
        """Return a fresh (non-expired) cached row, or ``None`` on miss."""
        ...

    def set(self, table: str, key: str, value: dict[str, Any], ttl_seconds: int) -> None:
        """Store *value* under (table, key) with a TTL.  Best-effort."""
        ...

    def clear(self) -> None:
        """Drop all response-cache state.  Test/maintenance convenience."""
        ...

    def size(self) -> int:
        """Number of live response-cache entries (best-effort)."""
        ...


class MemoryBackend:
    """In-process LRU + per-entry TTL response cache (default backend).

    Thread-safe via a short-held ``threading.Lock`` that only ever wraps
    dict mutations (no I/O), so it never blocks an asyncio event loop.  LRU
    eviction caps memory at *maxsize* live entries.
    """

    name = "memory"

    def __init__(self, maxsize: int = DEFAULT_MEMORY_MAXSIZE) -> None:
        self._maxsize = maxsize
        self._lock = threading.Lock()
        self._store: OrderedDict[str, tuple[dict[str, Any], float]] = OrderedDict()

    @staticmethod
    def _composite_key(table: str, key: str) -> str:
        return f"{table}\x1f{key}"

    def get(self, table: str, key: str) -> dict[str, Any] | None:
        composite = self._composite_key(table, key)
        now = time.monotonic()
        with self._lock:
            entry = self._store.get(composite)
            if entry is None:
                return None
            value, expires_at = entry
            if now >= expires_at:
                del self._store[composite]
                return None
            self._store.move_to_end(composite)
            return copy.deepcopy(value)

    def set(self, table: str, key: str, value: dict[str, Any], ttl_seconds: int) -> None:
        composite = self._composite_key(table, key)
        expires_at = time.monotonic() + max(0, ttl_seconds)
        stored = copy.deepcopy(value)
        with self._lock:
            if composite in self._store:
                self._store.move_to_end(composite)
            self._store[composite] = (stored, expires_at)
            self._evict_if_needed()

    def _evict_if_needed(self) -> None:
        # Caller holds the lock.
        if self._maxsize <= 0:
            return
        while len(self._store) > self._maxsize:
            self._store.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def size(self) -> int:
        with self._lock:
            return len(self._store)


def _resolve_backend_name() -> str:
    raw = os.environ.get(ENV_CACHE_BACKEND, "").strip().lower()
    return raw or "memory"


def get_cache_backend() -> CacheBackend:
    """Construct the configured cache backend (default ``memory``).

    Only ``memory`` is supported today; any other value falls back to it —
    the zero-dependency default that keeps the server working out of the box.
    """
    name = _resolve_backend_name()
    if name != "memory":
        log.warning("unrecognised %s=%r; falling back to memory", ENV_CACHE_BACKEND, name)
    return MemoryBackend()
