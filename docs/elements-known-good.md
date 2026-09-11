# Known-good element shapes

**Copy from here. Do not invent field names.** Every shape below was POSTed
against a live org and accepted. Each one that is missing from this file cost a
failed publish to discover, and the error message did not say what was wrong.

## Decoding `Invalid kind: "<kind>"`

This is the error you will actually hit, and it is misleading.

> `document.elements[2]: Invalid kind: "button"`

It does **not** mean the element kind is unsupported. It means **a field you
supplied has the wrong value shape**. Sigma:

- **rejects** a known field carrying a bad value shape — reported as
  `Invalid kind` on the *element*, naming neither the field nor the expected
  shape;
- **silently drops** field names it does not recognise, so a typo'd field is
  invisible until the element renders wrong.

So when you see it: bisect from a shape in this file, adding one field at a
time. Do not re-read the message for hints; it has none.

A different, *structural* error means you got closer — e.g.
`elements[0].actions[0]: an action must have at least one effect` proves the
surrounding `actions`/`effects` shape parsed fine.

## The 200-that-lies

`POST` returning `{"success": true}` does not mean the workbook works.

A **bare column reference against a warehouse source publishes with HTTP 200**
and then compiles to literal `'Unknown column "[PRICE]"'` strings in the SQL.
No error, no warning, and `validate-spec.py`'s `bare-ref-resolution` check does
not catch this case.

Always check the compiled SQL:

```bash
scripts/api/verify-workbook.sh <workbook-id>   # compiles?
# and look at the SQL itself:
bash -c 'source scripts/api/_env.sh
  sigma_curl "$SIGMA_BASE_URL/v2/workbooks/<id>/elements/tbl-data/query"' | jq -r .sql
```

`scripts/pipeline.sh` does this automatically and fails on `Unknown column`.

## text

Content field is **`body`**, and it takes markdown (inline
`<span style="...">` works too). There is **no `text` field and no `variant`
field** — supplying them fails with `Invalid kind: "text"`.

```json
{ "id": "txt-title", "kind": "text", "body": "**Revenue by Region**" }
```

## table — warehouse source, grouped

What `build-plugin-workbook.py --path DB SCHEMA TABLE` emits. Warehouse column formulas
reference the **last path segment**; both the raw column name and its display
name work (`[T/STORE_REGION]` and `[T/Store Region]` compile identically).

```json
{
  "id": "tbl-data",
  "kind": "table",
  "name": "Revenue by Store Region",
  "source": {
    "kind": "warehouse-table",
    "connectionId": "<conn-uuid>",
    "path": ["RETAIL", "PLUGS_ELECTRONICS", "PLUGS_ELECTRONICS_HANDS_ON_LAB_DATA"]
  },
  "columns": [
    { "id": "col-store-region", "name": "Store Region",
      "formula": "[PLUGS_ELECTRONICS_HANDS_ON_LAB_DATA/STORE_REGION]" },
    { "id": "col-revenue", "name": "Revenue",
      "formula": "Sum([PLUGS_ELECTRONICS_HANDS_ON_LAB_DATA/PRICE] * [PLUGS_ELECTRONICS_HANDS_ON_LAB_DATA/QUANTITY])" }
  ],
  "groupings": [
    { "id": "by-dim", "groupBy": ["col-store-region"], "calculations": ["col-revenue"] }
  ]
}
```

Once `groupings` exists, **every** column in `columns[]` must be either a
`groupBy` dimension or a `calculations` entry. An orphaned column renders a
nonsensical summary value instead of per-row data.

Compiles to:

```sql
select STORE_REGION "Store Region", SUM_22 "Revenue"
from (select STORE_REGION, sum(PRICE * QUANTITY) SUM_22
      from RETAIL.PLUGS_ELECTRONICS.PLUGS_ELECTRONICS_HANDS_ON_LAB_DATA
      group by STORE_REGION) Q1
```

## table — generated SQL source (fabricated rows)

The only API route for fabricated rows in a workbook. The literal lives in
the spec; nothing is uploaded and no data model is involved. Column formulas
reference the implicit source element name **`Custom SQL`**, and an explicit
`name` keeps the header as written — without it Sigma prettifies `UNITS_SOLD`
into "Units Sold".

```json
{
  "id": "tbl-data",
  "kind": "table",
  "name": "Generated data",
  "source": {
    "kind": "sql",
    "connectionId": "<conn-uuid>",
    "statement": "SELECT\n  v.c1::varchar AS TEAM,\n  v.c2::float AS VALUE\nFROM (VALUES\n  ('Team A', 4200.0),\n  ('Team B', 3283.0)\n) AS v(c1, c2)"
  },
  "columns": [
    { "id": "col-team",  "name": "TEAM",  "formula": "[Custom SQL/TEAM]" },
    { "id": "col-value", "name": "VALUE", "formula": "[Custom SQL/VALUE]" }
  ],
  "order": ["col-team", "col-value"]
}
```

The field is **`statement`**, not `sql`. Casts are Snowflake-flavoured
(`::varchar`, `::number`, `::float`, `::boolean`, `::date`,
`::timestamp_ntz`); another connection type needs them adjusted. No
`groupings` needed — you control the rows, so emit them pre-aggregated.
`build-plugin-workbook.py` generates all of this, escaping `'` as `''`.

Verified 2026-09-11: published, compiled, and exported back showing the exact
literal rows.

## table — data-model source

For data models rather than raw warehouse tables. Formulas reference the data
model **element name**, not its id.

```json
{
  "source": { "kind": "data-model",
              "dataModelId": "<dm-uuid>", "elementId": "<element-id>" },
  "columns": [
    { "id": "col-region", "name": "Store Region", "formula": "[Order Line Item/Store Region]" },
    { "id": "col-revenue", "name": "Revenue",     "formula": "Sum([Order Line Item/Revenue])" }
  ]
}
```

## plugin

Undocumented in Sigma's spec API, verified round-tripping byte-for-byte.
Config bindings are **bare column-ID strings**, keyed by the names the plugin
declared in `configureEditorPanel`.

```json
{
  "id": "plug-viz",
  "kind": "plugin",
  "pluginId": "<uuid from POST /v2/plugins>",
  "config": {
    "source": { "kind": "element", "elementId": "tbl-data" },
    "label": "col-store-region",
    "value": "col-revenue"
  }
}
```

## button

The `button` kind and its `actions`/`effects` structure are fine; the
*effects* are where it breaks (below).

```json
{
  "id": "btn-view", "kind": "button", "text": "Summary", "appearance": "outline",
  "actions": [{ "id": "a-view", "trigger": "on-click", "effects": [
    { "effect": "set-control-value", "control": "cView",
      "value": { "type": "constant", "value": { "type": "text", "value": "Summary" } } }
  ]}]
}
```

## control

An element's `id` and its `controlId` must **differ**, or you get
`elements[N].controlId: Duplicate id`.

```json
{ "id": "ctl-view", "kind": "control", "controlId": "cView", "name": "View",
  "controlType": "list", "selectionMode": "single",
  "source": { "kind": "manual", "valueType": "text", "values": ["Summary", "Detail"] } }
```

`controlType: "slider"` and `"range-slider"` are rejected outright. For a
numeric parameter use `list` + `selectionMode: "single"` + a manual source.

## Top level

```json
{ "name": "...", "folderId": "<uuid>", "schemaVersion": 1,
  "pages": [{ "id": "page-main", "name": "Page" }],
  "elements": [ ... ],
  "layout": "<?xml version=\"1.0\" ...?><Page type=\"grid\" ...>...</Page>" }
```

- `folderId` is effectively **required**; omitting it surfaces as
  `Expecting UUID at 0.folderId` inside a large union-type error.
- `layout` is an **XML string**, not an object. Every element in `elements[]`
  needs a slot in it or it will not appear.
- Pages carry only `{id, name}` — membership lives in the layout XML.
- `publish-workbook.sh` wraps this flat shape into the `document` envelope on
  POST. **Do not hand-roll a POST with `sigma_curl`** and forget the wrapper:
  you get a union-type error about `folderId` and `schemaVersion` that looks
  like a completely different problem.

## Rejected — do not retry these

| Shape | Result |
|---|---|
| `insert-rows` / `delete-rows` / `open-url` effects | `Invalid kind: "button"`; every variant tried (dynamic-value objects, plain scalars, `rows[]`, `elementId` instead of `table`, no values, `inputMode:"edit"`) |
| `source: {kind: "sql", ..., sql: "..."}` | `Invalid kind: "table"` — the field is **`statement`**, not `sql`; see the working shape above |
| `source: {kind: "custom-sql" / "customSql" / "warehouse-sql"}` | `Invalid kind: "table"` |
| `source: {kind: "manual" / "inline"}` on a table | `Invalid kind: "table"` |
| text element with `text` + `variant` | `Invalid kind: "text"` |
| bare `[COLUMN]` on a warehouse source | **publishes 200**, compiles to `Unknown column` |

Note the first row: `kind: "sql"` is valid and was misdiagnosed as rejected
because the probe passed the statement under a field called `sql`. Sigma
rejects a known field carrying a bad value shape, so a wrong field name on a
valid `kind` reports `Invalid kind` — exactly the trap at the top of this
file, self-inflicted.
