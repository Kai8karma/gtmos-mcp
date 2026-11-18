# Changelog

All notable changes to gtmos-mcp. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
aims to follow [Semantic Versioning](https://semver.org/) once it reaches 1.0.

## [0.6.0] - unreleased

Version fields are already set to 0.6.0. Put the date here when you tag `v0.6.0`.

### Added
- **MCP 2026-07-28 support, alongside 2025-06-18.** The server now answers
  `server/discover`, reads the protocol version from each request's `_meta`,
  stamps `resultType` on every result, marks `tools/list` cacheable
  (`ttlMs`, `cacheScope`) and rejects an unknown version with
  `UnsupportedProtocolVersionError` (`-32022`). Hosts still on the
  `initialize` handshake see no change. Checked against the official MCP
  Python SDK client in `auto`, `2026-07-28` and `legacy` modes
  (`scripts/interop_check.py`).
- **`as_of` on every tool.** Freshness, SLA and stall checks compare record
  dates to "now", so the same export used to score differently each day.
  Pass an ISO date to pin it.
- **Structured results.** Every tool returns `structuredContent` with its
  numbers next to the text summary.
- **`GTMOS_ALLOWED_DIR`.** When set, every path a tool reads or writes must
  sit inside that directory. Unset, behaviour is unchanged.
- **`stage_order` on `funnel_leak`**, so a custom pipeline can be judged for
  stalled deals. The default remains HubSpot's stock sales pipeline.
- **Possible duplicates.** Contacts that share a surname and company but not
  a first name are listed for review in the audit report and in
  `scores.json` (`possible_dupe_clusters`). They are not scored.
- Tool `title` and `annotations`. Tools that write report files no longer
  imply they are read-only in the protocol's sense; `ops_signals`, which
  writes nothing, is marked read-only.
- Claude Code plugin (`.claude-plugin/`) with a `crm-audit` skill.
- CI, a tag-driven release workflow (PyPI trusted publishing, then the MCP
  Registry), `SECURITY.md`, `CONTRIBUTING.md`, `docs/`, `examples/`, `evals/`.

### Changed
- **Duplicate rule.** `fuzzy_name_company` now needs the first names to
  agree, or one to be an initial that matches exactly one full first name
  in the group. Before, any two contacts with the same surname at the same
  company formed a cluster, counted against the portal score, and reached
  the deduper as a `merge_duplicates` proposal at confidence 0.60. Those
  pairs now appear under possible duplicates instead. The cluster key gains
  the first name (`priya kapoor / meridian steelworks`).
- **Company normalisation** drops commas and full stops and strips more
  legal suffixes (`limited`, `incorporated`, `corporation`, `company`,
  `plc`, `gmbh`, `pvt`), repeatedly. "Acme, Inc." and "Acme Inc" now match.
  This also affects how the context graph names accounts.
- An unknown tool name is now a JSON-RPC error (`-32602`) instead of a tool
  result with `isError`. A tool that runs and fails still reports through
  `isError`, so the model can read the message.
- `gtmos mcp --dry-run` prints both supported protocol versions.

### Fixed
- A `tools/call` whose `params` was not an object raised `AttributeError`
  and ended the server process. Malformed messages, params, tool names and
  arguments now come back as JSON-RPC errors, and no single message can end
  the session.
- `funnel_leak` documented a 30-day stall default; the engine uses 21. The
  schema now says 21 and declares `out_dir`, which the handler already read.

## [0.5.0] - 2026-09-02

Not published to PyPI.

### Added
- Cortex context graph (`cortex_graph`): contacts, accounts, owners, sources
  and deals with explicit-only edges and a per-account rollup.
- Governed agents (`cortex_proposals`): router, deduper, lifecycle steward,
  attribution steward, pipeline steward and schema steward, each returning
  proposals gated by a policy (action allow-list, blast-radius cap,
  confidence floor). Nothing is applied.

### Fixed
- Routing SLA denominator, an explicit `sla_hours` of 0, and records whose
  first-touch timestamp precedes their create date (2026-08-24).

## [0.4.0] - 2026-08-21

Not published to PyPI.

### Added
- Marketing Cortex: `cortex_scorecard` (GTM ops health across five
  dimensions) and `ops_signals` (severity-tagged signals on the stack).

## [0.3.1] - 2026-08-21

Not published to PyPI.

### Changed
- Registry ownership marker and `server.json` namespace match the GitHub
  login's case (`io.github.Kai8karma/gtmos-mcp`).

## [0.3.0] - 2026-08-19

### Added
- First release: a read-only CRM audit MCP server over stdio with
  `audit_crm` and `funnel_leak`, the `gtmos audit` and `gtmos calibrate`
  commands, and a `server.json` for the MCP Registry.

[0.6.0]: https://github.com/Kai8karma/gtmos-mcp/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/Kai8karma/gtmos-mcp/releases/tag/v0.5.0
