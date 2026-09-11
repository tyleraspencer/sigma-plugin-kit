# sigma-plugin-kit

Scaffolding for building and shipping Sigma Computing plugins for Claude Code,
with no plugin content of its own. Two halves:

- **The plugin harness** — plugin/marketplace/Cortex manifests, skill
  packaging, the `.cortex/` mirror, and CI that gates all of it.
- **The Sigma REST API toolkit** — browser OAuth sign-in, token caching and
  refresh, and publish/verify/harvest against a live Sigma org.

Extracted from
[ryan-workbook-skill](https://github.com/RyanLauderback/ryan-workbook-skill);
see [NOTICE](NOTICE) and [docs/provenance.md](docs/provenance.md).

## Quickstart

```bash
bash scripts/doctor.sh                              # check the host first
export SIGMA_BASE_URL=https://api.sigmacomputing.com   # your org's region host
eval "$(scripts/api/browser-login.sh)"              # browser sign-in, no admin credential
bash scripts/api/whoami.sh                          # confirm against the live API
```

`doctor.sh` verifies binaries, resolves the interpreters that will actually be
used, and runs an egress preflight that recognizes the sandbox-allowlist 403
by signature — so a misconfigured org fails in the first ten seconds with an
actionable message instead of mid-task.

## Layout

```
.claude-plugin/     plugin.json + marketplace.json (Claude Code)
.cortex-plugin/     plugin.json (Cortex Code)
.cortex/skills/     generated mirror — do not hand-edit
skills/_template/   skeleton to copy for a new skill
scripts/
  doctor.sh                 host + auth + egress preflight
  package-skill.sh          build an uploadable skill ZIP
  sync-cortex-mirror.py     regenerate .cortex/skills/
  load-env.sh               read SIGMA_* from .env, for eval
  refresh-vendor.sh          read-only mirror of upstream sigma-agent-skills
  validate-spec.py          workbook spec validation (runs on every publish)
  workbook-manifest.py      spec -> human-readable manifest
  sigma-resolve.py          freeform input -> Sigma object IDs
  api/                      the REST layer, below
docs/
  auth.md             the auth ladder, credential tiers, Cowork, egress hosts
  api-notes.md        wire formats and error modes of the live API
  plugin-harness.md   manifests, mirror, packaging, adding a skill
  provenance.md       what was extracted, what was left behind, what changed
```

## scripts/api/

Every script self-bootstraps auth by sourcing `_env.sh` — you never compose an
`eval`/`export`/`curl` chain by hand. Use `sigma_curl`, not raw `curl`: it
attaches the token and `Accept: application/json`, and on a `401` it evicts
the cached token, re-bootstraps, and retries once.

| Script | Does |
|---|---|
| `browser-login.sh` | PKCE browser sign-in; stores only a refresh token |
| `get-token.sh` | `client_credentials` token mint (Claude Code web, or a `.env`) |
| `refresh-token.sh` | Redeem the stored refresh token, no browser round-trip |
| `whoami.sh` | Confirm identity and list recent files |
| `publish-workbook.sh` | `post` / `put` / `get-spec` / `get-meta`; validates and audits automatically |
| `verify-workbook.sh` | Run each element's query, catch compile failures |
| `audit-workbook-schema.sh` | Catch error-typed columns that pass POST but break in the UI |
| `harvest-workbook.sh` | Pull a live spec + manifest into `workbooks/harvest/<slug>/` |
| `search-files.sh`, `find-file-by-urlid.sh`, `lookup-path.sh` | Find things via REST |
| `list-connections.sh`, `list-folders.sh`, `list-table-columns.sh`, `probe-schema-tables.sh` | Enumerate org objects |
| `mcp-search.sh`, `mcp-describe.sh` | Richer search/DDL via `/mcp/v2` — needs interactive user OAuth |

`_env.sh` and `_state.sh` are sourced, not executed; CI asserts every other
`scripts/api/*.sh` is mode `755`.

No credential is ever written inside the repo tree. The token cache lives at
`$SIGMA_TOKEN_CACHE` (per-user, `0600`, outside the repo) and the refresh
token in the OS keychain — or a `0600` file under `$SIGMA_STATE_DIR` where no
keychain exists. `_state.sh` hard-refuses a `$SIGMA_STATE_DIR` that resolves
inside this repo.

## Adding a skill

```bash
cp -r skills/_template skills/my-skill
# edit skills/my-skill/SKILL.md -- the description: field is the whole
# activation mechanism
python3 scripts/sync-cortex-mirror.py
bash scripts/package-skill.sh my-skill
```

Full walkthrough in [docs/plugin-harness.md](docs/plugin-harness.md).

## CI

Runs on ubuntu, macos, and windows: `bash -n` over every `.sh`, `py_compile`
over every `.py`, JSON parse over every `.json` (this is what gates the plugin
manifests), SKILL.md frontmatter, the `.cortex/` drift gate, the
`scripts/api/*.sh` exec-bit assertion, a `package-skill.sh` round trip, and a
`doctor.sh` smoke run.

## Platforms

macOS, Linux, WSL, and Git Bash/MSYS2 on native Windows for the bash layer.
The pure-Python tools run under a native Windows `python` with no bash at all.
The bash layer targets bash 3.2, since that is still what macOS ships as
`/bin/bash`.
