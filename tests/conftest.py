"""Shared pytest fixtures and helpers for clickhouse-mcp tests.

Tests NEVER connect to a live ClickHouse. A :class:`FakeClickHouseClient`
emulates the small slice of the ``clickhouse_connect`` client surface this
project uses (``query`` returning an object with ``column_names`` /
``result_rows``, plus ``close``). The factory fixtures construct a
:class:`ClickHouseReadOnlyClient` with the fake injected, so no network and no
``clickhouse_connect.get_client`` call ever happens unless a test explicitly
exercises the connect path (and even then it monkeypatches the importer).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import pytest

import clickhouse_mcp.tools._runtime as runtime_mod
from clickhouse_mcp.client import ClickHouseReadOnlyClient, ConnectionSettings


class FakeQueryResult:
    """Mimics the ``clickhouse_connect`` query-result object."""

    def __init__(self, column_names: list[str], result_rows: list[list[Any]]) -> None:
        self.column_names = column_names
        self.result_rows = result_rows


class FakeClickHouseClient:
    """In-memory stand-in for a ``clickhouse_connect`` client.

    *responses* maps a predicate over (sql, parameters) to a
    ``FakeQueryResult``. The simplest form is an ordered list consumed FIFO;
    a callable lets a test branch on the SQL/params. ``raise_on_query`` makes
    every ``query`` raise to exercise error paths.
    """

    def __init__(
        self,
        *,
        responses: list[FakeQueryResult] | None = None,
        handler: Any | None = None,
        raise_on_query: BaseException | None = None,
        raise_on_close: BaseException | None = None,
    ) -> None:
        self._responses = list(responses or [])
        self._handler = handler
        self._raise_on_query = raise_on_query
        self._raise_on_close = raise_on_close
        self.queries: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        self.closed = False

    def query(
        self,
        sql: str,
        *,
        parameters: dict[str, Any] | None = None,
        settings: dict[str, Any] | None = None,
    ) -> FakeQueryResult:
        self.queries.append((sql, dict(parameters or {}), dict(settings or {})))
        if self._raise_on_query is not None:
            raise self._raise_on_query
        if self._handler is not None:
            return self._handler(sql, parameters or {})
        if self._responses:
            return self._responses.pop(0)
        return FakeQueryResult([], [])

    def close(self) -> None:
        if self._raise_on_close is not None:
            raise self._raise_on_close
        self.closed = True


def make_settings() -> ConnectionSettings:
    """Build a ConnectionSettings against test env (host/user are set)."""
    return ConnectionSettings()


def make_readonly_client(fake: FakeClickHouseClient) -> ClickHouseReadOnlyClient:
    """Construct a ClickHouseReadOnlyClient with *fake* injected (no connect)."""
    return ClickHouseReadOnlyClient(settings=make_settings(), client=fake)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Set a valid read-only connection env so ConnectionSettings constructs.

    The values point at a non-routable host; nothing connects because tests
    inject the fake client. Reset the client singleton around each test.
    """
    monkeypatch.setenv("CLICKHOUSE_MCP_HOST", "ch.test.invalid")
    monkeypatch.setenv("CLICKHOUSE_MCP_HTTP_PORT", "8123")
    monkeypatch.setenv("CLICKHOUSE_MCP_USER", "mcp_readonly")
    monkeypatch.setenv("CLICKHOUSE_MCP_PASSWORD", "test-pass")  # pragma: allowlist secret
    monkeypatch.setenv("CLICKHOUSE_MCP_DATABASE", "usa")
    monkeypatch.delenv("CLICKHOUSE_MCP_ALLOW_RAW_SQL", raising=False)
    monkeypatch.delenv("CLICKHOUSE_MCP_SECURE", raising=False)
    runtime_mod.reset_client_cache()
    yield
    runtime_mod.reset_client_cache()


@pytest.fixture
def fake_client_factory():
    """Return a factory: (responses/handler) -> FakeClickHouseClient."""

    def _factory(
        *,
        responses: list[FakeQueryResult] | None = None,
        handler: Any | None = None,
        raise_on_query: BaseException | None = None,
    ) -> FakeClickHouseClient:
        return FakeClickHouseClient(responses=responses, handler=handler, raise_on_query=raise_on_query)

    return _factory


@pytest.fixture
def install_fake_client(fake_client_factory):
    """Install a fake client as the runtime singleton; return the fake."""

    def _install(
        *,
        responses: list[FakeQueryResult] | None = None,
        handler: Any | None = None,
        raise_on_query: BaseException | None = None,
    ) -> FakeClickHouseClient:
        fake = fake_client_factory(responses=responses, handler=handler, raise_on_query=raise_on_query)
        runtime_mod.set_client(make_readonly_client(fake))
        return fake

    return _install


def pytest_configure(config: pytest.Config) -> None:
    os.environ.pop("CLICKHOUSE_MCP_MAX_EXECUTION_TIME", None)
    os.environ.pop("CLICKHOUSE_MCP_MAX_RESULT_ROWS", None)
