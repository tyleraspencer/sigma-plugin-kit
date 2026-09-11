# sigma-plugin-kit

Scaffolding for building and shipping Sigma Computing plugins for Claude Code:
the plugin harness (manifests, skill packaging, `.cortex/` mirror, CI) and the
Sigma REST API toolkit (`scripts/api/`). This repo carries no plugin content of
its own — a skill lives here only once someone adds one under `skills/`.

Extracted from `ryan-workbook-skill`; see `NOTICE` and `docs/provenance.md`.

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
