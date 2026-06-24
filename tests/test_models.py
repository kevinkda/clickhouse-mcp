"""Unit tests for the Pydantic v2 input models — validation + allow-lists."""

from __future__ import annotations

from datetime import date

import pytest

from clickhouse_mcp.errors import ChValidationError
from clickhouse_mcp.models import (
    ALLOWED_INDICATORS,
    GetCorrelationMatrixInput,
    GetIndicatorsInput,
    GetOhlcvInput,
    RunSafeSqlInput,
    ScreenFilter,
    ScreenStocksInput,
    supported_tool_names,
)

D1 = date(2024, 1, 1)
D2 = date(2024, 6, 1)


class TestGetOhlcvInput:
    def test_valid(self) -> None:
        m = GetOhlcvInput(symbol="aapl", start=D1, end=D2, frequency="1d")
        assert m.symbol == "AAPL"
        assert m.frequency == "1d"

    def test_default_frequency_and_limit(self) -> None:
        m = GetOhlcvInput(symbol="MSFT", start=D1, end=D2)
        assert m.frequency == "1d"
        assert m.limit == 1000

    @pytest.mark.parametrize("bad", ["", "1abc", "a b", "AAPL;DROP", "http://x", "../e"])
    def test_bad_symbol_rejected(self, bad: str) -> None:
        with pytest.raises(ChValidationError):
            GetOhlcvInput(symbol=bad, start=D1, end=D2)

    def test_non_str_symbol_passes_through_to_pydantic(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            GetOhlcvInput(symbol=123, start=D1, end=D2)  # type: ignore[arg-type]

    def test_end_before_start_rejected(self) -> None:
        with pytest.raises(ChValidationError):
            GetOhlcvInput(symbol="AAPL", start=D2, end=D1)

    @pytest.mark.parametrize("bad_freq", ["2m", "1mo", "tick", ""])
    def test_bad_frequency_rejected(self, bad_freq: str) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            GetOhlcvInput(symbol="AAPL", start=D1, end=D2, frequency=bad_freq)  # type: ignore[arg-type]

    @pytest.mark.parametrize("bad_limit", [0, -1, 50001])
    def test_limit_bounds(self, bad_limit: int) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            GetOhlcvInput(symbol="AAPL", start=D1, end=D2, limit=bad_limit)

    def test_extra_field_forbidden(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            GetOhlcvInput(symbol="AAPL", start=D1, end=D2, evil="x")  # type: ignore[call-arg]


class TestGetIndicatorsInput:
    def test_valid(self) -> None:
        m = GetIndicatorsInput(symbol="AAPL", indicator="RSI14", start=D1, end=D2)
        assert m.indicator == "rsi14"

    @pytest.mark.parametrize("bad", ["x", "1abc", "rsi 14", "drop;", "rsi14!"])
    def test_indicator_regex(self, bad: str) -> None:
        with pytest.raises(ChValidationError):
            GetIndicatorsInput(symbol="AAPL", indicator=bad, start=D1, end=D2)

    def test_indicator_not_in_allowlist(self) -> None:
        with pytest.raises(ChValidationError):
            GetIndicatorsInput(symbol="AAPL", indicator="ma9999", start=D1, end=D2)

    def test_end_before_start_rejected(self) -> None:
        with pytest.raises(ChValidationError):
            GetIndicatorsInput(symbol="AAPL", indicator="rsi14", start=D2, end=D1)

    def test_every_allowlisted_indicator_validates(self) -> None:
        for ind in ALLOWED_INDICATORS:
            m = GetIndicatorsInput(symbol="AAPL", indicator=ind, start=D1, end=D2)
            assert m.indicator == ind


class TestScreenStocksInput:
    def test_valid(self) -> None:
        m = ScreenStocksInput(filters=[ScreenFilter(indicator="rsi14", operator="lt", value=30)])
        assert m.frequency == "1d"
        assert m.filters[0].operator == "lt"

    def test_filter_indicator_normalised(self) -> None:
        f = ScreenFilter(indicator="RSI14", operator="gt", value=70)
        assert f.indicator == "rsi14"

    def test_empty_filters_rejected(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ScreenStocksInput(filters=[])

    def test_bad_operator_rejected(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ScreenFilter(indicator="rsi14", operator="like", value=1)  # type: ignore[arg-type]

    def test_as_of_optional(self) -> None:
        m = ScreenStocksInput(
            filters=[ScreenFilter(indicator="rsi14", operator="lt", value=30)],
            as_of=D1,
        )
        assert m.as_of == D1


class TestGetCorrelationMatrixInput:
    def test_valid(self) -> None:
        m = GetCorrelationMatrixInput(symbols=["aapl", "msft"], start=D1, end=D2)
        assert m.symbols == ["AAPL", "MSFT"]
        assert m.method == "pearson"

    def test_min_two_symbols(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            GetCorrelationMatrixInput(symbols=["AAPL"], start=D1, end=D2)

    def test_duplicate_symbols_rejected(self) -> None:
        with pytest.raises(ChValidationError):
            GetCorrelationMatrixInput(symbols=["AAPL", "AAPL"], start=D1, end=D2)

    def test_bad_symbol_in_list(self) -> None:
        with pytest.raises(ChValidationError):
            GetCorrelationMatrixInput(symbols=["AAPL", "x y"], start=D1, end=D2)

    def test_end_before_start(self) -> None:
        with pytest.raises(ChValidationError):
            GetCorrelationMatrixInput(symbols=["AAPL", "MSFT"], start=D2, end=D1)

    def test_spearman_method(self) -> None:
        m = GetCorrelationMatrixInput(symbols=["AAPL", "MSFT"], start=D1, end=D2, method="spearman")
        assert m.method == "spearman"

    def test_non_list_symbols_passthrough(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            GetCorrelationMatrixInput(symbols="AAPL", start=D1, end=D2)  # type: ignore[arg-type]


class TestRunSafeSqlInput:
    def test_valid(self) -> None:
        m = RunSafeSqlInput(query="SELECT 1")
        assert m.query == "SELECT 1"
        assert m.limit == 1000

    def test_empty_query_rejected(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            RunSafeSqlInput(query="")


class TestRegistry:
    def test_supported_tool_names(self) -> None:
        names = supported_tool_names()
        assert names == [
            "get_ohlcv",
            "get_indicators",
            "screen_stocks",
            "get_correlation_matrix",
            "run_safe_sql",
            "health_check",
            "get_server_info",
        ]
