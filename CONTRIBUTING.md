# Contributing to `clickhouse-mcp`

Thanks for taking the time to contribute. This project is small and
batch-orientated; a tight, focused PR is much easier to review than a large
omnibus.

## Bootstrap

```bash
git clone https://github.com/kevinkda/clickhouse-mcp.git
cd clickhouse-mcp

uv sync --extra dev
uv run pre-commit install
```

Copy `.env.example` to `.env` and set the **read-only** ClickHouse connection
(`CLICKHOUSE_MCP_HOST` / `_HTTP_PORT` / `_USER` / `_PASSWORD` / `_DATABASE`).
The test suite never connects to a live ClickHouse — `clickhouse_connect` is
mocked — so `.env` is only needed for manual end-to-end checks.

## Workflow

1. Create a topic branch from `main`:

   ```bash
   git switch -c feature/short-description
   ```

2. Make small, logical commits. Conventional commit prefixes
   (`feat`, `fix`, `docs`, `test`, `chore`, `refactor`) are required.
3. Run the full local CI gate before pushing:

   ```bash
   bash scripts/local-ci.sh
   ```

   This runs `ruff check`, `ruff format --check`, `mypy --strict`,
   `bandit -r src -lll`, `pip-audit`, `pytest --cov` (100% required), and
   (best-effort) `pre-commit run --all-files`.

4. Open a PR using the template in `.github/PULL_REQUEST_TEMPLATE.md`.

## Code style

- Python 3.11+ with full type hints.
- 120-char line limit (handled by ruff format).
- Errors raised by the public surface MUST be subclasses of `ChError`.
- Imports at module top — no inline imports.

## Security (read this before touching the data path)

- **Read-only only.** No tool may write to ClickHouse. Every query goes through
  the `ClickHouseReadOnlyClient` (`readonly=1` + guardrails).
- **Parameterise everything.** Never f-string user input into SQL. Bind values
  as ClickHouse parameters; select table names from the frequency allow-list.
- **Never log credentials.** Run any error free-text through `redact_secrets`.
- New tools must include:
  - A Pydantic input model in `models.py` with anchored regexes / allow-lists.
  - Unit tests covering normal + validation-reject + ClickHouse-error paths.
  - OWASP / pentest / boundary assertions where the tool widens the attack
    surface.
  - A README tool-table entry.

## Licensing

By submitting a PR you agree your contribution is licensed under MIT (matching
the repo `LICENSE`).
