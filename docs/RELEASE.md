# Release process

1. Ensure `main` is green (CI passes).
2. Bump `version` in `pyproject.toml` and `__version__` in
   `src/clickhouse_mcp/__init__.py` (they must match — there is a test guard).
3. Update `CHANGELOG.md` with the new version section.
4. Run the full local gate:

   ```bash
   bash scripts/local-ci.sh
   ```

5. Commit, tag, and push:

   ```bash
   git commit -am "chore(release): vX.Y.Z"
   git tag vX.Y.Z
   git push origin main --tags
   ```

6. Create the GitHub release:

   ```bash
   gh release create vX.Y.Z --title "vX.Y.Z" --notes-file <(sed -n '/## \[X.Y.Z\]/,/## \[/p' CHANGELOG.md)
   ```

## Versioning

[Semantic Versioning](https://semver.org). A change to the connection env-var
contract or a removed/renamed tool is a breaking (major/minor) change and must
be called out in the CHANGELOG.
