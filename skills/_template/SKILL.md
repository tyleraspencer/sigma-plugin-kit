---
name: your-skill-name
description: Use when <concrete trigger phrases a user would actually type>. Covers <the artifacts and concepts this skill knows about>. Requires <prerequisites>. Does NOT cover <the near-neighbour skill's territory>.
---

# Your Skill Name

Copy this directory to `skills/<your-skill-name>/`, fill it in, then run
`python3 scripts/sync-cortex-mirror.py` so the `.cortex/` mirror matches.

## The description field is the whole activation mechanism

Claude decides whether to load this skill from the `description:` above and
nothing else. A vague description means the skill never fires, no matter how
good the body is. Make it:

- Lead with `Use when…` plus trigger phrases a user would really type.
- Name the concrete artifacts and concepts that distinguish this skill.
- State prerequisites explicitly.
- Say what it does NOT cover when a near-neighbour skill exists.

## Body

Keep the body to what Claude needs in-context every time this skill fires.
Anything long, enumerable, or lookup-shaped belongs in `reference/` so it is
read on demand instead of occupying the context window on every activation.

## Layout

```
skills/<your-skill-name>/
├── SKILL.md          # this file: frontmatter + a short body
├── reference/        # optional: on-demand detail, one topic per file
└── examples/         # optional: at least one known-good artifact
```

`reference/` and `examples/` are mirrored into `.cortex/skills/` by
`scripts/sync-cortex-mirror.py`; `scripts/` is not, so shared tooling stays
at the repo's own `scripts/` with exactly one copy to maintain.

## Shipping it

```bash
bash scripts/package-skill.sh <your-skill-name>
```

Writes `dist/<your-skill-name>-<sha>.zip` for upload at
`claude.ai/customize/skills`. See [docs/plugin-harness.md](../../docs/plugin-harness.md)
for how the plugin, marketplace, and Cortex manifests fit together, and why
Cowork needs the ZIP rather than the mounted clone.
