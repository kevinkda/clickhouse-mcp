"""Pydantic v2 input schemas for every outward-facing tool.

Strict validation is the first line of SQL-injection defence: a *symbol*,
*frequency*, *indicator name*, or *date* that reaches the client layer has
already been forced through an anchored regex / allow-list here, and is then
bound as a ClickHouse query **parameter** (never string-concatenated into
SQL).  Garbage is rejected at the gate.

Coverage target: **100 %**.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from .errors import ChValidationError

# ---------------------------------------------------------------------------
# Regexes — anchored to prevent partial-match search semantics.
# ---------------------------------------------------------------------------

#: USA-market ticker symbol: uppercase letters, digits, dot, dash; 1-12 chars.
#: Anchored + character-class-restricted so no SQL metacharacter, whitespace,
#: quote, semicolon, or URL fragment can ever survive validation.
SYMBOL_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Z][A-Z0-9.\-]{0,11}$")

#: Materialised / runtime indicator name: lowercase letters + digits +
#: underscore, 2-32 chars (e.g. ``ma20``, ``rsi14``, ``macd_hist``).
INDICATOR_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_]{1,31}$")

# ---------------------------------------------------------------------------
# Allow-lists.
# ---------------------------------------------------------------------------

#: User-facing bar frequencies → mapped to real L0/L1 tables in client.py.
Frequency = Literal["1m", "5m", "15m", "1h", "1d", "1w"]

#: Correlation methods supported by the cross-section correlation matrix.
CorrelationMethod = Literal["pearson", "spearman"]

#: Comparison operators allowed in a screen filter — bound as a fixed token,
#: never user free-text, so they cannot smuggle SQL.
ScreenOperator = Literal["gt", "gte", "lt", "lte", "eq"]

#: Indicators considered safe to expose by the structured tools.  Descriptive,
#: not exhaustive — covers the L2 materialised families + the long-tail MA/EMA
#: periods served by the runtime indicator view.
ALLOWED_INDICATORS: Final[frozenset[str]] = frozenset(
    {
        # moving averages (runtime view, parameterised periods)
        "ma5",
        "ma10",
        "ma20",
        "ma50",
        "ma60",
        "ma120",
        "ma200",
        "ma250",
        "ema12",
        "ema26",
        # MACD family (500 core)
        "macd_dif",
        "macd_dea",
        "macd_hist",
        # volatility (510)
        "atr14",
        "boll_mid",
        "boll_up",
        "boll_low",
        # oscillators (520)
        "rsi14",
        "stoch_rsi14",
        "mfi14",
        "kdj_k",
        "kdj_d",
        "kdj_j",
        # trend (530)
        "adx14",
        # volume (540)
        "obv",
        "vwap",
    }
)


# ---------------------------------------------------------------------------
# Constrained string types
# ---------------------------------------------------------------------------

Symbol = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=12),
]

Indicator = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=2, max_length=32),
]


# ---------------------------------------------------------------------------
# Base — strict-by-default mixin
# ---------------------------------------------------------------------------


class _BaseInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        frozen=True,
    )


def _normalize_symbol(v: object) -> object:
    if isinstance(v, str):
        v = v.strip().upper()
        if not SYMBOL_RE.match(v):
            raise ChValidationError(field="symbol", reason=f"must match {SYMBOL_RE.pattern}")
    return v


def _normalize_indicator(v: object) -> object:
    if isinstance(v, str):
        v = v.strip().lower()
        if not INDICATOR_RE.match(v):
            raise ChValidationError(field="indicator", reason=f"must match {INDICATOR_RE.pattern}")
        if v not in ALLOWED_INDICATORS:
            raise ChValidationError(
                field="indicator",
                reason=f"unsupported indicator {v!r}; allow-list in ALLOWED_INDICATORS",
            )
    return v


# ---------------------------------------------------------------------------
# Concrete schemas — one per tool.
# ---------------------------------------------------------------------------


class GetOhlcvInput(_BaseInput):
    """Input for ``get_ohlcv``."""

    symbol: Symbol
    start: date
    end: date
    frequency: Frequency = "1d"
    limit: int = Field(default=1000, ge=1, le=50000)

    @field_validator("symbol", mode="before")
    @classmethod
    def _v_symbol(cls, v: object) -> object:
        return _normalize_symbol(v)

    @field_validator("end")
    @classmethod
    def _v_range(cls, v: date, info) -> date:  # type: ignore[no-untyped-def]
        start = info.data.get("start")
        if start is not None and v < start:
            raise ChValidationError(field="end", reason="end must be >= start")
        return v


class GetIndicatorsInput(_BaseInput):
    """Input for ``get_indicators``."""

    symbol: Symbol
    indicator: Indicator
    start: date
    end: date
    frequency: Frequency = "1d"
    limit: int = Field(default=1000, ge=1, le=50000)

    @field_validator("symbol", mode="before")
    @classmethod
    def _v_symbol(cls, v: object) -> object:
        return _normalize_symbol(v)

    @field_validator("indicator", mode="before")
    @classmethod
    def _v_indicator(cls, v: object) -> object:
        return _normalize_indicator(v)

    @field_validator("end")
    @classmethod
    def _v_range(cls, v: date, info) -> date:  # type: ignore[no-untyped-def]
        start = info.data.get("start")
        if start is not None and v < start:
            raise ChValidationError(field="end", reason="end must be >= start")
        return v


class ScreenFilter(_BaseInput):
    """A single indicator threshold filter for ``screen_stocks``."""

    indicator: Indicator
    operator: ScreenOperator
    value: float

    @field_validator("indicator", mode="before")
    @classmethod
    def _v_indicator(cls, v: object) -> object:
        return _normalize_indicator(v)


class ScreenStocksInput(_BaseInput):
    """Input for ``screen_stocks`` — full-market technical-indicator scan."""

    filters: list[ScreenFilter] = Field(min_length=1, max_length=10)
    as_of: date | None = None
    frequency: Frequency = "1d"
    limit: int = Field(default=100, ge=1, le=2000)


class GetCorrelationMatrixInput(_BaseInput):
    """Input for ``get_correlation_matrix``."""

    symbols: list[Symbol] = Field(min_length=2, max_length=50)
    start: date
    end: date
    frequency: Frequency = "1d"
    method: CorrelationMethod = "pearson"

    @field_validator("symbols", mode="before")
    @classmethod
    def _v_symbols(cls, v: object) -> object:
        if isinstance(v, list):
            return [_normalize_symbol(item) for item in v]
        return v

    @field_validator("symbols")
    @classmethod
    def _v_unique(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ChValidationError(field="symbols", reason="symbols must be unique")
        return v

    @field_validator("end")
    @classmethod
    def _v_range(cls, v: date, info) -> date:  # type: ignore[no-untyped-def]
        start = info.data.get("start")
        if start is not None and v < start:
            raise ChValidationError(field="end", reason="end must be >= start")
        return v


class RunSafeSqlInput(_BaseInput):
    """Input for ``run_safe_sql`` — the (default-disabled) raw-SQL escape hatch."""

    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)]
    limit: int = Field(default=1000, ge=1, le=50000)


class HealthCheckInput(_BaseInput):
    """Input for ``health_check`` — empty."""


class GetServerInfoInput(_BaseInput):
    """Input for ``get_server_info`` — empty."""


# ---------------------------------------------------------------------------
# Tool registry — lets ``get_server_info`` enumerate tools without importing
# the server module (avoids a circular import in __init__).
# ---------------------------------------------------------------------------

_SUPPORTED_TOOLS: Final[tuple[str, ...]] = (
    "get_ohlcv",
    "get_indicators",
    "screen_stocks",
    "get_correlation_matrix",
    "run_safe_sql",
    "health_check",
    "get_server_info",
)


def supported_tool_names() -> list[str]:
    """Stable list of tool names the server exposes."""
    return list(_SUPPORTED_TOOLS)


__all__ = [
    "ALLOWED_INDICATORS",
    "INDICATOR_RE",
    "SYMBOL_RE",
    "CorrelationMethod",
    "Frequency",
    "GetCorrelationMatrixInput",
    "GetIndicatorsInput",
    "GetOhlcvInput",
    "GetServerInfoInput",
    "HealthCheckInput",
    "Indicator",
    "RunSafeSqlInput",
    "ScreenFilter",
    "ScreenOperator",
    "ScreenStocksInput",
    "Symbol",
    "supported_tool_names",
]
