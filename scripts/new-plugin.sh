#!/usr/bin/env bash
# Scaffold a new plugin.
#
# Usage:
#   bash scripts/new-plugin.sh <plugin-name> ["Display Title"] [--from <shape>]
#
#   <plugin-name>   kebab-case; becomes the directory name and the URL path
#                   segment, so it must be URL-safe.
#   "Display Title" optional; defaults to the name title-cased.
#   --from <shape>  start from an archetype instead of the default bar chart.
#                   `--from list` prints what is available.
#
# ARCHETYPES. plugins/_archetypes/<shape>.jsx is a finished src/App.jsx for one
# chart shape -- ranked table, KPI tiles, funnel, donut. Picking the nearest
# one turns "write 190 lines of React" into "edit 30", and every one of them
# already satisfies the rules preflight enforces: the guarded ResizeObserver,
# the 100%-of-the-iframe layout, the badged fallback, the SDK import.
#
# They are App.jsx files, NOT whole projects, on purpose: package.json,
# vite.config.js and index.html stay owned by _react-template alone, so
# `base: './'` and the bundled-SDK rule exist in exactly one place and cannot
# drift across copies. Every archetype declares `label` then `value` as its
# first two column bindings, which is what build-plugin-workbook.py binds
# positionally -- so `pipeline.sh <name>` works on any of them with no flags.
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

archetype_dir="$repo_root/plugins/_archetypes"

list_archetypes() {
  echo "Archetypes (bash scripts/new-plugin.sh <name> --from <shape>):"
  echo "  default   bar chart -- ranked horizontal bars (no --from needed)"
  for f in "$archetype_dir"/*.jsx; do
    [ -e "$f" ] || continue
    b="$(basename "$f" .jsx)"
    # The summary is the archetype's own first line ("// Archetype: ..."), so
    # this list cannot drift from the files it describes.
    d="$(sed -n '1s|^// *Archetype: *||p' "$f" | sed 's/\.$//')"
    printf '  %-9s %s\n' "$b" "$d"
  done
}

name=""
title=""
archetype=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    # Accepted and ignored: --react was how you opted IN to this archetype
    # back when there were two, so an old command line still works.
    --react) shift ;;
    --from)
      archetype="${2:-}"
      [ -n "$archetype" ] || { echo "new-plugin: --from needs a shape name (try --from list)" >&2; exit 2; }
      shift 2 ;;
    --single)
      echo "new-plugin: the single-file archetype has been removed -- every plugin" >&2
      echo "  is a Vite + React project now. Drop --single; see docs/plugins.md." >&2
      exit 2 ;;
    # Print the header block, however long it grows -- a fixed line range
    # silently truncates the help the moment someone adds a paragraph.
    -h|--help) sed -n '2,/^set -/p' "$0" | sed -e '$d' -e 's/^# \{0,1\}//'; exit 0 ;;
    -*) echo "new-plugin: unknown option '$1'" >&2; exit 2 ;;
    *)
      if [ -z "$name" ]; then name="$1"
      elif [ -z "$title" ]; then title="$1"
      else echo "new-plugin: unexpected argument '$1'" >&2; exit 2
      fi
      shift ;;
  esac
done

if [ "$archetype" = "list" ]; then
  list_archetypes
  exit 0
fi

if [ -z "$name" ]; then
  echo "usage: new-plugin.sh <plugin-name> [\"Display Title\"] [--from <shape>]" >&2
  exit 2
fi

# Resolve --from before anything is created, so a typo costs nothing. `basename`
# keeps a path out of the lookup -- the value is used to build a file path.
archetype_src=""
if [ -n "$archetype" ]; then
  archetype_src="$archetype_dir/$(basename "$archetype" .jsx).jsx"
  if [ ! -f "$archetype_src" ]; then
    echo "new-plugin: no archetype '$archetype'." >&2
    echo "" >&2
    list_archetypes >&2
    exit 2
  fi
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

# The archetype replaces src/App.jsx only. Everything that makes the project
# build -- package.json, vite.config.js (base: './'), index.html -- stays the
# template's, which is why an archetype cannot break the build. Must happen
# BEFORE the substitution pass below, so the archetype's own
# __PLUGIN_TITLE__ gets replaced too.
if [ -n "$archetype_src" ]; then
  cp "$archetype_src" "$dest/src/App.jsx"
  echo "  archetype: $(basename "$archetype_src" .jsx) -> src/App.jsx"
fi

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

# Reuse an already-installed node_modules for this exact dependency set, by
# hard link. Fails soft in every direction: no store, an unusable `cp -al` and
# `pax`, anything -- and then npm install runs as it always did.
deps_installed=0
# shellcheck source=scripts/_deps-cache.sh
. "$repo_root/scripts/_deps-cache.sh"
if dep_key="$(deps_cache_key "$dest")" && [ -n "$dep_key" ]; then
  if deps_cache_link "$dep_key" "$dest/node_modules"; then
    deps_installed=1
    echo "  node_modules: hard-linked from the shared store (no install needed)"
  fi
fi

echo ""
echo "Created plugins/$name  (Vite + React, title: $title)"
echo ""
echo "Next:"
if [ "$deps_installed" -eq 1 ]; then
echo "  1. cd plugins/$name && npm run dev                   # http://localhost:5173"
else
echo "  1. cd plugins/$name && npm install && npm run dev     # http://localhost:5173"
fi
echo "     Point a Sigma plugin element at that URL to iterate live:"
echo "     element ••• menu -> Point to Development URL."
echo "  2. Edit src/App.jsx -- the editor panel is declared at module scope."
echo "  3. bash scripts/pipeline.sh $name \"$title\"            # builds, deploys, registers, publishes"
echo "  4. bash scripts/pipeline.sh $name --redeploy         # every later bundle-only edit"
echo ""
if [ -z "$archetype_src" ]; then
echo "  Starting from a closer shape is cheaper than writing one: --from list"
fi
echo "  Editor-panel types, variables, actions: docs/plugin-api.md"
