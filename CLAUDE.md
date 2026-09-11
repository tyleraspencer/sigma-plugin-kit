# sigma-plugin-kit

End-to-end toolkit for Sigma Computing custom-visualization plugins:

```
build  →  deploy (public URL)  →  register (pluginId)  →  workbook (bind + publish)
```

Read `docs/plugins.md` before touching the pipeline. The
`sigma-plugin-pipeline` skill is the operating manual for running it.

Extracted from `ryan-workbook-skill` with plugin conventions from
`millersigma`; see `NOTICE` and `docs/provenance.md`.

## Never get these wrong

- **The SDK global is `window.SigmaPlugin`**, client at `SigmaPlugin.client`.
  `window.sigmaComputing.plugin.client` appears in most examples and in the
  majority of the `millersigma` library, but **no published bundle defines
  it** (verified against 1.3.2 and 1.2.0). A plugin reading it gets
  `client === null` and silently renders its fallback forever. CI gates this.
- **Deploy before registering.** `PATCH /v2/plugins/{id}` cannot change `url`;
  a wrong URL means delete + re-create, a new `pluginId`, and silently broken
  workbooks.
- **Hosting must be a public repo.** Sigma fetches the URL anonymously; a
  private repo's Pages output will not serve. This repo is private on purpose.
- **Always ask fake-vs-real** before generating a workbook. Default fake. Fake
  mode needs one manual click on the seed button — say so when handing it over.

## Secrets

**Never echo `$SIGMA_API_TOKEN`, `$SIGMA_CLIENT_SECRET`, or any other secret.**
Pass tokens only via `Authorization` headers. Never write a secret to a file
inside the repo tree — `_state.sh` enforces this for credential state and will
refuse a `$SIGMA_STATE_DIR` that resolves inside the repo, but nothing
enforces it for anything you write by hand.

## Authentication

Never compose an `eval`/`export`/`curl` chain by hand. Every script under
`scripts/api/` sources `_env.sh`, which resolves a token on its own, and
`sigma_curl` handles the bearer header, `Accept: application/json`, and the
401 retry. Full ladder and the Cowork/headless variants: `docs/auth.md`.

A CLI user's only auth step:

```bash
export SIGMA_BASE_URL=https://api.sigmacomputing.com
eval "$(scripts/api/browser-login.sh)"
bash scripts/api/whoami.sh
```

Claude Code web needs no action — the platform injects
`SIGMA_CLIENT_ID`/`SIGMA_CLIENT_SECRET`/`SIGMA_BASE_URL` and `_env.sh` falls
through to `get-token.sh`. Note that a `client_credentials` token is rejected
by Sigma's `/mcp/v2`, so `mcp-search.sh`/`mcp-describe.sh` need a
`browser-login.sh` token specifically.

## Before committing

- Edited anything under `skills/`? Run `python3 scripts/sync-cortex-mirror.py`.
  CI fails on mirror drift.
- Added a `scripts/api/*.sh`? `chmod 755` it. CI asserts the exec bit on every
  file there except `_env.sh` and `_state.sh`, which are sourced.
- Bumped a version? Bump it in all three manifests together:
  `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`,
  `.cortex-plugin/plugin.json`.
- `.cortex/skills/` is generated. Never hand-edit it.

## Platform constraints

The bash layer targets bash **3.2** — macOS still ships it as `/bin/bash`. No
`mapfile`, no associative arrays, and no `${arr[@]}` on a possibly-empty array
under `set -u`. Targets macOS, Linux, WSL, and Git Bash/MSYS2; the pure-Python
tools also run under a native Windows `python` with no bash at all.

`stat` differs across platforms — try the GNU form (`stat -c`) before the BSD
form (`stat -f`), since on Linux `stat -f` prints a filesystem report to
*stdout* and appears to succeed with garbage.

## Sigma documentation lookups

For a formula function reference or a REST endpoint shape, prefer the native
`mcp__claude_ai_Sigma_Docs__*` tools over `WebFetch`. They are a Claude.ai
account-level connector, not a plugin — if they aren't present, fall back to
`WebFetch` against `https://help.sigmacomputing.com/` and
`https://help.sigmacomputing.com/reference/`.

API wire formats and error modes this repo already knows about are in
`docs/api-notes.md` — check there before rediscovering one.
