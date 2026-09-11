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

Full walkthrough and every gotcha that costs a rebuild:
**[docs/plugins.md](docs/plugins.md)**. Verified element shapes to copy from
rather than invent: **[docs/elements-known-good.md](docs/elements-known-good.md)**.

## Three things that will bite you

**The SDK global is not what most examples say.** The UMD bundle defines
exactly one global, `window.SigmaPlugin`, with the client at
`SigmaPlugin.client`. `window.sigmaComputing.plugin.client` is a
widely-copied pattern that no published bundle defines — a plugin reading it
gets `client === null` and silently renders its synthetic fallback forever,
looking fine in a screenshot while never binding a real column. CI fails any
plugin here that doesn't reference `SigmaPlugin`.

**`PATCH /v2/plugins/{id}` cannot change `url`.** Deploy and verify the URL
serves *before* registering. Getting it wrong means delete + re-create, a new
`pluginId`, and every workbook referencing the old one silently broken.

**Hosting must be a separate public repo.** Sigma fetches the plugin URL
anonymously into an iframe, so a private repo's Pages output will not serve.
This toolkit is private; plugins deploy to `tyleraspencer/sigma-plugins`.
Use GitHub Pages, not jsDelivr — jsDelivr serves `.html` as `text/plain`.

## Data

Defaults to a verified real source, so the common case needs no data flags:
connection **Sigma Sample Database**, path
`RETAIL.PLUGS_ELECTRONICS.PLUGS_ELECTRONICS_HANDS_ON_LAB_DATA`, grouped by
`STORE_REGION` with `Sum(PRICE * QUANTITY)` as `Revenue`. Override with
`--connection-id`, `--path`, `--dimension`, `--measure`, `--measure-name`.

Measure expressions take **bare** column names and get qualified to
`[TABLE/COLUMN]` automatically — which matters, because a bare `[PRICE]` on a
warehouse source publishes with HTTP 200 and then compiles to literal
`'Unknown column "[PRICE]"'` in the SQL.

**There is no synthetic/input-table mode, deliberately.** Sigma cannot
populate an input table from a spec: `insert-rows` is a runtime effect (one
row each) and is currently rejected by the spec API, and no source kind
accepts literal rows — `sql`, `custom-sql`, `customSql`, `warehouse-sql`,
`manual` and `inline` were all probed and refused. An input table publishes
empty, the plugin falls back to its own hardcoded demo data, and you ship a
chart of fake numbers that looks real. Bind to a real table.

## Where plugins live

The public host repo (`tyleraspencer/sigma-plugins`) is the **single source of
truth** for deployed plugin HTML. `plugins/<name>/` here is a gitignored
working directory: you scaffold into it, edit, and `deploy-plugin.sh` pushes
the result to the host repo.

Only `plugins/_template/` is tracked. Committing deployed plugins here too
would mean two copies with nothing comparing them — the kit's copy could drift
from what Sigma actually loads and nothing would notice.

Because the kit doesn't track them, the HTML gates (SigmaPlugin global, SDK
loaded, no leftover placeholder) run in `deploy-plugin.sh` rather than CI —
deploy is the last moment the content is private and the only moment a check
can stop a broken plugin from getting a public URL and a `pluginId`.
`deploy-plugin.sh` also byte-compares what Pages serves against your local
file, so a stale Pages build can't masquerade as a successful deploy.

## Layout

```
plugins/
  _template/index.html        the only HTML this repo tracks; new-plugin.sh copies it
  <name>/index.html           gitignored working files -- see "Where plugins live"
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
  plugins.md                  the pipeline, the SDK, every gotcha
  elements-known-good.md      verified element shapes -- copy, don't invent
  auth.md                     auth ladder, credential tiers, Cowork, egress hosts
  api-notes.md                wire formats and error modes
  provenance.md               what came from where, and what changed
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
