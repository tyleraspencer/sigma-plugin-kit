# sigma-plugin-kit

End-to-end toolkit for Sigma Computing custom-visualization plugins: build one,
host it publicly, register it with a Sigma org, and publish a workbook with the
plugin bound to its data.

```
build  →  deploy (public URL)  →  register (pluginId)  →  workbook (bind + publish)
```

```bash
bash scripts/doctor.sh                                  # check the host
export SIGMA_BASE_URL=https://api.sigmacomputing.com
eval "$(scripts/api/browser-login.sh)"                  # browser sign-in (needs a tty)

bash scripts/pipeline.sh my-viz "My Viz"                # all four steps
```

That scaffolds, deploys, registers, generates a workbook bound to real data,
publishes, verifies the compiled SQL, and prints the workbook URL. Re-running
reuses the existing registration instead of minting a second `pluginId`.
Override the data source after `--`:

```bash
bash scripts/pipeline.sh my-viz "My Viz" -- \
  --dimension PRODUCT_FAMILY --measure "Sum(QUANTITY)" --measure-name Units
```

Need npm packages — Plotly, Mapbox, D3, Recharts? Scaffold the React
archetype first; `deploy-plugin.sh` builds it:

```bash
bash scripts/new-plugin.sh my-map "My Map" --react
bash scripts/pipeline.sh my-map "My Map"
```

**[docs/plugin-api.md](docs/plugin-api.md)** is the SDK reference — all 14
editor-panel types, the client surface, variables, actions, interactions, and
the help centre's own errors. Read it instead of the help pages, which cover
about a third of the API and get several details wrong.

Pipeline walkthrough and every gotcha that costs a rebuild:
**[docs/plugins.md](docs/plugins.md)**. Verified workbook element shapes to
copy from rather than invent:
**[docs/elements-known-good.md](docs/elements-known-good.md)**.

## Three things that will bite you

**The SDK global is not what most examples say.** The UMD bundle defines
exactly one global, `window.SigmaPlugin`, with the client at
`SigmaPlugin.client`. `window.sigmaComputing.plugin.client` is a
widely-copied pattern that no published bundle defines — a plugin reading it
gets `client === null` and silently renders its synthetic fallback forever,
looking fine in a screenshot while never binding a real column.
`deploy-plugin.sh` refuses to publish a plugin that doesn't reference it.

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
placeholders ("Team A", "Team B"): the shape is right, the meaning isn't. Pass
`--data` with a `.csv`/`.tsv` or JSON array to supply rows that mean something
for the plugin at hand. Or `--path DB SCHEMA TABLE` to bind a real warehouse
table instead, grouped and auto-qualified.

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

Only the two templates are tracked. Committing deployed plugins here as well
would mean two copies with nothing comparing them — the kit's copy could drift
from what Sigma actually loads and nothing would notice.

Because the kit doesn't track them, the gates run in `deploy-plugin.sh` rather
than CI: SigmaPlugin global and SDK present for single-file plugins, a real
`@sigmacomputing/plugin` dependency and **relative** built asset paths for
React ones, and no leftover placeholder either way. Deploy is the last moment
the content is private and the only moment a check can stop a broken plugin
from getting a public URL and an immutable `pluginId`. It also byte-compares
what Pages serves against the build, so a stale Pages build can't masquerade
as a successful deploy. CI gates the templates themselves.

## Layout

```
plugins/
  _template/index.html        single-file archetype: UMD SDK, no build
  _react-template/            React archetype: Vite + hooks, for npm packages
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
