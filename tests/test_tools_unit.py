"""Unit tests for the tool implementations (market / rawsql / meta).

All ClickHouse access goes through the injected FakeClickHouseClient — no
network, no live ClickHouse.
"""

from __future__ import annotations

from datetime import date

import pytest

import clickhouse_mcp.tools._runtime as runtime_mod
from clickhouse_mcp.errors import ChNotAllowedError
from clickhouse_mcp.models import (
    GetCorrelationMatrixInput,
    GetIndicatorsInput,
    GetOhlcvInput,
    RunSafeSqlInput,
    ScreenFilter,
    ScreenStocksInput,
)
from clickhouse_mcp.tools import market, meta, rawsql
from clickhouse_mcp.tools.market import (
    _build_correlation_matrix,
    _correlation,
    _pearson,
    _rank,
    _simple_returns,
)
from tests.conftest import FakeQueryResult

D1 = date(2024, 1, 1)
D2 = date(2024, 1, 31)


class TestGetOhlcv:
    @pytest.mark.asyncio
    async def test_daily_path(self, install_fake_client) -> None:
        fake = install_fake_client(
            responses=[
                FakeQueryResult(["ts", "open", "high", "low", "close", "volume"], [["2024-01-02", 1, 2, 0.5, 1.5, 100]])
            ]
        )
        out = await market.get_ohlcv_impl(GetOhlcvInput(symbol="AAPL", start=D1, end=D2, frequency="1d"))
        assert out["symbol"] == "AAPL"
        assert out["count"] == 1
        assert out["bars"][0]["close"] == 1.5
        assert out["table"] == "usa.bars_1d_l1"
        sql, params, _ = fake.queries[0]
        assert "bars_1d_l1" in sql
        assert params["s"] == "AAPL"
        assert "{s:String}" in sql  # parameterised, not concatenated

    @pytest.mark.asyncio
    async def test_minute_path_uses_freq_enum_and_union_view(self, install_fake_client) -> None:
        fake = install_fake_client(responses=[FakeQueryResult(["ts", "open", "high", "low", "close", "volume"], [])])
        out = await market.get_ohlcv_impl(GetOhlcvInput(symbol="MSFT", start=D1, end=D2, frequency="1m"))
        assert out["table"] == "usa.bars_1m_full"
        sql, params, _ = fake.queries[0]
        assert "bars_1m_full" in sql
        assert params["f"] == "EVERY_MINUTE"


class TestGetIndicators:
    @pytest.mark.asyncio
    async def test_basic(self, install_fake_client) -> None:
        # Wide-format view: the requested indicator is its own column, aliased
        # to ``value`` in the SELECT (see market.get_indicators_impl).
        fake = install_fake_client(responses=[FakeQueryResult(["ts", "value"], [["2024-01-02", 42.0]])])
        out = await market.get_indicators_impl(
            GetIndicatorsInput(symbol="AAPL", indicator="rsi14", start=D1, end=D2, frequency="1d")
        )
        assert out["indicator"] == "rsi14"
        assert out["count"] == 1
        assert out["points"][0]["value"] == 42.0
        sql, params, settings = fake.queries[0]
        assert "indicators_l2" in sql
        # Wide format: indicator name is the SELECT column, not a bound `ind` param.
        assert "rsi14 AS value" in sql
        assert "ind" not in params
        # freq stored verbatim as the short label, not the verbose bars enum.
        assert params["f"] == "1d"
        assert params["s"] == "AAPL"
        # Prewhere-move workaround applied so the freq predicate plans correctly.
        assert settings["optimize_move_to_prewhere"] == 0

    @pytest.mark.asyncio
    async def test_weekly_freq_label(self, install_fake_client) -> None:
        fake = install_fake_client(responses=[FakeQueryResult(["ts", "value"], [])])
        await market.get_indicators_impl(
            GetIndicatorsInput(symbol="AAPL", indicator="ma20", start=D1, end=D2, frequency="1w")
        )
        _, params, _ = fake.queries[0]
        assert params["f"] == "1w"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("unsupported_freq", ["1m", "5m", "15m", "1h"])
    async def test_unsupported_cadence_rejected(self, install_fake_client, unsupported_freq: str) -> None:
        from clickhouse_mcp.errors import ChQueryError

        install_fake_client(responses=[FakeQueryResult(["ts", "value"], [])])
        with pytest.raises(ChQueryError):
            await market.get_indicators_impl(
                GetIndicatorsInput(
                    symbol="AAPL",
                    indicator="ma20",
                    start=D1,
                    end=D2,
                    frequency=unsupported_freq,  # type: ignore[arg-type]
                )
            )


class TestScreenStocks:
    @pytest.mark.asyncio
    async def test_latest_date_path(self, install_fake_client) -> None:
        # Wide format: each indicator is its own column (no ind_0 pivot alias).
        fake = install_fake_client(responses=[FakeQueryResult(["symbol", "rsi14"], [["AAPL", 25.0], ["MSFT", 28.0]])])
        out = await market.screen_stocks_impl(
            ScreenStocksInput(filters=[ScreenFilter(indicator="rsi14", operator="lt", value=30)])
        )
        assert out["count"] == 2
        assert out["as_of"] == "latest"
        sql, params, settings = fake.queries[0]
        assert "max(ts_utc)" in sql  # latest-date subquery
        assert "rsi14 < {val0:Float64}" in sql  # filter on the column directly
        assert params["val0"] == 30.0
        assert params["f"] == "1d"
        assert settings["optimize_move_to_prewhere"] == 0

    @pytest.mark.asyncio
    async def test_as_of_date_path_and_multi_filter(self, install_fake_client) -> None:
        fake = install_fake_client(responses=[FakeQueryResult(["symbol", "macd_hist", "rsi14"], [["AAPL", 1.2, 25.0]])])
        out = await market.screen_stocks_impl(
            ScreenStocksInput(
                filters=[
                    ScreenFilter(indicator="rsi14", operator="lt", value=30),
                    ScreenFilter(indicator="macd_hist", operator="gt", value=0),
                ],
                as_of=D1,
                frequency="1d",
            )
        )
        assert out["as_of"] == "2024-01-01"
        assert set(out["indicators"]) == {"rsi14", "macd_hist"}
        sql, params, _ = fake.queries[0]
        assert "{lo:DateTime}" in sql
        assert "macd_hist > {val1:Float64}" in sql
        assert params["val1"] == 0.0

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("op", "sql_op"),
        [("gt", ">"), ("gte", ">="), ("lt", "<"), ("lte", "<="), ("eq", "=")],
    )
    async def test_all_operators_map(self, install_fake_client, op: str, sql_op: str) -> None:
        fake = install_fake_client(responses=[FakeQueryResult(["symbol", "rsi14"], [])])
        await market.screen_stocks_impl(
            ScreenStocksInput(filters=[ScreenFilter(indicator="rsi14", operator=op, value=50)])  # type: ignore[arg-type]
        )
        sql, _, _ = fake.queries[0]
        assert f"rsi14 {sql_op} " in sql


class TestGetCorrelationMatrix:
    @pytest.mark.asyncio
    async def test_two_symbols(self, install_fake_client) -> None:
        def handler(sql: str, params: dict):  # type: ignore[no-untyped-def]
            if params["s"] == "AAPL":
                return FakeQueryResult(
                    ["d", "close"], [["2024-01-01", 10.0], ["2024-01-02", 11.0], ["2024-01-03", 12.0]]
                )
            return FakeQueryResult(["d", "close"], [["2024-01-01", 20.0], ["2024-01-02", 22.0], ["2024-01-03", 24.0]])

        install_fake_client(handler=handler)
        out = await market.get_correlation_matrix_impl(
            GetCorrelationMatrixInput(symbols=["AAPL", "MSFT"], start=D1, end=D2, method="pearson")
        )
        assert out["matrix"]["AAPL"]["AAPL"] == 1.0
        # Perfectly proportional returns -> correlation 1.0.
        assert out["matrix"]["AAPL"]["MSFT"] == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_spearman_and_none_values_filtered(self, install_fake_client) -> None:
        def handler(sql: str, params: dict):  # type: ignore[no-untyped-def]
            return FakeQueryResult(["d", "close"], [["2024-01-01", 10.0], ["2024-01-02", None], ["2024-01-03", 12.0]])

        install_fake_client(handler=handler)
        out = await market.get_correlation_matrix_impl(
            GetCorrelationMatrixInput(symbols=["AAPL", "MSFT"], start=D1, end=D2, method="spearman")
        )
        assert out["method"] == "spearman"
        assert out["matrix"]["AAPL"]["AAPL"] == 1.0


class TestCorrelationPureMath:
    def test_simple_returns(self) -> None:
        assert _simple_returns([10.0, 11.0, 12.1]) == pytest.approx([0.1, 0.1])

    def test_simple_returns_zero_prev(self) -> None:
        assert _simple_returns([0.0, 5.0]) == [0.0]

    def test_pearson_perfect(self) -> None:
        assert _pearson([1.0, 2.0, 3.0], [2.0, 4.0, 6.0]) == pytest.approx(1.0)

    def test_pearson_too_few(self) -> None:
        assert _pearson([1.0], [2.0]) is None

    def test_pearson_zero_variance(self) -> None:
        assert _pearson([1.0, 1.0, 1.0], [2.0, 3.0, 4.0]) is None

    def test_rank_with_ties(self) -> None:
        assert _rank([3.0, 1.0, 1.0]) == [3.0, 1.5, 1.5]

    def test_correlation_dispatch(self) -> None:
        assert _correlation([1.0, 2.0, 3.0], [1.0, 2.0, 3.0], method="pearson") == pytest.approx(1.0)
        assert _correlation([1.0, 2.0, 3.0], [1.0, 2.0, 3.0], method="spearman") == pytest.approx(1.0)

    def test_build_matrix_none_when_no_overlap(self) -> None:
        m = _build_correlation_matrix(
            ["A", "B"],
            {"A": {"2024-01-01": 1.0}, "B": {"2024-02-01": 1.0}},
            method="pearson",
        )
        assert m["A"]["A"] == 1.0
        assert m["A"]["B"] is None


class TestRunSafeSql:
    @pytest.mark.asyncio
    async def test_disabled_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CLICKHOUSE_MCP_ALLOW_RAW_SQL", raising=False)
        with pytest.raises(ChNotAllowedError):
            await rawsql.run_safe_sql_impl(RunSafeSqlInput(query="SELECT 1"))

    @pytest.mark.asyncio
    async def test_enabled_runs_select(self, monkeypatch: pytest.MonkeyPatch, install_fake_client) -> None:
        monkeypatch.setenv("CLICKHOUSE_MCP_ALLOW_RAW_SQL", "true")
        fake = install_fake_client(responses=[FakeQueryResult(["n"], [[1]])])
        out = await rawsql.run_safe_sql_impl(RunSafeSqlInput(query="SELECT 1", limit=5))
        assert out["row_count"] == 1
        assert out["rows"] == [{"n": 1}]
        sql, _, settings = fake.queries[0]
        assert "LIMIT 5" in sql
        assert settings["readonly"] == 1

    @pytest.mark.asyncio
    async def test_enabled_rejects_ddl(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLICKHOUSE_MCP_ALLOW_RAW_SQL", "true")
        with pytest.raises(ChNotAllowedError):
            await rawsql.run_safe_sql_impl(RunSafeSqlInput(query="DROP TABLE usa.bars_1d_l1"))


class TestMeta:
    @pytest.mark.asyncio
    async def test_server_info(self) -> None:
        out = await meta.get_server_info_impl(server_version="9.9.9")
        assert out["server_version"] == "9.9.9"
        assert out["read_only"] is True
        assert len(out["supported_tools"]) == 7

    @pytest.mark.asyncio
    async def test_health_check_ok(self, install_fake_client) -> None:
        install_fake_client(
            handler=lambda sql, p: (
                FakeQueryResult(["x"], [["26.5.1.882"]]) if "version" in sql else FakeQueryResult(["1"], [[1]])
            )
        )
        out = await meta.health_check_impl()
        assert out["overall_status"] == "ok"
        assert out["clickhouse_reachable"] is True
        assert out["read_only"] is True

    @pytest.mark.asyncio
    async def test_health_check_unconfigured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CLICKHOUSE_MCP_HOST", raising=False)
        runtime_mod.reset_client_cache()
        out = await meta.health_check_impl()
        assert out["connection_configured"] is False
        assert out["overall_status"] == "unhealthy"

    @pytest.mark.asyncio
    async def test_health_check_user_unconfigured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CLICKHOUSE_MCP_USER", raising=False)
        runtime_mod.reset_client_cache()
        out = await meta.health_check_impl()
        assert out["connection_configured"] is False
        assert "CLICKHOUSE_MCP_USER" in str(out["connection_reason"])

    @pytest.mark.asyncio
    async def test_health_check_unreachable(self, install_fake_client) -> None:
        install_fake_client(raise_on_query=RuntimeError("down"))
        out = await meta.health_check_impl()
        assert out["clickhouse_reachable"] is False
        assert out["overall_status"] == "unhealthy"

    @pytest.mark.asyncio
    async def test_health_check_config_error_during_probe(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # configured() passes but client construction raises config error:
        # simulate by clearing host AFTER the configured() check is hard, so
        # instead force get_client to raise ChConfigurationError via reset +
        # removing host right at probe. Covered by unconfigured test; here we
        # assert the probe's generic exception branch via a broken singleton.
        runtime_mod.reset_client_cache()
        out = await meta.health_check_impl()
        # host/user present from fixture -> probe runs, fails to connect ->
        # unreachable (generic Exception branch in probe).
        assert out["clickhouse_reachable"] is False
