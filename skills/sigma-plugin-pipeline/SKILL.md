---
name: sigma-plugin-pipeline
description: 'Use when the user wants to build, deploy, host, register or embed a Sigma Computing custom-visualization plugin — "build me a Sigma plugin", "make a custom viz for Sigma", "host this plugin", "register the plugin with my org", "add the plugin to a workbook", "put a plugin in a new workbook". One command runs the whole chain: author a single-file index.html against the @sigmacomputing/plugin SDK, deploy to public GitHub Pages, register via POST /v2/plugins for a pluginId, then generate and publish a workbook with the plugin bound to real warehouse data. Does NOT cover authoring ordinary workbooks with no plugin in them, nor Claude Code plugin/skill packaging.'
---

# Sigma plugin pipeline

## Run this

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

`pipeline.sh` scaffolds the **single-file** archetype: one `index.html`, SDK
from unpkg, no build. For anything needing npm packages — Plotly, Mapbox, D3,
Recharts — scaffold the React archetype first, then run the pipeline:

```bash
bash scripts/new-plugin.sh my-map "My Map" --react
bash scripts/pipeline.sh my-map "My Map"
```

`deploy-plugin.sh` builds it. Then edit `plugins/<name>/` and re-run. That
directory is gitignored — the public host repo holds the deployed copy, and
only the two templates are tracked here.

If the plugin's `configureEditorPanel` DEFS use names other than
`label`/`value` — check with `grep -A6 'DEFS = \[' plugins/<name>/index.html` —
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
map. Don't ship "Team A".

```bash
bash scripts/pipeline.sh sec-bars "SEC Bars" -- --data /tmp/teams.csv
```

- Rows are compiled into a `SELECT ... FROM (VALUES ...)` literal published as
  a `kind:"sql"` element, so the data lives in the workbook spec.
- **A real source only when the user names one**: `--path DB SCHEMA TABLE`
  plus `--dimension`/`--measure`. Discover it with `list-connections.sh`,
  `mcp-search.sh`, `mcp-describe.sh` — don't guess names.
- **Never build an input table for fabricated rows.** It publishes empty and
  the plugin silently falls back to numbers hardcoded in its own HTML.
  `insert-rows` is rejected, row-ish fields are dropped, and there's no REST
  write endpoint. Evidence in `docs/plugins.md`.
- Don't ask the user fake-vs-real. Build, then say what you used.

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
build → preflight → bind harness → deploy (public URL) → register (pluginId)
      → workbook (bind + publish) → verify
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

The SDK's UMD bundle defines **one** global: `window.SigmaPlugin`, client at
`SigmaPlugin.client`. `window.sigmaComputing.plugin.client` is widely copied
and defined by **no published bundle** — a plugin reading it gets
`client === null` and renders its fallback forever, looking fine in a
screenshot. `deploy-plugin.sh` refuses to publish a plugin that doesn't
reference `SigmaPlugin`.

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
