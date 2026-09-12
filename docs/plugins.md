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

A Sigma plugin is a web app rendered in an iframe. There's no manifest and no
required folder structure. **Every plugin here is a Vite + React project** —
there is one archetype, so there's nothing to choose:

```bash
bash scripts/new-plugin.sh my-viz "My Viz"
```

The template is a Vite project using the SDK's React hooks, and already
demonstrates grouped editor-panel options, a `color` picker, a `dropdown`, a
loading state, a guarded resize observer, and a `variable` write-back that
cross-filters the workbook.

**Every plugin fills its iframe.** The workbook author sizes the element and
resizes it freely, and the host reports none of that — so the plugin must paint
the entire frame at whatever size it lands in and re-lay-out when the size
changes. That means `height: 100%` down the chain, no fixed `px`/`vh`/`vw` on
the root or the chart, `min-height: 0` on shrinking flex children, internal
scroll rather than an iframe scrollbar, and no assumption that the first
measurement is nonzero. Measure with a `ResizeObserver` on `document.body`,
guarded by a dimension comparison. The template does all of this; the rule and
the per-library switches are in [plugin-api.md](plugin-api.md) → "Loading,
sizing, errors". Drag the harness frames from `verify-plugin-binding.py` narrow
and wide to check it before deploy.

### Why there is no hand-written-HTML archetype

A single `index.html` pulling the SDK's UMD bundle off a CDN looks simpler and
is a trap. React is an *external* of that bundle, and its factory calls
`React.createContext` at module top level — so a page that loads the SDK
without loading React **first** throws right there, before the bundle assigns
anything. `window.SigmaPlugin` is left as a bare `{}` with zero keys,
`SigmaPlugin.client` is `undefined`, the plugin takes its no-client branch, and
it renders fallback data forever. The only symptom anywhere is one uncaught
`u.createContext is not a function` in the iframe console. It looks perfectly
correct in a screenshot.

That is not a hypothetical: it shipped. Bundling the SDK as an npm dependency
makes the failure unrepresentable, so the archetype was removed rather than
documented around. `preflight-plugin.py`, `deploy-plugin.sh` and CI each refuse
a plugin that isn't a React project.

Plugins deployed under the old archetype keep serving from their existing URLs
— nothing was taken down — but they cannot be re-deployed until they're ported.

**The complete API — all 14 editor-panel types, the client surface, variables,
actions, interactions, and the help-center's own errors — is in
[plugin-api.md](plugin-api.md).** Read that rather than the help centre; the
help pages document about a third of the API and get several details wrong.

The three things that matter most in any plugin:

```js
// 1. Declare the panel, at module scope. Each `name` is the config key AND the
//    key a workbook spec's plugin `config` must use.
client.config.configureEditorPanel([
  { type: 'element', name: 'source' },
  { type: 'column', name: 'label', source: 'source', allowMultiple: false,
    allowedTypes: ['text'] },
]);

// 2. React to config, re-subscribing when the bound element changes.
client.config.subscribe(cfg => { /* ... */ });
client.elements.subscribeToElementData(cfg.source, data => { /* ... */ });
```

- `column` requires **both** `source` and `allowMultiple`. `allowedTypes` is an
  **allowlist** (the help page says "prevent", and misspells it `allowTypes`).
- Data arrives **keyed by column ID** — an object of parallel arrays, not row
  objects. Zip by index. Capped at **25,000 values**; past that use
  `subscribeToIncrementalElementData`.
- Always release the previous element subscription before opening a new one, or
  re-binding leaks a listener that overwrites state with the old element's rows.
- **3. A synthetic fallback**, so the frame is never blank in the editor. Keep
  it deterministic — a plugin that reshuffles every render can't be
  screenshotted or eyeballed for regressions — and badge it visibly so nobody
  mistakes it for real data.

Renaming a panel entry's `name` silently unbinds every workbook using it.

**Import `client` from the package — never reach for a window global.**

```js
import { client, useConfig, useElementData } from '@sigmacomputing/plugin';
```

`window.sigmaComputing.plugin.client` is widely copied and defined by no
published bundle. `window.SigmaPlugin` *is* real, but it only exists when the
UMD build is loaded from a script tag — which a bundled plugin never does, so
either global appearing in your source means code was pasted from a
single-file example and will read `undefined` at runtime.
`preflight-plugin.py`'s `sdk-global` check fails on both.

**Iterate against a dev URL** rather than redeploying: `npm run dev` (Vite, port
5173 — the default `devUrl` Sigma registers), then in the workbook use the
element's **•••** menu → **Point to Development URL**. Changes hot-reload;
changing editor-panel *options* means re-entering the panel values.

When you do redeploy, redeploy *only*:

```bash
bash scripts/pipeline.sh my-viz --redeploy     # stops before the workbook
```

An edit inside the bundle changes neither the `pluginId` nor the workbook
spec, so steps 6–7 have no work to do — and re-running them used to POST a
second workbook with a second URL every single time. If the panel, bindings or
data *did* change, re-run the full command with the same flags: the
regenerated spec is byte-compared against the one last published, and a real
difference is PUT into the same workbook rather than posted as a new one. See
`skills/sigma-plugin-pipeline/SKILL.md` → "Editing after the first build".

## 2. Deploy

```bash
URL=$(bash scripts/deploy-plugin.sh my-viz)
```

Builds first if the plugin is the React archetype (`npm ci`/`install` then
`npm run build`), publishes the whole tree, and polls the live URL until it
returns `200 text/html` with bytes matching what was pushed.

**Built assets must use relative paths.** Vite's default `base: '/'` emits
`/assets/index-xxx.js`, which 404s under
`…/sigma-plugins/plugins/<name>/` — the page loads, the bundle doesn't, and
Sigma shows a blank iframe with nothing in any log. The template sets
`base: './'`, and `deploy-plugin.sh` refuses to publish a build with absolute
asset paths.

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

`post` always creates a **new** workbook, so it is the wrong verb for an
update: `put <workbook-id> <spec>` replaces the spec of one that already
exists, keeping its id and its URL. `pipeline.sh` picks between them — see
"Iterate against a dev URL" above — and only reaches for `post` on a first
build or under `--new-workbook`.

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

Config bindings are **bare column-ID strings**, keyed by the
`configureEditorPanel` entry names.

**Those keys must match the plugin's own panel.** The template declares
`label` and `value`, which is what the generator binds by default. A plugin is
free to declare anything, so tell the generator:

```bash
python3 scripts/build-plugin-workbook.py ... --label-key team --value-key wins
```

Bind the wrong key and the plugin renders its synthetic fallback, because
nothing it looks for resolves. Read the keys straight off the plugin:

```bash
grep -A6 'configureEditorPanel(\[' plugins/<name>/src/App.jsx
```

The panel array must be written **inline** at the call site — the indirect
`var DEFS = [...]` form the old single-file template used is no longer parsed.

Non-column entries (`text`, `toggle`, `dropdown`) are editor-panel inputs the
user sets in Sigma, not things a spec binds.
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

### Data: generated to fit the plugin

Three sources, in priority order. **There is deliberately no built-in row
set** — a generic default ("Alice Johnson", "SCORE") is the thing everyone
falls into and nobody notices is meaningless.

**1. `--data FILE` — rows you invent. Prefer this.** A `.csv`/`.tsv` or a JSON
array of objects; types are inferred per column, and `'` escapes to `''`.
Make them mean something for the plugin at hand: brands or product families
for a ranking chart, funnel stages for a funnel, store regions for a map.
Keep them in the Plugs Electronics retail world — no sports, teams or leagues,
so an example and a real binding share a vocabulary.

```bash
python3 scripts/build-plugin-workbook.py --name "Brand Revenue" \
  --plugin-id "$PID" --data brands.csv
```

**2. `--plugin-src PATH` — columns read off the plugin itself.** The generator
parses the plugin's `configureEditorPanel`, takes its column bindings and
their `allowedTypes`, and synthesizes correctly-typed columns *named to match*.
`pipeline.sh` passes this automatically, so a bare run always produces data
the plugin can bind.

For `brand-bars`, whose panel declares `brand` and `value`:

```sql
SELECT
  v.c1::varchar AS BRAND,
  v.c2::float AS VALUE
FROM (VALUES
  ('Brand A', 4200.0),
  ('Brand B', 3283.0)
) AS v(c1, c2)
```

...and the plugin config comes out as `{"brand": "col-brand", "value": "col-value"}`
with no `--label-key` needed. Every column binding the panel declares gets
bound, so a plugin wanting lat/long/tooltip gets all three.

The values are visibly placeholders — the *shape* is right, the meaning
isn't. That's the trade: it always binds, and it always looks like sample data.

**3. `--path DB SCHEMA TABLE` — a real warehouse table**, grouped by
`--dimension` with `--measure` aggregated over it. Measure expressions take
bare column names and get qualified to `[TABLE/COLUMN]`, which matters because
a bare `[PRICE]` publishes with HTTP 200 and compiles to `Unknown column`.
Generated mode needs no grouping at all — you control the rows, so emit them
pre-aggregated, one per category.

With none of the three, the generator errors rather than inventing something.

#### Binding more than two columns

`--dimension`/`--measure` emit exactly two columns, which is not enough for a
plugin whose panel declares more — a map wants zip, latitude, longitude *and*
a measure. Name each one with `--bind KEY[:Display]=FORMULA`, keyed by its
editor-panel binding name. A bare column reference goes to `groupBy`; an
expression goes to `calculations`.

```bash
bash scripts/pipeline.sh zip-map "ZIP Map" -- \
  --path RETAIL PLUGS_ELECTRONICS PLUGS_ELECTRONICS_HANDS_ON_LAB_DATA \
  --bind zip:ZIP=STORE_ZIP_CODE \
  --bind "value:Revenue=Sum(PRICE * QUANTITY)" \
  --bind "latitude:Latitude=Max(STORE_LATITUDE)" \
  --bind "longitude:Longitude=Max(STORE_LONGITUDE)" \
  --variable-control selectedZips --control-values /tmp/zips.txt
```

**`--variable-control` with `--path` also needs `--control-values FILE`** (one
value per line). A control's `source` must be `manual`, and with a warehouse
source there are no rows in the spec to read the distinct values out of. Get
them with the Sigma MCP `query` tool — `SELECT DISTINCT` against the
connection, table referenced by its inodeId.

#### The known-good real table

`RETAIL.PLUGS_ELECTRONICS.PLUGS_ELECTRONICS_HANDS_ON_LAB` in the Sigma Sample
Database. Bind it as:

```bash
--path RETAIL PLUGS_ELECTRONICS PLUGS_ELECTRONICS_HANDS_ON_LAB_DATA
```

**The actual table name carries a `_DATA` suffix** — the short name resolves
to nothing, with no error worth the name. Columns worth knowing:

| | |
| --- | --- |
| Dimensions | `STORE_REGION`, `STORE_STATE`, `PRODUCT_FAMILY`, `BRAND`, `PRODUCT_NAME` |
| Measures | `QUANTITY`, `PRICE`, `COST` |
| Time | `DATE` |
| Geo | `STORE_ZIP_CODE`, `STORE_LATITUDE`, `STORE_LONGITUDE` |

It is geocoded, which makes it the one to reach for whenever the plugin is
map-shaped. It only exists in orgs that have the sample connection — check
with `list-connections.sh` before offering it anywhere but the default org.

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
- **A plugin sized to anything but 100% of its iframe is wrong.** It looks fine
  in the one screenshot you took and clips or letterboxes for the author the
  moment they resize the element. See "Build", above.
- **Controls bind to a plugin directly** — declare a `variable` in the editor
  panel and read/write it with `getVariable`/`setVariable`. It works in both
  directions, so a plugin can also cross-filter the workbook by writing a
  selection into a control. ([plugin-api.md](plugin-api.md) → "Variables".)
  Earlier versions of this file told you to project a constant column with a
  bare `[<controlId>]` formula and bind that; **that workaround is retracted.**
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
