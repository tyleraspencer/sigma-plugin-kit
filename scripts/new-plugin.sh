#!/usr/bin/env bash
# Scaffold a new plugin.
#
# Usage:
#   bash scripts/new-plugin.sh <plugin-name> ["Display Title"]
#
#   <plugin-name>   kebab-case; becomes the directory name and the URL path
#                   segment, so it must be URL-safe.
#   "Display Title" optional; defaults to the name title-cased.
#
# Every plugin is a Vite + React project. There is no hand-written-HTML
# archetype: it was removed deliberately, not lost. A single index.html
# loading the SDK's UMD bundle from a CDN looks simpler and costs more --
# React is an external of that bundle, so the page has to load React first or
# the SDK never initializes and the plugin silently renders fallback data
# forever. Bundling the SDK as an npm dependency makes that failure
# unrepresentable, and npm packages (Plotly, Mapbox, D3, Recharts) are
# available the moment you want one.
#
# deploy-plugin.sh builds the project and publishes dist/, and refuses any
# plugin without a package.json.
#
# The scaffolded directory is a gitignored working directory; the public host
# repo holds the deployed copy. See docs/plugins.md.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

name=""
title=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    # Accepted and ignored: --react was how you opted IN to this archetype
    # back when there were two, so an old command line still works.
    --react) shift ;;
    --single)
      echo "new-plugin: the single-file archetype has been removed -- every plugin" >&2
      echo "  is a Vite + React project now. Drop --single; see docs/plugins.md." >&2
      exit 2 ;;
    -h|--help) sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) echo "new-plugin: unknown option '$1'" >&2; exit 2 ;;
    *)
      if [ -z "$name" ]; then name="$1"
      elif [ -z "$title" ]; then title="$1"
      else echo "new-plugin: unexpected argument '$1'" >&2; exit 2
      fi
      shift ;;
  esac
done

if [ -z "$name" ]; then
  echo "usage: new-plugin.sh <plugin-name> [\"Display Title\"]" >&2
  exit 2
fi

# The name lands in a filesystem path AND a public URL, so reject anything
# that would need escaping in either -- including a leading '_', which marks
# the template and the verification harness.
case "$name" in
  _*|*[!a-z0-9-]*|-*|*-)
    echo "new-plugin: '$name' must be lowercase kebab-case (a-z, 0-9, internal hyphens)." >&2
    exit 2 ;;
esac

dest="$repo_root/plugins/$name"
if [ -e "$dest" ]; then
  echo "new-plugin: plugins/$name already exists -- refusing to overwrite." >&2
  exit 1
fi

if [ -z "$title" ]; then
  # kebab-case -> Title Case, without bash 4's ${x^} (macOS ships bash 3.2).
  title="$(printf '%s' "$name" | tr '-' ' ' \
    | awk '{for(i=1;i<=NF;i++) $i=toupper(substr($i,1,1)) substr($i,2)}1')"
fi

src="$repo_root/plugins/_react-template"
[ -d "$src" ] || { echo "new-plugin: template missing at $src" >&2; exit 1; }

# Copy, then substitute placeholders in every text file. python, not sed: a
# title containing '/' or '&' would corrupt a sed replacement, and titles are
# free text.
mkdir -p "$dest"
( cd "$src" && tar cf - . ) | ( cd "$dest" && tar xf - )

"${SIGMA_PYTHON:-python3}" - "$dest" "$title" "$name" <<'PY'
import pathlib, sys
dest, title, name = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
for p in sorted(dest.rglob('*')):
    if not p.is_file():
        continue
    try:
        text = p.read_text(encoding='utf-8')
    except (UnicodeDecodeError, OSError):
        continue
    new = text.replace('__PLUGIN_TITLE__', title).replace('__PLUGIN_NAME__', name)
    if new != text:
        p.write_text(new, encoding='utf-8')
        print('  substituted %s' % p.relative_to(dest.parent.parent))
PY

echo ""
echo "Created plugins/$name  (Vite + React, title: $title)"
echo ""
echo "Next:"
echo "  1. cd plugins/$name && npm install && npm run dev     # http://localhost:5173"
echo "     Point a Sigma plugin element at that URL to iterate live:"
echo "     element ••• menu -> Point to Development URL."
echo "  2. Edit src/App.jsx -- the editor panel is declared at module scope."
echo "  3. bash scripts/pipeline.sh $name \"$title\"            # builds, deploys, registers, publishes"
echo ""
echo "  Editor-panel types, variables, actions: docs/plugin-api.md"
