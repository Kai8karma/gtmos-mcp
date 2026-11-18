# Contributing

Thanks for looking. This is a small project with three rules that are not
negotiable, and everything else is open.

## The three rules

1. **Zero runtime dependencies.** `dependencies = []` in `pyproject.toml`
   stays empty. CI fails if it does not. Dev and test tooling goes under
   `[project.optional-dependencies]`.
2. **Never write to the CRM.** No `POST`, `PATCH`, `PUT` or `DELETE`, no
   second network host. `tests/test_read_only.py` enforces both. Proposals
   are output for a person to act on.
3. **No model in the score path.** Every number comes from a deterministic
   function with a test. A change to a weight, threshold or rule updates
   `docs/scoring-methodology.md` in the same pull request.

## Set up

```bash
git clone https://github.com/Kai8karma/gtmos-mcp && cd gtmos-mcp
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest -q        # everything runs offline, no token needed
ruff check .
```

To check the protocol against the official MCP client as well:

```bash
pip install -e ".[interop]"
python scripts/interop_check.py
```

## Making a change

- **A scoring change** needs a test that fails before it and passes after,
  and a line in `CHANGELOG.md` under the unreleased heading that says what
  will score differently. People compare audits over time; a silent change
  to a score is a bug.
- **A new tool** needs an entry in `TOOLS` (with `title`, `inputSchema`,
  `as_of`, honest `annotations`), a handler that returns
  `(text, structured)`, tests for the happy path and for bad input, and a
  row in the README table.
- **A protocol change** needs a test in `tests/test_protocol.py` and a green
  `scripts/interop_check.py`. Legacy results must not gain fields.
- Tool handlers must raise `ValueError` with a message the model can act
  on. `call_tool` turns it into an `isError` result.

## Releasing (maintainer)

1. Set the version in `pyproject.toml`, `gtmos/__init__.py`, `server.json`
   (two places) and `.claude-plugin/plugin.json`.
   `python scripts/check_versions.py` confirms they agree.
2. Date the section in `CHANGELOG.md`.
3. Regenerate `examples/sample-output/` if any output changed.
4. Tag and push: `git tag v0.6.0 && git push origin v0.6.0`.

The release workflow tests, builds, publishes to PyPI through trusted
publishing, waits for the version to appear, then publishes `server.json`
to the MCP Registry. One-time setup is described at the top of
`.github/workflows/release.yml`.

## Reporting bugs and security issues

Bugs: open an issue with the tool call, the input shape (redact real
data), what you expected and what you got. Security issues: see
[SECURITY.md](SECURITY.md); please do not open a public issue.
