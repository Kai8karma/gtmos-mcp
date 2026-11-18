# Examples

A made-up CRM export and everything the tools produce from it. Nothing here
is real customer data.

| File | What it is |
| --- | --- |
| `contacts.sample.json` | 25 contacts in HubSpot export shape, with deliberate defects: duplicates, stale records, missing owners, placeholder values |
| `deals.sample.json` | Deals across the stock HubSpot sales pipeline, some stalled |
| `policy.strict.json` | A tighter policy for `cortex_proposals` |
| `sample-output/TRANSCRIPT.md` | Each tool's arguments and text result |
| `sample-output/` | The report files those calls wrote |

## Reproduce it

No token and no network are needed.

```bash
gtmos audit  --input examples/contacts.sample.json --acv 9000 --as-of 2026-08-19
gtmos cortex --input examples/contacts.sample.json --deals examples/deals.sample.json --as-of 2026-08-19
```

Or through your MCP host:

> Audit `examples/contacts.sample.json` as of 2026-08-19 and tell me the
> weakest dimension.

The outputs here were scored as of 2026-08-19. Pass the same `as_of` and
you will get the same numbers. Without it the freshness score falls as the
sample data ages.
