"""Unit tests for the errors module — hierarchy + credential redaction."""

from __future__ import annotations

import pytest

from clickhouse_mcp.errors import (
    ChConfigurationError,
    ChConnectionError,
    ChError,
    ChNotAllowedError,
    ChQueryError,
    ChValidationError,
    redact_secrets,
)


class TestRedactSecrets:
    def test_redacts_dsn_userinfo(self) -> None:
        out = redact_secrets("clickhouse://admin:s3cret@host:8123/usa")
        assert "s3cret" not in out
        assert "admin" not in out
        assert "***REDACTED***@host" in out

    def test_redacts_inline_password(self) -> None:
        assert "hunter2" not in redact_secrets("connect password=hunter2 ok")

    @pytest.mark.parametrize("kw", ["password", "passwd", "pwd", "secret", "token"])
    def test_redacts_all_secret_keywords(self, kw: str) -> None:
        assert "abc123" not in redact_secrets(f"{kw}=abc123")

    def test_idempotent(self) -> None:
        once = redact_secrets("password=x")
        assert redact_secrets(once) == once

    def test_plain_text_unchanged(self) -> None:
        assert redact_secrets("no secrets here") == "no secrets here"


class TestExceptionHierarchy:
    def test_all_subclass_cherror(self) -> None:
        for exc in (
            ChValidationError(field="f", reason="r"),
            ChConfigurationError(hint="h"),
            ChConnectionError(reason="r"),
            ChQueryError(reason="r"),
            ChNotAllowedError(reason="r"),
        ):
            assert isinstance(exc, ChError)

    def test_base_str_is_classname(self) -> None:
        assert str(ChError()) == "ChError"

    def test_validation_str_and_fields(self) -> None:
        exc = ChValidationError(field="symbol", reason="bad")
        assert exc.field == "symbol"
        assert "symbol" in str(exc)

    def test_validation_redacts_reason(self) -> None:
        exc = ChValidationError(field="x", reason="password=leak")
        assert "leak" not in str(exc)

    def test_configuration_str_and_redaction(self) -> None:
        exc = ChConfigurationError(hint="url clickhouse://u:p@h/db")
        assert "ChConfigurationError" in str(exc)
        assert "p@h" not in str(exc)

    def test_connection_str_and_redaction(self) -> None:
        exc = ChConnectionError(reason="failed pwd=abc")
        assert "ChConnectionError" in str(exc)
        assert "abc" not in str(exc)

    def test_query_str_and_redaction(self) -> None:
        exc = ChQueryError(reason="token=t1 boom")
        assert "ChQueryError" in str(exc)
        assert "t1" not in str(exc)

    def test_not_allowed_str(self) -> None:
        exc = ChNotAllowedError(reason="disabled")
        assert "ChNotAllowedError" in str(exc)
        assert exc.reason == "disabled"

    @pytest.mark.parametrize(
        "factory",
        [
            lambda: ChValidationError(field=1, reason="r"),  # type: ignore[arg-type]
            lambda: ChValidationError(field="f", reason=2),  # type: ignore[arg-type]
            lambda: ChConfigurationError(hint=3),  # type: ignore[arg-type]
            lambda: ChConnectionError(reason=4),  # type: ignore[arg-type]
            lambda: ChQueryError(reason=5),  # type: ignore[arg-type]
            lambda: ChNotAllowedError(reason=6),  # type: ignore[arg-type]
        ],
    )
    def test_type_guards_reject_non_str(self, factory) -> None:
        with pytest.raises(TypeError):
            factory()
