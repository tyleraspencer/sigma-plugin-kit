# Authentication

Every script under `scripts/api/` self-bootstraps its auth by sourcing
`_env.sh`. You never compose an `eval`/`export`/`curl` chain by hand, and
nothing in this repo reads a credential from a file you have to create first.

Run `bash scripts/doctor.sh` before anything else on an unfamiliar host. It
checks binaries, resolves the interpreters that will actually be used, and
runs an egress preflight that recognizes the sandbox-allowlist failure
described below.

## The auth ladder

`_env.sh` resolves a token in this order:

1. **`SIGMA_API_TOKEN` already exported** — used as-is. `SIGMA_BASE_URL` is
   still required, since every request URL is built from it.
2. **`SIGMA_CLIENT_ID` + `SIGMA_CLIENT_SECRET` + `SIGMA_BASE_URL` exported** —
   `get-token.sh` mints a token via `client_credentials`. This is the Claude
   Code web path: the platform injects all three directly, and there is no
   human or browser in that execution context. If the three are not in the
   environment but a repo-root `.env` exists, `get-token.sh` loads it via
   `scripts/load-env.sh`.
3. **`SIGMA_BASE_URL` exported, nothing else** — falls back to
   `refresh-token.sh`, which redeems the stored refresh token with no browser
   round-trip. This is the normal case on the call *after* a
   `browser-login.sh` session: Claude Code's Bash tool does not carry
   exported env vars across invocations, so the token from a moment ago is
   already gone. If no `browser-login.sh` session has ever run,
   `refresh-token.sh` fails fast with a pointer to it, before any network
   call.
4. **Nothing** — errors out pointing at `browser-login.sh`.

Resolved tokens are cached at `$SIGMA_TOKEN_CACHE` (default: a per-user path
under `$XDG_RUNTIME_DIR`/`$TMPDIR`, mode `0600`) and refreshed when older than
55 minutes — 5 minutes before the 60-minute OAuth expiry.

Use `sigma_curl` from `_env.sh` rather than raw `curl` for any Sigma API call.
It attaches the bearer token and `Accept: application/json`, and on a `401` it
evicts the cached token, re-bootstraps, and retries once. That retry is what
keeps a revoked or wrong-region cached token from failing silently.

## Signing in

```bash
export SIGMA_BASE_URL=https://api.sigmacomputing.com   # your org's region host
eval "$(scripts/api/browser-login.sh)"
bash scripts/api/whoami.sh
```

`browser-login.sh` does OAuth discovery, dynamic client registration, and a
PKCE authorization-code flow against your own Sigma login. It needs no
admin-provisioned client credential, and it stores only a refresh token —
never a password, never a long-lived secret in the repo.

Prefer this over a `.env`. A `.env` with a `client_credentials` pair is only
for an org that has actually issued you one; `scripts/load-env.sh` emits only
`SIGMA_*` keys and single-quotes every value, so a malformed `.env` cannot
inject shell into the caller's `eval`.

## Credential storage tiers

`_state.sh` picks the first tier available, so callers stay identical across
hosts:

| Tier | Mechanism | When |
|---|---|---|
| 1 | macOS keychain (`security`) | macOS |
| 2 | Linux libsecret (`secret-tool`) | most desktop Linux |
| 3 | `0600` file under `$SIGMA_STATE_DIR` | sandboxes with no keychain |

`$SIGMA_STATE_DIR` defaults to
`${XDG_STATE_HOME:-$HOME/.local/state}/sigma-plugin-kit`. `state_dir()` hard
**refuses** a `$SIGMA_STATE_DIR` that resolves inside this repo or its
enclosing git repo — a Cowork workspace syncs back to real disk, and a
credential written into the tree is a credential that gets committed.

## Cowork as a host

A Claude Cowork session runs shell commands in an isolated environment on
Anthropic's servers. Only two seams change; everything else — the REST API,
the spec format, `validate-spec.py`, `publish-workbook.sh` — runs identically.

| Aspect | Claude Code CLI | Claude Cowork |
|---|---|---|
| Shell | Persistent, on your machine | Isolated, on Anthropic's servers |
| tty | Present | Absent — no `/dev/tty`; a script blocking on `read` fails |
| OS keychain | `security` / `secret-tool` | Neither exists (tier 3 above) |
| Env vars across tool calls | Persist | Do **not** persist between calls |
| Skill discovery | `.claude/skills/` auto-discovered | ZIP uploaded to `claude.ai/customize/skills` |
| Outbound network | Direct | Forward proxy, org-admin domain allowlist |

### Two-phase browser sign-in

`browser-login.sh` detects a headless environment via `_state.sh`'s
`is_headless()` and splits into two calls. It cannot bind a loopback listener
there — the user's browser redirects to *their* localhost, which the sandbox
can never see.

- **`browser-login.sh --start`** (or a plain no-arg call — headless is
  auto-detected) prints an authorize URL to stdout and exits. PKCE verifier,
  CSRF state, client ID, and token URL are saved to a `0600` file, because env
  vars set in this call would not survive into the next one.
- **`browser-login.sh --finish "<pasted-callback-url>"`** loads that state,
  verifies the CSRF `state` parameter, exchanges the code, persists the
  refresh token, and prints `export SIGMA_API_TOKEN=...`.

The user's browser will fail to load the redirect address — nothing is
listening there. That failed URL, copied whole from the address bar, is the
argument to `--finish`. Later calls in the same session resolve auth
automatically again via `refresh-token.sh` reading the file tier.

### Egress allowlist

None of the above matters if the sandbox proxy blocks the Sigma host. A
blocked host fails closed with `403` and the response header
`X-Proxy-Error: blocked-by-allowlist` (hostname-based — pinning to a bare IP
is not a workaround). An org Owner/Admin must allowlist the host under
**Admin settings → Capabilities**. This is a customer prerequisite, not
something this repo can fix in code.

Every host `browser-login.sh`'s own region picker offers:

```
https://aws-api.sigmacomputing.com
https://api.us-a.aws.sigmacomputing.com
https://api.ca.aws.sigmacomputing.com
https://api.eu.aws.sigmacomputing.com
https://api.au.aws.sigmacomputing.com
https://api.uk.aws.sigmacomputing.com
https://api.us.azure.sigmacomputing.com
https://api.eu.azure.sigmacomputing.com
https://api.ca.azure.sigmacomputing.com
https://api.uk.azure.sigmacomputing.com
https://api.au.azure.sigmacomputing.com
https://api.sigmacomputing.com
https://api.sa.gcp.sigmacomputing.com
```

Plus `https://help.sigmacomputing.com` for function-reference lookups. An org
needs its **one** actual region host allowlisted, not all 13.

**The caveat an admin will hit if they stop at the API host.** The OAuth
*authorization server* is discovered at runtime — from the `WWW-Authenticate`
challenge on `/v2/whoami`, then `.well-known/oauth-authorization-server` —
and `assert_sigma_host()` only asserts it ends in `.sigmacomputing.com`, not
that it matches the API host. Allowlisting only `api.*` can still fail at
`--start` with a proxy 403 on the authorize URL. The printed authorize URL
reveals the real authorization-server hostname; allowlist that too.

### A working MCP connector does not imply shell egress

These are independent gates, and conflating them wastes debugging time:

- A native claude.ai-side Sigma MCP connector authenticates and calls Sigma
  from Anthropic's backend — never through the sandbox's shell proxy. A
  working connector says nothing about whether `curl` from
  `scripts/api/*.sh` can reach Sigma.
- Conversely, a passing egress check says nothing about *which Sigma org* a
  connector is authenticated to. Near-identical demo data across orgs masks
  that mismatch easily.

This repo's own auth (`browser-login.sh` / `whoami.sh`) is unaffected by
either.
