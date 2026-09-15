---
name: sigma-plugin-pipeline
description: 'Use when the user wants to build, deploy, host, register or embed a Sigma Computing custom-visualization plugin — "build me a Sigma plugin", "make a custom viz for Sigma", "host this plugin", "register the plugin with my org", "add the plugin to a workbook", "put a plugin in a new workbook". Opens with a short intake — which Sigma org to build in, synthetic rows vs a real table, visual direction, and anything else specific to the request — then one command runs the whole chain: author a Vite + React plugin against the @sigmacomputing/plugin SDK, deploy to public GitHub Pages, register via POST /v2/plugins for a pluginId, then generate and publish a workbook with the plugin bound to real warehouse data. Does NOT cover authoring ordinary workbooks with no plugin in them, nor Claude Code plugin/skill packaging.'
---

# Sigma plugin pipeline

```
intake (ask) → build → preflight → bind harness → deploy → register
      → workbook (bind + publish) → verify
```

The two gates come before deploy because **deploy and register are the
irreversible steps**: `PATCH /v2/plugins/{id}` cannot change `url`, so
registering a URL that does not serve means delete + re-create, a new
`pluginId`, and every workbook referencing the old one silently broken.

## Step 0: ask before you build

**One `AskUserQuestion` call, 3–4 questions, before anything runs.** One round
only. Deploy and register are irreversible and a workbook is something the user
shows other people, so ask about what you would otherwise guess.

1. **Environment — always, and first.** Registration writes a `pluginId` into
   whatever org the token belongs to. Recommend
   **`app.sigmacomputing.com/papercrane`** (`SIGMA_BASE_URL=https://api.sigmacomputing.com`).
   Anywhere else needs that org's *region host*, which is not derivable from the
   app URL — `docs/auth.md` → "Egress allowlist" — then re-auth. Confirm with
   `bash scripts/api/whoami.sh` before `pipeline.sh` runs: a token left over
   from another org registers in the wrong place without complaining.
2. **Data — always.** Recommend **synthetic rows** (`--data <file>`): you invent
   rows that fit the topic, compiled into the spec as a `kind:"sql"` VALUES
   literal, no warehouse access. Otherwise the **Sigma Sample Database**
   (`--path RETAIL PLUGS_ELECTRONICS PLUGS_ELECTRONICS_HANDS_ON_LAB_DATA` — the
   `_DATA` suffix is part of the name), or another table via `--path` +
   `--bind`. Don't guess table names: `list-connections.sh`, `mcp-search.sh`,
   `mcp-describe.sh`.
3. **Look and feel — always**, on whichever axis most changes *this* plugin's
   code: chart form (when the ask names a goal, not a shape), color, density,
   or branding. `docs/design-system.md` is already the default — ask what
   deviates from it, not what the style should be. Brand colors replace the
   accent and its tint pair; the neutrals never move.
4. **Whatever else changes the build**: does clicking it do anything
   (cross-filter needs a `variable`; an *action* needs an `action-trigger` —
   both structural); what the entities are; thresholds for anything computed;
   multiple element bindings; existing workbook or new.

**Skip anything the request already answers**, never ask four out of habit, put
the recommended option first and label it. If the user waves you off — "just
build it", "you pick" — take the recommendation on every question, build the
whole thing, and say what you chose. Don't ask twice. Don't ask what the
scripts can answer (column names, the pluginId, whether a registration
exists): look those up.

## Run this

```bash
bash scripts/pipeline.sh <plugin-name> "Display Title" [-- <build-workbook args>]
```

Scaffolds, preflights, generates the bind harness, deploys to public Pages,
registers (reusing an existing registration by name), generates a workbook
bound to real data, publishes, verifies the compiled SQL, prints the workbook
URL last. Data flags go after `--`:

```bash
bash scripts/pipeline.sh brand-bars "Brand Bars" -- --data /tmp/brands.csv
bash scripts/pipeline.sh brand-bars "Brand Bars" -- \
  --path RETAIL PLUGS_ELECTRONICS PLUGS_ELECTRONICS_HANDS_ON_LAB_DATA \
  --dimension BRAND --measure "Sum(PRICE * QUANTITY)" --measure-name Revenue
```

**Start from the nearest shape.** `plugins/_archetypes/` holds finished
`App.jsx` files — `table`, `kpi`, `funnel`, `donut` — that already satisfy every
preflight check and bind `label` then `value`:

```bash
bash scripts/new-plugin.sh <name> "Title" --from kpi   # --from list to see them
```

Vite + React is the only project shape; npm packages (Plotly, Mapbox, D3,
Recharts) are available from the start. If the panel uses names other than
`label`/`value`, pass `-- --label-key <name> --value-key <name>`; binding the
wrong key renders the fallback silently. More than two columns needs `--bind`
(→ `docs/plugins.md`).

**Tell the user the workbook needs publishing** when the run printed
`NOT PUBLISHED YET` — the spec API writes the draft and there is no publish
API, so a URL handed over straight from the pipeline shows everyone else the
previous published version. Say it with the link: *"open it and click
Publish."* When nothing was written (`--ship`/`--redeploy`, or an unchanged
spec), there is nothing pending and nothing to mention.

## How to report a finished build

**Two or three sentences, then four links.** The user watched the pipeline
scroll past; a retelling of it is noise, and the links are what they have to
click. No step-by-step, no inventory of files, no recap of the gates — they
passed or you would not be delivering. Save the long version for failures.

1. **Code** — `https://github.com/<host-repo>/tree/main/plugins/<name>`
   (`tyleraspencer/sigma-plugins` unless `SIGMA_PLUGIN_HOST_REPO` says
   otherwise). That holds the *built* bundle, so name the editable source too:
   `plugins/<name>/src/App.jsx`.
2. **Deployed plugin** — the Pages URL the run printed.
3. **Data** — one phrase. Synthetic: what the rows *are* ("18 rows, Plugs brands
   × quarterly revenue"). Real: the three-part path.
4. **Workbook, in edit mode** — last, closest to the cursor:
   `https://app.sigmacomputing.com/<org>/workbook/<id>/edit`.

`pipeline.sh` prints the workbook URL as its **last line of stdout** and caches
it as `workbook_url` in
`~/.cache/sigma-plugin-kit/deploys/<host-slug>__<name>.env`. Append `/edit`.
**Don't assemble it from the `workbookId`** — the URL carries Sigma's short
document id, not the UUID the publish response returns, and a URL built from
the UUID 404s. Without the printed line, take the `url` field from
`publish-workbook.sh get-meta <id>`.

## Editing after the first build

**Never re-run the whole chain for a visual tweak.** The URL and `pluginId` are
settled at the first deploy and the workbook goes on pointing at them, so an
edit to `App.jsx` changes exactly one thing: the bundle.

| What changed | Command |
| --- | --- |
| Still iterating on how it looks | `cd plugins/<name> && npm run dev`, then the element's **•••** → **Point to Development URL** → `http://localhost:5173`. Hot-reloads; changing the *panel* means re-entering panel values. |
| Want a local verdict, no Sigma tab | `( cd plugins/<name> && npm run build ) && python3 scripts/verify-plugin-binding.py <name> [--data FILE]` |
| Shipping a bundle change you are sure of | `bash scripts/pipeline.sh <name> --ship` — build + deploy + confirm the registration. Skips both gates; says so every time. |
| Shipping a bundle change, gates on | `bash scripts/pipeline.sh <name> --redeploy` |
| The panel, bindings, data or title changed | `bash scripts/pipeline.sh <name> "Title" -- <the same flags>` — regenerates the spec and **updates the same workbook in place**, so a shared link keeps working. |

Pass the **same** `--data`/`--path`/`--bind` flags you built with: dropping them
generates a different spec, which counts as a change and republishes.
`--new-workbook` forces a second workbook; `--workbook-id <id>` re-attaches one
the kit has lost track of.

**Neither `--ship` nor `--redeploy` buys wall clock** — the gates cost 0.25s
between them and the only real wait in a deploy is GitHub Pages. They buy fewer
moving parts.

**A workbook assembled by post-processing a generated spec must not be
regenerated** — the generator cannot see hand-added elements, so a regenerating
PUT drops them. Leave no `workbook_id` in the deploy cache for those, and PUT
by hand.

`plugins/<name>/` is gitignored; the public host repo holds the deployed copy.

## Data: make it fit the plugin

There is **no built-in row set**. `pipeline.sh` passes `--plugin-src`, so the
generator reads the plugin's own `configureEditorPanel` and synthesizes typed
columns named to match — it always binds, but the values are placeholders
("Brand A"). **Replace them**: invent rows that mean something and pass
`--data <file.csv|json>`.

**Stay in the Plugs Electronics world.** No sports, teams, leagues or seasons —
not in rows, not in plugin names, not in examples. Brands, product families,
SKUs, stores, regions, revenue, quantity, margin.

**Never build an input table for fabricated rows**: it publishes empty and the
plugin silently falls back to numbers hardcoded in its own source. Rows can
only get in from a user action (`insert-rows`), not from the spec. Report which
source you used and what the rows represent. Full reference, including
`groupingId` and `--bind`: `docs/plugins.md` → "Data: generated to fit the
plugin".

## A plugin owns its own actions

**Standing rule. An action a plugin causes is triggered BY the plugin** — never
by a button the user presses afterwards, never by the `on-change` of a control
the plugin wrote, never by any other native element standing in for it. "Clicking
the plugin does X" means one click.

```
panel:  { type: 'action-trigger', name: 'onPick' }
code:   client.config.triggerAction(config.onPick)   // AFTER the variable writes
spec:   plugin.config.onPick = {kind:"action-trigger", actionTriggerId: ID}
        plugin.actions = [{trigger:{kind:"action-trigger",actionTriggerId:ID},
                           effects:[...]}]           // on the PLUGIN element
```

`ID` is yours to invent (22 chars, base62) and must appear in both halves. The
trigger carries **no payload**, so write the `variable` controls first and fire
the trigger last — that ordering is what lets the effects read what was
clicked.

A control's `on-change` is **unverified** as a way to fire an action from a
plugin's `setVariable`, and the evidence is that it does not fire: it publishes
clean, validates clean, and silently never runs, which is indistinguishable
from a broken plugin. Both gates enforce the rule —
`preflight-plugin.py` → `action-trigger-wired` and `validate-spec.py` →
`plugin-owns-its-actions`, the latter on every `post`/`put`. Shapes and the
harvest behind them: `docs/plugin-api.md` → "A plugin owns its own actions".
Worked example: `docs/examples/price-swarm-queue.py`.

## Two gates, and what they cannot catch

```bash
python3 scripts/preflight-plugin.py <name> [--data FILE] [-v]   # static, blocking
python3 scripts/verify-plugin-binding.py <name> [--data FILE]   # renders it twice
```

Both quiet by default — one line unless something has something to say; `-v`
lists every check. `pipeline.sh` runs them before deploy. **The bind harness is
the only thing that proves the plugin renders bound rather than fallback data**:
open the URL it prints and wait for `document.title` to stop saying
`HARNESS RUNNING`. Checks marked *advisory* report without blocking.

Neither gate can catch these, so check them by hand:

- **Blank until resized.** Attach the `ResizeObserver` from a **callback ref**,
  not a mount effect: before data arrives there is no chart node, so the effect
  observes only `document.body`, which never resizes — and the plugin stays
  blank until the window does. The harness *hides* this, because its own
  `fills-frame` probe supplies the missing resize.
- **Variable writes and action triggers.** The generated harness `cfg` carries
  only column bindings, so every `if (config.pickX)` guard is false and clicks
  are inert.

Both, with the patch recipes: `docs/plugins.md` → "The two gates".

## What fails silently

Write the plugin normally and the gates will tell you if you tripped one:

- **Import the client** — `import { client } from '@sigmacomputing/plugin'`,
  never a window global. (`sdk-global`)
- **Fill the iframe at every size** — `height: 100%` down the chain, no fixed
  `px`/`vh`/`vw`, `minHeight: 0` on flex children that must shrink, and a
  guarded `ResizeObserver` that compares dimensions before re-rendering. The
  first measurement is often `0`; render from it anyway. (`root-height`,
  `viewport-units`, `flex-min-height`, `resize-observer-guard`, `fills-frame`)
- **Demo data showing in Sigma means the binding is wrong**, not that the
  plugin is fine. (`binds-vs-fallback`)

**No gate catches this one.** `Invalid kind: "<kind>"` at publish means **a
field has the wrong value shape** — not that the element kind is unsupported.
Sigma rejects known fields carrying bad shapes and silently drops field names
it does not recognise, so the obvious reading sends you rewriting a perfectly
good element as some other kind. Bisect from a known-good shape one field at a
time. A *structural* complaint instead ("an action must have at least one
effect") means that part parsed fine. → `docs/elements-known-good.md`, which
has every verified shape and the full decoder. When a shape is not in there,
**harvest it**: find a workbook where a human did it in the UI and
`GET /v2/workbooks/<id>/spec`. That is how `insert-rows`, the `input-table`
element and the plugin action-trigger were all settled, after probing blind had
concluded the first was impossible.

## Before you write a panel

`docs/plugin-api.md` is the SDK reference — **read it, not the help centre**,
which covers about a third of the API and gets several things wrong
(`allowTypes` vs `allowedTypes`, `config.set`'s signature, which namespace
`getElementColumns` lives on). Two things are structural, so decide them during
intake: **`variable`** is the two-way channel to a workbook control and the only
way a plugin cross-filters, and **a plugin can bind several elements** by
declaring multiple `element` entries. Everything else — all 14 panel types,
`column` needing both `source` and `allowMultiple`, `allowedTypes` being an
allowlist, `secure: true` for tokens, the 25,000-value cap — is reference.

**Read `docs/design-system.md` before writing or editing an `App.jsx`.** It is
short, and it is what keeps a plugin from looking embedded rather than native.
