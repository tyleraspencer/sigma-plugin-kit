#!/usr/bin/env bash
# Publish a plugin to the public GitHub Pages host and wait until it serves.
#
# Usage:
#   bash scripts/deploy-plugin.sh <plugin-name>
#
# Prints ONLY the public URL on stdout (diagnostics go to stderr), so it
# composes:  URL=$(bash scripts/deploy-plugin.sh my-viz)
#
# Every plugin is a Vite + React project: plugins/<name>/package.json must
# exist, and this runs npm ci/install + npm run build and publishes the whole
# dist/ tree. A plugin without a package.json is refused -- the hand-written
# single-file archetype was removed on purpose, because a page that loads the
# SDK's UMD bundle from a CDN silently renders fallback data forever if React
# isn't loaded ahead of it. Bundling the SDK makes that unrepresentable.
#
# Why a separate repo: Sigma renders a plugin by fetching its URL anonymously
# into an iframe. This toolkit is private, and a private repo's Pages output is
# not publicly fetchable -- so built plugins go to a public host repo that
# contains nothing but plugin output.
#
# Override the target with:
#   SIGMA_PLUGIN_HOST_REPO   default tyleraspencer/sigma-plugins
#   SIGMA_PLUGIN_HOST_URL    default https://tyleraspencer.github.io/sigma-plugins
#   SIGMA_PLUGIN_HOST_CLONE  local clone path (default: a cache under ~/.cache)
#
# A DEPLOY THAT WOULD CHANGE NOTHING COSTS ONE HTTP REQUEST. Before building,
# this hashes the plugin's sources against the hash recorded by the last
# successful deploy; if they match and the live URL still serves exactly those
# bytes, it prints the URL and stops -- no npm, no clone, no push, no poll.
# That matters because `pipeline.sh --redeploy` is now the normal way to ship
# an edit, and re-running it after no edit at all used to cost a full build.
#
# Other env knobs:
#   SIGMA_PREFLIGHT_DONE=1   caller already ran the full preflight (pipeline.sh
#                            sets this). Skips the duplicate run below.
#   SIGMA_FORCE_DEPLOY=1     ignore the unchanged-source short circuit.
#   SIGMA_SKIP_DEP_CACHE=1   do not share node_modules between plugins.
#
# Deploy BEFORE registering. Sigma's PATCH cannot change a plugin's url, so a
# URL that turns out not to serve costs you a delete + re-create and a new
# pluginId. This script polls the live URL and fails unless it comes back
# 200 text/html with bytes matching what was just pushed.
set -euo pipefail
note() { [ -n "${SIGMA_VERBOSE:-}" ] && printf '  %s\n' "$1" >&2 || true; }

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST_REPO="${SIGMA_PLUGIN_HOST_REPO:-tyleraspencer/sigma-plugins}"
HOST_URL="${SIGMA_PLUGIN_HOST_URL:-https://tyleraspencer.github.io/sigma-plugins}"
CLONE_DIR="${SIGMA_PLUGIN_HOST_CLONE:-${XDG_CACHE_HOME:-$HOME/.cache}/sigma-plugin-kit/$(basename "$HOST_REPO")}"

name="${1:-}"
if [ -z "$name" ]; then
  echo "usage: deploy-plugin.sh <plugin-name>" >&2
  exit 2
fi

src="$repo_root/plugins/$name"
[ -d "$src" ] || { echo "deploy-plugin: plugins/$name not found." >&2
                   ls "$repo_root/plugins" 2>/dev/null | sed 's/^/    /' >&2; exit 1; }

command -v git >/dev/null 2>&1 || { echo "deploy-plugin: git is required." >&2; exit 1; }

url="$HOST_URL/plugins/$name/index.html"

# State shared with pipeline.sh: ids and hashes, never a credential.
state_dir="${SIGMA_PLUGIN_KIT_CACHE:-${XDG_CACHE_HOME:-$HOME/.cache}/sigma-plugin-kit}/deploys"
host_slug="$(printf '%s' "$HOST_REPO" | sed 's#[^a-zA-Z0-9]#-#g')"
stamp_file="$state_dir/${host_slug}__${name}.srchash"

# plugin_source_hash and plugin_build_if_stale. Shared with pipeline.sh, whose
# bind harness needs the same dist/ this ships -- see scripts/_plugin-build.sh.
# shellcheck source=scripts/_plugin-build.sh
. "$repo_root/scripts/_plugin-build.sh"

# assets_match <dist-dir> <base-url>; 0 when every asset index.html names is
# served AND matches the local build byte for byte.
#
# Both the short circuit and the post-push poll need this, and they must not
# disagree about what "deployed" means -- so it exists once. Matching
# index.html is NOT sufficient on its own. Asset FILENAMES are deliberately
# stable (see vite.config.js), so before the reference carried a ?v= the whole
# file was byte-identical across builds and would pass instantly while Pages
# still served the previous bundle. It now changes whenever the bundle does,
# but the check stays: a matching index.html still says nothing about whether
# the bytes it names are reachable.
#
# `ref` here is the reference as the BROWSER will request it, query and all --
# `assets/index.js?v=1a2b3c4d` (see plugin_version_assets in
# scripts/_plugin-build.sh). Fetch that exact string, so the URL Sigma's iframe
# asks for is the URL this proves serves; strip the query to find the file on
# disk to compare it against.
assets_match() {
  local dist="$1" base="$2" ref bare got acode clen missing=""
  for ref in $(grep -o "assets/[^\"']*" "$dist/index.html" | sort -u); do
    bare="${ref%%\?*}"
    # Cheap prefilter: a length mismatch is a definite miss, and skipping the
    # body saves re-downloading a multi-megabyte bundle on every attempt.
    clen=$(curl -sSLI --connect-timeout 5 --max-time 15 "$base/$ref" 2>/dev/null \
             | tr -d '\r' | awk 'tolower($1)=="content-length:"{v=$2} END{print v+0}')
    if [ "${clen:-0}" -gt 0 ] && [ -f "$dist/$bare" ]; then
      local local_len
      local_len=$(wc -c < "$dist/$bare" | tr -d ' ')
      if [ "$clen" != "$local_len" ]; then
        missing="$missing $ref(len $clen!=$local_len)"
        continue
      fi
    fi
    got="$(mktemp "${TMPDIR:-/tmp}/asset.XXXXXX")"
    acode=$(curl -sSL --connect-timeout 5 --max-time 30 -o "$got" \
              -w '%{http_code}' "$base/$ref" 2>/dev/null || echo 000)
    if [ "$acode" != "200" ]; then
      missing="$missing $ref($acode)"
    elif ! cmp -s "$got" "$dist/$bare"; then
      missing="$missing $ref(stale)"
    fi
    rm -f "$got"
  done
  ASSETS_MISSING="$missing"
  [ -z "$missing" ]
}

# --- Build -----------------------------------------------------------------
if [ ! -f "$src/package.json" ]; then
  echo "deploy-plugin: plugins/$name has no package.json." >&2
  echo "" >&2
  if [ -f "$src/index.html" ]; then
    echo "  It looks like a hand-written single-file plugin. That archetype was" >&2
    echo "  removed: every plugin is a Vite + React project now, and this script" >&2
    echo "  will not publish anything else." >&2
    echo "" >&2
    echo "  Already-deployed single-file plugins keep serving from their existing" >&2
    echo "  URLs -- nothing was taken down -- but they cannot be re-deployed until" >&2
    echo "  they are ported. To port one: scaffold a replacement, move the drawing" >&2
    echo "  code into src/App.jsx, and bind via the SDK hooks instead of" >&2
    echo "  window.SigmaPlugin." >&2
  else
    echo "  Scaffold one with: bash scripts/new-plugin.sh $name" >&2
  fi
  echo "" >&2
  echo "  See docs/plugins.md." >&2
  exit 1
fi

archetype="react"
command -v npm >/dev/null 2>&1 || {
  echo "deploy-plugin: npm is not on PATH, and every plugin needs a build." >&2; exit 1; }

if ! grep -q '@sigmacomputing/plugin' "$src/package.json"; then
  echo "deploy-plugin: plugins/$name/package.json does not depend on" >&2
  echo "  @sigmacomputing/plugin -- it cannot talk to Sigma." >&2
  exit 1
fi

# Static gates before the build, so a failure costs a second rather than a
# full npm install. preflight-plugin.py owns the rules; this imports it rather
# than reimplementing, because two copies of a check are how they silently
# stop agreeing. pipeline.sh runs the full preflight, but deploy-plugin.sh is
# callable on its own -- which is exactly how a broken plugin shipped once.
preflight_py="$repo_root/scripts/preflight-plugin.py"
if [ -n "${SIGMA_PREFLIGHT_DONE:-}" ]; then
  # pipeline.sh already ran the full preflight, with --data, a few seconds ago.
  # Running it again would re-check the same bytes and print the same report
  # into the caller's output a second time.
  note "preflight: already run by the caller"
elif [ -f "$preflight_py" ] && command -v "${SIGMA_PYTHON:-python3}" >/dev/null 2>&1; then
  if ! "${SIGMA_PYTHON:-python3}" "$preflight_py" "$name" >&2; then
    echo "deploy-plugin: refusing to publish plugins/$name -- preflight failed." >&2
    exit 1
  fi
fi

# --- Nothing changed? Then nothing to do. ---------------------------------
# Checked after the static gates (so a broken plugin still gets caught) and
# before npm, the clone and the push (which are the expensive parts).
# pipeline.sh already hashed this tree at step 3 to decide whether to build.
# Recomputing it here would walk every source file a second time for the same
# answer; the handoff is scoped to that one call, like SIGMA_PREFLIGHT_DONE.
src_hash="${SIGMA_PLUGIN_SRC_HASH:-$(plugin_source_hash "$src")}"
if [ -z "${SIGMA_FORCE_DEPLOY:-}" ] && [ -n "$src_hash" ] \
   && [ -f "$stamp_file" ] && [ "$(cat "$stamp_file" 2>/dev/null)" = "$src_hash" ] \
   && [ -f "$src/dist/index.html" ]; then
  served="$(mktemp "${TMPDIR:-/tmp}/served.XXXXXX")"
  if curl -sSL --connect-timeout 5 --max-time 20 -o "$served" "$url" 2>/dev/null \
     && cmp -s "$served" "$src/dist/index.html" \
     && assets_match "$src/dist" "$HOST_URL/plugins/$name"; then
    rm -f "$served"
    echo "Unchanged since the last deploy, and $url still serves it." >&2
    note "Skipped build, clone and push. Force with SIGMA_FORCE_DEPLOY=1."
    printf '%s\n' "$url"
    exit 0
  fi
  rm -f "$served"
fi

# Usually a no-op by the time we get here: pipeline.sh builds at step 3 so the
# bind harness has a bundle to drive, and the stamp means this call sees the
# work is already done. Still called unconditionally, because deploy-plugin.sh
# is independently runnable and that is exactly how a plugin once shipped
# without its build.
plugin_build_if_stale "$src" "$name" "$src_hash" || exit 1

publish_dir="$src/dist"

# The failure this catches is invisible: with Vite's default `base: '/'` the
# built page references /assets/index-xxx.js, which 404s under the Pages
# subpath. The HTML loads, the bundle doesn't, and Sigma shows a blank
# iframe with nothing in any log.
if grep -qE '(src|href)="/(assets|static)/' "$publish_dir/index.html"; then
  echo "deploy-plugin: dist/index.html references assets at an ABSOLUTE path" >&2
  echo "  (/assets/... or /static/...), which will 404 under" >&2
  echo "  $HOST_URL/plugins/$name/ and render a blank iframe." >&2
  echo "  Set \`base: './'\` in vite.config.js and rebuild." >&2
  exit 1
fi

if grep -rq '__PLUGIN_TITLE__' "$publish_dir" 2>/dev/null; then
  echo "deploy-plugin: still contains the __PLUGIN_TITLE__ placeholder." >&2
  exit 1
fi

# --- Get a clone of the host repo -----------------------------------------
if [ -d "$CLONE_DIR/.git" ]; then
  note "Updating host clone at $CLONE_DIR ..."
  git -C "$CLONE_DIR" fetch -q origin main
  # A deploy cache, not a working tree anyone edits, so a hard reset is safe
  # and avoids a merge conflict blocking a deploy.
  git -C "$CLONE_DIR" checkout -q main 2>/dev/null || git -C "$CLONE_DIR" checkout -q -b main origin/main
  git -C "$CLONE_DIR" reset -q --hard origin/main
else
  echo "Cloning $HOST_REPO -> $CLONE_DIR ..." >&2
  mkdir -p "$(dirname "$CLONE_DIR")"
  rm -rf "$CLONE_DIR"
  if command -v gh >/dev/null 2>&1; then
    gh repo clone "$HOST_REPO" "$CLONE_DIR" -- -q
  else
    git clone -q "https://github.com/$HOST_REPO.git" "$CLONE_DIR"
  fi
fi

# The cache clone needs its own commit identity. Inherit this repo's rather
# than depending on a global git config that may not exist. Override with
# SIGMA_GIT_NAME / SIGMA_GIT_EMAIL.
deploy_name="${SIGMA_GIT_NAME:-$(git -C "$repo_root" config user.name 2>/dev/null || true)}"
deploy_email="${SIGMA_GIT_EMAIL:-$(git -C "$repo_root" config user.email 2>/dev/null || true)}"
if [ -z "$deploy_name" ] || [ -z "$deploy_email" ]; then
  echo "deploy-plugin: no git identity available for the deploy commit." >&2
  echo "  Set one on this repo, or export SIGMA_GIT_NAME and SIGMA_GIT_EMAIL." >&2
  exit 1
fi
git -C "$CLONE_DIR" config user.name "$deploy_name"
git -C "$CLONE_DIR" config user.email "$deploy_email"

# --- Refuse to publish into a private repo --------------------------------
# This is the failure the two-repo split exists to prevent, so check rather
# than trust the default.
if command -v gh >/dev/null 2>&1; then
  vis=$(gh repo view "$HOST_REPO" --json visibility -q .visibility 2>/dev/null || echo "")
  if [ "$vis" = "PRIVATE" ]; then
    echo "deploy-plugin: $HOST_REPO is PRIVATE. Sigma must fetch the plugin URL" >&2
    echo "  anonymously; a private repo's Pages output will not serve." >&2
    exit 1
  fi
fi

# --- Copy and commit ------------------------------------------------------
dest="$CLONE_DIR/plugins/$name"
rm -rf "$dest"
mkdir -p "$dest"
# Recursive: a React build is a tree (index.html + assets/), not a flat file.
( cd "$publish_dir" && tar cf - . ) | ( cd "$dest" && tar xf - )

git -C "$CLONE_DIR" add -A "plugins/$name"
pushed=0
if git -C "$CLONE_DIR" diff --cached --quiet; then
  echo "No change to plugins/$name -- already deployed." >&2
else
  sha="$(git -C "$repo_root" rev-parse --short HEAD 2>/dev/null || echo unknown)"
  git -C "$CLONE_DIR" -c commit.gpgsign=false commit -q \
    -m "Deploy plugin $name ($archetype, sigma-plugin-kit $sha)"
  note "Pushing to $HOST_REPO ..."
  git -C "$CLONE_DIR" push -q origin main
  pushed=1
fi

# --- Let Pages finish building before byte-checking it --------------------
# Without this the loop below spends its first several attempts downloading a
# bundle that provably cannot be there yet, because Pages has not built the
# commit we just pushed. Asking Pages directly turns those wasted round trips
# into one cheap status poll. Best-effort: needs gh, and a repo whose Pages
# build history is readable. Never fatal -- the byte check below is the real
# gate, this only stops us knocking early.
if [ "$pushed" -eq 1 ] && command -v gh >/dev/null 2>&1; then
  echo "Waiting for the Pages build ..." >&2
  pages_wait=0
  while [ "$pages_wait" -lt 20 ]; do
    pstatus=$(gh api "repos/$HOST_REPO/pages/builds/latest" --jq .status 2>/dev/null || echo "")
    case "$pstatus" in
      built) echo "  Pages build: built" >&2; break ;;
      errored)
        echo "  Pages build reported 'errored' -- checking what is served anyway." >&2
        break ;;
      "")  # no gh auth, no Pages API, or a repo we cannot read: stop asking
        break ;;
      *) pages_wait=$((pages_wait + 1)); sleep 3 ;;
    esac
  done
fi

# --- Wait for Pages to serve exactly what we pushed -----------------------
# A fresh Pages build routinely takes 30-60s and a brand-new path 404s until
# it lands. And a 200 only proves *something* is served -- Pages can still be
# on a previous build, which matters because the plugin URL is immutable once
# registered. So compare bytes, not just the status.
echo "Waiting for $url ..." >&2
attempt=0
max_attempts=20
while [ "$attempt" -lt "$max_attempts" ]; do
  attempt=$((attempt + 1))
  out=$(curl -sSL --connect-timeout 5 --max-time 20 \
          -o /dev/null -w '%{http_code} %{content_type}' "$url" 2>/dev/null || echo "000 none")
  status="${out%% *}"
  ctype="${out#* }"
  if [ "$status" = "200" ]; then
    case "$ctype" in
      text/html*)
        served="$(mktemp "${TMPDIR:-/tmp}/served.XXXXXX")"
        curl -sSL --connect-timeout 5 --max-time 20 -o "$served" "$url" 2>/dev/null || true
        if cmp -s "$served" "$publish_dir/index.html"; then
          rm -f "$served"
          # Matching index.html is NOT enough. It names its bundle, and if that
          # file does not serve, the iframe mounts nothing: an empty box in the
          # plugin's own background colour, Sigma's loading bar spinning
          # forever, and no error anywhere. Seen for real when a browser held a
          # cached index.html naming a hashed bundle that the next deploy had
          # already deleted -- hence stable asset names in vite.config.js, and
          # hence assets_match, which the unchanged-source short circuit above
          # uses too so the two can never disagree about "deployed".
          if ! assets_match "$publish_dir" "$HOST_URL/plugins/$name"; then
            if [ "$attempt" -lt "$max_attempts" ]; then
              echo "  index.html serves, its asset(s) do not yet:$ASSETS_MISSING (attempt ${attempt})" >&2
              sleep 6
              continue
            fi
            echo "deploy-plugin: index.html serves but these assets do not:$ASSETS_MISSING" >&2
            exit 1
          fi
          echo "  serving after ${attempt} check(s): 200 $ctype, index + assets verified" >&2
          # Record what was deployed, so an immediate re-run costs one request.
          if [ -n "$src_hash" ]; then
            mkdir -p "$state_dir" 2>/dev/null || true
            printf '%s\n' "$src_hash" > "$stamp_file" 2>/dev/null || true
          fi
          printf '%s\n' "$url"
          exit 0
        fi
        rm -f "$served"
        if [ "$attempt" -lt "$max_attempts" ]; then
          echo "  200 but content differs -- Pages still on an older build (attempt ${attempt})" >&2
          sleep 6
          continue
        fi
        echo "deploy-plugin: $url serves content that does not match the build." >&2
        exit 1 ;;
      *)
        echo "deploy-plugin: served as '$ctype', not text/html -- Sigma would render" >&2
        echo "  the source as plain text. Is the URL going through jsDelivr?" >&2
        exit 1 ;;
    esac
  fi
  sleep 6
done

echo "deploy-plugin: $url did not serve after $((max_attempts * 6))s (last: HTTP $status)." >&2
echo "  The push succeeded -- this is a Pages build delay or a Pages config problem." >&2
echo "  Check: gh api repos/$HOST_REPO/pages" >&2
exit 1
