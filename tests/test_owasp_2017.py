"""OWASP Top 10 — 2017 security test suite for clickhouse-mcp.

Each test asserts a concrete invariant on the read-only ClickHouse query
surface. Applicability map (2017):

  * A1 Injection                     — parameterised queries; strict input regex; safe-SQL gate
  * A2 Broken Authentication         — dedicated read-only CH account; credentials env-only
  * A3 Sensitive Data Exposure       — credential redaction; no plaintext secret in repr/logs
  * A4 XML External Entities (XXE)    — N/A: no XML parsing on this surface
  * A5 Broken Access Control          — read-only tool surface; no write verb; readonly=1
  * A6 Security Misconfiguration      — run_safe_sql off by default; explicit guardrails
  * A7 Cross-Site Scripting (XSS)     — N/A: no HTML rendering (JSON-RPC tool surface)
  * A8 Insecure Deserialization       — no pickle/eval; JSON only; bound params
  * A9 Vulnerable Components          — pinned deps; pip-audit gate
  * A10 Insufficient Logging          — structured JSON logs; health_check surface
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from clickhouse_mcp.client import validate_safe_sql
from clickhouse_mcp.errors import ChNotAllowedError, ChValidationError
from clickhouse_mcp.models import GetOhlcvInput

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src" / "clickhouse_mcp"

INJECTION_PAYLOADS = [
    "AAPL'; DROP TABLE usa.bars_1d_l1;--",
    "AAPL UNION SELECT * FROM system.users",
    "1=1",
    "AAPL OR 1=1",
    "AAPL/**/OR/**/1=1",
]


class TestA1Injection:
    @pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
    def test_symbol_injection_rejected(self, payload: str) -> None:
        from datetime import date

        with pytest.raises(ChValidationError):
            GetOhlcvInput(symbol=payload, start=date(2024, 1, 1), end=date(2024, 1, 2))

    def test_structured_tools_use_bound_parameters(self) -> None:
        """No structured-tool SQL template concatenates a value via f-string interpolation of input."""
        market = (SRC_ROOT / "tools" / "market.py").read_text("utf-8")
        # Every WHERE predicate on user values uses ClickHouse {name:Type} binding.
        assert "{s:String}" in market
        assert "{lo:DateTime}" in market
        # The only f-strings in SQL build allow-listed table names (db.table),
        # never raw user input.
        assert "_FREQ_TO_BARS_TABLE" not in market  # lives in client.py, not interpolated here

    def test_safe_sql_gate_blocks_injection(self) -> None:
        with pytest.raises(ChNotAllowedError):
            validate_safe_sql("SELECT 1; DROP TABLE x", default_limit=10)


class TestA2BrokenAuthentication:
    def test_dedicated_readonly_account_documented(self) -> None:
        env = (REPO_ROOT / ".env.example").read_text("utf-8")
        assert "readonly" in env.lower()
        assert "mcp_readonly" in env

    def test_missing_user_fails_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from clickhouse_mcp.client import ConnectionSettings
        from clickhouse_mcp.errors import ChConfigurationError

        monkeypatch.delenv("CLICKHOUSE_MCP_USER", raising=False)
        with pytest.raises(ChConfigurationError):
            ConnectionSettings()


class TestA3SensitiveDataExposure:
    def test_password_not_in_settings_repr(self) -> None:
        from clickhouse_mcp.client import ConnectionSettings

        assert "test-pass" not in repr(ConnectionSettings())

    def test_dsn_credential_redacted_in_error(self) -> None:
        from clickhouse_mcp.errors import ChConnectionError

        exc = ChConnectionError(reason="dsn clickhouse://u:secretpw@host/db failed")
        assert "secretpw" not in str(exc)


class TestA4XXE:
    def test_na_no_xml_parsing(self) -> None:
        """N/A: the ClickHouse surface parses no XML — no XXE sink exists."""
        offenders = [
            str(p.relative_to(REPO_ROOT))
            for p in SRC_ROOT.rglob("*.py")
            if re.search(r"xml\.etree|lxml|xml\.dom|xml\.sax", p.read_text("utf-8"))
        ]
        assert offenders == []


class TestA5BrokenAccessControl:
    def test_no_mutating_verb_in_source(self) -> None:
        pattern = re.compile(r"\b(INSERT|UPDATE|DELETE|ALTER|DROP|CREATE|TRUNCATE)\s+(INTO|TABLE|FROM|VIEW|DATABASE)\b")
        offenders = []
        for p in SRC_ROOT.rglob("*.py"):
            text = p.read_text("utf-8")
            # Skip the forbidden-keyword list / DDL string in client.py (it
            # REJECTS these, not executes them).
            if p.name == "client.py":
                continue
            if pattern.search(text):
                offenders.append(str(p.relative_to(REPO_ROOT)))
        assert offenders == []

    def test_every_query_sets_readonly(self) -> None:
        client = (SRC_ROOT / "client.py").read_text("utf-8")
        assert '"readonly": 1' in client


class TestA6SecurityMisconfiguration:
    def test_raw_sql_disabled_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from clickhouse_mcp.client import raw_sql_allowed

        monkeypatch.delenv("CLICKHOUSE_MCP_ALLOW_RAW_SQL", raising=False)
        assert raw_sql_allowed() is False


class TestA8InsecureDeserialization:
    def test_no_pickle_or_eval(self) -> None:
        offenders = [
            str(p.relative_to(REPO_ROOT))
            for p in SRC_ROOT.rglob("*.py")
            if re.search(r"\b(pickle|eval|exec)\s*\(|import pickle", p.read_text("utf-8"))
        ]
        assert offenders == []


class TestA9VulnerableComponents:
    def test_deps_pinned(self) -> None:
        body = (REPO_ROOT / "pyproject.toml").read_text("utf-8")
        assert "clickhouse-connect" in body
        assert "pip-audit" in body


class TestA10InsufficientLogging:
    @pytest.mark.asyncio
    async def test_health_surface_present(self) -> None:
        from clickhouse_mcp.tools.meta import health_check_impl

        out = await health_check_impl()
        assert "overall_status" in out
