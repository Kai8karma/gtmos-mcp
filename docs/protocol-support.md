# Protocol support

gtmos-mcp speaks MCP over stdio and serves two protocol eras from one
process. It implements the protocol itself, on the Python standard
library, to keep the package free of dependencies.

| Revision | Era | Status |
| --- | --- | --- |
| 2026-07-28 | Modern: stateless, no handshake | Supported |
| 2025-06-18 | Legacy: `initialize` handshake | Supported |
| 2025-11-25, 2025-03-26, 2024-11-05 | Legacy | A client asking for one of these in `initialize` is offered 2025-06-18 |

## How the era is chosen

Per request. A request whose `params._meta` carries
`io.modelcontextprotocol/protocolVersion` is modern. A request without it
is answered with legacy semantics. Nothing is remembered between requests.

## What is implemented

**Modern (2026-07-28)**

- `server/discover` returns `supportedVersions`, `capabilities`, `ttlMs`,
  `cacheScope` and the server's identity.
- Every result carries `resultType: "complete"` and
  `_meta["io.modelcontextprotocol/serverInfo"]`.
- `tools/list` returns the tools in a fixed order with `ttlMs` (one hour)
  and `cacheScope: "public"`. The list is static and holds nothing
  user-specific.
- `tools/call` returns text `content` and `structuredContent`.
- A version the server does not support returns
  `UnsupportedProtocolVersionError` (`-32022`) with `data.supported` and
  `data.requested`.
- `ping` is not offered, as the revision removed it.

**Legacy (2025-06-18)**

- `initialize`, `notifications/initialized`, `tools/list`, `tools/call`,
  `ping`, unchanged. Legacy results gain no new fields.

**Both**

- An unknown tool, a non-string tool name, or arguments that are not an
  object return JSON-RPC `-32602`. A tool that runs and fails returns a
  result with `isError: true`, so the model can read the message.
- A message that is not an object returns `-32600`. Invalid JSON returns
  `-32700`. No message ends the process.
- Notifications get no response.
- The server exits when stdin closes.

## What is not implemented

- **Streamable HTTP.** stdio only. The server is meant to run on your
  machine next to your data; a hosted mode would also need authorization.
- **Resources, prompts, subscriptions, tasks, elicitation.** The server
  offers tools only and declares only the `tools` capability.
- **Output schemas.** Tools return `structuredContent` but do not yet
  declare an `outputSchema`. The shapes are stable within a minor version
  and shown in `examples/sample-output/TRANSCRIPT.md`.

## How it is checked

- `tests/test_protocol.py` covers both eras and malformed input against the
  specification text.
- `scripts/interop_check.py` connects the official MCP Python SDK client in
  `auto`, `2026-07-28` and `legacy` modes, lists the tools and calls one.
  CI runs it on every push.
- It has not been run through the official conformance suite, which tests
  servers over HTTP.

The specification moves on a six-month cycle, with the next revision
targeted for 30 March 2027. Both checks above will show what breaks.
