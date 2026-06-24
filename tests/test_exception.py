"""Exception-path test suite — every error branch surfaces a structured,
non-leaky envelope and never propagates a raw stack trace.
"""

from __future__ import annotations

from datetime import date

import pytest

import clickhouse_mcp.tools._runtime as runtime_mod
from clickhouse_mcp.errors import ChQueryError
from clickhouse_mcp.models import GetIndicatorsInput, GetOhlcvInput
from clickhouse_mcp.server import _frame_error, app
from clickhouse_mcp.tools import market
from tests.conftest import FakeClickHouseClient, make_readonly_client

D1 = date(2024, 1, 1)
D2 = date(2024, 1, 2)


async def _call(name: str, args: dict):  # type: ignore[no-untyped-def]
    result = await app().call_tool(name, args)
    return result[1] if isinstance(result, tuple) else result


class TestQueryFailurePropagation:
    @pytest.mark.asyncio
    async def test_get_ohlcv_query_error_raises_cherror(self) -> None:
        runtime_mod.set_client(make_readonly_client(FakeClickHouseClient(raise_on_query=RuntimeError("ch down"))))
        with pytest.raises(ChQueryError):
            await market.get_ohlcv_impl(GetOhlcvInput(symbol="AAPL", start=D1, end=D2))

    @pytest.mark.asyncio
    async def test_get_indicators_query_error(self) -> None:
        runtime_mod.set_client(make_readonly_client(FakeClickHouseClient(raise_on_query=RuntimeError("boom"))))
        with pytest.raises(ChQueryError):
            await market.get_indicators_impl(GetIndicatorsInput(symbol="AAPL", indicator="rsi14", start=D1, end=D2))


class TestServerErrorFraming:
    @pytest.mark.asyncio
    async def test_query_error_framed_as_query(self) -> None:
        runtime_mod.set_client(make_readonly_client(FakeClickHouseClient(raise_on_query=RuntimeError("x"))))
        out = await _call("get_ohlcv", {"symbol": "AAPL", "start": "2024-01-01", "end": "2024-01-31"})
        assert out["error"] == "query"

    @pytest.mark.asyncio
    async def test_internal_error_not_leaking_details(self) -> None:
        framed = _frame_error(RuntimeError("super secret internal detail"))
        assert framed == {"error": "internal", "type": "RuntimeError"}
        assert "secret" not in str(framed)


class TestExceptionTextSafety:
    def test_query_error_message_redacted(self) -> None:
        exc = ChQueryError(reason="db pwd=hunter2 unreachable")
        assert "hunter2" not in str(exc)

    def test_str_of_base_does_not_capture_args(self) -> None:
        from clickhouse_mcp.errors import ChError

        # Base ChError str is just the class name even if instantiated with args.
        assert str(ChError("leaky arg")) == "ChError"


class TestCloseFailureSwallowed:
    def test_close_error_does_not_propagate(self) -> None:
        fake = FakeClickHouseClient(raise_on_close=RuntimeError("close failed"))
        client = make_readonly_client(fake)
        # Best-effort close must swallow the error.
        client.close()
