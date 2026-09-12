#!/usr/bin/env bash
# Run the whole plugin pipeline in one command.
#
#   bash scripts/pipeline.sh <plugin-name> ["Display Title"] [flags] [-- <build-workbook args...>]
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
# A re-run does the least work that is still correct. It remembers the
# pluginId and workbookId it used, per plugin and per org, and updates that
# same workbook rather than posting a new one -- so a link you have already
# shared keeps working. An edit confined to src/App.jsx changes neither, and
# needs only steps 1-5: that is --redeploy.
#
# Flags (before `--`):
#   --redeploy        stop after step 5: build, preflight, harness, deploy,
#                     confirm the registration. The fast loop for a change
#                     inside the bundle. Prints the remembered workbook URL
#                     and never touches the workbook itself.
#   --update-workbook force the PUT path even if the spec looks unchanged.
#   --new-workbook    force a brand-new workbook (the old default). Use when
#                     you actually want a second copy, not an update.
#   --workbook-id ID  adopt an existing workbook as this plugin's target, then
#                     update it. For re-attaching after manual surgery.
#
# State lives under $XDG_CACHE_HOME/sigma-plugin-kit/deploys (override the
# root with $SIGMA_PLUGIN_KIT_CACHE). Deleting it is safe: the next run falls
# back to looking the registration up and creating a fresh workbook. What
# happens when a plugin or workbook is deleted outside the kit is in
# docs/plugins.md -> "When something is deleted outside the kit".
set -euo pipefail

usage() { sed -n '2,/^set -/p' "$0" | sed -e '$d' -e 's/^# \{0,1\}//' >&2; }

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

: "${SIGMA_BASE_URL:=https://api.sigmacomputing.com}"
export SIGMA_BASE_URL

# Default publish destination. Override with SIGMA_FOLDER_ID.
FOLDER_ID="${SIGMA_FOLDER_ID:-dfd574ca-7299-4ef7-9d75-00952009b92b}"

name=""
title=""
mode="auto"
adopt_wb=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --) shift; break ;;
    --redeploy|--fast) mode="redeploy"; shift ;;
    --update-workbook) mode="update-workbook"; shift ;;
    --new-workbook) mode="new-workbook"; shift ;;
    --workbook-id)
      adopt_wb="${2:-}"
      [ -n "$adopt_wb" ] || { echo "pipeline: --workbook-id needs an id" >&2; exit 2; }
      shift 2 ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "pipeline: unknown option '$1' (flags go before '--')" >&2; exit 2 ;;
    *)
      if [ -z "$name" ]; then name="$1"
      elif [ -z "$title" ]; then title="$1"
      else echo "pipeline: unexpected argument '$1'" >&2; exit 2
      fi
      shift ;;
  esac
done
extra_args=("$@")

if [ -z "$name" ]; then
  usage
  exit 2
fi

# The name becomes a filesystem path here as well as under plugins/ and in a
# public URL, so reject anything that would need escaping before it is used to
# build one. new-plugin.sh checks the same thing, but only when it scaffolds.
case "$name" in
  _*|*[!a-z0-9-]*|-*|*-)
    echo "pipeline: '$name' must be lowercase kebab-case (a-z, 0-9, internal hyphens)." >&2
    exit 2 ;;
esac

[ -n "$adopt_wb" ] && [ "$mode" = "auto" ] && mode="update-workbook"

say() { printf '\n\033[1m== %s\033[0m\n' "$1" >&2; }

# Step 5 runs under --redeploy too. It is usually free -- the registration is
# read from cache -- and it is the check that catches the URL you just deployed
# to no longer being the URL Sigma has on file, which is otherwise invisible.
total=7
[ "$mode" = "redeploy" ] && total=5

# --- deploy state ---------------------------------------------------------
# What a re-run needs to know and cannot cheaply re-derive: which pluginId this
# plugin registered as, which workbook it published into, and the exact spec
# that workbook currently holds.
#
# Named deploy_state_* rather than state_* because scripts/api/_state.sh
# already owns state_read/state_write, and those are the CREDENTIAL store --
# the one that hard-refuses to write anywhere inside the repo. Nothing here is
# secret: four ids and URLs, in a cache, next to the host-repo clone. The file
# extension is .ids for the same reason -- `.env` would read as a secrets file
# in a repo whose .gitignore opens by excluding one.
#
# Keyed by org as well as plugin name. The same plugin built against two
# SIGMA_BASE_URLs has two pluginIds and two workbooks, and letting one
# overwrite the other is how a PUT lands in the wrong org.
state_dir="${SIGMA_PLUGIN_KIT_CACHE:-${XDG_CACHE_HOME:-$HOME/.cache}/sigma-plugin-kit}/deploys"
host_slug="$(printf '%s' "$SIGMA_BASE_URL" \
  | sed -e 's#^https\{0,1\}://##' -e 's#[^a-zA-Z0-9]#-#g')"
state_file="$state_dir/${host_slug}__${name}.ids"
state_spec="$state_dir/${host_slug}__${name}.spec.json"

deploy_state_get() { # deploy_state_get <key> -> value on stdout, empty if unset
  [ -f "$state_file" ] || return 0
  sed -n "s/^$1=//p" "$state_file" | tail -1
}

deploy_state_set() { # deploy_state_set <key> <value>
  mkdir -p "$state_dir"
  local tmp="$state_file.tmp.$$"
  if [ -f "$state_file" ]; then
    grep -v "^$1=" "$state_file" > "$tmp" || true
  else
    : > "$tmp"
  fi
  printf '%s=%s\n' "$1" "$2" >> "$tmp"
  mv "$tmp" "$state_file"
}

# --- 1. scaffold ----------------------------------------------------------
say "1/$total build"
if [ -f "plugins/$name/package.json" ]; then
  echo "  plugins/$name exists -- using it as-is" >&2
elif [ "$mode" = "redeploy" ]; then
  echo "pipeline: --redeploy but plugins/$name does not exist yet." >&2
  echo "  There is nothing to redeploy. Run without --redeploy to build it." >&2
  exit 1
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
say "2/$total preflight"
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
say "3/$total bind harness"
if [ -n "${SIGMA_SKIP_BINDTEST:-}" ]; then
  echo "  SIGMA_SKIP_BINDTEST set -- skipped" >&2
else
  "${SIGMA_PYTHON:-python3}" scripts/verify-plugin-binding.py "$name" \
    ${data_file:+--data "$data_file"} >&2 || true
fi

# --- 4. deploy ------------------------------------------------------------
# SIGMA_PREFLIGHT_DONE: deploy-plugin.sh runs its own preflight, because it is
# callable on its own and that is exactly how a broken plugin shipped once.
# Step 2 just ran the full one, with --data, so tell it not to repeat itself.
say "4/$total deploy"
url="$(SIGMA_PREFLIGHT_DONE=1 bash scripts/deploy-plugin.sh "$name")"
echo "  $url" >&2

# --- 5. register (reuse if present) ---------------------------------------
# The cheapest correct answer first: if a previous run registered this plugin
# and the URL it registered is still the URL we just deployed to, the
# registration cannot have *changed* -- `url` is the one field PATCH refuses
# to touch. Skipping the lookup is two fewer API calls on every re-run.
#
# It can still have been DELETED, though, outside the kit. That matters only
# when a workbook is about to be written, and it matters a lot: a workbook
# published against a pluginId that no longer exists renders an empty iframe,
# with a 200 on every call and no error on any surface -- the exact silent
# failure this kit is built to prevent. So the cache is trusted outright on
# the --redeploy path, which writes no workbook, and confirmed with one call
# on the path that does.
say "5/$total register"
cached_pid="$(deploy_state_get plugin_id)"
cached_reg_url="$(deploy_state_get plugin_url)"
registration_url="$url"
pid=""
pid_unconfirmed=0

# Look the registration up for real: by name, else create it.
resolve_registration() {
  if pid="$(bash scripts/api/register-plugin.sh id-for "$title" 2>/dev/null)" && [ -n "$pid" ]; then
    echo "  reusing existing registration for '$title' -> $pid" >&2
    registered_url="$(bash scripts/api/register-plugin.sh get "$pid" 2>/dev/null \
      | "${SIGMA_PYTHON:-python3}" -c 'import json,sys; print(json.load(sys.stdin).get("url",""))')"
    if [ -n "$registered_url" ] && [ "$registered_url" != "$url" ]; then
      # Cache what the registration actually points at, not what we deployed --
      # otherwise the next run's fast path would match its own wrong guess and
      # this warning would never print again.
      registration_url="$registered_url"
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
  deploy_state_set plugin_id "$pid"
  deploy_state_set plugin_url "$registration_url"
}

if [ -n "$cached_pid" ] && [ "$cached_reg_url" = "$url" ]; then
  # Taken on trust here, and confirmed in step 6 only if a workbook is about
  # to be written. A registration's `url` cannot change -- PATCH refuses it --
  # so the only thing the cache can be wrong about is the plugin having been
  # DELETED outside the kit. That is harmless until something publishes a
  # workbook pointing at it, and confirming it now would spend a call on every
  # re-run including the ones that publish nothing at all.
  pid="$cached_pid"
  pid_unconfirmed=1
  echo "  cached registration for this URL -> $pid" >&2
else
  resolve_registration
fi

# --- redeploy: stop here --------------------------------------------------
# An edit to src/App.jsx changes the bundle and nothing else. The pluginId is
# the same, the workbook spec is the same, and the workbook loads the new
# bundle the next time someone opens it -- so steps 6 and 7 have no work to do
# and a POST would only mint a duplicate workbook.
if [ "$mode" = "redeploy" ]; then
  remembered_wb_url="$(deploy_state_get workbook_url)"
  say "done (redeploy)"
  echo "  plugin:   $url" >&2
  echo "  pluginId: $pid" >&2
  if [ -n "$remembered_wb_url" ]; then
    echo "  workbook: unchanged -- reload it to pick up the new bundle" >&2
    printf '%s\n' "$remembered_wb_url"
  else
    echo "  No workbook recorded for this plugin in this org." >&2
    echo "  Run without --redeploy to generate and publish one." >&2
  fi
  exit 0
fi

# --- 6. workbook ----------------------------------------------------------
say "6/$total workbook"
target_wb="$adopt_wb"
[ -n "$target_wb" ] || target_wb="$(deploy_state_get workbook_id)"

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

# Which verb, and whether to publish at all. Generation is deterministic --
# same flags, same plugin, byte-identical JSON -- so a spec that matches the
# one last published proves the workbook is already correct, and the cheapest
# publish is the one that doesn't happen.
verb=""
case "$mode" in
  new-workbook)
    verb="post" ;;
  update-workbook)
    if [ -z "$target_wb" ]; then
      echo "pipeline: no workbook recorded for '$name' in this org, so there is" >&2
      echo "  nothing to update. Drop the flag to create one, or name it with" >&2
      echo "  --workbook-id <id>." >&2
      exit 2
    fi
    verb="put" ;;
  *)
    if [ -z "$target_wb" ]; then
      verb="post"
    elif [ -f "$state_spec" ] && cmp -s "$spec" "$state_spec"; then
      verb="none"
    else
      verb="put"
    fi ;;
esac

# Now, and only now, is a stale cached pluginId dangerous: a workbook
# published against one that no longer exists renders an empty iframe, 200 on
# every call, no error on any surface. One confirming call buys that -- on the
# publish path only, so an unchanged re-run still costs nothing.
if [ "$verb" != "none" ] && [ "$pid_unconfirmed" -eq 1 ]; then
  if bash scripts/api/register-plugin.sh get "$pid" >/dev/null 2>&1; then
    echo "  pluginId $pid confirmed live" >&2
  else
    echo "  cached pluginId $pid no longer exists in this org" >&2
    echo "  (deleted outside the kit) -- re-resolving before publishing." >&2
    resolve_registration
    # The spec embeds the pluginId, so it has to be rebuilt against the new
    # one. The recorded workbook is kept deliberately: it currently points at
    # a dead plugin, and rewriting it in place repairs it, where publishing a
    # new one would leave the broken original next to the fix.
    "${SIGMA_PYTHON:-python3}" scripts/build-plugin-workbook.py \
      --name "$title" --plugin-id "$pid" --folder-id "$FOLDER_ID" \
      --plugin-src "plugins/$name" \
      --out "$spec" "${extra_args[@]+"${extra_args[@]}"}" >&2
  fi
fi

publish_failed() { # publish_failed <captured output>
  echo "$1" >&2
  echo "" >&2
  echo "  Publish failed. If the message is 'Invalid kind: \"<kind>\"', that means a" >&2
  echo "  FIELD has a bad value shape -- not that the element kind is unsupported." >&2
  echo "  Sigma drops unknown field names silently and rejects known fields with" >&2
  echo "  wrong shapes. Compare against docs/elements-known-good.md." >&2
  exit 1
}

case "$verb" in
  none)
    wb_id="$target_wb"
    echo "  spec is byte-identical to the one already published" >&2
    echo "  reusing workbook $wb_id -- nothing to publish" >&2
    ;;
  put)
    echo "  updating workbook $target_wb in place (same id, same URL)" >&2
    if ! publish_out="$(bash scripts/api/publish-workbook.sh put "$target_wb" "$spec" 2>&1)"; then
      # The usual cause of a PUT failing here is the workbook having been
      # deleted outside the kit, which the recorded id cannot know about.
      case "$publish_out" in
        *not_found*|*inode_archived*|*404*)
          echo "$publish_out" >&2
          echo "" >&2
          echo "  Workbook $target_wb is gone -- deleted or archived outside the kit." >&2
          echo "  Re-run with --new-workbook to publish a fresh one, or with" >&2
          echo "  --workbook-id <id> to point at the one you meant." >&2
          exit 1 ;;
        *) publish_failed "$publish_out" ;;
      esac
    fi
    wb_id="$target_wb"
    ;;
  post)
    publish_out="$(bash scripts/api/publish-workbook.sh post "$spec" 2>&1)" \
      || publish_failed "$publish_out"
    wb_id="$(printf '%s' "$publish_out" | grep -o '"workbookId":"[^"]*"' | head -1 | cut -d'"' -f4)"
    if [ -z "$wb_id" ]; then
      echo "$publish_out" >&2
      echo "  Could not parse a workbookId from the publish response." >&2
      exit 1
    fi
    ;;
esac
echo "  workbookId: $wb_id" >&2

if [ "$verb" != "none" ]; then
  mkdir -p "$state_dir"
  cp "$spec" "$state_spec"
  deploy_state_set workbook_id "$wb_id"
fi

# --- 7. verify ------------------------------------------------------------
# POST returning 200 does not mean the data binds. A bare column reference
# against a warehouse source publishes fine and then compiles to
# 'Unknown column "[X]"' in the SQL, with no error anywhere -- so check the
# compiled SQL, not just the status code.
say "7/$total verify"
cached_wb_url="$(deploy_state_get workbook_url)"
if [ "$verb" = "none" ] && [ -n "$cached_wb_url" ]; then
  # Nothing was published, and the last run verified this exact spec against
  # this exact workbook. Re-running it would re-prove a fact already proven.
  echo "  skipped -- nothing was published" >&2
  wb_url="$cached_wb_url"
else
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
  [ -n "$wb_url" ] && deploy_state_set workbook_url "$wb_url"
fi

say "done"
echo "  plugin:   $url" >&2
echo "  pluginId: $pid" >&2
printf '%s\n' "$wb_url"
