"""OWASP Top 10 — 2025 (preview) security test suite for clickhouse-mcp.

Applicability map (2025 preview):
  * A01 Broken Access Control  — read-only; readonly=1; least-privilege CH account
  * A02 Cryptographic Failures — credential redaction; env-only; no secret in repr
  * A03 Injection (incl. SQL)  — bound parameters; allow-list table names; safe-SQL gate
  * A04 Insecure Design        — fail-closed; resource guardrails; run_safe_sql off
  * A05 Security Misconfig     — explicit defaults; readonly=1 always
  * A06 Vulnerable/Outdated    — pinned deps; pip-audit CI gate
  * A07 Auth Failures          — operator CH account is the only identity
  * A08 Data Integrity         — no eval/pickle; bound params; read-only
  * A09 Logging/Monitoring     — structured logs; health_check
  * A10 SSRF                   — fixed outbound host; injection-shaped input rejected
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

from clickhouse_mcp.client import validate_safe_sql
from clickhouse_mcp.errors import ChNotAllowedError, ChValidationError
from clickhouse_mcp.models import GetIndicatorsInput, ScreenFilter

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src" / "clickhouse_mcp"

D1 = date(2024, 1, 1)
D2 = date(2024, 1, 2)


class TestA01LeastPrivilege:
    def test_readonly_account_guidance_in_docs(self) -> None:
        sec = (REPO_ROOT / "docs" / "SECURITY.md").read_text("utf-8")
        assert "readonly = 1" in sec
        assert "GRANT SELECT" in sec


class TestA02Cryptographic:
    def test_inline_token_redacted(self) -> None:
        from clickhouse_mcp.errors import ChConnectionError

        assert "abc" not in str(ChConnectionError(reason="token=abc failed"))


class TestA03Injection:
    def test_indicator_allowlist_blocks_injection(self) -> None:
        with pytest.raises(ChValidationError):
            GetIndicatorsInput(symbol="AAPL", indicator="rsi14; drop", start=D1, end=D2)

    def test_screen_filter_indicator_allowlisted(self) -> None:
        with pytest.raises(ChValidationError):
            ScreenFilter(indicator="x'; DROP--", operator="lt", value=1)

    def test_table_names_from_allowlist_only(self) -> None:
        client = (SRC_ROOT / "client.py").read_text("utf-8")
        # Frequency -> table is a fixed dict; user input is a key lookup,
        # never the table string itself.
        assert "_FREQ_TO_BARS_TABLE: Final[dict[str, str]]" in client

    def test_prompt_style_payload_in_sql_rejected(self) -> None:
        with pytest.raises(ChNotAllowedError):
            validate_safe_sql("SELECT 1 /* ignore previous */ ; DROP TABLE x", default_limit=10)


class TestA04InsecureDesign:
    def test_execution_time_guardrail_default(self) -> None:
        from clickhouse_mcp.client import DEFAULT_MAX_EXECUTION_TIME

        assert DEFAULT_MAX_EXECUTION_TIME == 30


class TestA05Misconfiguration:
    def test_readonly_setting_always_applied(self) -> None:
        from tests.conftest import FakeClickHouseClient, FakeQueryResult, make_readonly_client

        fake = FakeClickHouseClient(responses=[FakeQueryResult([], [])])
        client = make_readonly_client(fake)
        client.query("SELECT 1")
        assert fake.queries[0][2]["readonly"] == 1


class TestA06Components:
    def test_pip_audit_in_dev_deps(self) -> None:
        assert "pip-audit" in (REPO_ROOT / "pyproject.toml").read_text("utf-8")


class TestA07Auth:
    @pytest.mark.asyncio
    async def test_health_reports_configuration(self) -> None:
        from clickhouse_mcp.tools.meta import health_check_impl

        out = await health_check_impl()
        assert "connection_configured" in out


class TestA08DataIntegrity:
    def test_no_dynamic_code_execution(self) -> None:
        offenders = [
            str(p.relative_to(REPO_ROOT))
            for p in SRC_ROOT.rglob("*.py")
            if re.search(r"\beval\s*\(|\bexec\s*\(|__import__\s*\(", p.read_text("utf-8"))
        ]
        assert offenders == []


class TestA09Logging:
    def test_no_print_to_stdout_in_source(self) -> None:
        # server.py monkeypatches builtins.print -> stderr; assert no bare
        # stdout writes in business modules.
        for name in ("client.py", "models.py", "errors.py"):
            body = (SRC_ROOT / name).read_text("utf-8")
            assert "print(" not in body


class TestA10SSRF:
    def test_host_not_derived_from_tool_input(self) -> None:
        runtime = (SRC_ROOT / "tools" / "_runtime.py").read_text("utf-8")
        # The runtime constructs the client with no host argument — host comes
        # from ConnectionSettings (env) inside the client.
        assert "ClickHouseReadOnlyClient()" in runtime
