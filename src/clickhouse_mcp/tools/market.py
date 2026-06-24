"""Market-data query tools — get_ohlcv / get_indicators / screen_stocks /
get_correlation_matrix.

Every query is parameterised: validated symbol / date / limit values are
bound as ClickHouse query parameters, never concatenated into SQL.  Table
names come from the frequency allow-list in :mod:`clickhouse_mcp.client`.
"""

from __future__ import annotations

import itertools
from datetime import date, timedelta
from typing import Any, Final

from ..client import bars_table, ch_freq_enum, indicators_view
from ..models import (
    GetCorrelationMatrixInput,
    GetIndicatorsInput,
    GetOhlcvInput,
    ScreenStocksInput,
)
from ._runtime import get_client

#: Comparison operator token → SQL operator.  Bound from a fixed enum
#: (models.ScreenOperator), never user free-text, so it cannot smuggle SQL.
_OP_TO_SQL: Final[dict[str, str]] = {
    "gt": ">",
    "gte": ">=",
    "lt": "<",
    "lte": "<=",
    "eq": "=",
}


def _rows_to_dicts(out: dict[str, Any]) -> list[dict[str, Any]]:
    columns = out["columns"]
    return [dict(zip(columns, row, strict=False)) for row in out["rows"]]


async def get_ohlcv_impl(args: GetOhlcvInput) -> dict[str, Any]:
    """Return OHLCV bars for a single symbol over [start, end] at *frequency*.

    Routes 1m through the ``bars_1m_full`` UNION view (keyed on ``freq``) and
    the L1 aggregates through their dedicated parquet link tables.
    """
    client = get_client()
    db = client.database
    table = bars_table(args.frequency)
    fq = f"{db}.{table}"

    # ``date`` bounds are bound as parameters; the table name is allow-listed.
    if args.frequency == "1m":
        sql = (
            f"SELECT toString(ts_utc) AS ts, open, high, low, close, volume "  # noqa: S608
            f"FROM {fq} "
            "WHERE symbol = {s:String} AND freq = {f:String} "
            "AND ts_utc >= {lo:DateTime} AND ts_utc < {hi:DateTime} "
            "ORDER BY ts_utc ASC LIMIT {n:UInt32}"
        )
        params: dict[str, Any] = {
            "s": args.symbol,
            "f": ch_freq_enum(args.frequency),
            "lo": f"{args.start.isoformat()} 00:00:00",
            "hi": f"{_exclusive_end(args.end)} 00:00:00",
            "n": args.limit,
        }
    else:
        sql = (
            f"SELECT toString(ts_utc) AS ts, open, high, low, close, volume "  # noqa: S608
            f"FROM {fq} "
            "WHERE symbol = {s:String} "
            "AND ts_utc >= {lo:DateTime} AND ts_utc < {hi:DateTime} "
            "ORDER BY ts_utc ASC LIMIT {n:UInt32}"
        )
        params = {
            "s": args.symbol,
            "lo": f"{args.start.isoformat()} 00:00:00",
            "hi": f"{_exclusive_end(args.end)} 00:00:00",
            "n": args.limit,
        }

    out = client.query(sql, parameters=params)
    bars = _rows_to_dicts(out)
    return {
        "symbol": args.symbol,
        "frequency": args.frequency,
        "start": args.start.isoformat(),
        "end": args.end.isoformat(),
        "table": fq,
        "count": len(bars),
        "bars": bars,
    }


async def get_indicators_impl(args: GetIndicatorsInput) -> dict[str, Any]:
    """Return one materialised / runtime indicator series for a symbol."""
    client = get_client()
    db = client.database
    fq = f"{db}.{indicators_view()}"

    sql = (
        f"SELECT toString(ts_utc) AS ts, value "  # noqa: S608
        f"FROM {fq} "
        "WHERE symbol = {s:String} AND indicator = {ind:String} AND freq = {f:String} "
        "AND ts_utc >= {lo:DateTime} AND ts_utc < {hi:DateTime} "
        "ORDER BY ts_utc ASC LIMIT {n:UInt32}"
    )
    params = {
        "s": args.symbol,
        "ind": args.indicator,
        "f": ch_freq_enum(args.frequency),
        "lo": f"{args.start.isoformat()} 00:00:00",
        "hi": f"{_exclusive_end(args.end)} 00:00:00",
        "n": args.limit,
    }
    out = client.query(sql, parameters=params)
    points = _rows_to_dicts(out)
    return {
        "symbol": args.symbol,
        "indicator": args.indicator,
        "frequency": args.frequency,
        "start": args.start.isoformat(),
        "end": args.end.isoformat(),
        "count": len(points),
        "points": points,
    }


async def screen_stocks_impl(args: ScreenStocksInput) -> dict[str, Any]:
    """Full-market technical-indicator scan.

    Pivots the latest indicator values per symbol (as-of *as_of* or the most
    recent date) and applies the threshold filters.  Each filter's indicator
    name + threshold are bound as parameters; the operator is a fixed token.
    """
    client = get_client()
    db = client.database
    fq = f"{db}.{indicators_view()}"
    fenum = ch_freq_enum(args.frequency)

    # Build a HAVING clause from the filters. Indicator names + thresholds are
    # bound as parameters; only the operator (fixed enum) and the parameter
    # placeholders are interpolated.
    distinct_inds = sorted({f.indicator for f in args.filters})
    select_aggs = ", ".join(
        f"anyIf(value, indicator = {{ind{i}:String}}) AS ind_{i}" for i in range(len(distinct_inds))
    )
    ind_index = {name: i for i, name in enumerate(distinct_inds)}

    params: dict[str, Any] = {"f": fenum}
    for i, name in enumerate(distinct_inds):
        params[f"ind{i}"] = name

    having_parts: list[str] = []
    for j, flt in enumerate(args.filters):
        col = f"ind_{ind_index[flt.indicator]}"
        op = _OP_TO_SQL[flt.operator]
        having_parts.append(f"{col} {op} {{val{j}:Float64}}")
        params[f"val{j}"] = float(flt.value)
    having_clause = " AND ".join(having_parts)

    if args.as_of is not None:
        date_pred = "ts_utc >= {lo:DateTime} AND ts_utc < {hi:DateTime}"
        params["lo"] = f"{args.as_of.isoformat()} 00:00:00"
        params["hi"] = f"{_exclusive_end(args.as_of)} 00:00:00"
    else:
        # Most recent available date in the view for this frequency.
        date_pred = (
            "ts_utc >= (SELECT max(ts_utc) FROM "  # noqa: S608
            f"{fq} WHERE freq = {{f:String}})"
        )

    params["n"] = args.limit
    sql = (
        f"SELECT symbol, {select_aggs} "  # noqa: S608
        f"FROM {fq} "
        "WHERE freq = {f:String} AND indicator IN ({inds:Array(String)}) "
        f"AND {date_pred} "
        "GROUP BY symbol "
        f"HAVING {having_clause} "
        "ORDER BY symbol ASC LIMIT {n:UInt32}"
    )
    params["inds"] = distinct_inds

    out = client.query(sql, parameters=params)
    matches = _rows_to_dicts(out)
    return {
        "frequency": args.frequency,
        "as_of": args.as_of.isoformat() if args.as_of else "latest",
        "filters": [{"indicator": f.indicator, "operator": f.operator, "value": f.value} for f in args.filters],
        "indicators": distinct_inds,
        "count": len(matches),
        "matches": matches,
    }


async def get_correlation_matrix_impl(args: GetCorrelationMatrixInput) -> dict[str, Any]:
    """Return the pairwise correlation matrix of closing prices.

    Reads each symbol's close series over [start, end] and computes the
    correlation in Python (Pearson or Spearman) — no heavy server-side join.
    """
    client = get_client()
    db = client.database
    fq = f"{db}.{bars_table(args.frequency)}"

    series: dict[str, dict[str, float]] = {}
    for sym in args.symbols:
        sql = (
            f"SELECT toString(toDate(ts_utc)) AS d, close "  # noqa: S608
            f"FROM {fq} "
            "WHERE symbol = {s:String} "
            "AND ts_utc >= {lo:DateTime} AND ts_utc < {hi:DateTime} "
            "ORDER BY ts_utc ASC"
        )
        params = {
            "s": sym,
            "lo": f"{args.start.isoformat()} 00:00:00",
            "hi": f"{_exclusive_end(args.end)} 00:00:00",
        }
        out = client.query(sql, parameters=params)
        series[sym] = {str(row[0]): float(row[1]) for row in out["rows"] if row[1] is not None}

    matrix = _build_correlation_matrix(args.symbols, series, method=args.method)
    return {
        "symbols": list(args.symbols),
        "frequency": args.frequency,
        "method": args.method,
        "start": args.start.isoformat(),
        "end": args.end.isoformat(),
        "matrix": matrix,
    }


# ---------------------------------------------------------------------------
# Pure helpers (no ClickHouse).
# ---------------------------------------------------------------------------


def _exclusive_end(d: date) -> str:
    """Return an end-exclusive upper bound (the day after *d*) as ISO date.

    OHLCV ranges are inclusive of the *end* date, so we query ``< end+1``.
    """
    return (d + timedelta(days=1)).isoformat()


def _aligned_returns(
    a: dict[str, float],
    b: dict[str, float],
) -> tuple[list[float], list[float]]:
    """Return paired daily simple returns over the dates *a* and *b* share."""
    common = sorted(set(a) & set(b))
    a_vals = [a[d] for d in common]
    b_vals = [b[d] for d in common]
    a_ret = _simple_returns(a_vals)
    b_ret = _simple_returns(b_vals)
    return a_ret, b_ret


def _simple_returns(prices: list[float]) -> list[float]:
    out: list[float] = []
    for prev, cur in itertools.pairwise(prices):
        if prev == 0:
            out.append(0.0)
        else:
            out.append((cur - prev) / prev)
    return out


def _rank(values: list[float]) -> list[float]:
    """Average-rank transform (for Spearman)."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _pearson(x: list[float], y: list[float]) -> float | None:
    n = len(x)
    if n < 2:
        return None
    mean_x = sum(x) / n
    mean_y = sum(y) / n
    cov = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y, strict=False))
    var_x = sum((xi - mean_x) ** 2 for xi in x)
    var_y = sum((yi - mean_y) ** 2 for yi in y)
    denom = (var_x * var_y) ** 0.5
    if denom == 0:
        return None
    return float(cov / denom)


def _correlation(x: list[float], y: list[float], *, method: str) -> float | None:
    if method == "spearman":
        return _pearson(_rank(x), _rank(y))
    return _pearson(x, y)


def _build_correlation_matrix(
    symbols: list[str],
    series: dict[str, dict[str, float]],
    *,
    method: str,
) -> dict[str, dict[str, float | None]]:
    matrix: dict[str, dict[str, float | None]] = {}
    for a in symbols:
        matrix[a] = {}
        for b in symbols:
            if a == b:
                matrix[a][b] = 1.0
                continue
            a_ret, b_ret = _aligned_returns(series.get(a, {}), series.get(b, {}))
            corr = _correlation(a_ret, b_ret, method=method)
            matrix[a][b] = round(corr, 6) if corr is not None else None
    return matrix


__all__ = [
    "get_correlation_matrix_impl",
    "get_indicators_impl",
    "get_ohlcv_impl",
    "screen_stocks_impl",
]
