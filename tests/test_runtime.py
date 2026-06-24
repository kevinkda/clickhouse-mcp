"""Unit tests for the runtime client singleton + reset hooks."""

from __future__ import annotations

import pytest

import clickhouse_mcp.tools._runtime as runtime_mod
from clickhouse_mcp.client import ClickHouseReadOnlyClient
from clickhouse_mcp.errors import ChConfigurationError
from tests.conftest import FakeClickHouseClient, make_readonly_client


class TestRuntimeSingleton:
    def test_get_client_constructs_once(self) -> None:
        runtime_mod.reset_client_cache()
        c1 = runtime_mod.get_client()
        c2 = runtime_mod.get_client()
        assert c1 is c2
        assert isinstance(c1, ClickHouseReadOnlyClient)

    def test_set_client_injects(self) -> None:
        fake = make_readonly_client(FakeClickHouseClient())
        runtime_mod.set_client(fake)
        assert runtime_mod.get_client() is fake

    def test_reset_clears_and_closes(self) -> None:
        underlying = FakeClickHouseClient()
        runtime_mod.set_client(make_readonly_client(underlying))
        runtime_mod.reset_client_cache()
        assert underlying.closed is True

    def test_get_client_missing_env_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CLICKHOUSE_MCP_HOST", raising=False)
        runtime_mod.reset_client_cache()
        with pytest.raises(ChConfigurationError):
            runtime_mod.get_client()
