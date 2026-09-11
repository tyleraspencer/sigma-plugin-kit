# Sigma REST API notes

Behaviour of the live Sigma API that the scripts in `scripts/api/` encode, and
that you will otherwise rediscover the hard way. These were established
against a real org; where a finding has since been retested and changed, both
the original and the retest are kept, because "this used to be true" is the
part that saves the next debugging session.

Everything here is about the API and its wire formats. The *authoring
conventions* for workbook specs — naming, layout, which rules
`validate-spec.py` enforces and why — are deliberately not in this repo; see
[provenance.md](provenance.md).

## The document wrapper

POST/PUT to `/v2/workbooks/spec` nest most of the spec under a top-level
`document` key. Only `name`, `folderId`, and `description` stay top-level
siblings:

```json
{
  "name": "…",
  "folderId": "…",
  "document": {
    "schemaVersion": 1,
    "kind": "workbook",
    "pages": [],
    "layout": "…"
  }
}
```

`document.kind` is **required** and always `"workbook"`.

The tooling here authors and validates the **flat** shape — `schemaVersion`,
`pages`, `layout` as top-level siblings. POSTing the flat shape as-is is
rejected with a large union-type validation error naming paths like
`0.document.0.0.0`, which reads like unrelated schema drift rather than "wrap
this in a document key."

`publish-workbook.sh` therefore wraps flat → wire on POST/PUT and unwraps
wire → flat on GET, so the flat shape stays the one stable authoring
convention everywhere else. Keys hoisted into `document`: `schemaVersion`,
`kind`, `pages`, `layout`, `settings`, `folders`, `agents`, `elements`,
`overlays`.

**2026-08-10 — shapes the API now rejects outright.** Three previously-valid
top-level document shapes started erroring, each with a message naming the
replacement:

| Old | Error says |
|---|---|
| `pages[].elements` | "Move elements to document.elements instead" |
| a per-page `type: "modal"` page | "modals now live in document.overlays[]" |
| `document.themeOverrides` | "Use document.settings.theme.overrides instead" |

**On response format.** Successful POST/PUT responses *are* JSON. A report
that they come back as plain `key: value` text was traced to calls made
outside `sigma_curl`: Sigma honours `Accept: application/json` for POST/PUT as
well as GET, and `sigma_curl` always sends it. No dual-format response
parsing is needed on the real script path.

## MCP endpoints require interactive user OAuth

`mcp-search.sh` and `mcp-describe.sh` call Sigma's `/mcp/v2` endpoint, which
gives richer output than the REST equivalents — `describe` returns
`CREATE TABLE`-style DDL with column types, source formulas, and a labeled
metrics catalog.

`/mcp/v2` only accepts **interactive user OAuth** tokens, confirmed by
Sigma's MCP engineering team (2026-08-07). A `client_credentials` token — the
Claude Code web path — will not work against it, and neither will a token
minted before `mcp:access` was added to scope. Both fail at the transport
level, which these scripts report as **exit 3**.

Exit 3 means "could not check," not "checked and found nothing." Callers like
`audit-workbook-schema.sh` must keep those distinct, or a total MCP outage
reads as a clean audit. Remedy: re-run `scripts/api/browser-login.sh`.

REST fallbacks when MCP is unavailable:

| MCP call | REST fallback | Loses |
|---|---|---|
| `mcp-search.sh` | `search-files.sh` | semantic match — REST is substring only |
| `mcp-describe.sh` | `GET /v2/dataModels/{id}/spec` | DDL, and the error-typed-column detection `audit-workbook-schema.sh` depends on |

## GET-spec can 500 when UI features aren't representable

`GET /v2/workbooks/{id}/spec` can return HTTP 500 with `code: service_error`
on a workbook that is otherwise healthy — opens in the UI, listed in
`/v2/files`, metadata fetchable via `GET /v2/workbooks/{id}`.

`harvest-workbook.sh` detects this envelope and deletes the partial
`spec.json` rather than seeding downstream work with an error body.

**Original confirmed trigger:** pivot-table cell-colour conditional
formatting, reproducible by toggling it in the UI (applied → 500, removed →
200, re-applied → 500).

**Retested 2026-08-13 — stale for `backgroundScale`.** A from-scratch POST of
a pivot table with `backgroundScale` conditional formatting, then an immediate
GET-spec, returned 200 with the format present. Treat the trigger above as
historical for that specific combination. Other conditional-format variants
(`single`, `fontScale`, `dataBars`) and other element kinds remain unconfirmed.

Suspected but untested: buttons, modal pages, tabbed containers; possibly
chart series breakout or colour-by combined with conditional formatting. Maps
were on this list until verified round-trippable (2026-07-02).

**Rollback does not help.** Once a workbook trips this, it stays tripped
across versions — all four versions of one test workbook returned
`service_error`.

When it happens:

1. Sanity-check another workbook's GET-spec to rule out a serializer outage.
2. Capture the `incident-id` from the body and file a Sigma support ticket —
   this is a server-side bug.
3. Isolate the trigger by undoing one UI change at a time, saving, retrying.
4. **Do not try to repair via PUT** — overwriting with a known-good spec
   destroys the new UI configuration.

Practical rule: configure suspected-trigger features last, after any spec
round-tripping for that workbook is done.

## Errored columns pass POST but break in the UI

A spec can POST/PUT successfully, compile to structurally valid SQL, and still
render as "Reference to errored column" or blank. `verify-workbook.sh` misses
this class because the query is valid; `audit-workbook-schema.sh` catches it
by inspecting the DDL at Sigma's data layer, which is why
`publish-workbook.sh` runs it automatically after a successful POST or PUT.
Suppress with `SIGMA_SKIP_AUDIT=1`, and only if you understand that the gate
then did not run.

Common causes:

- An unknown function — e.g. `NTile` is not a Sigma function; use a data-model
  metric.
- `Percentile` / `Sum` / `Avg` across an element boundary on a grouped-source
  column.
- A `Rollup` third argument that is neither the partition column nor an
  ordering column.
- Inline aggregation mixed with per-row references, on a table sourcing a
  grouped element.

## schemaVersion — don't hardcode

`validate-spec.py` warns rather than fails on a hardcoded `schemaVersion`.
Pull the current value from a live workbook instead:

```bash
scripts/api/publish-workbook.sh get-spec <reference-workbook-id>
```
