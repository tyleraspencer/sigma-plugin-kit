# The plugin pipeline

Four steps. Each one's output is the next one's input.

```
build  →  deploy (public URL)  →  register (pluginId)  →  workbook (bind + publish)
```

```bash
bash scripts/pipeline.sh my-viz "My Viz"     # all four steps, one command
```

Or step by step, when something needs unpicking:

```bash
bash scripts/new-plugin.sh my-viz "My Viz"                          # 1. build
URL=$(bash scripts/deploy-plugin.sh my-viz)                         # 2. deploy
PID=$(bash scripts/api/register-plugin.sh create "My Viz" "$URL")   # 3. register
python3 scripts/build-plugin-workbook.py --name "My Viz Demo" \
  --plugin-id "$PID" --folder-id <folder> --out spec.json           # 4. workbook
bash scripts/api/publish-workbook.sh post spec.json
```

**Never register before deploying.** `PATCH /v2/plugins/{id}` cannot change a
plugin's `url` — only `name`, `description` and `devUrl`. A URL that turns out
not to serve costs you a delete + re-create, which mints a *different*
`pluginId` and silently breaks every workbook pointing at the old one.

## 1. Build

A Sigma plugin is an iframe web app. It needs no bundler and no manifest — a
single hosted `index.html` is genuinely sufficient. `scripts/new-plugin.sh`
copies `plugins/_template/index.html` and substitutes the title.

`plugins/<name>/` is a **gitignored working directory**. The public host repo
is the single source of truth for deployed HTML; tracking a second copy here
would drift from what Sigma actually loads with nothing comparing the two.
Only the template is tracked, which is why the HTML gates live in
`deploy-plugin.sh` rather than CI.

### The SDK global is not what most examples say

The UMD bundle at `https://unpkg.com/@sigmacomputing/plugin` defines exactly
one global — **`window.SigmaPlugin`** — with the imperative client at
`SigmaPlugin.client`.

`window.sigmaComputing.plugin.client` is a widely-copied pattern that **no
published bundle defines**. Verified by reading the actual bundles for 1.3.2
and 1.2.0: the UMD factory assigns `e.SigmaPlugin = {}` and exports
`e.client`, and the string `sigmaComputing` never appears. A plugin reading
the wrong global gets `client === null`, takes its no-client branch, and
renders its synthetic fallback forever — looking fine in a screenshot while
never binding a single real column.

This is not hypothetical. In a 60-plugin library reviewed while building this
toolkit, 55 read the mythical global and only 7 read `SigmaPlugin`.

The template probes both, preferring the real one:

```js
var sdk = window.SigmaPlugin
       || (window.sigmaComputing && window.sigmaComputing.plugin)
       || null;
var client = (sdk && sdk.client) || null;
```

CI enforces this: `plugins/*/index.html` must reference `SigmaPlugin`.

React is a peer dependency of the UMD build, but only the hooks need it. The
imperative `client` works without React loaded.

### The three things every plugin needs

```js
// 1. Declare the editor-panel bindings. Each `name` is the key that arrives
//    in config AND the key a workbook spec's plugin `config` must use.
client.config.configureEditorPanel([
  { name: 'source', type: 'element' },
  { name: 'label',  type: 'column', source: 'source', allowedTypes: ['text'] },
  { name: 'value',  type: 'column', source: 'source', allowedTypes: ['number','integer'] }
]);

// 2. React to config, and re-subscribe when the bound element changes.
client.config.subscribe(function(cfg){ /* ... */ });
client.elements.subscribeToElementData(cfg.source, function(data){ /* ... */ });
```

- `subscribeToElementData` yields an object **keyed by column ID**, each value
  a parallel array of cells — *not* an array of row objects. Zip by index.
- Always release the previous subscription before opening a new one. Re-binding
  the source otherwise leaks a listener that keeps overwriting state with the
  old element's rows.
- **3. A synthetic fallback**, so the frame is never blank in the editor. Make
  it deterministic — a plugin that reshuffles every render can't be screenshot
  or eyeballed for regressions. Render a visible "demo data" badge so nobody
  mistakes the fallback for real numbers.

Other `configureEditorPanel` types beyond `element` and `column` include
`text`, `toggle` and `dropdown`; full list in Sigma's plugin development API
docs.

Renaming a `DEFS` key silently unbinds every workbook already using it.

The template runs with no Sigma client at all, so you can open it directly in a
browser while iterating.

## 2. Deploy

```bash
URL=$(bash scripts/deploy-plugin.sh my-viz)
```

Pushes `plugins/my-viz/` to the public host repo and polls the live URL until
it returns `200 text/html`.

Two hard requirements, both enforced by the script:

- **The host repo must be public.** Sigma fetches the URL anonymously to render
  the iframe, so a private repo's Pages output will not serve. This toolkit is
  private, which is exactly why hosting lives in a separate public repo
  (`tyleraspencer/sigma-plugins` by default; override with
  `SIGMA_PLUGIN_HOST_REPO` / `SIGMA_PLUGIN_HOST_URL`).
- **Serve from GitHub Pages, not jsDelivr.** jsDelivr returns `.html` as
  `text/plain`, which renders the plugin as raw source text and hangs PNG
  export.

A first Pages build routinely takes 30–60s and a brand-new path 404s until it
lands, so the script polls rather than trusting the push.

## 3. Register

```bash
PID=$(bash scripts/api/register-plugin.sh create "My Viz" "$URL")
```

| Method | Path | Notes |
|---|---|---|
| GET | `/v2/plugins` | paginated, `entries[]`; name → id lookup |
| POST | `/v2/plugins` | `{name, url, devUrl, description}` → `{pluginId, ...}` |
| GET | `/v2/plugins/{id}` | |
| PATCH | `/v2/plugins/{id}` | `name`, `description`, `devUrl` only — **not `url`** |
| DELETE | `/v2/plugins/{id}` | permanent; referencing elements stop rendering |

`type` is an enum whose only value is `element`. `devUrl` defaults to
`http://localhost:5173` (Vite's port).

Writes need Admin, or an account type with the **Manage plugins** permission —
a 403 on `create` means an org admin has to do it or grant that.

Other subcommands: `list [--name <substr>]`, `get <id>`, `id-for <exact name>`,
`rename <id> <new-name>`, `delete <id>` (guarded by `SIGMA_CONFIRM_DELETE=1`).

## 4. Workbook

`build-plugin-workbook.py` emits the flat spec shape;
`publish-workbook.sh post` validates it, wraps it in the `document` envelope,
POSTs, and runs the schema audit.

The plugin element:

```json
{
  "id": "plug-viz",
  "kind": "plugin",
  "pluginId": "<uuid from step 3>",
  "config": {
    "source": { "kind": "element", "elementId": "tbl-data" },
    "label": "col-region",
    "value": "col-revenue"
  }
}
```

Config bindings are **bare column-ID strings**, keyed by the `DEFS` names.

**Those keys must match the plugin's own `DEFS`.** The bundled template
declares `label` and `value`, which is what the generator binds by default. A
plugin is free to declare anything — `sec-logo-bars` uses `team` for its
dimension — so tell the generator:

```bash
python3 scripts/build-plugin-workbook.py ... --label-key team --value-key wins
```

Bind the wrong key and the plugin renders its synthetic fallback, because
nothing it looks for resolves. Read the keys straight off the plugin:

```bash
grep -A6 'DEFS = \[' plugins/<name>/index.html
```

Non-column `DEFS` entries (`text`, `toggle`, `dropdown`) are editor-panel
inputs the user sets in Sigma, not things a spec binds.
`kind: "plugin"` is undocumented in Sigma's spec API (the spec endpoints are
private Beta) but **verified working end-to-end** — POSTed and GET-back
byte-for-byte on papercrane 2026-09-11, `pluginId` and `config` intact.

Two field-name traps in the surrounding elements, both of which fail with a
message that blames the element kind rather than the field:

- A **text** element's content field is **`body`** and takes markdown. There
  is no `text` or `variant` field; supplying them fails with
  `Invalid kind: "text"`.
- Sigma **rejects a known field with a bad value shape** and **silently drops
  unknown field names**. So `Invalid kind: "<kind>"` almost always means "a
  field you supplied has the wrong value shape", not "this element kind is
  unsupported". Bisect from a known-good element rather than reading the
  message literally.

`folderId` is effectively required on POST — omitting it surfaces as
`Expecting UUID at 0.folderId` inside a large union-type error.

### Data: generated by default

No data flags needed — the generator fabricates rows and compiles them into a
`kind: "sql"` element:

```bash
python3 scripts/build-plugin-workbook.py --name "Demo" --plugin-id "$PID"
```

That emits a built-in sample shape (text / float / int / date / boolean), and
auto-binds the plugin's label to the first text column and its value to the
first numeric one. Override with `--label-column` / `--value-column`.

Supply your own rows with `--data`, either a `.csv`/`.tsv` or a JSON array of
objects — types are inferred per column, and `'` is escaped to `''`:

```bash
python3 scripts/build-plugin-workbook.py --name "SEC" --plugin-id "$PID" \
  --data teams.csv --label-key team
```

**To bind a real table instead**, pass `--path`:

```bash
python3 scripts/build-plugin-workbook.py --name "Revenue" --plugin-id "$PID" \
  --path RETAIL PLUGS_ELECTRONICS PLUGS_ELECTRONICS_HANDS_ON_LAB_DATA \
  --dimension STORE_REGION --measure "Sum(PRICE * QUANTITY)" --measure-name Revenue
```

That emits a *grouped* table. Measure expressions take **bare** column names;
the generator qualifies them to `[TABLE/COLUMN]`, which matters because a bare
`[PRICE]` publishes with HTTP 200 and compiles to `Unknown column`. Generated
mode needs no grouping at all — you control the rows, so emit them
pre-aggregated, one per category.

Other useful columns on the default table: `STORE_STATE`, `STORE_CITY`,
`STORE_NAME`, `PRODUCT_TYPE`, `PRODUCT_FAMILY`, `PRODUCT_LINE`, `BRAND`,
`PRODUCT_NAME`, `SKU_NUMBER`, `CUSTOMER_NAME`, `DATE`, `QUANTITY`, `PRICE`,
`COST`, `ORDER_NUMBER`.

For a different source, discover it instead of guessing:

```bash
bash scripts/api/list-connections.sh
bash scripts/api/mcp-search.sh "<topic>" --types table --limit 5
bash scripts/api/mcp-describe.sh table <inodeId>   # exact columns + warehouse path
```

Warehouse column formulas reference the **last path segment**; both the raw
column name and its display name compile identically (`[T/STORE_REGION]` and
`[T/Store Region]`). A **bare** `[STORE_REGION]` does not — see "The
200-that-lies" in elements-known-good.md.

### Generated rows, and why input tables still don't work

Generated mode compiles your rows into a `SELECT ... FROM (VALUES ...)`
literal and publishes it as a `kind: "sql"` table element, so the data lives
in the workbook spec. Shape: [elements-known-good.md](elements-known-good.md)
→ "table — generated SQL source".

This is the **only** API route for fabricated rows. The two obvious
alternatives are both closed:

- **Input tables cannot be written from code.** `insert-rows` is a runtime
  action effect (one row per effect) and is rejected outright by the spec API —
  bisected against a known-good baseline, where the same button carrying
  `set-control-value` publishes fine. `rows`, `data`, `seedData`, `values` and
  `initialRows` on an `input-table` element are all silently dropped. There is
  no REST write endpoint either: nothing in Sigma's published OpenAPI spec
  matches `input` or `row`.
- **No CSV upload.** `/v2/files` POST creates folders and documents only, so
  the `source: {kind: "csv-table", inodeId}` used by CSV-backed elements can't
  be produced from the API.

So an input table publishes **empty**, a plugin bound to it falls through to
its own synthetic fallback, and you ship a chart whose numbers are hardcoded
in the HTML while looking entirely real.

## Verifying, and the failures that hide

A plugin element that publishes cleanly and renders blank is the normal failure
mode: Sigma validates neither the `pluginId` nor the config bindings at POST
time, so both return 200 and then show an empty iframe.

`validate-spec.py`'s `plugin-refs-resolve` check catches a missing or
non-UUID `pluginId`, a malformed `config.source`, and bindings naming columns
that don't exist on the bound element. It **cannot** confirm the `pluginId` is
registered in your org — use `register-plugin.sh get "$PID"` for that.

**If the plugin shows its demo data in Sigma, the binding is wrong.** That
fallback is precisely what renders when nothing resolves.

## Gotchas that cost a rebuild

- **`url` is immutable on PATCH.** Deploy and verify, then register.
- **Controls cannot bind to a plugin's `config`.** Plugin config binds columns;
  controls only parametrize filter values. To drive a plugin from a control,
  add a constant column to the plugin's source element whose formula is a bare
  `[<controlId>]` reference, then bind that column. `list`-with-manual-source
  and `segmented` both resolve through a bare ref; use `segmented` with
  `values: [1, 0]` for booleans.
- **Re-publishing a harvested spec with an input table:** strip every system
  column (`ID`, `CREATED_AT`, `CREATED_BY`, `UPDATED_AT`, `UPDATED_BY`) back to
  a bare `{"id": ...}` first. Sigma adds a `formula` field to them on GET, and
  re-submitting fails PUT with "system column `ID` cannot set `type` or
  `formula`". Every time, not just the first.
- **`linked`-source input tables reject `delete-rows`** (`empty` is fine).
- **Input-table column order is not preserved** on GET-back. Diff by column
  `id`, never by array position.
- **A spec that POSTed once is not guaranteed to PUT later** — Sigma has
  changed element schemas mid-project. When a PUT fails on an element you did
  not touch, suspect a server-side schema change before your own diff.
