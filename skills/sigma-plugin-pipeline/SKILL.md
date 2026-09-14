---
name: sigma-plugin-pipeline
description: 'Use when the user wants to build, deploy, host, register or embed a Sigma Computing custom-visualization plugin — "build me a Sigma plugin", "make a custom viz for Sigma", "host this plugin", "register the plugin with my org", "add the plugin to a workbook", "put a plugin in a new workbook". Opens with a short intake — which Sigma org to build in, synthetic rows vs a real table, visual direction, and anything else specific to the request — then one command runs the whole chain: author a Vite + React plugin against the @sigmacomputing/plugin SDK, deploy to public GitHub Pages, register via POST /v2/plugins for a pluginId, then generate and publish a workbook with the plugin bound to real warehouse data. Does NOT cover authoring ordinary workbooks with no plugin in them, nor Claude Code plugin/skill packaging.'
---

# Sigma plugin pipeline

## Step 0: ask before you build

**One `AskUserQuestion` call, before `pipeline.sh` runs.** Deploy and register
are irreversible and a workbook is something the user shows other people, so
the things worth a round trip are the ones you would otherwise guess: which
org it lands in, where the data comes from, and what it should look like.
Guess the environment wrong and a `pluginId` gets written into somebody else's
org; guess the data source wrong and it's a rebuild; guess the visual
direction wrong and you ship a plugin nobody uses.

Ask **3–4 questions in a single call**, then build. One round only — don't
come back for a second. Questions 1 and 2 have fixed shapes; the rest are
yours to pick.

**1. Environment — always ask, and ask it first.** Registration writes a
`pluginId` into whatever org the token belongs to, and `PATCH /v2/plugins/{id}`
cannot move it afterwards.

- **`app.sigmacomputing.com/papercrane`** *(recommend first)* — the default
  org. `SIGMA_BASE_URL=https://api.sigmacomputing.com`.
- **Somewhere else** — ask for the org's app URL, then set `SIGMA_BASE_URL` to
  that org's **region host**, which is not derivable from it. Look it up in
  `docs/auth.md` → "Egress allowlist", and re-auth against that host.

Then confirm the session really is pointed there before `pipeline.sh` runs:
`bash scripts/api/whoami.sh` prints the host and the signed-in user. A token
left over from another org registers the plugin in the wrong place without
complaining.

**2. Data — always ask.** The other fixed shape:

- **Synthetic rows** *(recommend first)* — you invent rows that fit the topic
  and they compile into the workbook as a `kind:"sql"` VALUES literal. No
  warehouse access, works in any org. Pass `--data <file.csv|json>`.
- **Sigma Sample Database** — the known-good real table. `--path RETAIL
  PLUGS_ELECTRONICS PLUGS_ELECTRONICS_HANDS_ON_LAB_DATA`, and **the `_DATA`
  suffix is part of the name** — the short one won't resolve. Geocoded, so
  it's what anything map-shaped should bind. Column list and the
  which-orgs-have-it caveat: `docs/plugins.md` → "The known-good real table".
- **Something else** — any other warehouse table: `--path DB SCHEMA TABLE`
  with `--dimension`/`--measure`, or `--bind` past two columns. Find it with
  `list-connections.sh` / `mcp-search.sh` / `mcp-describe.sh`; don't guess
  names.

If they want a real table and can't name one, don't stall: search, propose the
best match, and say you'll fall back to synthetic rows if it's wrong.

**3. Look and feel — always ask**, on whichever axis most changes the code
*for this plugin*, not a generic "what style?":

- **Chart form**, when the request names a goal rather than a shape —
  "compare regions" could be bars, a map or a dot plot. This one also picks
  the archetype.
- **Color** — one accent, a categorical palette, or a value-driven scale.
- **Density** — compact and data-dense, or large and presentation-ready.
- **Branding** — Sigma-native, or someone's colors and logos.

**4. Whatever else actually changes the build**, and nothing that doesn't:

- **Does clicking it do anything?** Cross-filtering needs a `variable` panel
  entry wired to a workbook control — structural, not a later tweak.
- **What are the entities?** Brands, product families, store regions, funnel
  stages, SKUs. You need real names before you can write synthetic rows.
- **Thresholds and rules** for anything computed — what counts as good, how a
  score is weighted, where a cutoff sits.
- **Multiple elements?** A plugin can bind more than one.
- **Where it lives** — an existing workbook, or a new one.

### Rules for the intake

- **Skip anything the request already answers.** "A bar chart of Plugs revenue
  by product family, in papercrane, off the sample retail table" has already
  told you the org, the source, the form and the entities — ask about the color
  scale and whether clicking it filters anything, and stop.
- **Never ask four out of habit.** Environment and data are fixed; add a third
  only if it changes the code, and a fourth only if it changes the code too.
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
bash scripts/pipeline.sh brand-bars "Brand Bars" -- --data /tmp/brands.csv

# real table — the Sigma Sample Database option from question 2
bash scripts/pipeline.sh brand-bars "Brand Bars" -- \
  --path RETAIL PLUGS_ELECTRONICS PLUGS_ELECTRONICS_HANDS_ON_LAB_DATA \
  --dimension BRAND --measure "Sum(PRICE * QUANTITY)" --measure-name Revenue
```

**More than a label and a value** — a map wants zip, latitude, longitude *and*
a measure — names each column with `--bind KEY[:Display]=FORMULA`, keyed by
its editor-panel binding. A bare column reference groups, an expression
aggregates, and `--dimension`/`--measure` stay the two-column default. A
`--variable-control` on a warehouse source additionally needs
`--control-values FILE`. Both, with worked examples: `docs/plugins.md` →
"Binding more than two columns".

Pass `--data` alongside `--path` when you have a sample of the real rows: the
workbook still binds the warehouse table, but preflight checks the binding
names against those headers and the bind harness renders them.

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
known silent-failure mode never gets a pluginId. That is the *first* build —
every run after it takes a shorter path, see "Editing after the first build".
Override the data source after `--`:

```bash
bash scripts/pipeline.sh my-viz "My Viz" -- \
  --dimension PRODUCT_FAMILY --measure "Sum(QUANTITY)" --measure-name Units
```

**Start from the nearest shape.** `plugins/_archetypes/` holds finished
`src/App.jsx` files — `table`, `kpi`, `funnel`, `donut` — that already satisfy
every rule preflight enforces. Editing one beats writing 190 lines of React:

```bash
bash scripts/new-plugin.sh <name> "Title" --from kpi   # --from list to see them
bash scripts/pipeline.sh <name> "Title"                # then the usual chain
```

They all bind `label` then `value`, so no extra flags are needed. Pick by
shape, then edit — the palette, the fields, the labels are yours to change.
Take the default bar chart when nothing is close.

`pipeline.sh` scaffolds a **Vite + React** project — the only project shape.
npm packages (Plotly, Mapbox, D3, Recharts) are available from the start, and
`deploy-plugin.sh` runs the build. There is no hand-written-HTML archetype;
preflight, deploy and CI each refuse a non-React plugin. Why it was removed:
`docs/plugins.md` → "Why there is no hand-written-HTML archetype".

If the plugin's `configureEditorPanel` entries use names other than
`label`/`value` — check `plugins/<name>/src/App.jsx` —
pass `-- --label-key <name> --value-key <name>`. Binding the wrong key renders
the fallback, silently.

**Tell the user the workbook needs publishing.** The spec API writes the
workbook's *draft*, and Sigma has no API to publish it — so a URL handed over
straight from `pipeline.sh` shows everyone else the previous published
version. When the run printed `NOT PUBLISHED YET`, say so when you deliver the
link: *"open it and click Publish."* When it didn't — a `--redeploy`, or a
spec that didn't change — nothing is pending and there is nothing to mention.
→ `docs/plugins.md` → "A published workbook needs a human"

The publish note goes *with* the workbook link, in the handoff shape below.

Run the four steps by hand only when something fails. They're in
`docs/plugins.md`, along with the SDK reference and every gotcha.

## How to report a finished build

When the chain finishes, the user is looking for four links and almost nothing
else. They already watched the pipeline scroll past, so a retelling of it is
noise — and the links are what they actually have to click next.

**Two or three sentences, then the list.** The prose says what the plugin is
and what it shows; that's all. No step-by-step of the seven pipeline stages, no
inventory of files touched, no recap of the gates — the gates passed or you
wouldn't be delivering. Save the long version for when something went wrong.

Then exactly these four, in this order:

1. **Code** — `https://github.com/<host-repo>/tree/main/plugins/<name>`
   (`tyleraspencer/sigma-plugins` unless `SIGMA_PLUGIN_HOST_REPO` says
   otherwise). That folder holds the *built* bundle; the editable source is
   gitignored and local, so name that path too — `plugins/<name>/src/App.jsx`.
2. **Deployed plugin** — the Pages URL, which is the `plugin:` line
   `pipeline.sh` prints at the end and the `plugin_url` in its state file:
   `https://<owner>.github.io/<repo>/plugins/<name>/index.html`.
3. **Data** — one phrase, not a paragraph. Synthetic: what the rows *are*
   ("18 synthetic rows, Plugs brands × quarterly revenue"). Real table: the
   three-part path you bound.
4. **Workbook, in edit mode** — the one that matters most, and last so it sits
   closest to the user's cursor:
   `https://app.sigmacomputing.com/<org>/workbook/<id>/edit`

### Getting the edit URL right

`pipeline.sh` prints the workbook URL as its **last line of stdout** — already
`https://app.sigmacomputing.com/<org>/workbook/<id>` — and caches it as
`workbook_url` in
`~/.cache/sigma-plugin-kit/deploys/<host-slug>__<name>.env`. Append `/edit` to
that string and you are done.

**Don't assemble it from the `workbookId`.** The id in the URL is Sigma's
short document id (`7EW5xMZojxSqmgyR7JmEdi`), not the UUID the publish
response returns; a URL built from the UUID 404s. If for some reason you don't
have the printed URL, get it from `publish-workbook.sh get-meta <id>` and take
its `url` field rather than composing one.

Edit mode, not view mode, because the first thing the user has to do is click
Publish — and the second is usually move the element or tweak the panel. A
view-mode link makes them find the editor themselves.

## Editing after the first build

**Never re-run the whole chain for a visual tweak.** The plugin's URL and its
`pluginId` are settled at the first deploy and the workbook goes on pointing at
them, so an edit to `src/App.jsx` changes exactly one thing — the bundle.
Three loops, cheapest first. Use the one that matches what you changed, and
stay in loop 1 or 2 until it looks right.

**1. While you're still changing how it looks — the dev server.** No deploy,
no publish, no Sigma round trip per edit.

```bash
cd plugins/<name> && npm run dev        # Vite, http://localhost:5173
```

In the workbook, the plugin element's **•••** menu → **Point to Development
URL** → `http://localhost:5173`. That's already the `devUrl` every
registration gets and the port `vite.config.js` pins, so there is nothing to
configure. Edits hot-reload in place. Changing the editor *panel* means
re-entering that element's panel values. → `docs/plugins.md` → "Iterate
against a dev URL"

**2. With no Sigma tab at all — the bind harness.** Rebuild and re-render
locally; no login, no network, seconds per iteration.

```bash
( cd plugins/<name> && npm run build ) && \
  "${SIGMA_PYTHON:-python3}" scripts/verify-plugin-binding.py <name> [--data FILE]
```

Reload the page it prints and read `document.title`. This is also where you
resize-test — see the gates section below.

**3. To ship what you have.**

| What changed | Command | What runs |
| --- | --- | --- |
| Only the bundle — `src/App.jsx`, styling, a dependency | `bash scripts/pipeline.sh <name> --redeploy` | Stops before the workbook: no spec, no publish, nothing created. The workbook is untouched and serves the new bundle on its next load. |
| The editor panel, the bindings, the data, the title | `bash scripts/pipeline.sh <name> "Title" -- <the same flags you built with>` | Regenerates the spec and **updates the same workbook in place** — same id, same URL, so a link already shared keeps working. |
| Not sure | as above | The regenerated spec is byte-compared against the one last published; identical means nothing is published at all. |

Pass the **same** `--data`/`--path`/`--bind` flags you built with. Dropping
them generates a different spec, which counts as a change and republishes.

`--new-workbook` forces a second, separate workbook, for when you actually
want one. `--workbook-id <id>` re-attaches a plugin to a workbook the kit has
lost track of.

`plugins/<name>/` is gitignored — the public host repo holds the deployed copy,
and only `_react-template` is tracked here.

## Data: make it fit the plugin

There is **no built-in row set**. `pipeline.sh` passes `--plugin-src`, so the
generator reads the plugin's own `configureEditorPanel`, takes its column
bindings and `allowedTypes`, and synthesizes typed columns named to match —
it always binds, and the binding keys come from the plugin rather than a
guess. Values are visible placeholders — each text column gets its own binding
name plus a letter, so a `brand` binding yields "Brand A", "Brand B".

**Your job is to replace the placeholders with rows that mean something.**
Invent data appropriate to the plugin and pass `--data <file.csv|json>`:
brands or product families for a ranking chart, funnel stages for a funnel,
store regions or ZIPs for a map. Don't ship "Brand A". If the intake asked
which entities, use the ones the user named.

```bash
bash scripts/pipeline.sh brand-bars "Brand Bars" -- --data /tmp/brands.csv
```

**Stay in the Plugs Electronics world.** No sports, teams, leagues or seasons —
not in synthetic rows, not in plugin names, not in examples. Reach for retail
instead: brands, product families, SKUs, stores, store regions and states,
revenue, quantity, margin. It keeps every demo consistent with the sample
retail table and with the workbooks these plugins land next to.

Rows are compiled into a `SELECT ... FROM (VALUES ...)` literal published as a
`kind:"sql"` element, so the data lives in the workbook spec. **Never build an
input table for fabricated rows** — it publishes empty and the plugin silently
falls back to numbers hardcoded in its own source. There is no REST write
endpoint and there is no workaround; `docs/plugins.md` has the evidence.

Whichever source question 2 picked, **report which one you used** and what the
rows represent.

## Two gates run before anything irreversible

`pipeline.sh` runs both for you, before deploy, because deploy and register
are the steps you cannot take back. Run them by hand while iterating:

```bash
python3 scripts/preflight-plugin.py <name> [--data FILE]      # static, blocking
python3 scripts/verify-plugin-binding.py <name> [--data FILE] # renders it twice
```

**Preflight** is static and blocking: every check in it is a mode where the
plugin deploys clean, publishes clean, and screenshots perfectly. **The bind
harness** is the only thing that proves the plugin renders *bound* data rather
than its own fallback — it runs the plugin twice, bound and unbound, compares
the two, and resizes the frame to check the plugin follows it. No Sigma login,
no deploy, no network.

Open the URL it prints; the verdict is in `document.title` (`HARNESS PASS` /
`HARNESS FAIL`) and `window.__HARNESS__`. **Wait for the title to stop saying
`HARNESS RUNNING`** — read it early and unfinished checks look like failures.

What each check means, and why: `docs/plugins.md` → "The two gates". Escape
hatches, for when a check is wrong rather than the plugin:
`SIGMA_SKIP_PREFLIGHT=1`, `SIGMA_SKIP_BINDTEST=1`.

## Order is not negotiable

```
intake (ask) → build → preflight → bind harness → deploy (public URL)
      → register (pluginId) → workbook (bind + publish) → verify
```

The two gates come first because **deploy and register are the irreversible
steps.**

That order is the *first* build. Afterwards the irreversible steps have
already happened, so the chain shrinks — and running the long one anyway is
how you end up with a folder full of near-identical workbooks:

```
edit → preflight → bind harness → deploy → confirm registration   (--redeploy)
```

`PATCH /v2/plugins/{id}` **cannot change `url`**. Registering a URL that
doesn't serve means delete + re-create, a new `pluginId`, and every workbook
referencing the old one silently broken. Deploy first; `deploy-plugin.sh`
polls until Pages serves the exact bytes, and `register-plugin.sh create`
re-checks.

Hosting must be a **public** repo — Sigma fetches the URL anonymously for the
iframe.

## What fails silently

None of these show up in a status code. Most are now caught by a gate rather
than by you remembering them — those get a line. The last one cannot be
checked by anything, and gets the space it needs.

**Caught for you, so just don't fight them.** Write the plugin normally and
the two gates will tell you if you tripped one; the failure message carries
the reasoning:

- **Import the client** — `import { client } from '@sigmacomputing/plugin'`,
  never a window global. (`sdk-global`) → `docs/plugin-api.md` → "Getting the
  SDK"
- **Fill the iframe at every size** — `height: 100%` down the chain, no fixed
  `px`/`vh`/`vw`, `minHeight: 0` on any flex child that must shrink, and a
  `ResizeObserver` that compares dimensions before re-rendering. The first
  measurement is often `0`; render from it anyway. (`root-height`,
  `viewport-units`, `flex-min-height`, `resize-observer-guard`, and
  `fills-frame`, which resizes the frame and watches whether your root
  follows.) Per-library switches and the rest → `docs/plugin-api.md` →
  "Loading, sizing, errors"
- **Demo data showing in Sigma means the binding is wrong** — not that the
  plugin is fine. (`binds-vs-fallback`, `bound-values-visible`) →
  `docs/plugins.md` → "Verifying, and the failures that hide"

**No gate can catch this one, so read it now.**
**`Invalid kind: "<kind>"` means a *field* has the wrong value shape** — not
that the element kind is unsupported. The message comes back from Sigma at
publish time and says nothing else, so the obvious reading sends you off
rewriting a perfectly good element as some other kind. Sigma rejects known
fields carrying bad shapes and silently drops field names it doesn't know, so
bisect from a known-good shape one field at a time rather than inventing one.
A *structural* complaint instead ("an action must have at least one effect")
means that part parsed fine. → `docs/elements-known-good.md`, which has every
verified shape, the known-rejected list, and the full decoder

## Before you write a panel

`docs/plugin-api.md` is the SDK reference — **read it, not the help centre**,
which covers about a third of the API and gets several things wrong
(`allowTypes` vs `allowedTypes`, `config.set`'s signature, which namespace
`getElementColumns` lives on).

Two things that change what you ask in the intake, so they're here rather than
only there:

- **`variable` is the two-way channel to a workbook control** — the only way a
  plugin cross-filters the rest of the dashboard. It has to be declared in
  `configureEditorPanel` before the first deploy, which makes it structural.
- **A plugin can bind several elements** — declare multiple `element` entries.

Everything else about the panel — all 14 types, `column` needing both `source`
and `allowMultiple`, `allowedTypes` being an allowlist, `secure: true` for
tokens, the column-keyed parallel-array data shape and its 25,000-value cap —
is reference. Look it up when you write the panel.
