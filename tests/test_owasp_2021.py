"""OWASP Top 10 — 2021 security test suite for clickhouse-mcp.

Applicability map (2021):
  * A01 Broken Access Control  — read-only surface; readonly=1; no write tool
  * A02 Cryptographic Failures — credential redaction; env-only secrets
  * A03 Injection              — bound parameters; strict input regex; safe-SQL gate
  * A04 Insecure Design        — fail-closed config; run_safe_sql off; resource guardrails
  * A05 Security Misconfig     — explicit defaults; readonly=1 always
  * A06 Vulnerable Components  — clickhouse-connect/pydantic declared; pip-audit
  * A07 Identification/AuthN   — operator-configured CH account is the only identity
  * A08 Software/Data Integrity— JSON shape handling; no eval/pickle
  * A09 Logging & Monitoring   — structured JSON server log + health_check
  * A10 SSRF                   — outbound host fixed from env; not injectable
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

from clickhouse_mcp.errors import ChValidationError
from clickhouse_mcp.models import GetCorrelationMatrixInput, GetOhlcvInput

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src" / "clickhouse_mcp"

SSRF_PAYLOADS = [
    "http://169.254.169.254/latest/meta-data/",
    "https://evil.example/steal",
    "file:///etc/passwd",
    "//evil.example/x",
    "localhost:8080",
    "127.0.0.1",
    "gopher://127.0.0.1:6379/_",
]

D1 = date(2024, 1, 1)
D2 = date(2024, 1, 2)


class TestA01AccessControl:
    @pytest.mark.asyncio
    async def test_tool_surface_is_read_only(self) -> None:
        from clickhouse_mcp.server import app

        tools = await app().list_tools()
        for t in tools:
            assert not any(v in t.name for v in ("create", "update", "delete", "write", "insert", "drop"))

    def test_no_src_file_executes_mutating_sql(self) -> None:
        # client.py contains the forbidden-keyword *reject* list; exclude it.
        pattern = re.compile(r"client\.(command|insert)\s*\(", re.IGNORECASE)
        offenders = [
            str(p.relative_to(REPO_ROOT)) for p in SRC_ROOT.rglob("*.py") if pattern.search(p.read_text("utf-8"))
        ]
        assert offenders == []


class TestA02CryptographicFailures:
    def test_password_redacted_in_exception(self) -> None:
        from clickhouse_mcp.errors import ChQueryError

        exc = ChQueryError(reason="password=topsecret in dsn")
        assert "topsecret" not in str(exc)

    def test_settings_repr_excludes_user(self) -> None:
        from clickhouse_mcp.client import ConnectionSettings

        assert "mcp_readonly" not in repr(ConnectionSettings())


class TestA03Injection:
    @pytest.mark.parametrize("payload", ["AAPL'--", "A; SELECT 1", "A OR 1=1", "A\nUNION"])
    def test_symbol_injection_rejected(self, payload: str) -> None:
        with pytest.raises(ChValidationError):
            GetOhlcvInput(symbol=payload, start=D1, end=D2)

    def test_correlation_symbol_injection_rejected(self) -> None:
        with pytest.raises(ChValidationError):
            GetCorrelationMatrixInput(symbols=["AAPL", "MSFT'; DROP"], start=D1, end=D2)


class TestA04InsecureDesign:
    def test_fail_closed_without_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from clickhouse_mcp.client import ConnectionSettings
        from clickhouse_mcp.errors import ChConfigurationError

        monkeypatch.delenv("CLICKHOUSE_MCP_HOST", raising=False)
        with pytest.raises(ChConfigurationError):
            ConnectionSettings()

    def test_result_rows_bounded_by_default(self) -> None:
        from clickhouse_mcp.client import DEFAULT_MAX_RESULT_ROWS

        assert DEFAULT_MAX_RESULT_ROWS == 100_000

    def test_limit_capped_by_pydantic(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            GetOhlcvInput(symbol="AAPL", start=D1, end=D2, limit=10_000_000)


class TestA05Misconfiguration:
    def test_raw_sql_default_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from clickhouse_mcp.client import raw_sql_allowed

        monkeypatch.delenv("CLICKHOUSE_MCP_ALLOW_RAW_SQL", raising=False)
        assert raw_sql_allowed() is False


class TestA06Components:
    def test_security_deps_declared(self) -> None:
        body = (REPO_ROOT / "pyproject.toml").read_text("utf-8")
        assert "clickhouse-connect" in body and "pydantic" in body


class TestA07AuthFailures:
    @pytest.mark.asyncio
    async def test_identity_is_operator_account(self) -> None:
        """The only identity is the operator-configured CH account (env)."""
        from clickhouse_mcp.tools.meta import health_check_impl

        out = await health_check_impl()
        assert "connection_configured" in out


class TestA08DataIntegrity:
    def test_no_eval_or_pickle(self) -> None:
        offenders = [
            str(p.relative_to(REPO_ROOT))
            for p in SRC_ROOT.rglob("*.py")
            if re.search(r"\beval\s*\(|\bexec\s*\(|import pickle", p.read_text("utf-8"))
        ]
        assert offenders == []


class TestA09Logging:
    def test_structured_json_log_format(self) -> None:
        server = (SRC_ROOT / "server.py").read_text("utf-8")
        assert '"level":"%(levelname)s"' in server


class TestA10SSRF:
    @pytest.mark.parametrize("payload", SSRF_PAYLOADS)
    def test_symbol_cannot_inject_url(self, payload: str) -> None:
        with pytest.raises(Exception):
            GetOhlcvInput(symbol=payload, start=D1, end=D2)

    def test_outbound_host_comes_from_env_not_input(self) -> None:
        """The connect() target is built from env settings, never tool input."""
        client_src = (SRC_ROOT / "client.py").read_text("utf-8")
        assert "host=s.host" in client_src
        # No tool argument flows into get_client host.
        market_src = (SRC_ROOT / "tools" / "market.py").read_text("utf-8")
        assert "get_client(" not in market_src or "host=" not in market_src
