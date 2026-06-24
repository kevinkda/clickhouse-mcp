"""Structured exception hierarchy for clickhouse-mcp.

Unlike SEC EDGAR (no auth), the ClickHouse warehouse is reached with
**credentials** (host / port / user / password).  The threat model therefore
treats those credentials as the crown jewels: every exception runs its
human-readable text through :func:`redact_secrets` so a ``repr(exc)`` or a
logged error can never leak a password, a ``clickhouse://user:pass@host`` DSN,
or an inline ``password=...`` token.

Coverage target: **100 %**.
"""

from __future__ import annotations

import re
from typing import Final

# ---------------------------------------------------------------------------
# Conservative redaction — strip credentials from any rendered string.
# ---------------------------------------------------------------------------

_REDACTED: Final[str] = "***REDACTED***"

#: ``scheme://user:password@host`` — redact the ``user:password`` userinfo.
_DSN_USERINFO_RE: Final[re.Pattern[str]] = re.compile(
    r"(?i)\b([a-z][a-z0-9+.\-]*://)[^/\s:@]+:[^/\s@]+@",
)

#: Inline ``password=...`` / ``pwd=...`` / ``secret=...`` assignments.
_INLINE_SECRET_RE: Final[re.Pattern[str]] = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|token)\s*[=:]\s*\S+",
)


def redact_secrets(text: str) -> str:
    """Replace credentials inside *text* with a redacted placeholder.

    Idempotent and side-effect-free.  Used by every exception's
    ``__init__`` so neither ``str(exc)`` nor ``repr(exc)`` can leak the
    ClickHouse password even if it arrived inside a DSN or a
    ``password=`` token echoed from a config string.
    """
    redacted = _DSN_USERINFO_RE.sub(rf"\g<1>{_REDACTED}@", text)
    redacted = _INLINE_SECRET_RE.sub(rf"\g<1>={_REDACTED}", redacted)
    return redacted


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


class ChError(Exception):
    """Base class for all clickhouse-mcp errors.

    Subclasses MUST only accept allow-listed structured fields and run any
    free-text field through :func:`redact_secrets`.  This base keeps
    ``__str__`` short and captures no extra args so a raw ``repr(exc)``
    cannot accidentally leak operator data.
    """

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.__class__.__name__


class ChValidationError(ChError):
    """Input validation failure (raised before any ClickHouse call)."""

    def __init__(self, *, field: str, reason: str) -> None:
        if not isinstance(field, str):
            raise TypeError("field must be str")
        if not isinstance(reason, str):
            raise TypeError("reason must be str")
        self.field: str = field
        self.reason: str = redact_secrets(reason)
        super().__init__(f"validation failed: {field} — {self.reason}")

    def __str__(self) -> str:
        return f"ChValidationError(field={self.field}): {self.reason}"


class ChConfigurationError(ChError):
    """A required connection env var is missing or malformed.

    Raised before any query when ``CLICKHOUSE_MCP_HOST`` / ``_USER`` are
    unset, so the server fails closed rather than silently connecting to a
    wrong host.
    """

    def __init__(self, *, hint: str) -> None:
        if not isinstance(hint, str):
            raise TypeError("hint must be str")
        self.hint: str = redact_secrets(hint)
        super().__init__(self.hint)

    def __str__(self) -> str:
        return f"ChConfigurationError: {self.hint}"


class ChConnectionError(ChError):
    """Could not establish / use the ClickHouse connection (network, auth, timeout)."""

    def __init__(self, *, reason: str) -> None:
        if not isinstance(reason, str):
            raise TypeError("reason must be str")
        self.reason: str = redact_secrets(reason)
        super().__init__(self.reason)

    def __str__(self) -> str:
        return f"ChConnectionError: {self.reason}"


class ChQueryError(ChError):
    """A ClickHouse query failed server-side (bad SQL, resource limit, timeout)."""

    def __init__(self, *, reason: str) -> None:
        if not isinstance(reason, str):
            raise TypeError("reason must be str")
        self.reason: str = redact_secrets(reason)
        super().__init__(self.reason)

    def __str__(self) -> str:
        return f"ChQueryError: {self.reason}"


class ChNotAllowedError(ChError):
    """A request was refused by a policy guardrail (e.g. raw SQL disabled).

    Used for the ``run_safe_sql`` escape hatch when
    ``CLICKHOUSE_MCP_ALLOW_RAW_SQL`` is not enabled, and for SQL that fails
    the SELECT-only / single-statement / DDL-DML rejection gate.
    """

    def __init__(self, *, reason: str) -> None:
        if not isinstance(reason, str):
            raise TypeError("reason must be str")
        self.reason: str = redact_secrets(reason)
        super().__init__(self.reason)

    def __str__(self) -> str:
        return f"ChNotAllowedError: {self.reason}"


__all__ = [
    "ChConfigurationError",
    "ChConnectionError",
    "ChError",
    "ChNotAllowedError",
    "ChQueryError",
    "ChValidationError",
    "redact_secrets",
]
