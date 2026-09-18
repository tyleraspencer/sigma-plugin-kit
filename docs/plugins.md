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
is a trap. React is an *external* of that bundle and its factory calls
`React.createContext` at module top level, so loading the SDK without loading
React **first** throws before the bundle assigns anything: `window.SigmaPlugin`
stays a bare `{}`, `client` is `undefined`, the plugin takes its no-client
branch and renders fallback data forever. The only symptom anywhere is one
uncaught `u.createContext is not a function` in the iframe console, and it
screenshots perfectly. It shipped that way once. Bundling the SDK from npm
makes the failure unrepresentable, so the archetype was removed rather than
documented around; preflight, deploy and CI each refuse a non-React plugin.
Plugins deployed under the old archetype keep serving, but cannot be
re-deployed until ported.

**The complete API is in [plugin-api.md](plugin-api.md)** — all 14 panel types,
the client surface, variables, actions, interactions, and which help-center
pages are wrong. Read that, not the help centre. The four things that matter in
every plugin:

```js
import { client, useConfig, useElementData } from '@sigmacomputing/plugin';

client.config.configureEditorPanel([          // module scope, once
  { type: 'element', name: 'source' },
  { type: 'column', name: 'label', source: 'source', allowMultiple: false,
    allowedTypes: ['text'] },
]);
```

- **Import `client`; never reach for a window global.** `window.sigmaComputing`
  is defined by no published bundle, and `window.SigmaPlugin` only exists under
  a UMD script tag. Both read `undefined`; `sdk-global` fails on either.
- `column` requires **both** `source` and `allowMultiple`. `allowedTypes` is an
  **allowlist** (the help page says "prevent" and misspells it `allowTypes`).
- Data arrives **keyed by column id** — parallel arrays, not row objects; zip by
  index. Capped at **25,000 values**; past that use
  `subscribeToIncrementalElementData`. Release the previous element
  subscription before opening a new one, or re-binding leaks a listener that
  overwrites state with the old element's rows.
- Ship a **deterministic, badged synthetic fallback** so the frame is never
  blank in the editor and nobody mistakes it for real data.

Renaming a panel entry's `name` silently unbinds every workbook using it.

**Iterate against a dev URL** rather than redeploying: `npm run dev` (Vite,
5173 — the `devUrl` every registration gets), then the element's **•••** menu →
**Point to Development URL**. Edits hot-reload; changing panel *options* means
re-entering the panel values.

When you do ship, ship *only* the bundle — `--ship` for a change you are sure
of, `--redeploy` to run the gates first. Both stop before the workbook, because
a bundle edit changes neither the `pluginId` nor the spec. If the panel,
bindings or data changed, re-run the full command with the same flags: the
regenerated spec is byte-compared against the one last published, and a real
difference is PUT into the same workbook. →
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
  the iframe, so a private repo's Pages output will not serve. Hosting lives in
  its own repo rather than this one so a deployed plugin's HTML has a single
  home (`tyleraspencer/sigma-plugins` by default; override with
  `SIGMA_PLUGIN_HOST_REPO` / `SIGMA_PLUGIN_HOST_URL`).
- **Serve from GitHub Pages, not jsDelivr.** jsDelivr returns `.html` as
  `text/plain`, which renders the plugin as raw source text and hangs PNG
  export.

A first Pages build routinely takes 30–60s and a brand-new path 404s until it
lands, so the script polls rather than trusting the push.

### What a deploy costs, and why the iterate loop is not a deploy

Measured 2026-09-15 on a real bundle change: **44s total, of which 39.5s was
GitHub Pages** — six poll attempts before it served the new bytes. Build and
push were 4s; the two gates would have added 0.25s.

**A deploy waits for Pages on purpose.** `url` is immutable on PATCH, so
registering a URL that does not serve means delete, re-create, a new
`pluginId`, and every referencing workbook silently broken — and shipping bytes
you have not confirmed are live is how you end up debugging a stale bundle
instead of your change. `SIGMA_DEPLOY_NO_WAIT=1` skips the wait; nothing sets it
for you.

**So do not iterate through GitHub.** `pipeline.sh <name> --dev` serves the
plugin from Vite in ~1s and the workbook element can point straight at it —
nothing pushed, nothing public, edits hot-reloading. Deploy when the change
should exist for other people, which is a different decision from "I want to
see it". After the first build the pipeline refuses to choose for you: a bare
re-run with a changed bundle exits 2 and lists `--dev` / `--ship` /
`--redeploy` / `--deploy`.

Two things the dev path needs:

- **One-time, per element, in the browser:** ••• → **Point to Development URL**
  → `http://localhost:5173`. Nothing happens in the workbook until that is set,
  and clearing it goes back to the deployed bundle.
- **One plugin per port.** Vite serves whatever directory it was started in, so
  a shared port shows you the wrong plugin while your edits appear to do
  nothing. `--dev` records the owner and refuses a port another plugin holds;
  `SIGMA_DEV_PORT` moves it.

### Why a deploy can look like it never happened

Pages serves `index.html` and the bundle with `Cache-Control: max-age=600`, and
plugin assets are deliberately **not** content-hashed (a hashed name 404s out
of a cached `index.html` once a deploy replaces `dist/assets` — `vite.config.js`
has the detail). Stable filename *plus* stable reference means a browser
holding `assets/index.js` renders the previous build from a fresh `index.html`
for up to ten minutes: not a blank iframe, the *old plugin*, looking exactly
like a deploy that silently failed.

So the build stamps the reference, not the file:

```html
<script type="module" src="./assets/index.js?v=1a2b3c4d"></script>
```

`plugin_version_assets` (`scripts/_plugin-build.sh`) does this after every build
through the pipeline, and on the skipped-build path too, so an older `dist/` is
brought up to date without a rebuild. It lives in the shell helper rather than
`vite.config.js` because that config is copied into each plugin at scaffold
time — a config-based version would reach only *new* plugins. The trade: a bare
`npm run build` inside a plugin directory leaves an unversioned `index.html`,
which is harmless since `deploy-plugin.sh` re-stamps on every path before
pushing. `assets_match` fetches the reference **with** its query, the URL the
iframe really requests, and strips it to find the local file to compare.

Worst case is now an `index.html` up to ten minutes old naming exactly the
bytes it shipped with: consistent, never a 404, self-healing.

**If you are staring at an old build right now**, that ten minutes is the
answer — and a hard reload of the *workbook* does not fix it. Sigma builds the
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
set** — a generic default ("Alice Johnson", "SCORE") is what everyone falls
into and nobody notices is meaningless. With none of the three, the generator
errors rather than inventing something.

**1. `--data FILE` — rows you invent. Prefer this.** `.csv`/`.tsv` or a JSON
array of objects; types inferred per column, `'` escaped to `''`. Make them
mean something for the plugin at hand, and stay in the Plugs Electronics
retail world — no sports, teams or leagues — so examples and real bindings
share a vocabulary.

**2. `--plugin-src PATH` — columns read off the plugin.** Parses the plugin's
`configureEditorPanel` and synthesizes correctly-typed columns *named to
match*, so every declared binding binds with no `--label-key`. `pipeline.sh`
passes it automatically. The values are visibly placeholders ("Brand A"): the
shape is right, the meaning isn't.

**3. `--path DB SCHEMA TABLE` — a real warehouse table**, grouped by
`--dimension` with `--measure` aggregated over it. Measure expressions take
bare column names and are qualified to `[TABLE/COLUMN]` for you — a bare
`[PRICE]` publishes 200 and compiles to `Unknown column`. Generated mode needs
no grouping: emit rows pre-aggregated, one per category.

**More than two columns** — a map wants zip, latitude, longitude *and* a
measure — needs `--bind KEY[:Display]=FORMULA` per editor-panel binding. Bare
column refs go to `groupBy`, expressions to `calculations`:

```bash
bash scripts/pipeline.sh zip-map "ZIP Map" -- \
  --path RETAIL PLUGS_ELECTRONICS PLUGS_ELECTRONICS_HANDS_ON_LAB_DATA \
  --bind zip:ZIP=STORE_ZIP_CODE \
  --bind "value:Revenue=Sum(PRICE * QUANTITY)" \
  --bind "latitude:Latitude=Max(STORE_LATITUDE)" \
  --variable-control selectedZips --control-values /tmp/zips.txt
```

**`--variable-control` with `--path` also needs `--control-values FILE`** (one
per line): a control's `source` must be `manual`, and a warehouse source puts
no rows in the spec to read distinct values from. Get them with the Sigma MCP
`query` tool, table referenced by inodeId.

#### A grouped element has two levels, and the plugin must name one

```json
"source": { "kind": "element", "elementId": "tbl-data", "groupingId": "by-dim" }
```

**`groupingId` is not optional when the bound element has `groupings`.** Omit
it and the plugin reads the element's *ungrouped* rows — 25,000 of them, each
repeating its group's aggregate — while the same element draws a correct
five-row table underneath it. Nothing else catches it: the spec validates, the
SQL compiles with its `GROUP BY` intact, publish returns 200, and the harness
hands the plugin rows directly so it never exercises this path.
`build-plugin-workbook.py` emits it, and `validate-spec.py`'s
`plugin-refs-resolve` fails a spec that gets it wrong. The value is the
grouping's own `id`; the editor exposes the same choice as **Source grouping**,
which is the manual fix for an older workbook. `--data` rows are
pre-aggregated, so they produce no grouping and need no `groupingId`.

#### The bundle is built at step 3, not step 4

The harness drives the **built** bundle, so the build has to precede it. It
used to live in `deploy-plugin.sh` at step 4, which meant a first build found
no `dist/` and no-opped past a `|| true` — the gate that proves a plugin
renders bound data never ran on the one run where the plugin was new.

`scripts/_plugin-build.sh` owns building now and both steps call it,
content-addressed against the plugin's sources (`dist/`, `node_modules/`,
`.git/` excluded). Step 3 builds when `dist/` is missing or stale, step 4 finds
the work done, so a run costs one build. `SIGMA_FORCE_BUILD=1` overrides, and a
failed build writes no stamp so the next run retries. `plugin_build_if_stale`
checks `npm run build`'s exit status explicitly: callers invoke it inside an
`if`, which suppresses errexit for the whole function, and without that check a
failed build falls through and stamps the *previous* bundle as current.

#### A published workbook needs a human

**The spec API writes the workbook's draft, not its published version.** POST
and PUT create a version the owner sees on opening and nobody else does until
someone clicks **Publish**. Hand over a fresh `pipeline.sh` URL and your
audience gets the previous published version, or an empty workbook.

There is no API for it (verified 2026-09-12): no publish parameter on
`POST /v2/workbooks/spec` or `PUT /v2/workbooks/{id}/spec`, no `/publish` or
`/versions` route on `/v2/workbooks`, `/v2/files` or `/v2/documents`, and
`?version=published|draft` is accepted and ignored — so the API cannot even
report that a draft is pending. `pipeline.sh` therefore prints NOT PUBLISHED
YET whenever it wrote a workbook. Two cases need nothing: `--redeploy` (no
draft created; the iframe fetches the new bundle on its next load) and an
unchanged spec.

`documentVersion` on the PUT means "only if still at this version". The kit
does not send it, so **a PUT overwrites edits made in the UI since the last
run** — and a PUT is not necessarily visible to an editor that already has the
workbook open, so don't iterate this way against a workbook someone is looking
at. POST a new one instead.

#### The known-good real table

`RETAIL.PLUGS_ELECTRONICS.PLUGS_ELECTRONICS_HANDS_ON_LAB_DATA` in the Sigma
Sample Database. **The `_DATA` suffix is part of the name** — the short one
resolves to nothing, with no error worth the name.

| | |
| --- | --- |
| Dimensions | `STORE_REGION`, `STORE_STATE`, `PRODUCT_FAMILY`, `BRAND`, `PRODUCT_NAME` |
| Measures | `QUANTITY`, `PRICE`, `COST` |
| Time | `DATE` |
| Geo | `STORE_ZIP_CODE`, `STORE_LATITUDE`, `STORE_LONGITUDE` |

Geocoded, so it is the one to reach for when the plugin is map-shaped. It only
exists in orgs with the sample connection — check `list-connections.sh` before
offering it outside the default org.

### Generated rows, and why input tables cannot be seeded

Generated mode compiles your rows into a `SELECT ... FROM (VALUES ...)`
literal and publishes it as a `kind: "sql"` table element, so the data lives
in the workbook spec. Shape: [elements-known-good.md](elements-known-good.md)
→ "table — generated SQL source".

This is the **only** API route for fabricated rows. The two obvious
alternatives are both closed:

- **Input tables cannot be *seeded* from code.** `rows`, `data`, `seedData`,
  `values` and `initialRows` on an `input-table` element are all silently
  dropped, and there is no REST write endpoint: nothing in Sigma's published
  OpenAPI spec matches `input` or `row`. An `insert-rows` action effect *does*
  publish (corrected 2026-09-15 — the earlier "rejected outright" finding was a
  wrong field name; see
  [elements-known-good.md](elements-known-good.md) → "input-table, and
  insert-rows"), but it is a **runtime** effect: it needs a user to click
  something, so it cannot put rows in a workbook you are about to hand over.
- **No CSV upload.** `/v2/files` POST creates folders and documents only, so
  the `source: {kind: "csv-table", inodeId}` used by CSV-backed elements can't
  be produced from the API.

So an input table publishes **empty**, a plugin bound to it falls through to
its own synthetic fallback, and you ship a chart whose numbers are hardcoded
in the HTML while looking entirely real.

An input table a plugin *writes into* is a different thing and does work: bind
the plugin's `variable` entries to scratch controls, hang an `insert-rows`
action off the last-written control's `on-change`, and every click appends a
row. `plugins/price-swarm` is the worked example -- but the rows arrive from
clicks, so the data the plugin *reads* still has to come from somewhere else.

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

```bash
python3 scripts/preflight-plugin.py <name> [--data FILE] [-v]  # static, blocking
python3 scripts/verify-plugin-binding.py <name> [--data FILE]  # renders it twice
npx serve -l 7824 plugins                 # then open the URL it printed
```

Both are quiet: one summary line unless something has something to say. `-v`
lists every check.

**`preflight-plugin.py` is what stops a bad deploy.** Every check in it is a
mode where the plugin deploys clean, publishes clean, renders its own fallback
and screenshots perfectly — no status code catches any of them. It imports
`build-plugin-workbook.py`'s *own* panel parser rather than re-implementing
one, so authoring and building cannot silently disagree about the binding
contract. With `--data` it also checks every `column` binding name against a
matching header, which is what makes all the bindings resolve rather than just
the first two.

**`verify-plugin-binding.py` is the only thing that proves the plugin renders
bound data.** It runs the plugin twice in isolated iframes — nothing bound, then
real rows in Sigma's column-keyed parallel-array shape — and fails if the two
renders are identical, because that means the bindings are being ignored. No
Sigma login, no deploy, no network. It reports which bindings the plugin
actually *read* (via a Proxy on the config object) and **resizes the bound frame
to measure whether the plugin's root follows** — a root pinned to a fixed height
reads the same at both sizes and fails `fills-frame`.

The verdict is in `document.title` (`HARNESS PASS` / `HARNESS FAIL`) and
`window.__HARNESS__`. **Wait for the title to stop saying `HARNESS RUNNING`** —
read it early and unfinished checks look like failures.

Checks marked **advisory** report but never block: `bound-values-visible` looks
for raw label strings in `innerText`, which a plugin that identifies on hover
legitimately never prints, and `innerText` collapses whitespace so a label with
a double space can never match. It false-FAILed a correct plugin twice. A gate
is only worth blocking on if it has never been wrong — a false FAIL costs a
debugging round and teaches you to stop believing the verdict.

Two things the harness structurally **cannot** catch, so check them by hand:

- **Blank until resized.** Its own `fills-frame` probe resizes the frame, which
  is exactly the event a plugin with a mis-attached `ResizeObserver` needs in
  order to render at all. Stub out the `probeFill` call in a copy of the
  generated harness and confirm an `<svg>` appears with no resize.
- **Variable writes and action triggers.** The generated `cfg` carries only
  column bindings, so `if (config.pickX)` guards are all false and clicks are
  inert. Add the keys to `P.cfg` in a copy, and push `wb:plugin:variable:set` /
  `wb:plugin:action-trigger:invoke` onto an array to see the order they fire in.

Escape hatches, for when a check is wrong rather than the plugin:
`SIGMA_SKIP_PREFLIGHT=1` (runs it, does not stop on failure),
`SIGMA_SKIP_BINDTEST=1`. `pipeline.sh --ship` skips both outright.

## Gotchas that cost a rebuild

- **`url` is immutable on PATCH.** Deploy and verify, then register.
- **A plugin sized to anything but 100% of its iframe is wrong** — fine in the
  one screenshot you took, clipped the moment the author resizes the element.
- **Controls bind to a plugin directly**, via a `variable` panel entry and
  `getVariable`/`setVariable`, in both directions
  ([plugin-api.md](plugin-api.md) → "Variables"). The old
  project-a-`[<controlId>]`-column workaround is **retracted**.
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
