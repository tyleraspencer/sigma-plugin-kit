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
eval "$(scripts/api/browser-login.sh)"                  # browser sign-in

bash scripts/new-plugin.sh my-viz "My Viz"              # 1. build
URL=$(bash scripts/deploy-plugin.sh my-viz)             # 2. deploy -> public URL
PID=$(bash scripts/api/register-plugin.sh create "My Viz" "$URL")  # 3. register

python3 scripts/build-plugin-workbook.py --name "My Viz Demo" \
  --plugin-id "$PID" --data-mode fake --connection-id <conn> \
  --data rows.json --bind label=Region --bind value=Revenue --out spec.json
bash scripts/api/publish-workbook.sh post spec.json     # 4. workbook
```

Full walkthrough, including every gotcha that costs a rebuild:
**[docs/plugins.md](docs/plugins.md)**.

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

## Fake data or a real table

`build-plugin-workbook.py --data-mode` decides how the plugin gets data, and
the `sigma-plugin-pipeline` skill always asks before building.

- **`fake`** (default) — generates an `input-table` element plus a "Seed demo
  data" button, and binds the plugin to it. The rows become a real, editable
  Sigma table, so the plugin exercises its production data path and a reviewer
  can change the numbers. Sigma can't pre-populate an input table from a spec
  (`insert-rows` is a runtime effect, one row per effect), so **someone must
  open the workbook and click the button once.**
- **`real`** — generates a `table` element on a warehouse table and binds the
  plugin to that. Discover targets with `list-connections.sh`,
  `probe-schema-tables.sh`, `list-table-columns.sh`.

Both modes need `--connection-id`: even a fake-data input table is
warehouse-backed.

## Layout

```
plugins/
  _template/index.html        canonical single-file plugin (SDK, bindings, fallback)
  <name>/index.html           your plugins, deployed to the public host repo
scripts/
  new-plugin.sh               scaffold plugins/<name>/ from the template
  deploy-plugin.sh            push to public Pages, poll until it serves
  build-plugin-workbook.py    generate a workbook spec around a plugin element
  doctor.sh                   host + auth + egress preflight
  validate-spec.py            19 checks, incl. plugin-refs-resolve
  workbook-manifest.py        spec -> human-readable manifest
  sigma-resolve.py            freeform input -> Sigma object IDs
  package-skill.sh            build an uploadable skill ZIP
  sync-cortex-mirror.py       regenerate .cortex/skills/
  load-env.sh                 read SIGMA_* from .env, for eval
  api/
    register-plugin.sh        create / list / get / id-for / rename / delete
    browser-login.sh          PKCE sign-in; stores only a refresh token
    publish-workbook.sh       post / put / get-spec / get-meta, validates + audits
    verify-workbook.sh        run each element's query, catch compile failures
    audit-workbook-schema.sh  catch error-typed columns that pass POST
    harvest-workbook.sh       pull a live spec + manifest
    list-*, search-*, probe-* discovery
    mcp-search.sh, mcp-describe.sh   richer search/DDL; needs user OAuth
skills/
  sigma-plugin-pipeline/      drives the pipeline, asks fake-vs-real first
  _template/                  skeleton for a new skill
docs/
  plugins.md                  the pipeline, the SDK, every gotcha
  auth.md                    auth ladder, credential tiers, Cowork, egress hosts
  api-notes.md               wire formats and error modes
  plugin-harness.md          Claude Code plugin manifests, mirror, packaging
  provenance.md              what came from where, and what changed
```

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
global, placeholder, SDK loaded), `.cortex/` drift, `scripts/api/*.sh` exec
bits, a `package-skill.sh` round trip, and a `doctor.sh` smoke run.

## Platforms

macOS, Linux, WSL, Git Bash/MSYS2 for the bash layer, which targets bash 3.2
since that is still what macOS ships as `/bin/bash`. The Python tools also run
under a native Windows `python`.
