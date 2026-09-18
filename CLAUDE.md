# sigma-plugin-kit

End-to-end toolkit for Sigma Computing custom-visualization plugins:

```
build  →  deploy (public URL)  →  register (pluginId)  →  workbook (bind + publish)
```

Read `docs/plugins.md` before touching the pipeline. The
`sigma-plugin-pipeline` skill is the operating manual for running it.

Extracted from `ryan-workbook-skill` with plugin conventions from
`millersigma`; see `NOTICE`.

## The traps

Rationale and evidence for all of these live in `docs/`, linked per line.
`skills/sigma-plugin-pipeline/SKILL.md` is the operating manual and loads
automatically when the task is *building* a plugin; this list exists only so an
agent editing this repo for some other reason doesn't step on one:

- Import `client` from `@sigmacomputing/plugin`; **never** reach for a window
  global. `window.sigmaComputing.*` is defined by no published bundle and
  `window.SigmaPlugin` only exists under a UMD script tag, which a bundled
  plugin never uses — both read `undefined` and render the fallback forever.
  (`docs/plugin-api.md` → "Getting the SDK".)
- Deploy before registering — `PATCH /v2/plugins/{id}` cannot change `url`.
  (`docs/plugins.md` → "3. Register".) A **re-deploy** does not wait for Pages
  (that wait only protected registration), so it returns in ~5s and the bytes
  land ~40s later — reload the workbook a minute after, not immediately.
- Plugin hosting stays a **separate** repo (`tyleraspencer/sigma-plugins`):
  it is the single source of truth for deployed plugin HTML, and the kit
  tracks no copy of it.
- Fabricated rows go in a `kind:"sql"` VALUES literal; never an input table.
- Copy element shapes from `docs/elements-known-good.md`; never invent fields.
- A plugin fills 100% of its iframe and re-lays-out on resize — no fixed px,
  and guard the `ResizeObserver`. Attach it from a **callback ref**: a mount
  effect never sees the chart node a data-gated plugin mounts later, and the
  plugin stays blank until the window is resized. (`docs/plugin-api.md` →
  "Loading, sizing".)
- **An action a plugin causes is triggered BY the plugin** — `action-trigger` +
  `triggerAction()`, with the action on the plugin element. Never a button the
  user presses afterwards, never a written control's `on-change`. Standing rule;
  both gates enforce it (`action-trigger-wired`, `plugin-owns-its-actions`).
  (`skills/sigma-plugin-pipeline/SKILL.md` → "A plugin owns its own actions".)
- A plugin looks like a Sigma element or it looks embedded. Tokens, chart
  conventions and the five design principles as React decisions:
  `docs/design-system.md`. There is no theme API to read them from.
- `Invalid kind: "<kind>"` means a *field* has a bad value shape.
  (`docs/elements-known-good.md` → "Decoding".)
- HTTP 200 does not mean it worked. Check the compiled SQL.
  (`docs/plugins.md` → "Verifying, and the failures that hide".)
- Ask the intake questions before scaffolding — data source and look-and-feel
  at minimum. Building first and asking later means a rebuild.
- Use `scripts/pipeline.sh` rather than running the four steps by hand.
- Editing a plugin that already shipped? **Ask where the change goes** — a
  `--dev` session on localhost (invisible, instant, hot-reloading) or a GitHub
  deploy (public, permanent, ~44s). `pipeline.sh` exits 2 rather than guess.
  Never re-run the full chain for a style change; it used to POST a duplicate
  workbook every time.

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

- Added a `scripts/api/*.sh`? `chmod 755` it. CI asserts the exec bit on every
  file there except `_env.sh` and `_state.sh`, which are sourced.
- Bumped a version? Bump it in both manifests together:
  `.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json`.

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
