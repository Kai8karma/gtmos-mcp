## What changes

<!-- One or two sentences. Link the issue if there is one. -->

## Checklist

- [ ] `python -m pytest -q` and `ruff check .` pass
- [ ] No new runtime dependency, no write call to the CRM, no new network host
- [ ] If a score, threshold or rule changed: a test covers it, `docs/scoring-methodology.md` is updated, and `CHANGELOG.md` says what will score differently
- [ ] If a tool's input or output changed: README table and `examples/` are updated
- [ ] If the protocol surface changed: `scripts/interop_check.py` passes
