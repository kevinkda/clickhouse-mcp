"""Unit tests for the pluggable response-cache backend."""

from __future__ import annotations

import threading
import time

import pytest

from clickhouse_mcp.cache_backend import (
    CacheBackend,
    MemoryBackend,
    get_cache_backend,
)


class TestMemoryBackend:
    def test_set_get_roundtrip(self) -> None:
        b = MemoryBackend()
        b.set("t", "k", {"v": 1}, ttl_seconds=60)
        assert b.get("t", "k") == {"v": 1}

    def test_miss_returns_none(self) -> None:
        assert MemoryBackend().get("t", "missing") is None

    def test_deepcopy_isolation(self) -> None:
        b = MemoryBackend()
        payload = {"nested": [1, 2]}
        b.set("t", "k", payload, ttl_seconds=60)
        payload["nested"].append(3)
        got = b.get("t", "k")
        assert got == {"nested": [1, 2]}
        got["nested"].append(9)  # mutating the returned copy must not leak back
        assert b.get("t", "k") == {"nested": [1, 2]}

    def test_ttl_expiry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        b = MemoryBackend()
        now = [1000.0]
        monkeypatch.setattr(time, "monotonic", lambda: now[0])
        b.set("t", "k", {"v": 1}, ttl_seconds=10)
        now[0] = 1011.0
        assert b.get("t", "k") is None

    def test_negative_ttl_immediate_miss(self, monkeypatch: pytest.MonkeyPatch) -> None:
        b = MemoryBackend()
        monkeypatch.setattr(time, "monotonic", lambda: 5.0)
        b.set("t", "k", {"v": 1}, ttl_seconds=-1)
        assert b.get("t", "k") is None

    def test_lru_eviction(self) -> None:
        b = MemoryBackend(maxsize=2)
        b.set("t", "a", {"v": "a"}, ttl_seconds=60)
        b.set("t", "b", {"v": "b"}, ttl_seconds=60)
        b.get("t", "a")  # touch a -> b is now LRU
        b.set("t", "c", {"v": "c"}, ttl_seconds=60)
        assert b.get("t", "b") is None
        assert b.get("t", "a") == {"v": "a"}
        assert b.get("t", "c") == {"v": "c"}

    def test_unbounded_when_maxsize_zero(self) -> None:
        b = MemoryBackend(maxsize=0)
        for i in range(50):
            b.set("t", str(i), {"i": i}, ttl_seconds=60)
        assert b.size() == 50

    def test_overwrite_moves_to_end(self) -> None:
        b = MemoryBackend(maxsize=2)
        b.set("t", "a", {"v": 1}, ttl_seconds=60)
        b.set("t", "b", {"v": 1}, ttl_seconds=60)
        b.set("t", "a", {"v": 2}, ttl_seconds=60)  # refresh a
        b.set("t", "c", {"v": 1}, ttl_seconds=60)  # evict LRU = b
        assert b.get("t", "b") is None
        assert b.get("t", "a") == {"v": 2}

    def test_clear_and_size(self) -> None:
        b = MemoryBackend()
        b.set("t", "k", {"v": 1}, ttl_seconds=60)
        assert b.size() == 1
        b.clear()
        assert b.size() == 0

    def test_concurrent_writes_thread_safe(self) -> None:
        b = MemoryBackend(maxsize=0)

        def worker(start: int) -> None:
            for i in range(start, start + 200):
                b.set("t", str(i), {"i": i}, ttl_seconds=60)

        threads = [threading.Thread(target=worker, args=(n * 200,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert b.size() == 800

    def test_satisfies_protocol(self) -> None:
        assert isinstance(MemoryBackend(), CacheBackend)


class TestFactory:
    def test_default_is_memory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CLICKHOUSE_MCP_CACHE_BACKEND", raising=False)
        b = get_cache_backend()
        assert b.name == "memory"

    def test_explicit_memory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLICKHOUSE_MCP_CACHE_BACKEND", "memory")
        assert get_cache_backend().name == "memory"

    def test_unknown_falls_back_to_memory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLICKHOUSE_MCP_CACHE_BACKEND", "redis")
        assert get_cache_backend().name == "memory"
