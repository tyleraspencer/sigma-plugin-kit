#!/usr/bin/env bash
# Run the whole plugin pipeline in one command.
#
#   bash scripts/pipeline.sh <plugin-name> ["Display Title"] [-- <build-workbook args...>]
#
# Steps, in the only order that works:
#   1. scaffold plugins/<plugin-name>/ if it does not exist yet
#   2. deploy it to the public Pages host and wait until it serves
#   3. register it (or reuse an existing registration by name)
#   4. generate a workbook spec bound to real data and publish it
#   5. verify: element queries compile, and the bound SQL is real
#
# Prints the workbook URL last. Everything else goes to stderr.
#
# Anything after `--` is passed through to build-plugin-workbook.py, so the
# data source is overridable without editing anything:
#   bash scripts/pipeline.sh my-viz "My Viz" -- --dimension PRODUCT_FAMILY \
#       --measure "Sum(QUANTITY)" --measure-name Units
#
# Idempotent by design: re-running with the same plugin name reuses the
# existing registration rather than minting a second pluginId. That matters
# because Sigma's PATCH cannot change a plugin's url, so duplicate
# registrations are the usual way people end up with orphaned workbooks.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

: "${SIGMA_BASE_URL:=https://api.sigmacomputing.com}"
export SIGMA_BASE_URL

# Default publish destination. Override with SIGMA_FOLDER_ID.
FOLDER_ID="${SIGMA_FOLDER_ID:-dfd574ca-7299-4ef7-9d75-00952009b92b}"

name="${1:-}"
if [ -z "$name" ]; then
  sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//' >&2
  exit 2
fi
shift
title=""
if [ "${1:-}" != "--" ] && [ -n "${1:-}" ]; then
  title="$1"; shift
fi
[ "${1:-}" = "--" ] && shift
extra_args=("$@")

say() { printf '\n\033[1m== %s\033[0m\n' "$1" >&2; }

# --- 1. scaffold ----------------------------------------------------------
say "1/5 build"
if [ -f "plugins/$name/index.html" ]; then
  echo "  plugins/$name/index.html exists -- using it as-is" >&2
else
  bash scripts/new-plugin.sh "$name" ${title:+"$title"} >&2
fi
[ -n "$title" ] || title="$(printf '%s' "$name" | tr '-' ' ' \
  | awk '{for(i=1;i<=NF;i++) $i=toupper(substr($i,1,1)) substr($i,2)}1')"

# --- 2. deploy ------------------------------------------------------------
say "2/5 deploy"
url="$(bash scripts/deploy-plugin.sh "$name")"
echo "  $url" >&2

# --- 3. register (reuse if present) ---------------------------------------
say "3/5 register"
if pid="$(bash scripts/api/register-plugin.sh id-for "$title" 2>/dev/null)" && [ -n "$pid" ]; then
  echo "  reusing existing registration for '$title' -> $pid" >&2
  registered_url="$(bash scripts/api/register-plugin.sh get "$pid" 2>/dev/null \
    | "${SIGMA_PYTHON:-python3}" -c 'import json,sys; print(json.load(sys.stdin).get("url",""))')"
  if [ "$registered_url" != "$url" ]; then
    echo "  WARNING: the existing registration points at a different URL:" >&2
    echo "    registered: $registered_url" >&2
    echo "    deployed:   $url" >&2
    echo "  Sigma's PATCH cannot change a plugin's url. To repoint it you must" >&2
    echo "  delete and re-create, which mints a new pluginId and breaks every" >&2
    echo "  workbook already referencing the old one. Continuing with the" >&2
    echo "  registered URL above." >&2
  fi
else
  pid="$(bash scripts/api/register-plugin.sh create "$title" "$url" \
           "$title (sigma-plugin-kit)")"
fi

# --- 4. workbook ----------------------------------------------------------
say "4/5 workbook"
spec="$(mktemp "${TMPDIR:-/tmp}/plugin-spec.XXXXXX.json")"
trap 'rm -f "$spec"' EXIT
# --plugin-src lets the generator read this plugin's own configureEditorPanel
# and synthesize columns that match its bindings, so the data fits the plugin
# instead of being generic. Anything in extra_args wins -- notably --data,
# which is what you want for rows that actually mean something.
"${SIGMA_PYTHON:-python3}" scripts/build-plugin-workbook.py \
  --name "$title" --plugin-id "$pid" --folder-id "$FOLDER_ID" \
  --plugin-src "plugins/$name" \
  --out "$spec" "${extra_args[@]+"${extra_args[@]}"}" >&2

publish_out="$(bash scripts/api/publish-workbook.sh post "$spec" 2>&1)" || {
  echo "$publish_out" >&2
  echo "" >&2
  echo "  Publish failed. If the message is 'Invalid kind: \"<kind>\"', that means a" >&2
  echo "  FIELD has a bad value shape -- not that the element kind is unsupported." >&2
  echo "  Sigma drops unknown field names silently and rejects known fields with" >&2
  echo "  wrong shapes. Compare against docs/elements-known-good.md." >&2
  exit 1
}
wb_id="$(printf '%s' "$publish_out" | grep -o '"workbookId":"[^"]*"' | head -1 | cut -d'"' -f4)"
if [ -z "$wb_id" ]; then
  echo "$publish_out" >&2
  echo "  Could not parse a workbookId from the publish response." >&2
  exit 1
fi
echo "  workbookId: $wb_id" >&2

# --- 5. verify ------------------------------------------------------------
# POST returning 200 does not mean the data binds. A bare column reference
# against a warehouse source publishes fine and then compiles to
# 'Unknown column "[X]"' in the SQL, with no error anywhere -- so check the
# compiled SQL, not just the status code.
say "5/5 verify"
bash scripts/api/verify-workbook.sh "$wb_id" 2>&1 | tail -5 >&2
sql="$(bash -c 'source scripts/api/_env.sh
  sigma_curl "$SIGMA_BASE_URL/v2/workbooks/'"$wb_id"'/elements/tbl-data/query"' 2>/dev/null \
  | "${SIGMA_PYTHON:-python3}" -c 'import json,sys
try: print(json.load(sys.stdin).get("sql",""))
except Exception: print("")')"
if printf '%s' "$sql" | grep -q 'Unknown column'; then
  echo "  FAIL: the element compiled to SQL containing 'Unknown column'." >&2
  printf '  %s\n' "$(printf '%s' "$sql" | head -c 240)" >&2
  echo "  A column reference did not resolve. Warehouse columns must be" >&2
  echo "  [TABLE/COLUMN], not bare [COLUMN]." >&2
  exit 1
fi
if [ -n "$sql" ]; then
  echo "  SQL resolves cleanly:" >&2
  printf '    %s\n' "$(printf '%s' "$sql" | head -1 | head -c 200)" >&2
fi

wb_url="$(bash scripts/api/publish-workbook.sh get-meta "$wb_id" 2>/dev/null \
  | "${SIGMA_PYTHON:-python3}" -c 'import json,sys; print(json.load(sys.stdin).get("url",""))')"

say "done"
echo "  plugin:   $url" >&2
echo "  pluginId: $pid" >&2
printf '%s\n' "$wb_url"
