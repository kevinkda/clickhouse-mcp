"""Read-only ``clickhouse_connect`` wrapper.

Security posture (financial data — see docs/THREAT_MODEL.md):

* **Dedicated read-only account.** Connection params come from
  ``CLICKHOUSE_MCP_HOST`` / ``_HTTP_PORT`` / ``_USER`` / ``_PASSWORD`` /
  ``_DATABASE`` env vars, read once at construction and never persisted,
  logged, or rendered through ``repr``.  Operators are instructed
  (``.env.example`` + docs) to point ``_USER`` at a ClickHouse account
  created with ``readonly = 1`` and ``GRANT SELECT`` only.
* **Defence in depth — readonly=1.** Even if the configured account had
  write grants, every query is issued with the ClickHouse session setting
  ``readonly=1`` plus ``max_execution_time`` / ``max_result_rows``
  guardrails, so a runaway or mutating query is rejected server-side.
* **Parameterised, never concatenated.** Structured tools bind every
  symbol / date / limit as a ClickHouse query parameter (``{x:String}``
  style); user input never lands in the SQL string.  Table names are
  module-private constants selected from a frequency allow-list.
* **SSRF-safe.** The ClickHouse host is read from env at startup and is
  never derived from tool input — no tool argument can redirect the
  outbound connection to an attacker-controlled host or the cloud
  metadata endpoint.
* **Raw SQL is opt-in.** :meth:`run_safe_sql` is gated behind
  ``CLICKHOUSE_MCP_ALLOW_RAW_SQL=true`` and, even when enabled, enforces
  SELECT-only single statements, rejects DDL/DML, forces a ``LIMIT``, and
  inherits the ``readonly=1`` + resource guardrails.

``clickhouse_connect`` is imported lazily inside :meth:`_connect` so tests
can monkeypatch the importer to inject a fake client without a live
ClickHouse, and so import-time failures surface a friendly message.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from typing import Any, Final

from .errors import (
    ChConfigurationError,
    ChConnectionError,
    ChNotAllowedError,
    ChQueryError,
)
from .models import ALLOWED_INDICATORS

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Environment knobs
# ---------------------------------------------------------------------------

ENV_HOST: Final[str] = "CLICKHOUSE_MCP_HOST"
ENV_PORT: Final[str] = "CLICKHOUSE_MCP_HTTP_PORT"
ENV_USER: Final[str] = "CLICKHOUSE_MCP_USER"
ENV_PASSWORD: Final[str] = "CLICKHOUSE_MCP_PASSWORD"
ENV_DATABASE: Final[str] = "CLICKHOUSE_MCP_DATABASE"
ENV_SECURE: Final[str] = "CLICKHOUSE_MCP_SECURE"
ENV_CONNECT_TIMEOUT: Final[str] = "CLICKHOUSE_MCP_CONNECT_TIMEOUT"
ENV_MAX_EXECUTION_TIME: Final[str] = "CLICKHOUSE_MCP_MAX_EXECUTION_TIME"
ENV_MAX_RESULT_ROWS: Final[str] = "CLICKHOUSE_MCP_MAX_RESULT_ROWS"
ENV_ALLOW_RAW_SQL: Final[str] = "CLICKHOUSE_MCP_ALLOW_RAW_SQL"

DEFAULT_PORT: Final[int] = 8123
DEFAULT_DATABASE: Final[str] = "usa"
DEFAULT_CONNECT_TIMEOUT: Final[int] = 5
DEFAULT_MAX_EXECUTION_TIME: Final[int] = 30
DEFAULT_MAX_RESULT_ROWS: Final[int] = 100_000

# ---------------------------------------------------------------------------
# Frequency → real ClickHouse table mapping (USA warehouse schema).
#
# 1m reads route through the ``usa.bars_1m_full`` UNION view (history parquet
# + live incremental); the L1 aggregates are dedicated parquet link tables.
# Keys are the user-facing frequencies validated by models.Frequency, so a
# value reaching here is always one of the allow-listed keys.
# ---------------------------------------------------------------------------

_FREQ_TO_BARS_TABLE: Final[dict[str, str]] = {
    "1m": "bars_1m_full",
    "5m": "bars_5m_l1",
    "15m": "bars_15m_l1",
    "1h": "bars_1h_l1",
    "1d": "bars_1d_l1",
    "1w": "bars_1w_l1",
}

#: Real CH ``freq`` enum value stored on bar rows (USA schema uses verbose
#: enum labels). Used only for the 1m UNION-view path which keys on ``freq``.
_FREQ_TO_CH_ENUM: Final[dict[str, str]] = {
    "1m": "EVERY_MINUTE",
    "5m": "EVERY_FIVE_MINUTES",
    "15m": "EVERY_FIFTEEN_MINUTES",
    "1h": "EVERY_HOUR",
    "1d": "DAILY",
    "1w": "WEEKLY",
}

#: L2 indicator view — **wide format**: one row per ``symbol`` / ``ts_utc`` /
#: ``freq``, with each technical indicator as its own ``Nullable(Float64)``
#: column (``ma20``, ``macd_hist``, ``rsi14`` …).  Verified against the live
#: ``DESCRIBE usa.indicators_l2``.
_INDICATORS_VIEW: Final[str] = "indicators_l2"

#: ``freq`` values stored on ``indicators_l2`` rows.  Unlike the bars tables
#: (verbose ``DAILY`` / ``WEEKLY`` enums), this view stores the *short*
#: user-facing labels verbatim, and only the daily/weekly cadences are
#: materialised.  Keys are the subset of ``models.Frequency`` the view serves.
_FREQ_TO_INDICATORS_FREQ: Final[dict[str, str]] = {
    "1d": "1d",
    "1w": "1w",
}

#: ClickHouse server-side bug workaround: on this view a ``WHERE`` predicate on
#: the ``LowCardinality(String)`` ``freq`` column combined with a ``symbol``
#: predicate is moved into ``PREWHERE`` by the optimiser and then fails to
#: resolve (``NOT_FOUND_COLUMN_IN_BLOCK`` / ``THERE_IS_NO_COLUMN``).  Disabling
#: the prewhere move makes the (correct) wide-format query plan succeed.  This
#: is a read-only query hint — it changes nothing about the data.
_INDICATORS_QUERY_SETTINGS: Final[dict[str, Any]] = {"optimize_move_to_prewhere": 0}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _truthy(raw: str | None, *, default: bool) -> bool:
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def raw_sql_allowed() -> bool:
    """Whether the raw-SQL escape hatch is enabled (default **False**)."""
    return _truthy(os.environ.get(ENV_ALLOW_RAW_SQL), default=False)


# ---------------------------------------------------------------------------
# Safe-SQL gate (used by run_safe_sql).
# ---------------------------------------------------------------------------

#: Mutating / DDL / dangerous keywords rejected outright (word-boundary match,
#: case-insensitive).  ClickHouse readonly=1 is the hard guarantee; this gate
#: is defence-in-depth that also gives a clear, early error.
_FORBIDDEN_SQL_KEYWORDS: Final[tuple[str, ...]] = (
    "INSERT",
    "UPDATE",
    "DELETE",
    "ALTER",
    "DROP",
    "CREATE",
    "TRUNCATE",
    "RENAME",
    "ATTACH",
    "DETACH",
    "OPTIMIZE",
    "GRANT",
    "REVOKE",
    "SET",
    "SYSTEM",
    "KILL",
    "EXCHANGE",
    "MOVE",
    "FREEZE",
    "RESTORE",
)

_FORBIDDEN_RE: Final[re.Pattern[str]] = re.compile(
    r"(?i)\b(" + "|".join(_FORBIDDEN_SQL_KEYWORDS) + r")\b",
)

_LIMIT_RE: Final[re.Pattern[str]] = re.compile(r"(?i)\blimit\b")
_COMMENT_RE: Final[re.Pattern[str]] = re.compile(r"--|/\*|\*/|#")


def validate_safe_sql(query: str, *, default_limit: int) -> str:
    """Validate + normalise a raw SELECT for ``run_safe_sql``.

    Raises :class:`ChNotAllowedError` if the query is not a single read-only
    ``SELECT`` (or ``WITH ... SELECT``).  Returns the (possibly
    ``LIMIT``-augmented) query when it passes.  This runs **in addition to**
    the server-side ``readonly=1`` enforcement.
    """
    stripped = query.strip().rstrip(";").strip()
    if not stripped:
        raise ChNotAllowedError(reason="empty query")
    # Reject multi-statement payloads (a remaining semicolon means a second
    # statement was smuggled in).
    if ";" in stripped:
        raise ChNotAllowedError(reason="multiple statements are not allowed")
    if _COMMENT_RE.search(stripped):
        raise ChNotAllowedError(reason="SQL comments are not allowed")
    lowered = stripped.lower()
    if not (lowered.startswith(("select", "with"))):
        raise ChNotAllowedError(reason="only single SELECT (or WITH ... SELECT) queries are allowed")
    if _FORBIDDEN_RE.search(stripped):
        raise ChNotAllowedError(reason="DDL/DML and administrative statements are forbidden")
    if not _LIMIT_RE.search(stripped):
        stripped = f"{stripped} LIMIT {default_limit}"
    return stripped


# ---------------------------------------------------------------------------
# Connection settings resolved from env
# ---------------------------------------------------------------------------


class ConnectionSettings:
    """Immutable snapshot of connection knobs resolved from env.

    The password is stored but excluded from ``__repr__`` so a settings
    object can be logged without leaking the credential.
    """

    __slots__ = (
        "_password",
        "connect_timeout",
        "database",
        "host",
        "max_execution_time",
        "max_result_rows",
        "port",
        "secure",
        "user",
    )

    def __init__(self) -> None:
        host = os.environ.get(ENV_HOST, "").strip()
        if not host:
            raise ChConfigurationError(
                hint=(
                    f"{ENV_HOST} is not set. Configure the read-only ClickHouse "
                    f"connection in .env ({ENV_HOST}/{ENV_PORT}/{ENV_USER}/{ENV_PASSWORD})."
                ),
            )
        user = os.environ.get(ENV_USER, "").strip()
        if not user:
            raise ChConfigurationError(
                hint=(
                    f"{ENV_USER} is not set. Use a DEDICATED read-only account "
                    "(readonly=1, GRANT SELECT only) — never an admin user."
                ),
            )
        self.host: str = host
        self.port: int = _env_int(ENV_PORT, DEFAULT_PORT)
        self.user: str = user
        self._password: str = os.environ.get(ENV_PASSWORD, "")
        self.database: str = os.environ.get(ENV_DATABASE, "").strip() or DEFAULT_DATABASE
        self.secure: bool = _truthy(os.environ.get(ENV_SECURE), default=False)
        self.connect_timeout: int = _env_int(ENV_CONNECT_TIMEOUT, DEFAULT_CONNECT_TIMEOUT)
        self.max_execution_time: int = _env_int(ENV_MAX_EXECUTION_TIME, DEFAULT_MAX_EXECUTION_TIME)
        self.max_result_rows: int = _env_int(ENV_MAX_RESULT_ROWS, DEFAULT_MAX_RESULT_ROWS)

    @property
    def password(self) -> str:
        """The credential — access only at connect time, never log it."""
        return self._password

    def __repr__(self) -> str:
        # NEVER include user/password; host:port/db only for diagnostics.
        return f"ConnectionSettings(host={self.host!r}, port={self.port}, database={self.database!r}, secure={self.secure})"


def _import_clickhouse_connect() -> Any:
    """Lazily import ``clickhouse_connect`` with a friendly failure.

    Kept module-level (not inline) so tests can monkeypatch it to inject a
    mock client without a live ClickHouse.
    """
    try:
        import clickhouse_connect
    except ImportError as exc:  # pragma: no cover - clickhouse-connect is a hard dep
        raise ChConnectionError(
            reason="clickhouse-connect is not installed; run `pip install clickhouse-mcp`",
        ) from exc
    return clickhouse_connect


# ---------------------------------------------------------------------------
# Read-only ClickHouse client
# ---------------------------------------------------------------------------


class ClickHouseReadOnlyClient:
    """Read-only query surface over the USA ClickHouse warehouse.

    Lazy-connects on first query and reuses the connection thereafter.  All
    queries carry ``readonly=1`` + execution-time / result-row guardrails.
    """

    def __init__(self, settings: ConnectionSettings | None = None, *, client: Any | None = None) -> None:
        self._settings: ConnectionSettings = settings if settings is not None else ConnectionSettings()
        self._lock = threading.Lock()
        self._client: Any | None = client

    @property
    def settings(self) -> ConnectionSettings:
        return self._settings

    @property
    def database(self) -> str:
        return self._settings.database

    def _connect(self) -> Any:
        module = _import_clickhouse_connect()
        s = self._settings
        try:
            return module.get_client(
                host=s.host,
                port=s.port,
                username=s.user,
                password=s.password,
                database=s.database,
                secure=s.secure,
                connect_timeout=s.connect_timeout,
            )
        except Exception as exc:
            raise ChConnectionError(reason=f"failed to connect to ClickHouse: {type(exc).__name__}") from exc

    def _get_client(self) -> Any:
        with self._lock:
            if self._client is None:
                self._client = self._connect()
            return self._client

    def _query_settings(self) -> dict[str, Any]:
        """ClickHouse session settings applied to every query (read-only + guardrails)."""
        s = self._settings
        return {
            "readonly": 1,
            "max_execution_time": s.max_execution_time,
            "max_result_rows": s.max_result_rows,
            "result_overflow_mode": "throw",
        }

    def query(
        self,
        sql: str,
        *,
        parameters: dict[str, Any] | None = None,
        settings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run a parameterised read-only query; return ``{columns, rows}``.

        *sql* is built only from module-private templates + allow-listed
        table / column names; all user-supplied values arrive via *parameters*
        and are bound by ``clickhouse_connect`` (never concatenated into *sql*).

        *settings* are merged on top of the mandatory read-only guardrails for
        callers that need a per-query optimiser hint (e.g. the indicators view
        prewhere-move workaround).  The read-only / resource-limit guardrails
        always win — a caller cannot relax ``readonly=1`` via *settings*.
        """
        client = self._get_client()
        effective_settings = self._query_settings()
        if settings:
            effective_settings = {**settings, **effective_settings}
        try:
            result = client.query(
                sql,
                parameters=parameters or {},
                settings=effective_settings,
            )
        except Exception as exc:
            raise ChQueryError(reason=f"query failed: {type(exc).__name__}") from exc
        columns = list(getattr(result, "column_names", []) or [])
        raw_rows = getattr(result, "result_rows", None) or []
        rows = [list(r) for r in raw_rows]
        return {"columns": columns, "rows": rows}

    def ping(self) -> bool:
        """Lightweight connectivity probe — ``SELECT 1``.  Never raises."""
        try:
            out = self.query("SELECT 1")
        except Exception:
            return False
        return bool(out["rows"])

    def server_version(self) -> str | None:
        """Best-effort ClickHouse server version string, or ``None``."""
        try:
            out = self.query("SELECT version()")
        except Exception:
            return None
        if out["rows"] and out["rows"][0]:
            return str(out["rows"][0][0])
        return None

    def close(self) -> None:
        with self._lock:
            client = self._client
            self._client = None
        if client is not None:
            try:
                client.close()
            except Exception as exc:  # pragma: no cover - best-effort close
                log.debug("clickhouse client close failed (best-effort): %s", exc)


# ---------------------------------------------------------------------------
# Table / parameter helpers (used by the tools layer).
# ---------------------------------------------------------------------------


def bars_table(frequency: str) -> str:
    """Return the real bars table short-name for a validated *frequency*."""
    return _FREQ_TO_BARS_TABLE[frequency]


def ch_freq_enum(frequency: str) -> str:
    """Return the verbose CH ``freq`` enum value for a validated *frequency*."""
    return _FREQ_TO_CH_ENUM[frequency]


def indicators_view() -> str:
    """Return the L2 indicator view short-name."""
    return _INDICATORS_VIEW


def indicators_freq(frequency: str) -> str:
    """Return the ``freq`` label stored on ``indicators_l2`` for *frequency*.

    Raises :class:`ChQueryError` if the cadence is not materialised in the
    indicator view (only ``1d`` / ``1w`` are).  *frequency* is already one of
    the allow-listed ``models.Frequency`` keys when it reaches here.
    """
    try:
        return _FREQ_TO_INDICATORS_FREQ[frequency]
    except KeyError as exc:
        raise ChQueryError(
            reason=(f"indicators_l2 has no {frequency!r} cadence; supported: {sorted(_FREQ_TO_INDICATORS_FREQ)}"),
        ) from exc


def indicators_query_settings() -> dict[str, Any]:
    """Per-query optimiser hint required for the indicators-view wide-format plan."""
    return dict(_INDICATORS_QUERY_SETTINGS)


def indicator_column(name: str) -> str:
    """Return *name* as a safe SQL identifier for the indicators view.

    *name* MUST already be a member of :data:`models.ALLOWED_INDICATORS` (the
    tools layer validates via the Pydantic model before calling).  This is the
    single, explicit boundary where an allow-listed indicator becomes a SQL
    column identifier — re-checking here means even a future mis-wire cannot
    smuggle an arbitrary identifier into the SELECT list.
    """
    if name not in ALLOWED_INDICATORS:
        raise ChQueryError(reason=f"indicator {name!r} is not in the allow-list")
    return name


__all__ = [
    "DEFAULT_CONNECT_TIMEOUT",
    "DEFAULT_DATABASE",
    "DEFAULT_MAX_EXECUTION_TIME",
    "DEFAULT_MAX_RESULT_ROWS",
    "DEFAULT_PORT",
    "ENV_ALLOW_RAW_SQL",
    "ENV_DATABASE",
    "ENV_HOST",
    "ENV_MAX_EXECUTION_TIME",
    "ENV_MAX_RESULT_ROWS",
    "ENV_PASSWORD",
    "ENV_PORT",
    "ENV_SECURE",
    "ENV_USER",
    "ClickHouseReadOnlyClient",
    "ConnectionSettings",
    "bars_table",
    "ch_freq_enum",
    "indicator_column",
    "indicators_freq",
    "indicators_query_settings",
    "indicators_view",
    "raw_sql_allowed",
    "validate_safe_sql",
]
