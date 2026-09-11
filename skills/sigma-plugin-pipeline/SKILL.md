---
name: sigma-plugin-pipeline
description: Use when the user wants to build, deploy, host, register or embed a Sigma Computing custom-visualization plugin — "build me a Sigma plugin", "make a custom viz for Sigma", "host this plugin", "register the plugin with my org", "add the plugin to a workbook", "put a plugin in a new workbook". Runs the whole chain: author a single-file index.html against the @sigmacomputing/plugin SDK, deploy it to public GitHub Pages, register it via POST /v2/plugins to get a pluginId, then generate and publish a workbook with a plugin element bound to its data. Always asks first whether the workbook should use fake seeded data or a real warehouse table. Does NOT cover authoring ordinary workbooks with no plugin in them, nor Claude Code plugin/skill packaging.
---

# Sigma plugin pipeline

Four steps, in this order, because each one's output is the next one's input:

```
build  →  deploy (public URL)  →  register (pluginId)  →  workbook (bind + publish)
```

Never reorder deploy and register. **Sigma's `PATCH /v2/plugins/{id}` cannot
change a plugin's `url`.** Registering a URL that turns out not to serve means
delete + re-create, which mints a *different* `pluginId` and silently breaks
every workbook already pointing at the old one.

## Step 0 — the data question. Ask it before building anything.

Before writing a plugin or touching the API, ask the user — with
`AskUserQuestion` if available, otherwise in plain text, **ending the turn**
immediately after so they actually answer:

> Should the workbook use **fake seeded data** or a **real table / dataset**?

Default to **fake**. Offer these as the options:

- **Fake (default)** — the generated workbook gets an `input-table` element
  plus a "Seed demo data" button. The rows land in Sigma as a real, editable
  table, so the plugin exercises the same data path it will use in production
  and a reviewer can change the numbers in the UI. Needs only a connection id.
- **Real** — the workbook gets a `table` element on a warehouse table, and the
  plugin binds to that. Needs a connection, a `DATABASE SCHEMA TABLE` path, and
  the column names.

Do not assume the answer from context and do not proceed on silence. If they
pick real, discover what is actually available rather than guessing at names:

```bash
bash scripts/api/list-connections.sh
bash scripts/api/probe-schema-tables.sh <connection-id>
bash scripts/api/list-table-columns.sh <connection-id> DB SCHEMA TABLE
```

## Step 1 — build

```bash
bash scripts/new-plugin.sh <plugin-name> "Display Title"
```

Scaffolds `plugins/<plugin-name>/index.html` from the template: SDK from
unpkg, `configureEditorPanel`, `config.subscribe` +
`subscribeToElementData`, and a synthetic fallback so the frame is never
blank. Then edit two things:

- **`DEFS`** — the editor-panel bindings. Each `name` is the key that arrives
  in the config object *and* the key a workbook spec's plugin `config` must
  use. Keep them stable; renaming one silently unbinds existing workbooks.
- **`draw()`** and **`synth()`** — the visual, and the demo data behind it.

Two runtime facts that shape any plugin you write here:

- `subscribeToElementData` yields an object **keyed by column ID**, each value
  a parallel array of cells — not an array of row objects. Zip by index.
- Always release the previous element subscription before opening a new one.
  Re-binding the source otherwise leaks a listener that keeps overwriting your
  state with the old element's rows.

The file runs with no Sigma client at all, so open it in a browser to iterate.

## Step 2 — deploy

```bash
URL=$(bash scripts/deploy-plugin.sh <plugin-name>)
```

Pushes to the **public** host repo (`tyleraspencer/sigma-plugins`) and polls
the live URL until it returns `200 text/html`, then prints it.

It must be a public repo: Sigma fetches the URL anonymously into an iframe, so
a private repo's Pages output will not serve. And it must be GitHub Pages, not
jsDelivr — jsDelivr returns `.html` as `text/plain`, which renders the plugin
as raw source text and hangs PNG export. The script fails on both conditions
rather than letting you register a bad URL.

## Step 3 — register

```bash
PID=$(bash scripts/api/register-plugin.sh create "Display Title" "$URL")
```

Prints only the `pluginId` on stdout. It re-checks the URL first, for the
immutability reason above. Writes need Admin or the **Manage plugins**
permission; a 403 here means an org admin has to register it or grant that.

Already registered? `register-plugin.sh list` or `id-for "<exact name>"`.

## Step 4 — workbook

```bash
# fake (default)
python3 scripts/build-plugin-workbook.py --name "<Workbook Name>" \
  --plugin-id "$PID" --data-mode fake \
  --connection-id <conn> --data rows.json \
  --bind <plugin-key>=<Column Name> --bind <plugin-key>=<Column Name> \
  --out spec.json

# real
python3 scripts/build-plugin-workbook.py --name "<Workbook Name>" \
  --plugin-id "$PID" --data-mode real \
  --connection-id <conn> --path DB SCHEMA TABLE --columns COL_A COL_B \
  --bind <plugin-key>=COL_A --bind <plugin-key>=COL_B \
  --out spec.json

bash scripts/api/publish-workbook.sh post spec.json
```

`publish-workbook.sh` validates the spec, wraps it in the `document` envelope,
POSTs it, and then runs the schema audit automatically.

`--bind` maps a `DEFS` key to a column and is checked against the columns the
generator just created, so a typo fails locally instead of rendering an empty
plugin in Sigma.

**Fake mode needs one manual click.** Sigma cannot pre-populate an input table
from a spec — `insert-rows` is a runtime action effect, and one effect inserts
exactly one row. The generated workbook therefore carries a "Seed demo data"
button holding one effect per row. Tell the user plainly: open the workbook and
click it once. Don't report the build as finished without saying so.

## Verify before claiming success

A plugin element that publishes cleanly and renders blank is the normal failure
here — Sigma validates neither the `pluginId` nor the config bindings at POST
time, so both return 200 and then show an empty iframe.

`validate-spec.py`'s `plugin-refs-resolve` catches a malformed `pluginId`, a
bad `config.source`, and bindings naming columns that don't exist. It **cannot**
confirm the `pluginId` is registered in this org — check that separately:

```bash
bash scripts/api/register-plugin.sh get "$PID"
bash scripts/api/verify-workbook.sh <workbook-id>
```

Then look at the workbook. If the plugin shows its demo data in Sigma, the
binding is wrong — that fallback is exactly what renders when nothing resolves.

## Gotchas that will cost you a rebuild

- **`url` is immutable on PATCH.** Deploy and verify before registering.
- **Re-publishing a harvested spec** that contains an input table: strip every
  system column (`ID`, `CREATED_AT`, `CREATED_BY`, `UPDATED_AT`, `UPDATED_BY`)
  back to a bare `{"id": ...}` first. Sigma adds a `formula` field to them on
  GET, and re-submitting that fails PUT with "system column `ID` cannot set
  `type` or `formula`". Every time, not just the first.
- **Controls cannot bind to a plugin's `config` directly.** Plugin config binds
  columns, and controls only parametrize filter values. To drive a plugin from
  a control, add one constant column to the plugin's source element whose
  formula is a bare `[<controlId>]` reference, then bind that column.
- **A `linked`-source input table rejects `delete-rows`** (`empty`-source is
  fine).
- **Input-table column order is not preserved** on GET-back. Diff by column
  `id`, never by array position.
