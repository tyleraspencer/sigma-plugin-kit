# sigma-plugin-kit

End-to-end toolkit for Sigma Computing custom-visualization plugins: build one,
host it publicly, register it with a Sigma org, and publish a workbook with the
plugin bound to its data.

```
ask  →  build  →  deploy (public URL)  →  register (pluginId)  →  workbook (bind + publish)
```

The `ask` step is a short intake — which Sigma org to build in, synthetic rows
or a real table, visual direction, and whatever else the specific request
leaves open. It runs before anything is scaffolded, because deploy and register
can't be undone. Question set:
[SKILL.md](skills/sigma-plugin-pipeline/SKILL.md).

```bash
bash scripts/doctor.sh                                  # check the host
export SIGMA_BASE_URL=https://api.sigmacomputing.com
eval "$(scripts/api/browser-login.sh)"                  # browser sign-in (needs a tty)

bash scripts/pipeline.sh my-viz "My Viz"                # all four steps
```

That scaffolds, deploys, registers, generates a workbook bound to real data,
publishes, verifies the compiled SQL, and prints the workbook URL. Override
the data source after `--`:

```bash
bash scripts/pipeline.sh my-viz "My Viz" -- \
  --dimension PRODUCT_FAMILY --measure "Sum(QUANTITY)" --measure-name Units
```

**The second run is not the first run.** Editing `src/App.jsx` changes the
bundle and nothing else — the `pluginId` and the workbook both stay valid — so
ship it without touching the workbook at all:

```bash
bash scripts/pipeline.sh my-viz --redeploy              # build + deploy, and stop
```

A full re-run reuses the existing registration rather than minting a second
`pluginId`, regenerates the spec, byte-compares it against the one last
published, and **updates that same workbook in place** when it differs — same
id, same URL, so a shared link keeps working. Nothing differs, nothing is
published. `--new-workbook` opts back into a fresh one. Faster still, while
you're iterating on the look: `npm run dev` and point the element at
`http://localhost:5173` — details in
[docs/plugins.md](docs/plugins.md).

Every plugin is a Vite + React project, so npm packages — Plotly, Mapbox, D3,
Recharts — are available from the start and `deploy-plugin.sh` runs the build.
There is no hand-written-HTML archetype: it was removed because loading the
SDK's UMD bundle from a CDN renders fallback data forever if React isn't loaded
ahead of it, with one uncaught console error as the only symptom.

**[docs/plugin-api.md](docs/plugin-api.md)** is the SDK reference — all 14
editor-panel types, the client surface, variables, actions, interactions, and
the help centre's own errors. Read it instead of the help pages, which cover
about a third of the API and get several details wrong.

Pipeline walkthrough and every gotcha that costs a rebuild:
**[docs/plugins.md](docs/plugins.md)**. Verified workbook element shapes to
copy from rather than invent:
**[docs/elements-known-good.md](docs/elements-known-good.md)**.

## Three things that will bite you

**Import `client`; never reach for a window global.**
`import { client } from '@sigmacomputing/plugin'`.
`window.sigmaComputing.plugin.client` is a widely-copied pattern that no
published bundle defines, and `window.SigmaPlugin` only exists when the UMD
build is loaded from a script tag — which a bundled plugin never does. Either
one reads `undefined`, and the plugin silently renders its synthetic fallback
forever, looking fine in a screenshot while never binding a real column.
`preflight-plugin.py` fails on both, and `deploy-plugin.sh` runs it.

**`PATCH /v2/plugins/{id}` cannot change `url`.** Deploy and verify the URL
serves *before* registering. Getting it wrong means delete + re-create, a new
`pluginId`, and every workbook referencing the old one silently broken.

**Hosting must be a separate public repo.** Sigma fetches the plugin URL
anonymously into an iframe, so a private repo's Pages output will not serve.
This toolkit is private; plugins deploy to `tyleraspencer/sigma-plugins`.
Use GitHub Pages, not jsDelivr — jsDelivr serves `.html` as `text/plain`.

## Data

**Generated to fit the plugin.** The generator parses the plugin's own
`configureEditorPanel`, takes its column bindings and `allowedTypes`, and
synthesizes correctly-typed columns *named to match* — so the plugin binds
with no guesswork, and the binding keys come from the plugin rather than a
flag. Rows are compiled into a `SELECT ... FROM (VALUES ...)` literal and
published as a `kind: "sql"` table element, so the data lives in the workbook
spec with no upload and no data model.

**There is no built-in row set on purpose.** Synthesized values are visible
placeholders — each text column gets its binding name plus a letter, so a
`brand` binding yields "Brand A", "Brand B". The shape is right, the meaning
isn't. Pass `--data` with a `.csv`/`.tsv` or JSON array to supply rows that
mean something for the plugin at hand, keeping them in the Plugs Electronics
retail world rather than reaching for sports or teams. Or
`--path DB SCHEMA TABLE` to bind a real warehouse table instead, grouped and
auto-qualified — the known-good one is
`RETAIL.PLUGS_ELECTRONICS.PLUGS_ELECTRONICS_HANDS_ON_LAB_DATA`.

This is the only API route for fabricated rows. Input tables can't be written
from code — `insert-rows` is rejected, every row-ish field on the element is
silently dropped, and there's no REST write endpoint — and `/v2/files` has no
CSV upload. An input table would publish empty and the plugin would fall back
to numbers hardcoded in its own HTML. Details in
[docs/plugins.md](docs/plugins.md).

## Where plugins live

The public host repo (`tyleraspencer/sigma-plugins`) is the **single source of
truth** for deployed plugin HTML. `plugins/<name>/` here is a gitignored
working directory: you scaffold into it, edit, and `deploy-plugin.sh` pushes
the result to the host repo.

Only `_react-template` is tracked. Committing deployed plugins here as well
would mean two copies with nothing comparing them — the kit's copy could drift
from what Sigma actually loads and nothing would notice.

Because the kit doesn't track them, the gates run in `deploy-plugin.sh` rather
than CI: it refuses anything without a `package.json`, runs the full
`preflight-plugin.py`, and requires a real `@sigmacomputing/plugin` dependency,
**relative** built asset paths, and no leftover placeholder. Deploy is the last moment
the content is private and the only moment a check can stop a broken plugin
from getting a public URL and an immutable `pluginId`. It also byte-compares
what Pages serves against the build, so a stale Pages build can't masquerade
as a successful deploy. CI gates the templates themselves.

## Layout

```
plugins/
  _react-template/            the only archetype: Vite + React, SDK bundled
  <name>/                     gitignored working dirs -- see "Where plugins live"
scripts/
  pipeline.sh                 all four steps in one command; start here
  new-plugin.sh               1. scaffold plugins/<name>/
  deploy-plugin.sh            2. push to public Pages, poll until it serves
  build-plugin-workbook.py    4. generate the workbook spec
  validate-spec.py            8 pre-POST checks (publish-workbook.sh runs it)
  doctor.sh                   host + auth + egress preflight
  load-env.sh                 read SIGMA_* from .env, for eval
  api/
    _env.sh  _state.sh        sourced, not run: auth bootstrap + credential tiers
    browser-login.sh          OAuth + PKCE sign-in; stores only a refresh token
    get-token.sh              client_credentials mint (Claude Code web, or .env)
    refresh-token.sh          redeem the stored refresh token, no browser
    whoami.sh                 confirm identity against the live API
    register-plugin.sh        3. create / list / get / id-for / rename / delete
    publish-workbook.sh       post / put / get-spec / get-meta; validates + audits
    verify-workbook.sh        run each element's query, catch compile failures
    audit-workbook-schema.sh  catch error-typed columns that pass POST
    list-connections.sh       connections available to you
    list-table-columns.sh     columns of a warehouse table
    mcp-search.sh             find workbooks/models/tables by topic
    mcp-describe.sh           DDL for a table/model/element
skills/
  sigma-plugin-pipeline/      the operating manual
docs/
  plugin-api.md               THE SDK reference: 14 panel types, client, variables, actions
  plugins.md                  the pipeline, archetypes, every gotcha
  elements-known-good.md      verified element shapes -- copy, don't invent
  auth.md                     auth ladder, credential tiers, Cowork, egress hosts
  api-notes.md                wire formats and error modes
```

The numbered scripts are the four pipeline steps; `pipeline.sh` runs all of
them. Everything else is either auth plumbing or a discovery helper.


## Auth

Every script under `scripts/api/` self-bootstraps by sourcing `_env.sh`; use
`sigma_curl` rather than raw `curl` and the token, `Accept` header and 401
retry are handled. `browser-login.sh` needs no admin-provisioned credential
and stores only a refresh token — in the OS keychain, or a `0600` file under
`$SIGMA_STATE_DIR` where no keychain exists. No credential is ever written
inside the repo tree. Details and the Cowork headless flow: [docs/auth.md](docs/auth.md).

Plugin registration writes need Admin or the **Manage plugins** permission.

## CI

ubuntu / macos / windows: `bash -n`, `py_compile`, JSON parse (this is what
gates the plugin manifests), SKILL.md frontmatter, plugin HTML sanity (SDK
global, unsubstituted placeholder, SDK actually loaded), `scripts/api/*.sh`
exec bits, and a `doctor.sh` smoke run.

## Platforms

macOS, Linux, WSL, Git Bash/MSYS2 for the bash layer, which targets bash 3.2
since that is still what macOS ships as `/bin/bash`. The Python tools also run
under a native Windows `python`.
