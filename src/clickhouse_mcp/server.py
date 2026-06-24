"""FastMCP server entry point — 7 read-only ClickHouse tools.

The first thing this module does is harden stdio so no stray ``print`` /
log line pollutes the JSON-RPC stream:

* monkey-patch ``builtins.print`` so the default ``file`` is ``sys.stderr``;
* install a :class:`RotatingFileHandler` writing to
  ``${XDG_STATE_HOME}/clickhouse-mcp/logs/server.log``;
* force ``clickhouse_connect`` / ``urllib3`` to ``WARNING``.

The server is **read-only**: it exposes no tool that writes to ClickHouse,
and every query is executed with ``readonly=1`` + resource guardrails (see
:mod:`clickhouse_mcp.client`).
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 0) Stdio hardening — must run BEFORE we import anything that might log /
#    print at import time.
# ---------------------------------------------------------------------------
import builtins
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any


def _harden_stdio() -> None:
    """Install the print + logging mitigations."""
    _orig_print = builtins.print

    def _safe_print(*args: Any, file: Any = None, **kwargs: Any) -> None:
        _orig_print(*args, file=file or sys.stderr, **kwargs)

    builtins.print = _safe_print

    from . import _platform

    log_dir: Path | None = _platform.state_root() / "clickhouse-mcp" / "logs"
    try:
        assert log_dir is not None
        with _platform.restrictive_umask():
            log_dir.mkdir(parents=True, exist_ok=True)
        if not _platform.IS_WINDOWS:  # pragma: no branch - POSIX-only chmod; Windows side N/A in CI
            _platform.secure_chmod(log_dir, 0o700)
    except OSError:
        log_dir = None

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_dir is not None:
        try:
            file_handler = RotatingFileHandler(
                log_dir / "server.log",
                maxBytes=10 * 1024 * 1024,
                backupCount=5,
                encoding="utf-8",
            )
            file_handler.setFormatter(
                logging.Formatter('{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":%(message)r}')
            )
            handlers.append(file_handler)
        except OSError:
            pass

    level = os.environ.get("LOG_LEVEL", "WARNING").upper()
    logging.basicConfig(
        handlers=handlers,
        level=getattr(logging, level, logging.WARNING),
        format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":%(message)r}',
        force=True,
    )
    for noisy in ("clickhouse_connect", "urllib3", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


_harden_stdio()


# ---------------------------------------------------------------------------
# 0b) Load .env from the current working directory.  Host-injected env vars
#     win because ``override=False``.
# ---------------------------------------------------------------------------
def _bootstrap_dotenv() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(override=False)
    except ImportError:  # pragma: no cover
        pass


_bootstrap_dotenv()


# ---------------------------------------------------------------------------
# Imports after hardening
# ---------------------------------------------------------------------------

from typing import Final  # noqa: E402

from mcp.server.fastmcp import FastMCP  # noqa: E402

from . import __version__ as SERVER_VERSION  # noqa: E402
from .errors import (  # noqa: E402
    ChConfigurationError,
    ChConnectionError,
    ChError,
    ChNotAllowedError,
    ChQueryError,
    ChValidationError,
)
from .models import (  # noqa: E402
    GetCorrelationMatrixInput,
    GetIndicatorsInput,
    GetOhlcvInput,
    RunSafeSqlInput,
    ScreenFilter,
    ScreenStocksInput,
)
from .tools import market, meta, rawsql  # noqa: E402

log = logging.getLogger("clickhouse_mcp.server")

SERVER_NAME: Final[str] = "clickhouse-mcp"


# ---------------------------------------------------------------------------
# Error framing — convert structured exceptions to JSON-friendly dicts so the
# MCP client surfaces actionable messages instead of stack traces.  Free-text
# fields are already credential-redacted at construction time.
# ---------------------------------------------------------------------------


def _frame_error(exc: BaseException) -> dict[str, Any]:
    """Convert any exception into a structured error envelope."""
    if isinstance(exc, ChValidationError):
        return {"error": "validation", "field": exc.field, "reason": exc.reason}
    if isinstance(exc, ChConfigurationError):
        return {"error": "configuration", "hint": exc.hint}
    if isinstance(exc, ChNotAllowedError):
        return {"error": "not_allowed", "reason": exc.reason}
    if isinstance(exc, ChConnectionError):
        return {"error": "connection", "reason": exc.reason}
    if isinstance(exc, ChQueryError):
        return {"error": "query", "reason": exc.reason}
    if isinstance(exc, ChError):
        return {"error": "clickhouse_error", "type": type(exc).__name__}
    return {"error": "internal", "type": type(exc).__name__}


# ---------------------------------------------------------------------------
# FastMCP wiring
# ---------------------------------------------------------------------------


def _build_mcp() -> FastMCP:
    mcp_app = FastMCP(SERVER_NAME)

    # FastMCP ctor does not expose a ``version=`` kwarg, so inject the project
    # release tag directly on the lowlevel server so ``serverInfo.version``
    # reflects this package's ``__version__`` (G2 version-desync guard).
    mcp_app._mcp_server.version = SERVER_VERSION

    @mcp_app.tool()
    async def get_ohlcv(
        symbol: str,
        start: str,
        end: str,
        frequency: str = "1d",
        limit: int = 1000,
    ) -> dict[str, Any]:
        """Return OHLCV bars for one symbol over [start, end] at *frequency*.

        ``frequency`` is one of 1m/5m/15m/1h/1d/1w. ``start`` / ``end`` are
        ISO dates (YYYY-MM-DD), inclusive.
        """
        try:
            args = GetOhlcvInput(
                symbol=symbol,
                start=start,  # type: ignore[arg-type]
                end=end,  # type: ignore[arg-type]
                frequency=frequency,  # type: ignore[arg-type]
                limit=limit,
            )
            return await market.get_ohlcv_impl(args)
        except ChError as exc:
            return _frame_error(exc)

    @mcp_app.tool()
    async def get_indicators(
        symbol: str,
        indicator: str,
        start: str,
        end: str,
        frequency: str = "1d",
        limit: int = 1000,
    ) -> dict[str, Any]:
        """Return one technical-indicator series (e.g. ma20, rsi14, macd_hist)."""
        try:
            args = GetIndicatorsInput(
                symbol=symbol,
                indicator=indicator,
                start=start,  # type: ignore[arg-type]
                end=end,  # type: ignore[arg-type]
                frequency=frequency,  # type: ignore[arg-type]
                limit=limit,
            )
            return await market.get_indicators_impl(args)
        except ChError as exc:
            return _frame_error(exc)

    @mcp_app.tool()
    async def screen_stocks(
        filters: list[dict[str, Any]],
        as_of: str | None = None,
        frequency: str = "1d",
        limit: int = 100,
    ) -> dict[str, Any]:
        """Full-market technical-indicator scan.

        ``filters`` is a list of ``{indicator, operator, value}`` where
        operator is one of gt/gte/lt/lte/eq (e.g.
        ``[{"indicator": "rsi14", "operator": "lt", "value": 30}]`` for an
        oversold screen). ``as_of`` is an ISO date or omitted for latest.
        """
        try:
            parsed = [ScreenFilter(**f) for f in filters]
            args = ScreenStocksInput(
                filters=parsed,
                as_of=as_of,  # type: ignore[arg-type]
                frequency=frequency,  # type: ignore[arg-type]
                limit=limit,
            )
            return await market.screen_stocks_impl(args)
        except ChError as exc:
            return _frame_error(exc)

    @mcp_app.tool()
    async def get_correlation_matrix(
        symbols: list[str],
        start: str,
        end: str,
        frequency: str = "1d",
        method: str = "pearson",
    ) -> dict[str, Any]:
        """Return the pairwise return-correlation matrix for 2-50 symbols."""
        try:
            args = GetCorrelationMatrixInput(
                symbols=symbols,
                start=start,  # type: ignore[arg-type]
                end=end,  # type: ignore[arg-type]
                frequency=frequency,  # type: ignore[arg-type]
                method=method,  # type: ignore[arg-type]
            )
            return await market.get_correlation_matrix_impl(args)
        except ChError as exc:
            return _frame_error(exc)

    @mcp_app.tool()
    async def run_safe_sql(query: str, limit: int = 1000) -> dict[str, Any]:
        """Run a single read-only SELECT (DISABLED by default).

        Off unless CLICKHOUSE_MCP_ALLOW_RAW_SQL=true. Even when enabled only
        single read-only SELECT statements are allowed (readonly=1, forced
        LIMIT, execution-time + result-row guardrails). Prefer the structured
        tools.
        """
        try:
            args = RunSafeSqlInput(query=query, limit=limit)
            return await rawsql.run_safe_sql_impl(args)
        except ChError as exc:
            return _frame_error(exc)

    @mcp_app.tool()
    async def health_check() -> dict[str, Any]:
        """Local health probe + optional ClickHouse reachability check."""
        return await meta.health_check_impl()

    @mcp_app.tool()
    async def get_server_info() -> dict[str, Any]:
        """Local server metadata.  Never calls ClickHouse."""
        return await meta.get_server_info_impl(server_version=SERVER_VERSION)

    return mcp_app


# Lazy build so test collection (which imports server) doesn't fail when stdio
# is already connected to pytest's capture.
_app: FastMCP | None = None


def app() -> FastMCP:
    global _app
    if _app is None:
        _app = _build_mcp()
    return _app


def main() -> None:
    """Console-script entry point."""
    log.warning('{"event":"server_start","version":"%s","read_only":true}', SERVER_VERSION)
    app().run()


__all__ = [
    "SERVER_NAME",
    "SERVER_VERSION",
    "app",
    "main",
]
