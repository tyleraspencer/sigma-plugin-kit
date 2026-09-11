#!/usr/bin/env bash
# Run the whole plugin pipeline in one command.
#
#   bash scripts/pipeline.sh <plugin-name> ["Display Title"] [-- <build-workbook args...>]
#
# Step 0 happens before this script: ask the user where the data comes from
# (synthetic rows vs a real table) and what it should look like. The skill
# skills/sigma-plugin-pipeline/SKILL.md has the question set.
#
# Steps, in the only order that works:
#   1. scaffold plugins/<plugin-name>/ if it does not exist yet
#   2. preflight: static checks for the silent failure modes (BLOCKING)
#   3. generate the local bind harness and print its URL
#   4. deploy it to the public Pages host and wait until it serves
#   5. register it (or reuse an existing registration by name)
#   6. generate a workbook spec bound to real data and publish it
#   7. verify: element queries compile, and the bound SQL is real
#
# Steps 2 and 3 come before 4 deliberately: deploy and register are the
# irreversible steps, so everything cheap that can fail runs first.
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
  sed -n '2,28p' "$0" | sed 's/^# \{0,1\}//' >&2
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
say "1/7 build"
if [ -f "plugins/$name/package.json" ]; then
  echo "  plugins/$name exists -- using it as-is" >&2
else
  bash scripts/new-plugin.sh "$name" ${title:+"$title"} >&2
fi
[ -n "$title" ] || title="$(printf '%s' "$name" | tr '-' ' ' \
  | awk '{for(i=1;i<=NF;i++) $i=toupper(substr($i,1,1)) substr($i,2)}1')"

# Pull --data out of the passthrough args so preflight and the bind harness
# can check against the same rows the workbook will be built from.
data_file=""
for ((i = 0; i < ${#extra_args[@]}; i++)); do
  if [ "${extra_args[$i]}" = "--data" ]; then
    data_file="${extra_args[$((i + 1))]:-}"
    break
  fi
done

# --- 2. preflight ---------------------------------------------------------
# Static checks for the silent failure modes, BEFORE anything irreversible.
# Every one of these deploys clean, publishes clean, renders the plugin's own
# fallback data and screenshots perfectly -- so none of them is caught by a
# status code. Blocking on purpose: deploy and register cannot be undone,
# because PATCH /v2/plugins/{id} cannot change a plugin's url.
say "2/7 preflight"
if ! "${SIGMA_PYTHON:-python3}" scripts/preflight-plugin.py "$name" \
       ${data_file:+--data "$data_file"} >&2; then
  echo "" >&2
  echo "  Preflight failed -- stopping before deploy. Nothing has been" >&2
  echo "  created, so there is no orphaned pluginId to clean up." >&2
  echo "  Override with SIGMA_SKIP_PREFLIGHT=1 if you know better." >&2
  [ -n "${SIGMA_SKIP_PREFLIGHT:-}" ] || exit 1
  echo "  SIGMA_SKIP_PREFLIGHT set -- continuing anyway." >&2
fi

# --- 3. bind harness ------------------------------------------------------
# Static checks cannot prove the plugin renders BOUND data. This generates a
# local page that runs the plugin twice -- unbound and bound -- and compares
# the two renders. It needs a browser, so the pipeline generates it and
# prints the URL rather than asserting; open it before trusting step 7.
say "3/7 bind harness"
if [ -n "${SIGMA_SKIP_BINDTEST:-}" ]; then
  echo "  SIGMA_SKIP_BINDTEST set -- skipped" >&2
else
  "${SIGMA_PYTHON:-python3}" scripts/verify-plugin-binding.py "$name" \
    ${data_file:+--data "$data_file"} >&2 || true
fi

# --- 4. deploy ------------------------------------------------------------
say "4/7 deploy"
url="$(bash scripts/deploy-plugin.sh "$name")"
echo "  $url" >&2

# --- 5. register (reuse if present) ---------------------------------------
say "5/7 register"
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

# --- 6. workbook ----------------------------------------------------------
say "6/7 workbook"
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

# --- 7. verify ------------------------------------------------------------
# POST returning 200 does not mean the data binds. A bare column reference
# against a warehouse source publishes fine and then compiles to
# 'Unknown column "[X]"' in the SQL, with no error anywhere -- so check the
# compiled SQL, not just the status code.
say "7/7 verify"
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
