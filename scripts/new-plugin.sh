#!/usr/bin/env bash
# Scaffold a new plugin from plugins/_template/.
#
# Usage:
#   bash scripts/new-plugin.sh <plugin-name> ["Display Title"]
#
#   <plugin-name>   kebab-case; becomes the directory name and the URL path
#                   segment, so it must be URL-safe.
#   "Display Title" optional; defaults to the name title-cased. Substituted
#                   for __PLUGIN_TITLE__ in the template.
#
# Next steps are printed on success: edit, deploy, register, build a workbook.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
template="$repo_root/plugins/_template/index.html"

name="${1:-}"
if [ -z "$name" ]; then
  echo "usage: new-plugin.sh <plugin-name> [\"Display Title\"]" >&2
  exit 2
fi

# The name lands in a filesystem path AND a public URL, so reject anything
# that would need escaping in either -- including a leading '_', which is how
# the template itself is marked as not-a-real-plugin.
case "$name" in
  _*|*[!a-z0-9-]*|-*|*-)
    echo "new-plugin: '$name' must be lowercase kebab-case (a-z, 0-9, internal hyphens)." >&2
    exit 2 ;;
esac

[ -f "$template" ] || { echo "new-plugin: template missing at $template" >&2; exit 1; }

dest="$repo_root/plugins/$name"
if [ -e "$dest" ]; then
  echo "new-plugin: plugins/$name already exists -- refusing to overwrite." >&2
  exit 1
fi

title="${2:-}"
if [ -z "$title" ]; then
  # kebab-case -> Title Case, without bash 4's ${x^} (macOS ships bash 3.2).
  title="$(printf '%s' "$name" | tr '-' ' ' | awk '{for(i=1;i<=NF;i++) $i=toupper(substr($i,1,1)) substr($i,2)}1')"
fi

mkdir -p "$dest"
# Substitute via python, not sed: a title containing '/' or '&' would corrupt
# a sed replacement, and titles are free text.
"${SIGMA_PYTHON:-python3}" - "$template" "$dest/index.html" "$title" <<'PY'
import sys
src, dst, title = sys.argv[1], sys.argv[2], sys.argv[3]
with open(src, encoding='utf-8') as fh:
    text = fh.read()
with open(dst, 'w', encoding='utf-8') as fh:
    fh.write(text.replace('__PLUGIN_TITLE__', title))
PY

echo "Created plugins/$name/index.html  (title: $title)"
echo ""
echo "Next:"
echo "  1. Edit plugins/$name/index.html -- adjust DEFS and draw()."
echo "  2. bash scripts/deploy-plugin.sh $name       # host it on GitHub Pages"
echo "  3. bash scripts/api/register-plugin.sh create \"$title\" <hosted-url>"
echo "  4. python3 scripts/build-plugin-workbook.py --plugin-id <id> ..."
