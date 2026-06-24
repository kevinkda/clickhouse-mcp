"""Boundary-value test suite — edges of every numeric / collection / date input."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from clickhouse_mcp.errors import ChValidationError
from clickhouse_mcp.models import (
    GetCorrelationMatrixInput,
    GetOhlcvInput,
    ScreenFilter,
    ScreenStocksInput,
)

D1 = date(2024, 1, 1)
D2 = date(2024, 1, 2)


class TestSymbolLengthBoundaries:
    def test_single_char_ok(self) -> None:
        assert GetOhlcvInput(symbol="F", start=D1, end=D2).symbol == "F"

    def test_twelve_chars_ok(self) -> None:
        assert GetOhlcvInput(symbol="A" * 12, start=D1, end=D2).symbol == "A" * 12

    def test_thirteen_chars_rejected(self) -> None:
        with pytest.raises((ChValidationError, ValidationError)):
            GetOhlcvInput(symbol="A" * 13, start=D1, end=D2)


class TestLimitBoundaries:
    @pytest.mark.parametrize("limit", [1, 50000])
    def test_inclusive_bounds_ok(self, limit: int) -> None:
        assert GetOhlcvInput(symbol="AAPL", start=D1, end=D2, limit=limit).limit == limit

    @pytest.mark.parametrize("limit", [0, 50001])
    def test_just_outside_bounds_rejected(self, limit: int) -> None:
        with pytest.raises(ValidationError):
            GetOhlcvInput(symbol="AAPL", start=D1, end=D2, limit=limit)


class TestDateBoundaries:
    def test_equal_start_end_ok(self) -> None:
        m = GetOhlcvInput(symbol="AAPL", start=D1, end=D1)
        assert m.start == m.end

    def test_one_day_before_rejected(self) -> None:
        with pytest.raises(ChValidationError):
            GetOhlcvInput(symbol="AAPL", start=D2, end=D1)


class TestCorrelationSymbolCountBoundaries:
    def test_minimum_two_ok(self) -> None:
        assert len(GetCorrelationMatrixInput(symbols=["AAPL", "MSFT"], start=D1, end=D2).symbols) == 2

    def test_fifty_ok(self) -> None:
        syms = [f"S{i:03d}".replace("0", "A") for i in range(50)]
        # Ensure uniqueness + valid symbol pattern.
        syms = [f"SY{i:02d}" for i in range(50)]
        m = GetCorrelationMatrixInput(symbols=syms, start=D1, end=D2)
        assert len(m.symbols) == 50

    def test_one_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GetCorrelationMatrixInput(symbols=["AAPL"], start=D1, end=D2)

    def test_fifty_one_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GetCorrelationMatrixInput(symbols=[f"SY{i:02d}" for i in range(51)], start=D1, end=D2)


class TestScreenFilterCountBoundaries:
    def test_one_filter_ok(self) -> None:
        m = ScreenStocksInput(filters=[ScreenFilter(indicator="rsi14", operator="lt", value=30)])
        assert len(m.filters) == 1

    def test_ten_filters_ok(self) -> None:
        m = ScreenStocksInput(filters=[ScreenFilter(indicator="rsi14", operator="lt", value=i) for i in range(10)])
        assert len(m.filters) == 10

    def test_eleven_filters_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ScreenStocksInput(filters=[ScreenFilter(indicator="rsi14", operator="lt", value=i) for i in range(11)])


class TestScreenLimitBoundaries:
    @pytest.mark.parametrize("limit", [1, 2000])
    def test_inclusive_ok(self, limit: int) -> None:
        m = ScreenStocksInput(
            filters=[ScreenFilter(indicator="rsi14", operator="lt", value=30)],
            limit=limit,
        )
        assert m.limit == limit

    @pytest.mark.parametrize("limit", [0, 2001])
    def test_outside_rejected(self, limit: int) -> None:
        with pytest.raises(ValidationError):
            ScreenStocksInput(
                filters=[ScreenFilter(indicator="rsi14", operator="lt", value=30)],
                limit=limit,
            )


class TestNegativeAndZeroFilterValues:
    @pytest.mark.parametrize("value", [-1e9, 0.0, 1e9])
    def test_extreme_float_values_accepted(self, value: float) -> None:
        f = ScreenFilter(indicator="macd_hist", operator="gt", value=value)
        assert f.value == value
