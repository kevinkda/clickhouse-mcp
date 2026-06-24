"""Unit tests for the read-only ClickHouse client wrapper."""

from __future__ import annotations

import pytest

from clickhouse_mcp.client import (
    ClickHouseReadOnlyClient,
    ConnectionSettings,
    bars_table,
    ch_freq_enum,
    indicator_column,
    indicators_freq,
    indicators_query_settings,
    indicators_view,
    raw_sql_allowed,
    validate_safe_sql,
)
from clickhouse_mcp.errors import (
    ChConfigurationError,
    ChConnectionError,
    ChNotAllowedError,
    ChQueryError,
)
from tests.conftest import FakeClickHouseClient, FakeQueryResult, make_readonly_client


class TestConnectionSettings:
    def test_defaults(self) -> None:
        s = ConnectionSettings()
        assert s.host == "ch.test.invalid"
        assert s.port == 8123
        assert s.user == "mcp_readonly"
        assert s.database == "usa"
        assert s.secure is False
        assert s.connect_timeout == 5
        assert s.max_execution_time == 30
        assert s.max_result_rows == 100_000

    def test_password_property(self) -> None:
        assert ConnectionSettings().password == "test-pass"  # pragma: allowlist secret

    def test_repr_excludes_credentials(self) -> None:
        s = ConnectionSettings()
        r = repr(s)
        assert "mcp_readonly" not in r
        assert "test-pass" not in r
        assert "ch.test.invalid" in r

    def test_missing_host_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CLICKHOUSE_MCP_HOST", raising=False)
        with pytest.raises(ChConfigurationError):
            ConnectionSettings()

    def test_missing_user_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CLICKHOUSE_MCP_USER", raising=False)
        with pytest.raises(ChConfigurationError):
            ConnectionSettings()

    def test_secure_truthy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLICKHOUSE_MCP_SECURE", "true")
        assert ConnectionSettings().secure is True

    def test_blank_database_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLICKHOUSE_MCP_DATABASE", "   ")
        assert ConnectionSettings().database == "usa"

    @pytest.mark.parametrize(
        ("env", "val", "attr", "expected"),
        [
            ("CLICKHOUSE_MCP_HTTP_PORT", "9000", "port", 9000),
            ("CLICKHOUSE_MCP_HTTP_PORT", "notint", "port", 8123),
            ("CLICKHOUSE_MCP_HTTP_PORT", "0", "port", 8123),
            ("CLICKHOUSE_MCP_MAX_EXECUTION_TIME", "60", "max_execution_time", 60),
            ("CLICKHOUSE_MCP_MAX_RESULT_ROWS", "5", "max_result_rows", 5),
            ("CLICKHOUSE_MCP_CONNECT_TIMEOUT", "", "connect_timeout", 5),
        ],
    )
    def test_env_int_parsing(
        self, monkeypatch: pytest.MonkeyPatch, env: str, val: str, attr: str, expected: int
    ) -> None:
        monkeypatch.setenv(env, val)
        assert getattr(ConnectionSettings(), attr) == expected


class TestRawSqlAllowed:
    def test_default_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CLICKHOUSE_MCP_ALLOW_RAW_SQL", raising=False)
        assert raw_sql_allowed() is False

    @pytest.mark.parametrize("val", ["true", "1", "yes", "on", "TRUE"])
    def test_truthy_values(self, monkeypatch: pytest.MonkeyPatch, val: str) -> None:
        monkeypatch.setenv("CLICKHOUSE_MCP_ALLOW_RAW_SQL", val)
        assert raw_sql_allowed() is True

    @pytest.mark.parametrize("val", ["false", "0", "no", "off", "garbage"])
    def test_falsey_values(self, monkeypatch: pytest.MonkeyPatch, val: str) -> None:
        monkeypatch.setenv("CLICKHOUSE_MCP_ALLOW_RAW_SQL", val)
        assert raw_sql_allowed() is False


class TestValidateSafeSql:
    def test_simple_select_gets_limit(self) -> None:
        out = validate_safe_sql("SELECT 1", default_limit=10)
        assert out == "SELECT 1 LIMIT 10"

    def test_with_select_allowed(self) -> None:
        out = validate_safe_sql("WITH x AS (SELECT 1) SELECT * FROM x LIMIT 5", default_limit=10)
        assert out.lower().startswith("with")

    def test_existing_limit_preserved(self) -> None:
        out = validate_safe_sql("SELECT 1 LIMIT 3", default_limit=10)
        assert out == "SELECT 1 LIMIT 3"

    def test_trailing_semicolon_stripped(self) -> None:
        out = validate_safe_sql("SELECT 1;", default_limit=10)
        assert ";" not in out

    def test_empty_rejected(self) -> None:
        with pytest.raises(ChNotAllowedError):
            validate_safe_sql("   ", default_limit=10)

    def test_multiple_statements_rejected(self) -> None:
        with pytest.raises(ChNotAllowedError):
            validate_safe_sql("SELECT 1; SELECT 2", default_limit=10)

    def test_comment_rejected(self) -> None:
        with pytest.raises(ChNotAllowedError):
            validate_safe_sql("SELECT 1 -- sneaky", default_limit=10)

    def test_non_select_rejected(self) -> None:
        with pytest.raises(ChNotAllowedError):
            validate_safe_sql("SHOW TABLES", default_limit=10)

    @pytest.mark.parametrize(
        "sql",
        [
            "INSERT INTO t VALUES (1)",
            "UPDATE t SET x=1",
            "DELETE FROM t",
            "DROP TABLE t",
            "ALTER TABLE t ADD COLUMN c Int",
            "CREATE TABLE t (x Int) ENGINE=Memory",
            "TRUNCATE TABLE t",
            "GRANT SELECT ON t TO u",
            "SYSTEM RELOAD CONFIG",
        ],
    )
    def test_ddl_dml_rejected(self, sql: str) -> None:
        with pytest.raises(ChNotAllowedError):
            validate_safe_sql(sql, default_limit=10)

    def test_select_with_forbidden_keyword_in_body_rejected(self) -> None:
        # A SELECT that smuggles a forbidden keyword is still rejected.
        with pytest.raises(ChNotAllowedError):
            validate_safe_sql("SELECT 1 FROM t WHERE x IN (DROP)", default_limit=10)


class TestFreqHelpers:
    @pytest.mark.parametrize(
        ("freq", "table"),
        [
            ("1m", "bars_1m_full"),
            ("5m", "bars_5m_l1"),
            ("15m", "bars_15m_l1"),
            ("1h", "bars_1h_l1"),
            ("1d", "bars_1d_l1"),
            ("1w", "bars_1w_l1"),
        ],
    )
    def test_bars_table(self, freq: str, table: str) -> None:
        assert bars_table(freq) == table

    def test_ch_freq_enum(self) -> None:
        assert ch_freq_enum("1m") == "EVERY_MINUTE"
        assert ch_freq_enum("1d") == "DAILY"

    def test_indicators_view(self) -> None:
        assert indicators_view() == "indicators_l2"

    @pytest.mark.parametrize(("freq", "label"), [("1d", "1d"), ("1w", "1w")])
    def test_indicators_freq_supported(self, freq: str, label: str) -> None:
        assert indicators_freq(freq) == label

    @pytest.mark.parametrize("freq", ["1m", "5m", "15m", "1h"])
    def test_indicators_freq_unsupported_raises(self, freq: str) -> None:
        with pytest.raises(ChQueryError):
            indicators_freq(freq)

    def test_indicators_query_settings_disables_prewhere_move(self) -> None:
        assert indicators_query_settings() == {"optimize_move_to_prewhere": 0}

    @pytest.mark.parametrize("col", ["ma20", "macd_hist", "rsi14", "bb_low", "kdj_j", "obv", "vwap"])
    def test_indicator_column_allowlisted_passes_through(self, col: str) -> None:
        assert indicator_column(col) == col

    @pytest.mark.parametrize("col", ["ma9999", "evil; DROP", "atr14", "boll_up"])
    def test_indicator_column_rejects_non_allowlisted(self, col: str) -> None:
        with pytest.raises(ChQueryError):
            indicator_column(col)


class TestClientQuery:
    def test_query_returns_columns_and_rows(self) -> None:
        fake = FakeClickHouseClient(responses=[FakeQueryResult(["a", "b"], [[1, 2], [3, 4]])])
        client = make_readonly_client(fake)
        out = client.query("SELECT a, b FROM t", parameters={"x": 1})
        assert out == {"columns": ["a", "b"], "rows": [[1, 2], [3, 4]]}

    def test_query_applies_readonly_and_guardrails(self) -> None:
        fake = FakeClickHouseClient(responses=[FakeQueryResult([], [])])
        client = make_readonly_client(fake)
        client.query("SELECT 1")
        _, _, settings = fake.queries[0]
        assert settings["readonly"] == 1
        assert settings["max_execution_time"] == 30
        assert settings["max_result_rows"] == 100_000
        assert settings["result_overflow_mode"] == "throw"

    def test_query_merges_custom_settings_but_guardrails_win(self) -> None:
        fake = FakeClickHouseClient(responses=[FakeQueryResult([], [])])
        client = make_readonly_client(fake)
        # Caller-supplied optimiser hint survives; an attempt to relax readonly
        # via settings must be overridden by the mandatory guardrail.
        client.query("SELECT 1", settings={"optimize_move_to_prewhere": 0, "readonly": 0})
        _, _, settings = fake.queries[0]
        assert settings["optimize_move_to_prewhere"] == 0
        assert settings["readonly"] == 1  # guardrail wins over caller override

    def test_query_error_wrapped(self) -> None:
        fake = FakeClickHouseClient(raise_on_query=RuntimeError("boom"))
        client = make_readonly_client(fake)
        with pytest.raises(ChQueryError):
            client.query("SELECT 1")

    def test_query_handles_none_result_rows(self) -> None:
        fake = FakeClickHouseClient(responses=[FakeQueryResult([], None)])  # type: ignore[arg-type]
        client = make_readonly_client(fake)
        assert client.query("SELECT 1") == {"columns": [], "rows": []}


class TestClientProbes:
    def test_ping_true(self) -> None:
        fake = FakeClickHouseClient(responses=[FakeQueryResult(["1"], [[1]])])
        assert make_readonly_client(fake).ping() is True

    def test_ping_false_on_error(self) -> None:
        fake = FakeClickHouseClient(raise_on_query=RuntimeError("x"))
        assert make_readonly_client(fake).ping() is False

    def test_ping_false_on_empty(self) -> None:
        fake = FakeClickHouseClient(responses=[FakeQueryResult([], [])])
        assert make_readonly_client(fake).ping() is False

    def test_server_version(self) -> None:
        fake = FakeClickHouseClient(responses=[FakeQueryResult(["v"], [["26.5.1.882"]])])
        assert make_readonly_client(fake).server_version() == "26.5.1.882"

    def test_server_version_none_on_error(self) -> None:
        fake = FakeClickHouseClient(raise_on_query=RuntimeError("x"))
        assert make_readonly_client(fake).server_version() is None

    def test_server_version_none_on_empty(self) -> None:
        fake = FakeClickHouseClient(responses=[FakeQueryResult([], [])])
        assert make_readonly_client(fake).server_version() is None


class TestClientLifecycle:
    def test_database_property(self) -> None:
        fake = FakeClickHouseClient()
        assert make_readonly_client(fake).database == "usa"

    def test_settings_property(self) -> None:
        fake = FakeClickHouseClient()
        client = make_readonly_client(fake)
        assert client.settings.host == "ch.test.invalid"

    def test_close_calls_underlying(self) -> None:
        fake = FakeClickHouseClient()
        client = make_readonly_client(fake)
        client.close()
        assert fake.closed is True

    def test_close_when_no_client_noop(self) -> None:
        client = ClickHouseReadOnlyClient(client=None)  # settings from env
        client.close()  # should not raise

    def test_lazy_connect_uses_injected_client(self) -> None:
        fake = FakeClickHouseClient(responses=[FakeQueryResult([], [])])
        client = make_readonly_client(fake)
        client.query("SELECT 1")
        # Re-query reuses the same fake (no reconnect path triggered).
        client.query("SELECT 1")
        assert len(fake.queries) == 2


class TestConnectPath:
    def test_connect_invokes_get_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, object] = {}

        class _Module:
            @staticmethod
            def get_client(**kwargs: object):  # type: ignore[no-untyped-def]
                captured.update(kwargs)
                return FakeClickHouseClient(responses=[FakeQueryResult(["1"], [[1]])])

        monkeypatch.setattr("clickhouse_mcp.client._import_clickhouse_connect", lambda: _Module)
        client = ClickHouseReadOnlyClient()  # no injected client -> lazy connect
        assert client.ping() is True
        assert captured["host"] == "ch.test.invalid"
        assert captured["username"] == "mcp_readonly"
        assert captured["database"] == "usa"

    def test_connect_failure_wrapped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _Module:
            @staticmethod
            def get_client(**kwargs: object):  # type: ignore[no-untyped-def]
                raise OSError("refused")

        monkeypatch.setattr("clickhouse_mcp.client._import_clickhouse_connect", lambda: _Module)
        client = ClickHouseReadOnlyClient()
        with pytest.raises(ChConnectionError):
            client.query("SELECT 1")
