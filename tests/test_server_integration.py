"""Integration tests for the FastMCP server wiring (all 7 tools)."""

from __future__ import annotations

import pytest

import clickhouse_mcp.tools._runtime as runtime_mod
from clickhouse_mcp import __version__
from clickhouse_mcp.errors import (
    ChConfigurationError,
    ChConnectionError,
    ChError,
    ChNotAllowedError,
    ChQueryError,
    ChValidationError,
)
from clickhouse_mcp.server import (
    SERVER_NAME,
    SERVER_VERSION,
    _build_mcp,
    _frame_error,
    app,
)
from tests.conftest import FakeClickHouseClient, FakeQueryResult, make_readonly_client


async def _call(app_obj, name: str, args: dict):  # type: ignore[no-untyped-def]
    result = await app_obj.call_tool(name, args)
    # FastMCP returns (content, structured) — use the structured dict.
    if isinstance(result, tuple):
        return result[1]
    return result  # pragma: no cover - SDK shape fallback


class TestServerMetadata:
    def test_server_name(self) -> None:
        assert SERVER_NAME == "clickhouse-mcp"

    def test_version_matches_package(self) -> None:
        # G2 guard: serverInfo version reflects package __version__.
        assert __version__ == SERVER_VERSION
        assert app()._mcp_server.version == __version__

    @pytest.mark.asyncio
    async def test_lists_seven_tools(self) -> None:
        tools = await app().list_tools()
        assert {t.name for t in tools} == {
            "get_ohlcv",
            "get_indicators",
            "screen_stocks",
            "get_correlation_matrix",
            "run_safe_sql",
            "health_check",
            "get_server_info",
        }

    def test_app_is_singleton(self) -> None:
        assert app() is app()


class TestFrameError:
    @pytest.mark.parametrize(
        ("exc", "key"),
        [
            (ChValidationError(field="f", reason="r"), "validation"),
            (ChConfigurationError(hint="h"), "configuration"),
            (ChNotAllowedError(reason="r"), "not_allowed"),
            (ChConnectionError(reason="r"), "connection"),
            (ChQueryError(reason="r"), "query"),
            (ChError(), "clickhouse_error"),
            (ValueError("x"), "internal"),
        ],
    )
    def test_frames(self, exc: BaseException, key: str) -> None:
        assert _frame_error(exc)["error"] == key


class TestToolDispatch:
    @pytest.mark.asyncio
    async def test_get_ohlcv_success(self, install_fake_client) -> None:
        install_fake_client(
            responses=[
                FakeQueryResult(["ts", "open", "high", "low", "close", "volume"], [["2024-01-02", 1, 2, 0.5, 1.5, 9]])
            ]
        )
        out = await _call(app(), "get_ohlcv", {"symbol": "AAPL", "start": "2024-01-01", "end": "2024-01-31"})
        assert out["symbol"] == "AAPL"

    @pytest.mark.asyncio
    async def test_get_ohlcv_validation_error_framed(self) -> None:
        out = await _call(app(), "get_ohlcv", {"symbol": "bad symbol", "start": "2024-01-01", "end": "2024-01-31"})
        assert out["error"] == "validation"

    @pytest.mark.asyncio
    async def test_get_indicators_success(self, install_fake_client) -> None:
        install_fake_client(responses=[FakeQueryResult(["ts", "value"], [["2024-01-02", 42.0]])])
        out = await _call(
            app(),
            "get_indicators",
            {"symbol": "AAPL", "indicator": "rsi14", "start": "2024-01-01", "end": "2024-01-31"},
        )
        assert out["indicator"] == "rsi14"

    @pytest.mark.asyncio
    async def test_get_indicators_validation_error(self) -> None:
        out = await _call(
            app(),
            "get_indicators",
            {"symbol": "AAPL", "indicator": "bogus999", "start": "2024-01-01", "end": "2024-01-31"},
        )
        assert out["error"] == "validation"

    @pytest.mark.asyncio
    async def test_screen_stocks_success(self, install_fake_client) -> None:
        install_fake_client(responses=[FakeQueryResult(["symbol", "ind_0"], [["AAPL", 25.0]])])
        out = await _call(
            app(),
            "screen_stocks",
            {"filters": [{"indicator": "rsi14", "operator": "lt", "value": 30}]},
        )
        assert out["count"] == 1

    @pytest.mark.asyncio
    async def test_screen_stocks_bad_filter_framed(self) -> None:
        out = await _call(
            app(),
            "screen_stocks",
            {"filters": [{"indicator": "nope999", "operator": "lt", "value": 30}]},
        )
        assert out["error"] == "validation"

    @pytest.mark.asyncio
    async def test_correlation_success(self, install_fake_client) -> None:
        install_fake_client(
            handler=lambda sql, p: FakeQueryResult(["d", "close"], [["2024-01-01", 1.0], ["2024-01-02", 2.0]])
        )
        out = await _call(
            app(),
            "get_correlation_matrix",
            {"symbols": ["AAPL", "MSFT"], "start": "2024-01-01", "end": "2024-01-31"},
        )
        assert "matrix" in out

    @pytest.mark.asyncio
    async def test_correlation_validation_error(self) -> None:
        # A duplicate-symbol payload passes FastMCP arg validation (list of 2
        # strings) but trips our ChValidationError, which is framed.
        out = await _call(
            app(),
            "get_correlation_matrix",
            {"symbols": ["AAPL", "AAPL"], "start": "2024-01-01", "end": "2024-01-31"},
        )
        assert out["error"] == "validation"

    @pytest.mark.asyncio
    async def test_run_safe_sql_disabled_framed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CLICKHOUSE_MCP_ALLOW_RAW_SQL", raising=False)
        out = await _call(app(), "run_safe_sql", {"query": "SELECT 1"})
        assert out["error"] == "not_allowed"

    @pytest.mark.asyncio
    async def test_run_safe_sql_query_error_framed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLICKHOUSE_MCP_ALLOW_RAW_SQL", "true")
        runtime_mod.set_client(make_readonly_client(FakeClickHouseClient(raise_on_query=RuntimeError("boom"))))
        out = await _call(app(), "run_safe_sql", {"query": "SELECT 1"})
        assert out["error"] == "query"

    @pytest.mark.asyncio
    async def test_health_check_tool(self, install_fake_client) -> None:
        install_fake_client(handler=lambda sql, p: FakeQueryResult(["1"], [[1]]))
        out = await _call(app(), "health_check", {})
        assert "overall_status" in out

    @pytest.mark.asyncio
    async def test_get_server_info_tool(self) -> None:
        out = await _call(app(), "get_server_info", {})
        assert out["server_version"] == SERVER_VERSION


class TestBuildMcpFresh:
    def test_build_mcp_returns_fresh_instance(self) -> None:
        a = _build_mcp()
        b = _build_mcp()
        assert a is not b
