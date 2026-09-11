---
name: sigma-plugin-pipeline
description: 'Use when the user wants to build, deploy, host, register or embed a Sigma Computing custom-visualization plugin — "build me a Sigma plugin", "make a custom viz for Sigma", "host this plugin", "register the plugin with my org", "add the plugin to a workbook", "put a plugin in a new workbook". Opens with a short intake — synthetic rows vs a real table, visual direction, and anything else specific to the request — then one command runs the whole chain: author a Vite + React plugin against the @sigmacomputing/plugin SDK, deploy to public GitHub Pages, register via POST /v2/plugins for a pluginId, then generate and publish a workbook with the plugin bound to real warehouse data. Does NOT cover authoring ordinary workbooks with no plugin in them, nor Claude Code plugin/skill packaging.'
---

# Sigma plugin pipeline

## Step 0: ask before you build

**One `AskUserQuestion` call, before `pipeline.sh` runs.** Deploy and register
are irreversible and a workbook is something the user shows other people, so
the two things worth a round trip are the two you would otherwise guess:
where the data comes from, and what it should look like. Guess the data source
wrong and it's a rebuild; guess the visual direction wrong and you ship a
plugin nobody uses.

Ask **2–4 questions in a single call**, then build. One round only — don't
come back for a second.

**1. Data — always ask.** This is the one question with a fixed shape:

| Option | What it means |
| --- | --- |
| Synthetic rows *(recommend this first)* | You invent rows that fit the topic and they're compiled into the workbook as a `kind:"sql"` VALUES literal. Self-contained, no warehouse access, works in any org. |
| A real table | `--path DB SCHEMA TABLE` with `--dimension`/`--measure`. Ask which table, or offer to find it — then actually find it with `list-connections.sh` / `mcp-search.sh` / `mcp-describe.sh` rather than guessing names. |

If they pick a real table and can't name one, don't stall: search, propose the
best match by name, and say you'll fall back to synthetic rows if it's wrong.

**2. Look and feel — always ask.** Pick the axis that most changes the code
*for this plugin*, not a generic "what style?". The useful ones:

- **Density** — compact and data-dense, or large and presentation-ready? Drives
  font sizes, padding, how many rows survive at a small size.
- **Color** — one accent, a categorical palette, or a value-driven scale
  (good/bad, low/high)? Drives the whole palette and any conditional formatting.
- **Chart form**, when the request names a goal instead of a shape — "compare
  regions" could be bars, a map, or a dot plot.
- **Branding** — Sigma-native look, or the user's/a third party's colors and
  logos?

**3–4. Whatever else actually changes the build.** Ask only when the answer
would send you down a different path. The usual candidates:

- **Does clicking it do anything?** A plugin that filters the rest of the
  dashboard needs a `variable` panel entry wired to a workbook control — that's
  structural, not a later tweak. See the variables section below.
- **What are the entities?** Teams, regions, stages, SKUs. Real names beat
  "Team A" and you need them before you can write the synthetic rows.
- **Thresholds and rules** for anything computed — what counts as good, how a
  score is weighted, where a cutoff sits.
- **Multiple elements?** A plugin can bind more than one; ask if the request
  hints at combining two sources.
- **Where it lives** — an existing workbook, or a new one.

### Rules for the intake

- **Skip anything the request already answers.** "A bar chart of SEC team wins
  with ESPN logos" has already told you the form, the entities and the
  branding — ask about the data source and the color scale, and stop.
- **Never ask four out of habit.** Two good questions beat four padded ones.
- **Put the recommended option first** and label it, so "whatever you think" is
  a one-click answer.
- **If the user waves you off** — "just build it", "you pick" — take the
  recommended option on every question, build the whole thing, and say what you
  chose. Don't ask again.
- **Don't ask what the scripts can answer.** Column names, the pluginId,
  whether a registration already exists: look those up.

### Answers map straight to flags

```bash
# synthetic rows, entities the user named
bash scripts/pipeline.sh sec-bars "SEC Bars" -- --data /tmp/teams.csv

# real table
bash scripts/pipeline.sh sec-bars "SEC Bars" -- \
  --path MY_DB PUBLIC GAMES --dimension TEAM --measure "Sum(WINS)"
```

Look-and-feel answers land in `plugins/<name>/src/App.jsx`. Interactivity
answers land in `configureEditorPanel` as a `variable` entry **before** the
first deploy.

## Run this

After the intake above — the answers decide the flags.

```bash
bash scripts/pipeline.sh <plugin-name> "Display Title"
```

Scaffolds, preflights, generates a bind harness, deploys to public Pages,
registers (reusing an existing registration by name), generates a workbook
bound to real data, publishes, verifies the compiled SQL, prints the workbook
URL. The preflight is blocking and runs before deploy, so a plugin with a
known silent-failure mode never gets a pluginId. Override the data source
after `--`:

```bash
bash scripts/pipeline.sh my-viz "My Viz" -- \
  --dimension PRODUCT_FAMILY --measure "Sum(QUANTITY)" --measure-name Units
```

`pipeline.sh` scaffolds a **Vite + React** project — the only archetype. npm
packages (Plotly, Mapbox, D3, Recharts) are available from the start, and
`deploy-plugin.sh` runs the build.

There is no hand-written-HTML archetype. It was removed because a page loading
the SDK's UMD bundle from a CDN renders fallback data forever if React isn't
loaded ahead of it — React is an external of that bundle — and the only symptom
is one uncaught `u.createContext is not a function`. `preflight-plugin.py`,
`deploy-plugin.sh` and CI each refuse a non-React plugin. Plugins already
deployed under the old archetype keep serving, but can't be re-deployed until
ported.

Edit `plugins/<name>/src/App.jsx` and re-run. That directory is gitignored —
the public host repo holds the deployed copy, and only `_react-template` is
tracked here.

If the plugin's `configureEditorPanel` entries use names other than
`label`/`value` — check `plugins/<name>/src/App.jsx` —
pass `-- --label-key <name> --value-key <name>`. Binding the wrong key renders
the fallback, silently.

Run the four steps by hand only when something fails. They're in
`docs/plugins.md`, along with the SDK reference and every gotcha.

## Data: make it fit the plugin

There is **no built-in row set**. `pipeline.sh` passes `--plugin-src`, so the
generator reads the plugin's own `configureEditorPanel`, takes its column
bindings and `allowedTypes`, and synthesizes typed columns named to match —
it always binds, and the binding keys come from the plugin rather than a
guess. Values are visible placeholders ("Team A", "Team B").

**Your job is to replace the placeholders with rows that mean something.**
Invent data appropriate to the plugin and pass `--data <file.csv|json>`:
team names for a standings chart, funnel stages for a funnel, regions for a
map. Don't ship "Team A". If the intake asked which entities, use the ones
the user named.

```bash
bash scripts/pipeline.sh sec-bars "SEC Bars" -- --data /tmp/teams.csv
```

- Rows are compiled into a `SELECT ... FROM (VALUES ...)` literal published as
  a `kind:"sql"` element, so the data lives in the workbook spec.
- **A real source when the intake picks one**: `--path DB SCHEMA TABLE`
  plus `--dimension`/`--measure`. Discover it with `list-connections.sh`,
  `mcp-search.sh`, `mcp-describe.sh` — don't guess names. If the user wants a
  real table but can't name it, search and propose one rather than stalling.
- **Never build an input table for fabricated rows.** It publishes empty and
  the plugin silently falls back to numbers hardcoded in its own HTML.
  `insert-rows` is rejected, row-ish fields are dropped, and there's no REST
  write endpoint. Evidence in `docs/plugins.md`.
- Synthetic-vs-real is question 1 of the intake — ask it, then build to the
  answer. Still report which one you used and what the rows represent.

## Two gates run before anything irreversible

`pipeline.sh` runs both for you. Reach for them by hand while iterating.

```bash
python3 scripts/preflight-plugin.py <name> [--data FILE]      # static, blocking
python3 scripts/verify-plugin-binding.py <name> [--data FILE] # renders it twice
```

**`preflight-plugin.py` is the one that stops a bad deploy.** Every check in it
is a mode where the plugin deploys clean, publishes clean, renders its own
fallback data and screenshots perfectly -- no status code catches any of them.
It imports `build-plugin-workbook.py`'s *own* panel parser rather than
re-implementing one, so authoring and building cannot silently disagree about
the binding contract. It also checks that every `column` binding name has a
matching header in `--data`, which is the thing that makes all the bindings
resolve instead of just the first two.

**`verify-plugin-binding.py` is the only thing that proves the plugin renders
bound data.** It generates a local page that runs the plugin twice in isolated
iframes -- once with nothing bound, once with the real rows in Sigma's
column-keyed parallel-array shape -- and fails if the two renders are
identical, because that means the plugin is ignoring its bindings. No Sigma
login, no deploy, no network. It also reports which declared bindings the
plugin actually *read*, via a Proxy on the config object.

Open the URL it prints. The verdict lands in the page, in `document.title`
(`HARNESS PASS` / `HARNESS FAIL`) and in `window.__HARNESS__`, so one
`javascript_tool` call or a glance at the tab title is enough.

Escape hatches, for when a check is wrong rather than the plugin:
`SIGMA_SKIP_PREFLIGHT=1`, `SIGMA_SKIP_BINDTEST=1`.

## Order is not negotiable

```
intake (ask) → build → preflight → bind harness → deploy (public URL)
      → register (pluginId) → workbook (bind + publish) → verify
```

The two gates come first because **deploy and register are the irreversible
steps.**

`PATCH /v2/plugins/{id}` **cannot change `url`**. Registering a URL that
doesn't serve means delete + re-create, a new `pluginId`, and every workbook
referencing the old one silently broken. Deploy first; `deploy-plugin.sh`
polls until Pages serves the exact bytes, and `register-plugin.sh create`
re-checks.

Hosting must be a **public** repo — Sigma fetches the URL anonymously for the
iframe.

## Never invent a field name

Copy element shapes from `docs/elements-known-good.md`. It has every verified
shape plus a list of known-rejected ones, so nothing gets re-probed.

When a publish fails with `Invalid kind: "<kind>"`, read it as **a field has
the wrong value shape** — not "this element kind is unsupported". Sigma
rejects known fields carrying bad shapes and silently drops unknown field
names. Bisect from a known-good shape, one field at a time; the message has no
further hints. A *structural* error instead ("an action must have at least one
effect") means that part parsed fine.

## HTTP 200 does not mean it worked

Both of these publish clean and fail silently:

- A **bare `[COLUMN]`** on a warehouse source compiles to literal
  `Unknown column "[COLUMN]"`. Must be `[TABLE/COLUMN]`.
  `build-plugin-workbook.py` qualifies these for you, `validate-spec.py`'s
  `warehouse-refs-qualified` catches hand-written ones, and `pipeline.sh`
  greps the compiled SQL.
- A wrong `pluginId`, or a config binding naming a nonexistent column, renders
  an empty iframe. `validate-spec.py` catches the binding; only
  `register-plugin.sh get "$PID"` proves the plugin is really registered.

**If the plugin shows its demo data in Sigma, the binding is wrong** — that
fallback is what renders when nothing resolves. Check before reporting success.

Checking that used to need eyes on the rendered iframe, which needs a Sigma
login the in-app browser does not have. It does not any more:
`verify-plugin-binding.py` reproduces the same fallback-vs-bound distinction
locally, before deploy. Run it rather than inferring success from a 200.

## Writing the plugin

Import the client — `import { client } from '@sigmacomputing/plugin'` — and
never reach for a window global. `window.sigmaComputing.plugin.client` is
widely copied and defined by **no published bundle**;  `window.SigmaPlugin` is
real but only exists when the UMD build is loaded from a script tag, which a
bundled plugin never does. Either one reads `undefined` and the plugin renders
its fallback forever, looking fine in a screenshot. `preflight-plugin.py`'s
`sdk-global` check fails on both, and `deploy-plugin.sh` runs it.

**`docs/plugin-api.md` is the API reference — read it, not the help centre.**
The help pages cover about a third of the SDK and get several things wrong
(`allowTypes` vs `allowedTypes`, groups holding only `text`, `config.set`'s
signature, which namespace `getElementColumns` lives on).

Things worth knowing before you write a panel:

- **14 editor-panel types**, not the handful the help page lists: `group`,
  `element`, `column`, `text`, `toggle`, `checkbox`, `radio`, `dropdown`,
  `color`, `variable`, `interaction`, `action-trigger`, `action-effect`,
  `url-parameter`.
- `column` requires **both** `source` and `allowMultiple`. `allowedTypes` is
  an **allowlist**.
- **A plugin can bind several elements** — declare multiple `element` entries.
- **`variable` is the two-way channel to a workbook control**: read with
  `getVariable`, write with `setVariable`, and the *current* value lives at
  `.defaultValue.value` despite the name. This is how a plugin cross-filters.
  Do **not** use the old projected-`[controlId]`-column trick; it's retracted.
- `secure: true` on a `text` entry for an API token.
- Data is **column-keyed parallel arrays**, capped at 25,000 values.

## Fill the frame, always

**The plugin must paint the entire iframe at whatever size it is given, and
re-lay-out whenever that size changes.** The workbook author sizes the element,
not you, and they resize it freely — dragging the element, toggling the editor
panel, switching to a phone layout, expanding to full screen. A plugin that
renders at a size it chose itself is wrong at every size but one.

What that means concretely:

- `html, body, #root { height: 100% }` in `index.html`, and the root container
  `width: 100%; height: 100%`. The template ships this; don't undo it.
- **No fixed pixel width or height** on the root, the chart, or the canvas —
  and no `vh`/`vw` either. The iframe is the viewport only by accident; size
  everything from the parent box.
- Flex or grid for layout, with `minHeight: 0` on any child that has to shrink
  or scroll. Without it a flex child refuses to go below its content height and
  the plugin overflows the frame instead of fitting it.
- Content that can't fit gets an internal scroll region or drops rows — never
  an iframe-level scrollbar, and never clipped content with no way to reach it.
- Nothing may depend on the size at first paint. Sigma mounts the iframe before
  the element settles, so the first measurement is often `0` — render from the
  measurement you have now, and re-render when it changes.

Measure with a `ResizeObserver` on `document.body`, and **compare the
dimensions before re-rendering**. Re-rendering rewrites DOM inside the observed
element, so an unguarded observer feeds itself forever — a pinned CPU core with
an empty console. `preflight-plugin.py`'s `resize-observer-guard` check fails
on an observer with no size comparison; `_react-template/src/App.jsx` has the
shape to copy.

Charting libraries have their own switch for this — use it instead of hand-
rolling: Recharts `<ResponsiveContainer>`, Plotly `config={{responsive: true}}`
with `layout.autosize` and `useResizeHandler`, ECharts `chart.resize()` on the
observer tick, D3/canvas re-read `clientWidth`/`clientHeight` every tick and
re-scale the ranges (and multiply by `devicePixelRatio` for canvas).

Verify it: the bind harness from `verify-plugin-binding.py` renders the plugin
in a resizable iframe. Drag it narrow and tall, then short and wide. Anything
that clips, letterboxes, or leaves dead space at a size is a bug to fix before
deploy — after deploy the only way to see it is a Sigma login.
