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

### Why a deploy can look like it never happened

Pages serves both `index.html` and the bundle with `Cache-Control: max-age=600`,
and plugin assets are **not** content-hashed (`vite.config.js` explains why: a
hashed name 404s out of a cached `index.html` after a deploy replaces
`dist/assets`). With a stable filename *and* a stable reference, a browser that
already has `assets/index.js` cached renders the previous build from a fresh
`index.html` for up to ten minutes — not a blank iframe, but the old plugin,
looking exactly like a deploy that silently failed.

So the build stamps the reference instead of the file:

```html
<script type="module" src="./assets/index.js?v=1a2b3c4d"></script>
```

`plugin_version_assets` in `scripts/_plugin-build.sh` does this after every
build (and on the skipped-build path, so an older `dist/` is brought up to date
without a rebuild). It is idempotent, and it leaves a reference it cannot
resolve on disk exactly as the build wrote it. `assets_match` in
`deploy-plugin.sh` fetches the reference **with** its query — the URL the
iframe will actually request — and strips the query to find the local file to
compare against.

The worst case is now "an `index.html` up to ten minutes old, naming bytes that
are exactly the build it came from": consistent, never a 404, self-healing.
Only `index.html` itself can be stale, and it expires on its own.

**If you are staring at an old build right now**, that ten minutes is the
answer. A hard reload of the *workbook* does not fix it — Sigma creates the
plugin iframe from JavaScript, and a script-inserted iframe issues an ordinary
fetch that does not inherit the reload's cache-bypass. Open the plugin URL as a
top-level page, hard-reload that, then reload the workbook.

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

#### A grouped element has two levels, and the plugin must name one

```json
"source": { "kind": "element", "elementId": "tbl-data", "groupingId": "by-dim" }
```

**`groupingId` is not optional when the bound element has `groupings`.** Leave
it out and the plugin reads "All source columns" — the element's *ungrouped*
warehouse rows, capped at the SDK's 25,000, each repeating its group's
aggregate. A five-region revenue plugin renders 25,000 rows of `West / 731.5M`
while the very same element draws a correct five-row table underneath it,
because the table reads the grouping and the plugin does not.

Nothing else catches this. The spec validates. The element's own SQL compiles
with its `GROUP BY` intact, so the `verify` step passes. Publish returns 200.
The bind harness hands the plugin rows directly, so it never exercises this
path at all. It has to be right when the spec is generated — which
`build-plugin-workbook.py` now does, and `validate-spec.py`'s
`plugin-refs-resolve` fails a spec that gets it wrong.

The value matches the grouping's own `id`. The shape was confirmed by setting
**Source grouping** in the editor panel and reading the spec back, not
invented — the editor exposes the same choice as a dropdown, which is the
manual fix for a workbook built before this was handled.

`--data` rows are pre-aggregated (one row per category) so they produce no
grouping, and no `groupingId` — correct, and the check stays quiet.

#### Why the bundle is built at step 3, not step 4

The bind harness drives the **built** bundle, because that is what Sigma
loads. The only `npm run build` used to live inside `deploy-plugin.sh` at step
4, one step after the harness — so on a *first* build step 3 found no `dist/`,
printed "build it first" and no-opped past a `|| true`. The gate that proves a
plugin renders bound rather than fallback data never ran on the one run where
the plugin was new, which is the run where it matters.

`scripts/_plugin-build.sh` now owns building, and both steps call it. It is
content-addressed against the plugin's sources (`dist/`, `node_modules/` and
`.git/` excluded, since they are outputs), so:

- **step 3** builds when there is no `dist/`, or when the sources changed
  since the one that is there. A stale `dist/` is worse than a missing one —
  every downstream check then passes against code nobody edited.
- **step 4** is handed the same hash and finds the work already done, so a
  run costs one build, not two. `SIGMA_FORCE_BUILD=1` overrides.
- a build that **fails** writes no stamp, so the next run rebuilds rather than
  trusting the previous bundle.

One subtlety worth keeping: `plugin_build_if_stale` checks `npm run build`'s
exit status explicitly instead of relying on `set -e`. Callers invoke it
inside an `if`, and errexit is suppressed for everything in a function called
that way — without the explicit check a failed build falls through to the
`dist/index.html` guard, finds the *previous* build's file, and stamps it as
current.

#### A published workbook needs a human

**The spec API writes the workbook's draft, not its published version.** POST
and PUT both create a new version that the owner sees on opening — and that
everyone else does not, until someone clicks **Publish** in the UI. Send the
URL straight out of `pipeline.sh` and your audience gets the previous
published version, or an empty workbook if there has never been one.

There is no API for this. Verified against Sigma's own reference on
2026-09-12: `PUT /v2/workbooks/{workbookId}/spec` accepts `document` and an
optional `documentVersion` and nothing else; `POST /v2/workbooks/spec` accepts
`name`, `folderId`, `document`, `description`. Neither has a publish
parameter. There is no `/publish` route on `/v2/workbooks`, `/v2/files` or
`/v2/documents` — all 404 to both GET and POST — no `/versions` route, and
`/tags` is GET-only. `?version=published` and `?version=draft` are accepted
and ignored on `/spec` and `/elements`: every read returns current state, so
the API cannot even *report* whether a draft is pending.

So `pipeline.sh` prints a NOT PUBLISHED YET warning whenever it wrote a
workbook, and stays quiet when it didn't. Two cases genuinely need nothing:

- **`--redeploy`** — the spec is untouched, so no draft is created, and the
  iframe fetches the new bundle on its next load. A bundle-only change reaches
  viewers with nobody opening Sigma.
- **an unchanged spec** — nothing was published, so nothing is pending.

`documentVersion` on the PUT is worth knowing about for a different reason:
"update only if the workbook is still at this document version". The kit does
not send it, so a PUT will overwrite edits someone made in the UI since the
last run.

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

### The two gates

`pipeline.sh` runs both. Reach for them by hand while iterating.

```bash
python3 scripts/preflight-plugin.py <name> [--data FILE]      # static, blocking
python3 scripts/verify-plugin-binding.py <name> [--data FILE] # renders it twice
```

**`preflight-plugin.py` is what stops a bad deploy.** Every check in it is a
mode where the plugin deploys clean, publishes clean, renders its own fallback
and screenshots perfectly — no status code catches any of them. It imports
`build-plugin-workbook.py`'s *own* panel parser rather than re-implementing
one, so authoring and building cannot silently disagree about the binding
contract. With `--data` it also checks that every `column` binding name has a
matching header, which is what makes all the bindings resolve instead of just
the first two.

**`verify-plugin-binding.py` is the only thing that proves the plugin renders
bound data.** It generates a local page that runs the plugin twice in isolated
iframes — once with nothing bound, once with real rows in Sigma's column-keyed
parallel-array shape — and fails if the two renders are identical, because that
means the plugin is ignoring its bindings. No Sigma login, no deploy, no
network. It reports which declared bindings the plugin actually *read*, via a
Proxy on the config object, and it **resizes the bound frame and measures
whether the plugin's root follows** — a root pinned to a fixed height reads the
same at both sizes and fails `fills-frame`.

The verdict lands in the page, in `document.title` (`HARNESS PASS` /
`HARNESS FAIL`) and in `window.__HARNESS__`. Wait for the title to stop saying
`HARNESS RUNNING`: read it early and the checks that had not finished yet look
like failures.

Escape hatches, for when a check is wrong rather than the plugin:
`SIGMA_SKIP_PREFLIGHT=1`, `SIGMA_SKIP_BINDTEST=1`.

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
