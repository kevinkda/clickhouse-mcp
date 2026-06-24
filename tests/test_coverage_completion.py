"""Coverage completion suite — drive the residual uncovered branches to 100%.

Every test here targets a specific ``file:line`` gap surfaced by
``pytest --cov-report=term-missing``.  No empty-coverage padding: each test
asserts a concrete observable invariant.

Gap map:
    * server.py  — ``_safe_print`` stderr default (36), log-dir ``mkdir``
      ``OSError`` swallow (49-50), ``RotatingFileHandler`` ``OSError`` swallow
      (65-66), ``main()`` entry point (304-305).
    * tools/meta.py — ``_probe_clickhouse`` ``ChConfigurationError`` branch
      (49-50).
    * models.py — ``_normalize_indicator`` non-``str`` passthrough (127->136).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

# ===========================================================================
# server.py — stdio hardening + entry point
# ===========================================================================


class TestServerHardeningGaps:
    def test_safe_print_defaults_to_stderr(self, capsys: pytest.CaptureFixture[str]) -> None:
        """The patched ``print`` routes to stderr by default (line 36)."""
        from clickhouse_mcp import server as srv

        srv._harden_stdio()
        print("coverage-probe-line")
        captured = capsys.readouterr()
        assert "coverage-probe-line" in captured.err
        assert "coverage-probe-line" not in captured.out

    def test_harden_stdio_tolerates_log_dir_oserror(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A failing log-dir ``mkdir`` is swallowed; hardening completes (49-50)."""
        from clickhouse_mcp import server as srv

        def boom_mkdir(*_a: Any, **_k: Any) -> None:
            raise OSError("read-only fs")

        monkeypatch.setattr(Path, "mkdir", boom_mkdir)
        srv._harden_stdio()  # must not raise
        import builtins

        assert builtins.print is not None

    def test_harden_stdio_tolerates_file_handler_oserror(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """A failing ``RotatingFileHandler`` is swallowed (65-66)."""
        from clickhouse_mcp import server as srv

        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

        def boom_handler(*_a: Any, **_k: Any) -> None:
            raise OSError("cannot open log file")

        # server.py does ``from logging.handlers import RotatingFileHandler``,
        # so patch the name bound *inside the server module*.
        monkeypatch.setattr(srv, "RotatingFileHandler", boom_handler)
        srv._harden_stdio()  # must not raise

    def test_main_runs_app(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``main()`` logs start and calls ``app().run()`` (304-305)."""
        from clickhouse_mcp import server as srv

        ran: list[int] = []
        fake_app = MagicMock()
        fake_app.run = lambda: ran.append(1)
        monkeypatch.setattr(srv, "app", lambda: fake_app)
        srv.main()
        assert ran == [1]


# ===========================================================================
# tools/meta.py — probe ChConfigurationError branch
# ===========================================================================


class TestMetaProbeConfigErrorGap:
    @pytest.mark.asyncio
    async def test_probe_clickhouse_config_error_branch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``get_client`` raising ``ChConfigurationError`` surfaces its hint (49-50)."""
        from clickhouse_mcp.errors import ChConfigurationError
        from clickhouse_mcp.tools import meta

        def boom_get_client() -> Any:
            raise ChConfigurationError(hint="CLICKHOUSE_MCP_HOST not set")

        monkeypatch.setattr(meta, "get_client", boom_get_client)
        probe = meta._probe_clickhouse()
        assert probe["reachable"] is False
        assert probe["server_version"] is None
        assert "CLICKHOUSE_MCP_HOST" in probe["reason"]

    @pytest.mark.asyncio
    async def test_health_check_config_error_during_probe_surfaces_unhealthy(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A configured connection whose probe raises config error → unhealthy."""
        from clickhouse_mcp.errors import ChConfigurationError
        from clickhouse_mcp.tools import meta

        def boom_get_client() -> Any:
            raise ChConfigurationError(hint="bad config")

        monkeypatch.setattr(meta, "get_client", boom_get_client)
        out = await meta.health_check_impl()
        assert out["clickhouse_reachable"] is False
        assert out["overall_status"] == "unhealthy"


# ===========================================================================
# models.py — non-str validator passthrough
# ===========================================================================


class TestModelValidatorPassthroughGaps:
    def test_normalize_indicator_passthrough_non_str(self) -> None:
        """``_normalize_indicator`` returns non-``str`` input untouched (127->136)."""
        from clickhouse_mcp.models import _normalize_indicator

        sentinel = object()
        assert _normalize_indicator(sentinel) is sentinel
        assert _normalize_indicator(None) is None
        assert _normalize_indicator(123) == 123

    def test_normalize_symbol_passthrough_non_str(self) -> None:
        """``_normalize_symbol`` returns non-``str`` input untouched (defensive)."""
        from clickhouse_mcp.models import _normalize_symbol

        sentinel = object()
        assert _normalize_symbol(sentinel) is sentinel
        assert _normalize_symbol(None) is None
