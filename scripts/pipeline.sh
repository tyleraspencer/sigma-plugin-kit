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
#   3. build the bundle, then generate the local bind harness and print its URL
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
# THE SECOND RUN IS NOT THE FIRST RUN. Re-running used to repeat all seven
# steps and POST a brand-new workbook every time, so ten style tweaks left ten
# workbooks with ten URLs and nine of them dead -- and if the first URL had
# already been shared, the edit never showed up where anyone was looking. So
# this script now remembers what it built (per plugin, per org) and does the
# least work that can still be correct:
#
#   * the registration is reused from cache when the deployed URL still
#     matches it -- zero API calls instead of two
#   * the workbook spec is regenerated locally and compared against the one
#     last published. Identical -> nothing is published at all. Different ->
#     PUT into the SAME workbook, so the id and the shared URL survive.
#
# An edit to src/App.jsx changes neither the pluginId nor the spec, so it
# needs steps 1-5 and nothing else. That is what --redeploy does.
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
# back to looking the registration up and creating a fresh workbook.
set -euo pipefail

usage() { sed -n '2,/^set -/p' "$0" | sed -e '$d' -e 's/^# \{0,1\}//' >&2; }

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

# plugin_build_if_stale: the bind harness at step 3 needs the same dist/ that
# deploy ships at step 4, so one function owns building it.
# shellcheck source=scripts/_plugin-build.sh
. "$repo_root/scripts/_plugin-build.sh"

: "${SIGMA_BASE_URL:=https://api.sigmacomputing.com}"
export SIGMA_BASE_URL

# Default publish destination. Override with SIGMA_FOLDER_ID.
FOLDER_ID="${SIGMA_FOLDER_ID:-dfd574ca-7299-4ef7-9d75-00952009b92b}"

name=""
title=""
mode="auto"
adopt_wb=""
ship=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --) shift; break ;;
    --redeploy|--fast) mode="redeploy"; shift ;;
    # The small-update path: build + deploy + confirm the registration, and
    # nothing else. The gates cost 0.25s between them, so this does NOT buy
    # wall clock -- the only real wait in a deploy is GitHub Pages. What it
    # buys is fewer moving parts when you already know what you changed: no
    # harness to regenerate and open, and no static check that can block a
    # one-line style tweak. Use --redeploy when you want the gates.
    --ship) mode="redeploy"; ship=1; shift ;;
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


# One line per step, no leading blank. Seven bold banners separated by blank
# lines is fourteen lines of structure around about six lines of content.
say()  { printf '\033[1m== %s\033[0m\n' "$1" >&2; }
# Steps are counted, not numbered by hand: --ship drops two of them, and a
# hardcoded "2/5 preflight" on a run that never preflights is worse than no
# label at all.
step=0
next_step() { step=$((step + 1)); say "$step/$total $1"; }
# Routine progress that is only interesting when you are debugging the
# pipeline itself. Errors and results never go through this.
note() { [ -n "${SIGMA_VERBOSE:-}" ] && printf '  %s\n' "$1" >&2 || true; }

# Step 5 runs under --redeploy too. It is usually free -- the registration is
# read from cache -- and it is the check that catches the URL you just deployed
# to no longer being the URL Sigma has on file, which is otherwise invisible.
total=7
[ "$mode" = "redeploy" ] && total=5
[ -n "$ship" ] && total=3

# --- deploy state ---------------------------------------------------------
# What a re-run needs to know and cannot cheaply re-derive: which pluginId this
# plugin registered as, which workbook it published into, and the exact spec
# that workbook currently holds. None of it is secret -- ids and URLs -- so it
# sits in the same cache root as the host-repo clone rather than going through
# _state.sh, whose whole job is keeping credentials out of files like this one.
#
# Keyed by org as well as plugin name. The same plugin built against two
# SIGMA_BASE_URLs has two pluginIds and two workbooks, and letting one
# overwrite the other is how a PUT lands in the wrong org.
state_dir="${SIGMA_PLUGIN_KIT_CACHE:-${XDG_CACHE_HOME:-$HOME/.cache}/sigma-plugin-kit}/deploys"
host_slug="$(printf '%s' "$SIGMA_BASE_URL" \
  | sed -e 's#^https\{0,1\}://##' -e 's#[^a-zA-Z0-9]#-#g')"
state_file="$state_dir/${host_slug}__${name}.env"
state_spec="$state_dir/${host_slug}__${name}.spec.json"

state_get() { # state_get <key> -> value on stdout, empty if unset
  [ -f "$state_file" ] || return 0
  sed -n "s/^$1=//p" "$state_file" | tail -1
}

state_set() { # state_set <key> <value>
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
next_step "build"
if [ -f "plugins/$name/package.json" ]; then
  note "plugins/$name exists -- using it as-is"
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
if [ -n "$ship" ]; then
  # Said out loud, every time. A skipped gate you forgot about is how a
  # plugin ships broken, and --ship is meant for changes you are sure of, not
  # for changes you have not looked at.
  printf '\033[1m== ship\033[0m  preflight + bind harness skipped\n' >&2
fi
if [ -z "$ship" ]; then
next_step "preflight"
if ! "${SIGMA_PYTHON:-python3}" scripts/preflight-plugin.py "$name" \
       ${data_file:+--data "$data_file"} >&2; then
  echo "" >&2
  echo "  Preflight failed -- stopping before deploy. Nothing has been" >&2
  echo "  created, so there is no orphaned pluginId to clean up." >&2
  echo "  Override with SIGMA_SKIP_PREFLIGHT=1 if you know better." >&2
  [ -n "${SIGMA_SKIP_PREFLIGHT:-}" ] || exit 1
  echo "  SIGMA_SKIP_PREFLIGHT set -- continuing anyway." >&2
fi
fi

# --- 3. bind harness ------------------------------------------------------
# Static checks cannot prove the plugin renders BOUND data. This generates a
# local page that runs the plugin twice -- unbound and bound -- and compares
# the two renders. It needs a browser, so the pipeline generates it and
# prints the URL rather than asserting; open it before trusting step 7.
#
# The build has to happen HERE, not at step 4. The harness drives the built
# bundle, because that is what Sigma loads -- and the only build used to live
# inside deploy. So on a first build this step found no dist/, printed "build
# it first" and no-opped, past a `|| true`: the one gate that proves a plugin
# renders bound data never ran on the one run where the plugin was new.
# Building here costs nothing at step 4, which stamps the source hash and
# skips a build it has already done.
if [ -z "$ship" ]; then
next_step "bind harness"
if [ -n "${SIGMA_SKIP_BINDTEST:-}" ]; then
  echo "  SIGMA_SKIP_BINDTEST set -- skipped" >&2
else
  # A build failure is not fatal here on purpose -- step 4 calls the same
  # function and fails on it properly, with deploy's own diagnostics.
  # Computed once here and handed to deploy below, the same way preflight is:
  # hashing the source tree twice per run was most of what this step added to
  # an unchanged re-run, and nothing touches the plugin between the two.
  src_hash="$(plugin_source_hash "$repo_root/plugins/$name")"
  if plugin_build_if_stale "$repo_root/plugins/$name" "$name" "$src_hash"; then
    "${SIGMA_PYTHON:-python3}" scripts/verify-plugin-binding.py "$name" \
      ${data_file:+--data "$data_file"} >&2 || true
  else
    echo "  build failed -- skipping the harness; the deploy step reports why." >&2
  fi
fi
fi

# --- 4. deploy ------------------------------------------------------------
# SIGMA_PREFLIGHT_DONE: deploy-plugin.sh runs its own preflight, because it is
# callable on its own and that is exactly how a broken plugin shipped once.
# Step 2 just ran the full one, with --data, so tell it not to repeat itself.
next_step "deploy"
url="$(SIGMA_PREFLIGHT_DONE=1 SIGMA_PLUGIN_SRC_HASH="${src_hash:-}" \
       bash scripts/deploy-plugin.sh "$name")"
echo "  $url" >&2

# --- 5. register (reuse if present) ---------------------------------------
# The cheapest correct answer first: if a previous run registered this plugin
# and the URL it registered is still the URL we just deployed to, the
# registration cannot have changed -- `url` is the one field PATCH refuses to
# touch. Skipping the lookup is two fewer API calls on every re-run.
next_step "register"
cached_pid="$(state_get plugin_id)"
cached_reg_url="$(state_get plugin_url)"
registration_url="$url"
if [ -n "$cached_pid" ] && [ "$cached_reg_url" = "$url" ]; then
  pid="$cached_pid"
  note "cached registration for this URL -> $pid"
elif pid="$(bash scripts/api/register-plugin.sh id-for "$title" 2>/dev/null)" && [ -n "$pid" ]; then
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
state_set plugin_id "$pid"
state_set plugin_url "$registration_url"

# --- redeploy: stop here --------------------------------------------------
# An edit to src/App.jsx changes the bundle and nothing else. The pluginId is
# the same, the workbook spec is the same, and the workbook loads the new
# bundle the next time someone opens it -- so steps 6 and 7 have no work to do
# and a POST would only mint a duplicate workbook.
if [ "$mode" = "redeploy" ]; then
  remembered_wb_url="$(state_get workbook_url)"
  say "done (redeploy)"
  echo "  plugin:   $url" >&2
  echo "  pluginId: $pid" >&2
  if [ -n "$remembered_wb_url" ]; then
    # Nothing to publish on this path, and that is not a consolation prize:
    # the workbook spec is untouched, so no new draft exists, and the iframe
    # fetches the new bundle on its next load. A bundle-only change reaches
    # viewers without anyone opening Sigma at all.
    echo "  workbook: unchanged and already published -- it picks up the new" >&2
    echo "            bundle on next load, with nothing to publish" >&2
    printf '%s\n' "$remembered_wb_url"
  else
    echo "  No workbook recorded for this plugin in this org." >&2
    echo "  Run without --redeploy to generate and publish one." >&2
  fi
  exit 0
fi

# --- 6. workbook ----------------------------------------------------------
next_step "workbook"
target_wb="$adopt_wb"
[ -n "$target_wb" ] || target_wb="$(state_get workbook_id)"

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
    publish_out="$(bash scripts/api/publish-workbook.sh put "$target_wb" "$spec" 2>&1)" \
      || publish_failed "$publish_out"
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
  state_set workbook_id "$wb_id"
fi

# --- 7. verify ------------------------------------------------------------
# POST returning 200 does not mean the data binds. A bare column reference
# against a warehouse source publishes fine and then compiles to
# 'Unknown column "[X]"' in the SQL, with no error anywhere -- so check the
# compiled SQL, not just the status code.
next_step "verify"
cached_wb_url="$(state_get workbook_url)"
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
  [ -n "$wb_url" ] && state_set workbook_url "$wb_url"
fi

say "done"
echo "  plugin:   $url" >&2
echo "  pluginId: $pid" >&2

# A spec write lands in the workbook's DRAFT. Sigma has no API to publish it:
# PUT /v2/workbooks/{id}/spec takes `document` and an optional
# `documentVersion` and nothing else, and there is no /publish route on
# workbooks, files or documents (all 404, GET and POST). Publishing is a UI
# action only. Confirmed against Sigma's own API reference, 2026-09-12.
#
# So the one thing this script CAN do is refuse to imply the job is finished.
# Handing over a URL with no further comment is how a workbook gets shared
# while still showing its old published version to everyone who opens it.
if [ "$verb" != "none" ]; then
  echo "" >&2
  echo "  NOT PUBLISHED YET. The spec API writes the workbook's draft, and" >&2
  echo "  Sigma exposes no API to publish it. Open the URL below and click" >&2
  echo "  Publish, or whoever you send it to sees the previous version." >&2
fi
printf '%s\n' "$wb_url"
