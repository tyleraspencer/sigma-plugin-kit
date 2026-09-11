---
name: sigma-plugin-pipeline
description: Use when the user wants to build, deploy, host, register or embed a Sigma Computing custom-visualization plugin — "build me a Sigma plugin", "make a custom viz for Sigma", "host this plugin", "register the plugin with my org", "add the plugin to a workbook", "put a plugin in a new workbook". One command runs the whole chain: author a single-file index.html against the @sigmacomputing/plugin SDK, deploy to public GitHub Pages, register via POST /v2/plugins for a pluginId, then generate and publish a workbook with the plugin bound to real warehouse data. Does NOT cover authoring ordinary workbooks with no plugin in them, nor Claude Code plugin/skill packaging.
---

# Sigma plugin pipeline

## Just run this

```bash
bash scripts/pipeline.sh <plugin-name> "Display Title"
```

Scaffolds, deploys to public Pages, registers (or reuses an existing
registration), generates a workbook bound to real data, publishes, verifies
the compiled SQL, and prints the workbook URL. Override the data source by
passing anything after `--`:

```bash
bash scripts/pipeline.sh my-viz "My Viz" -- \
  --dimension PRODUCT_FAMILY --measure "Sum(QUANTITY)" --measure-name Units
```

Then edit `plugins/<plugin-name>/index.html` and re-run. Re-running reuses the
registration rather than minting a second `pluginId`.

`plugins/<plugin-name>/` is a gitignored working directory — the public host
repo holds the deployed copy. Don't try to commit a plugin to this repo; only
`plugins/_template/` is tracked.

Do the steps by hand only when something fails or the shape is unusual.

## Data: default to the sample retail table. Do not build input tables.

The default source is **verified and hardcoded**: connection
`Sigma Sample Database`, warehouse path
`RETAIL.PLUGS_ELECTRONICS.PLUGS_ELECTRONICS_HANDS_ON_LAB_DATA`, grouped by
`STORE_REGION` with `Sum(PRICE * QUANTITY)` as `Revenue`. Use it unless the
user names a different source. **Do not ask which data to use** — just build
on the default and say which source you used.

Useful columns on that table: `STORE_REGION`, `STORE_STATE`, `STORE_CITY`,
`STORE_NAME`, `PRODUCT_TYPE`, `PRODUCT_FAMILY`, `PRODUCT_LINE`, `BRAND`,
`PRODUCT_NAME`, `SKU_NUMBER`, `CUSTOMER_NAME`, `DATE`, `QUANTITY`, `PRICE`,
`COST`, `ORDER_NUMBER`.

**Never generate an input table for synthetic data.** It cannot work: Sigma
has no way to populate an input table from a spec (`insert-rows` is a runtime
effect, one row each, and is currently rejected by the spec API), and no
source kind accepts literal rows — `sql`, `custom-sql`, `customSql`,
`warehouse-sql`, `manual` and `inline` were all probed and refused. An input
table publishes empty, the plugin falls through to its own synthetic
fallback, and you ship a chart showing hardcoded numbers. Bind to a real
table instead.

For a different real source, discover it rather than guessing names:

```bash
bash scripts/api/list-connections.sh
bash scripts/api/mcp-search.sh "<topic>" --types table --limit 5
bash scripts/api/mcp-describe.sh table <inodeId>      # exact columns + path
```

## Two rules that will save you a failed publish

**1. Copy element shapes from `docs/elements-known-good.md`. Never invent
field names.** Every verified shape is there. A text element's content field
is `body`, not `text`/`variant` — getting that wrong costs a round trip and
the error blames the element kind.

**2. Read `Invalid kind: "<kind>"` as "a field has the wrong value shape",**
not "this element kind is unsupported". Sigma rejects known fields carrying
bad shapes and silently drops unknown field names. Bisect from a known-good
shape, adding one field at a time. The message contains no further hints, so
do not re-read it looking for them. A *structural* error instead (e.g. "an
action must have at least one effect") means that part parsed fine.

## HTTP 200 does not mean it works

Two silent failures to check for, both of which return success:

- A **bare column reference** against a warehouse source publishes fine and
  compiles to literal `'Unknown column "[PRICE]"'` in the SQL. Warehouse
  columns must be `[TABLE/COLUMN]`. `pipeline.sh` greps the compiled SQL and
  fails on this; `validate-spec.py` does **not** catch it.
- A wrong `pluginId` or a config binding naming a nonexistent column both
  publish clean and render an empty iframe. `validate-spec.py`'s
  `plugin-refs-resolve` catches the binding case; only
  `register-plugin.sh get "$PID"` confirms the plugin is really registered.

**If the plugin shows its demo data in Sigma, the binding is wrong.** That
fallback is exactly what renders when nothing resolves.

## Order is not negotiable

```
build → deploy (public URL) → register (pluginId) → workbook (bind + publish)
```

`PATCH /v2/plugins/{id}` **cannot change `url`**. Registering a URL that
doesn't serve means delete + re-create, a new `pluginId`, and every workbook
referencing the old one silently broken. Deploy and confirm `200 text/html`
first — `deploy-plugin.sh` polls, and `register-plugin.sh create` re-checks.

Hosting must be a **public** repo (`tyleraspencer/sigma-plugins`): Sigma
fetches the URL anonymously for the iframe, so a private repo's Pages output
will not serve. Use Pages, not jsDelivr, which serves `.html` as `text/plain`.

## Writing the plugin

The SDK's UMD bundle defines **one** global: `window.SigmaPlugin`, client at
`SigmaPlugin.client`. `window.sigmaComputing.plugin.client` is a
widely-copied pattern that **no published bundle defines** — a plugin reading
it gets `client === null` and renders its fallback forever, looking fine in a
screenshot. `deploy-plugin.sh` refuses to publish a plugin that doesn't
reference `SigmaPlugin`.

- `configureEditorPanel(DEFS)` — each `name` is the config key *and* the key
  a workbook spec's plugin `config` must use. Renaming one silently unbinds
  existing workbooks.
- `subscribeToElementData` yields an object **keyed by column ID**, each value
  a parallel array of cells — not row objects. Zip by index.
- Release the previous subscription before opening a new one, or re-binding
  leaks a listener that overwrites state with the old element's rows.
- Keep the synthetic fallback deterministic, and badge it visibly so nobody
  mistakes it for real data.

Full detail: `docs/plugins.md`. Verified shapes:
`docs/elements-known-good.md`.
