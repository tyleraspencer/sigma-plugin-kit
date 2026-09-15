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
    "statement": "SELECT\n  v.c1::varchar AS BRAND,\n  v.c2::float AS VALUE\nFROM (VALUES\n  ('Brand A', 4200.0),\n  ('Brand B', 3283.0)\n) AS v(c1, c2)"
  },
  "columns": [
    { "id": "col-brand", "name": "BRAND", "formula": "[Custom SQL/BRAND]" },
    { "id": "col-value", "name": "VALUE", "formula": "[Custom SQL/VALUE]" }
  ],
  "order": ["col-brand", "col-value"]
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

**`pluginId` is not validated on write.** An element whose `pluginId` does not
resolve to a real plugin is accepted and round-trips intact -- it renders empty,
with no error on any surface. Nothing in the write path checks it, so the id
`register-plugin.sh` prints is the only confirmation you get that the one in the
spec exists. Pasting a `pluginId` by hand is therefore a silent failure mode;
let `pipeline.sh` carry it.

## plugin — bound to a control (a `variable` binding)

A plugin's `variable` editor-panel entry binds through `config`, keyed by the
panel entry's `name` — but unlike a column binding, **the value is an object**.
This is what lets a plugin write a selection back into the workbook;
`setVariable(<panel name>, ...values)` lands in the bound control.

```json
{ "id": "ctl-selectedseats", "kind": "control", "controlId": "cSelectedSeats",
  "name": "Selected seats", "controlType": "list", "selectionMode": "multiple",
  "source": { "kind": "manual", "valueType": "text",
              "values": ["A1", "A2", "A3"] } },

{ "id": "plug-viz", "kind": "plugin", "pluginId": "<uuid>",
  "config": { "source": { "kind": "element", "elementId": "tbl-data" },
              "seat": "col-seat",
              "selectedSeats": { "kind": "control",
                                 "controlId": "cSelectedSeats" } } }
```

Verified 2026-09-11 on workbook `e894541d-54bc-4858-bfec-5b97b717039a`: POSTed
and round-tripped through GET unchanged.

**The object form is Sigma's own.** A bare `"selectedSeats": "cSelectedSeats"`
is also accepted and works, but the moment the workbook is opened in the UI
Sigma rewrites the whole config to its canonical shape — control bindings to
`{"kind":"control","controlId":...}` and *column* bindings to
`{"kind":"column","columnId":...,"source":...}`. Emitting the canonical form
for the control means the spec matches what a UI edit would produce. Column
bindings stay bare strings here because that is the form this kit has verified
end to end.

`selectionMode: "multiple"` is accepted — the docs' `"single"` is not the only
value. The server adds `mode: "include"` and `values: []` to the control on
the way in; neither needs to be sent.

**`Control variable name: <id> not found` in published mode, while edit mode
works, is not a binding bug.** It means the published version predates an edit
made in the UI — publish the workbook. Anything edited in the browser after an
API `POST`/`PUT` needs publishing before a viewer sees it, and a plugin's write
to a control is where that shows up first.

`build-plugin-workbook.py --variable-control BINDING[:COLUMN]` emits both
halves, taking the control's choices from a data column so every value the
plugin can write is one the control already accepts. It refuses a BINDING the
plugin's panel does not declare as a `variable`, because a mismatch there is
silently absent from `config` rather than an error.

### The canonical column form, and the check it slips past

In the canonical column form, **`source` names another `config` key** -- the
one holding the `{"kind":"element",...}` reference -- not an `elementId`. And a
binding that takes several columns uses a **plural** key:

```json
"measures": { "kind": "column", "columnIds": ["col-revenue", "col-units"],
              "source": "source" }
```

**`plugin-refs-resolve` goes dark on both.** Its column-binding check only
inspects config values that are plain strings (`if not isinstance(value, str):
continue`) -- the bare form this kit emits. A `{"kind":"column",...}` value is
skipped entirely, `columnId` and `columnIds` alike, so a normalized config is
carried past the one check that would catch a binding naming a column that
does not exist. Re-`GET` a workbook after editing it in the UI and the
validator has quietly stopped covering its plugin columns.

Probed 2026-09-15: a spec with three bad bindings -- one bare string, one
`columnId`, one `columnIds` -- reports exactly one `[FAIL]`, for the bare one.

## button

```json
{
  "id": "btn-view", "kind": "button", "text": "Summary", "appearance": "outline",
  "actions": [{ "id": "a-view", "trigger": "on-click", "effects": [
    { "effect": "set-control-value", "control": "cView",
      "value": { "type": "constant", "value": { "type": "text", "value": "Summary" } } }
  ]}]
}
```

A bare `"trigger": "on-click"` is accepted, but **the object form is what
Sigma's own UI emits** and the only form that can carry a condition:

```json
"trigger": { "on": "on-click",
             "condition": { "type": "formula", "formula": "IsNotNull([cPick])" } }
```

Triggers seen on UI-authored workbooks, by element kind: `button` →
`on-click`; `control` → `on-change`; `table`/`pivot-table`/`input-table`/chart
→ `on-select`; `table` → `on-context-menu-click`. **A `control` with an
`on-change` action is how a plugin's `setVariable` reaches an action** — the
plugin writes a control, the control fires.

## input-table, and insert-rows

**Both work from the spec API.** This file previously listed `insert-rows` as
rejected outright; that was a wrong *field name*, not a gated feature — the
exact trap at the top of this file, self-inflicted. The field is
`tableElementId`, and `values` is a **map keyed by input-table column id**.
The probes that failed used `table`, `elementId`, a `rows[]` array, and `values`
as a list. Verified 2026-09-15 on papercrane: POST 200, GET-spec round-trip
unchanged, both tables compile (workbook `757ed0ae`).

```json
{ "id": "tbl-queue", "kind": "input-table",
  "source": { "kind": "empty", "connectionId": "<uuid>" },
  "inputMode": "explore",
  "name": { "text": "Pricing review queue", "fontWeight": "bold" },
  "columns": [
    { "id": "q-product", "type": "text", "name": "Product" },
    { "id": "q-price", "type": "number", "name": "Avg price",
      "format": { "kind": "number", "formatString": "$,.2f" } },
    { "id": "q-verdict", "type": "text", "name": "Verdict",
      "values": ["Needs review", "Keep as-is"], "pills": "color-by-option" },
    { "id": "q-at", "type": "datetime", "name": "Queued at" }
  ],
  "sort": [{ "columnId": "q-at", "direction": "descending", "nulls": "last" }] }
```

- Column `type` is one of `text` / `number` / `datetime` / `checkbox`. A column
  with a `formula` instead is computed; `{"id": "ID"}`, `{"id": "UPDATED_AT"}`,
  `{"id": "UPDATED_BY"}` re-declare Sigma's system columns.
- `source: {kind: "empty", connectionId}` needs a connection that hosts input
  tables. `bee6615c-7d11-435c-8819-e32207b27fe4` (Sigma Sample Database) does.
  `{kind: "linked", from: "<elementId>"}` is the other form the UI emits.
- **Seeding rows from the spec is still impossible** (`rows`, `data`,
  `seedData`, `initialRows` are dropped; `source: {kind: "csv"}` is rejected) and
  there is still no REST write endpoint. `insert-rows` is now the way to get a
  row in from code — fired by an action, not by an API call.

```json
{ "effect": "insert-rows", "tableElementId": "tbl-queue",
  "values": {
    "q-product": { "type": "control", "control": "cPickProduct" },
    "q-verdict": { "type": "constant", "value": { "type": "text", "value": "Needs review" } },
    "q-at": { "type": "formula", "formula": "Now()" } } }
```

`update-rows` takes the same `tableElementId` + `values`, plus
`whichRows: {type: "single-row", primaryKeys: {<colId>: {type: "formula", ...}}}`.
A formula in `values` can reference a control by `[controlId]`, so one packed
control is an alternative to one control per column.

**Harvest, do not guess.** All of the above came from `GET
/v2/workbooks/<id>/spec` on workbooks the Sigma UI had authored — the fastest
way to settle any "can the spec API do X" question is to find a workbook where
a human already did X and read its spec back.

`docs/examples/price-swarm-queue.py` is the worked example: it takes a spec
from `build-plugin-workbook.py` and adds the controls, the action, the input
table, the button and the layout slots for all of them. **A workbook assembled
that way must not be regenerated by `pipeline.sh`** — the generator knows
nothing about the added elements, so a `--update-workbook` PUT would drop the
input table and every row in it. That is why `price-swarm` deliberately has no
`workbook_id` in the deploy cache: `--redeploy` (bundle only) stays safe, and a
full run creates a separate workbook instead of overwriting this one.

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

**A free-text or numeric scratch control needs no source at all**, which is
what a plugin writing arbitrary values wants — a manual `values` list would
reject anything not already in it:

```json
{ "id": "ctl-pick-product", "kind": "control", "controlId": "cPickProduct",
  "name": "Picked product", "controlType": "text",
  "mode": "equals", "showOperators": false },
{ "id": "ctl-pick-price", "kind": "control", "controlId": "cPickPrice",
  "name": "Avg price", "controlType": "number", "mode": "=" }
```

`controlType` values seen on UI-authored workbooks: `list`, `number`,
`segmented`, `text`, `text-area`, `switch`, `date-range`, `number-range`,
`slider`, `synced`. (`slider` exists in the product but the spec API rejects
it, so treat the others as unverified until POSTed.)

**A control's `source` must be `manual`.** Sourcing its choices from a column
of an element -- the obvious shape, and what the UI offers --

```json
"source": { "kind": "element", "elementId": "tbl-data", "columnId": "col-zip" }
```

is rejected with `document.elements[N]: Invalid kind: "control"` (probed
2026-09-11 against a warehouse-backed table). So a control whose choices come
from a real table has to be filled in from a query first: run the distinct
values through `scripts/api/` MCP `query` (Postgres syntax, tables referenced
by inodeId) and pass the list to `build-plugin-workbook.py --control-values
FILE`. That is why `--variable-control` needs `--control-values` under
`--path`: with warehouse data there are no rows in the spec to take them from.

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
| ~~`insert-rows` effects~~ | **Retracted 2026-09-15 — `insert-rows` works.** The probes named the target `table`/`elementId` instead of `tableElementId`; see "input-table, and insert-rows" above. `delete-rows` and `open-url` failed in that same session and were never re-probed, so assume the same mistake rather than a limit. |
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
