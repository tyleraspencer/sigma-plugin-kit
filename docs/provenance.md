# Provenance

This repo is an extraction from
[RyanLauderback/ryan-workbook-skill](https://github.com/RyanLauderback/ryan-workbook-skill),
which packages Sigma workbook-authoring *conventions* plus the auth/CRUD
tooling those conventions depend on. Only the second half was carried over —
the REST API toolkit — with no workbook-spec content.

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

- `scripts/load-env.sh` — **new.** `get-token.sh` already called
  `scripts/load-env.sh` when a `.env` was present, but no such file existed
  upstream; a `.env` would have produced a "no such file" error rather than
  loading. Emits only `SIGMA_*` keys, single-quoted.
- `scripts/api/_state.sh` — default `$SIGMA_STATE_DIR` is now
  `…/state/sigma-plugin-kit`, so this toolkit's saved refresh token is
  separate from the upstream skill's.
- `.github/workflows/ci.yml` — dropped the spec-validation steps that ran over
  `examples/`, `evals/` and `workbooks/_exemplars/` files this repo doesn't
  carry. Added a SKILL.md frontmatter check and plugin HTML gates.

## The plugin pipeline (added after the initial extraction)

The first extraction produced an auth + workbook-publish toolkit that could
not build, host, register or safely embed a Sigma plugin — the thing it was
named for. These were then written to close that gap:

- `plugins/_template/index.html` — written from scratch, modelled on the
  single-file/unpkg/synthetic-fallback pattern in `millersigma`, and
  correcting the SDK global that library uses.
- `scripts/new-plugin.sh`, `scripts/deploy-plugin.sh`,
  `scripts/api/register-plugin.sh`, `scripts/build-plugin-workbook.py`.
- `skills/sigma-plugin-pipeline/` — the operating manual.
- `docs/plugins.md`.
- `validate-spec.py` gained a 19th check, `plugin-refs-resolve`. Before it, a
  `kind: "plugin"` element passed all 18 checks by being an unrecognized kind
  the validator skipped — it checked nothing.
- `workbook-manifest.py` learned the `plugin` and `button` element kinds and
  the 2026-08-10 top-level keys, having previously flagged all of them as
  `⚠️ UNKNOWN`. It was removed in the later cleanup (below) along with its
  only caller.

`POST /v2/plugins` was believed to be admin-UI-only at the start of this work.
It is not — there is a documented five-endpoint CRUD namespace. The
`url`-immutable-on-`PATCH` constraint is the one that shapes the pipeline's
ordering.

## Cleanup (2026-09-11)

Removed as unused by this repo's one job. Roughly 2,400 lines:

| Removed | Why |
|---|---|
| `sigma-resolve.py` (540 lines) | Referenced only by a comment. `mcp-search.sh` covers the same ground. |
| `harvest-workbook.sh` + `workbook-manifest.py` (825) | A pair serving nothing else; pulling and summarising live specs is not part of the plugin pipeline. |
| `refresh-vendor.sh` | Mirrored upstream `sigma-agent-skills` into `vendor/`; vestigial from the extraction. |
| `api/{lookup-path,list-folders,find-file-by-urlid,probe-schema-tables,search-files}.sh` | Zero references and zero docs. `mcp-search.sh` is a better discovery path. |
| `package-skill.sh`, `sync-cortex-mirror.py`, `.cortex/`, `.cortex-plugin/`, `skills/_template/` | The Cowork ZIP + Cortex Code mirror harness — a second, unrelated concern. `.claude-plugin/` stays, so the skill still loads in Claude Code. |
| `docs/plugin-harness.md` | Documented the harness above. |
| 12 of `validate-spec.py`'s 19 checks (~800 lines) | Gated on element kinds this kit never emits: containers, controls, KPIs, pivots, charts, input tables. |

`validate-spec.py` went 1,403 → ~590 lines and gained
`warehouse-refs-qualified`, which catches the bare-`[COLUMN]`-on-a-warehouse-
source bug that publishes with HTTP 200 and compiles to
`Unknown column`. Nothing in the original 19 checks caught it.

If you need any of the removed workbook-authoring tooling, it is intact
upstream in `ryan-workbook-skill`.
