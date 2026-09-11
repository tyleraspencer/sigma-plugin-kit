#!/usr/bin/env python3
"""Regenerate the .cortex/skills/ mirror from the canonical skills/ sources.

Cortex Code auto-discovers skills from .cortex/skills/<name>/SKILL.md. Upstream
sigma-agent-skills symlinks that file to the canonical copy; symlinks are a
Windows-checkout footgun (need core.symlinks=true + Developer Mode/admin, else
git checks out a plain text file containing the link target instead of the
real content). This keeps real, checked-in copies instead -- cheap to
regenerate, safe on every platform.

Run after any edit under skills/, before committing. CI enforces that the
mirror is in sync (`git diff --exit-code .cortex/`).
"""
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_ROOT = REPO_ROOT / "skills"
CORTEX_ROOT = REPO_ROOT / ".cortex" / "skills"

# Copied alongside SKILL.md when present. scripts/ is deliberately absent:
# Cortex reads instructions and reference material from the mirror, while
# executable tooling stays at the repo's own scripts/ so there is exactly one
# copy to maintain.
MIRRORED_SUBDIRS = ("reference", "examples")


def main():
    if not SKILLS_ROOT.is_dir():
        print(f"no skills/ directory at {SKILLS_ROOT}", file=sys.stderr)
        return 1

    # Rebuilt from scratch so a renamed or deleted skill does not leave a
    # stale directory behind in the mirror for CI to then call "in sync".
    if CORTEX_ROOT.exists():
        shutil.rmtree(CORTEX_ROOT)
    CORTEX_ROOT.mkdir(parents=True)

    synced = 0
    for skill_dir in sorted(SKILLS_ROOT.iterdir()):
        if not (skill_dir / "SKILL.md").is_file():
            continue
        dest = CORTEX_ROOT / skill_dir.name
        dest.mkdir()
        shutil.copy2(skill_dir / "SKILL.md", dest / "SKILL.md")
        for sub in MIRRORED_SUBDIRS:
            src = skill_dir / sub
            if src.is_dir():
                shutil.copytree(src, dest / sub)
        print(f"synced {dest.relative_to(REPO_ROOT)} from {skill_dir.relative_to(REPO_ROOT)}")
        synced += 1

    if synced == 0:
        print("no skills found (need skills/<name>/SKILL.md)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
