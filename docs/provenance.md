# Provenance

This repo is an extraction from
[RyanLauderback/ryan-workbook-skill](https://github.com/RyanLauderback/ryan-workbook-skill),
which packages Sigma workbook-authoring *conventions* plus the auth/CRUD
tooling those conventions depend on. Only the second half was carried over:
the plugin harness and the REST API toolkit, with no workbook-spec content.

See [NOTICE](../NOTICE) for attribution and licensing status.

## What was left behind

| Upstream | Why it isn't here |
|---|---|
| `skills/sigma-workbook-conventions/SKILL.md` (43 KB) | The workbook-authoring skill itself — the content, not the scaffolding. |
| `reference/specification/` | The Sigma workbook JSON spec: charts, controls, containers, actions, theming, formulas. |
| `reference/workflows/`, `reference/conventions.md`, `reference/patterns/` | Authoring conventions, naming rules, the plan/validate/iterate workflow. |
| `examples/` (19 specs), `workbooks/`, `evals/`, `prompts/` | Example and exemplar workbooks, eval fixtures, prompt library. |
| `docs/skill-authoring.md`, `docs/iteration-playbook.md` | Guidance specific to authoring workbook-*pattern* skills. |

## Why some comments cite paths that don't exist here

`validate-spec.py` enforces workbook-spec authoring rules, and its comments
explain *why* each rule exists by citing the upstream docs that argued for it
— `reference/conventions.md`, `reference/workflows/crud.md`, `SKILL.md` →
"Load-bearing rules", and so on. Those citations are marked **upstream** in
the source. They resolve in the repo linked above, not in this one.

Pointers whose substance *was* carried forward were repointed rather than
marked: the Cowork/auth material is now in [auth.md](auth.md), and the API
wire-format and error-mode findings are in [api-notes.md](api-notes.md).

## Local edits to the extracted scripts

- `scripts/package-skill.sh` — takes a `<skill-name>` argument instead of
  inferring one skill from its own location. Avoids `mapfile` and bash arrays,
  which break on the bash 3.2 that macOS still ships. No longer appends to
  `.gitignore` at runtime; `dist/` is ignored up front.
- `scripts/sync-cortex-mirror.py` — mirrors every `skills/<name>/` rather than
  one hardcoded skill, and rebuilds `.cortex/skills/` from scratch so a
  renamed or deleted skill can't leave a stale directory behind.
- `scripts/load-env.sh` — **new.** `get-token.sh` already called
  `scripts/load-env.sh` when a `.env` was present, but no such file existed
  upstream; a `.env` would have produced a "no such file" error rather than
  loading. Emits only `SIGMA_*` keys, single-quoted.
- `scripts/api/_state.sh` — default `$SIGMA_STATE_DIR` is now
  `…/state/sigma-plugin-kit`, so this toolkit's saved refresh token is
  separate from the upstream skill's.
- `.github/workflows/ci.yml` — dropped the spec-validation and
  `workbook-manifest.py` smoke steps, which ran over the `examples/`, `evals/`,
  and `workbooks/_exemplars/` files this repo doesn't carry. Added a SKILL.md
  frontmatter check and a `package-skill.sh` round trip, since the harness is
  now the thing under test.

`workbook-manifest.py` was going to be dropped as workbook-spec tooling, but
`harvest-workbook.sh` invokes it directly to write `manifest.md` alongside a
harvested spec, so it stayed.

## The plugin pipeline (added after the initial extraction)

The first extraction produced an auth + workbook-publish toolkit that could
not build, host, register or safely embed a Sigma plugin — the thing it was
named for. These were then written to close that gap:

- `plugins/_template/index.html` — written from scratch, modelled on the
  single-file/unpkg/synthetic-fallback pattern in `millersigma`, and
  correcting the SDK global that library uses.
- `scripts/new-plugin.sh`, `scripts/deploy-plugin.sh`,
  `scripts/api/register-plugin.sh`, `scripts/build-plugin-workbook.py`.
- `skills/sigma-plugin-pipeline/` — the operating manual, including the
  mandatory fake-vs-real question.
- `docs/plugins.md`.
- `validate-spec.py` gained a 19th check, `plugin-refs-resolve`. Before it, a
  `kind: "plugin"` element passed all 18 checks by being an unrecognized kind
  the validator skipped — it checked nothing.
- `workbook-manifest.py` learned `plugin` and `button` element kinds, and the
  top-level `elements`/`overlays`/`settings`/`agents`/`kind` keys from Sigma's
  2026-08-10 document-shape change. It previously reported all of these as
  `⚠️ UNKNOWN`, which made `harvest-workbook.sh` print "GAPS DETECTED" on any
  modern workbook.

`POST /v2/plugins` was believed to be admin-UI-only at the start of this work.
It is not — there is a documented five-endpoint CRUD namespace. The
`url`-immutable-on-`PATCH` constraint is the one that shapes the pipeline's
ordering.
