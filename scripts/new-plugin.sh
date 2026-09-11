#!/usr/bin/env bash
# Scaffold a new plugin.
#
# Usage:
#   bash scripts/new-plugin.sh <plugin-name> ["Display Title"] [--react]
#
#   <plugin-name>   kebab-case; becomes the directory name and the URL path
#                   segment, so it must be URL-safe.
#   "Display Title" optional; defaults to the name title-cased.
#   --react         scaffold a Vite + React project instead of a single file.
#
# Which archetype:
#
#   single file (default)  One index.html, SDK from unpkg as UMD, no build.
#                          Right for hand-rolled DOM/SVG/canvas. Fastest
#                          iteration -- open the file in a browser and it
#                          renders its demo data.
#
#   --react                A Vite project using the SDK's React hooks. Right
#                          the moment you need npm packages -- Plotly, Mapbox,
#                          D3, Recharts. deploy-plugin.sh builds it for you.
#
# Both are gitignored working directories; the public host repo holds the
# deployed copy. See docs/plugins.md.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

name=""
title=""
archetype="single"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --react) archetype="react"; shift ;;
    --single) archetype="single"; shift ;;
    -h|--help) sed -n '2,28p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
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
  echo "usage: new-plugin.sh <plugin-name> [\"Display Title\"] [--react]" >&2
  exit 2
fi

# The name lands in a filesystem path AND a public URL, so reject anything
# that would need escaping in either -- including a leading '_', which marks
# the templates themselves.
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

if [ "$archetype" = "react" ]; then
  src="$repo_root/plugins/_react-template"
else
  src="$repo_root/plugins/_template"
fi
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
echo "Created plugins/$name  ($archetype archetype, title: $title)"
echo ""
echo "Next:"
if [ "$archetype" = "react" ]; then
  echo "  1. cd plugins/$name && npm install && npm run dev     # http://localhost:5173"
  echo "     Point a Sigma plugin element at that URL to iterate live:"
  echo "     element ••• menu -> Point to Development URL."
  echo "  2. Edit src/App.jsx -- the editor panel is declared at module scope."
  echo "  3. bash scripts/pipeline.sh $name \"$title\"            # builds, deploys, registers, publishes"
else
  echo "  1. Open plugins/$name/index.html in a browser -- it renders demo data"
  echo "     with no Sigma client, so you can iterate without deploying."
  echo "  2. Edit DEFS and draw()."
  echo "  3. bash scripts/pipeline.sh $name \"$title\"            # deploys, registers, publishes"
fi
echo ""
echo "  Editor-panel types, variables, actions: docs/plugin-api.md"
