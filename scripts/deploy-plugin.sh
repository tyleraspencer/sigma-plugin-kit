#!/usr/bin/env bash
# Publish a plugin to the public GitHub Pages host and wait until it serves.
#
# Usage:
#   bash scripts/deploy-plugin.sh <plugin-name>
#
# Prints ONLY the public URL on stdout (diagnostics go to stderr), so it
# composes:  URL=$(bash scripts/deploy-plugin.sh my-viz)
#
# Handles both archetypes:
#   single file  plugins/<name>/index.html is published as-is.
#   react        plugins/<name>/package.json present -> npm ci/install and
#                npm run build, then publish the whole dist/ tree.
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
# Deploy BEFORE registering. Sigma's PATCH cannot change a plugin's url, so a
# URL that turns out not to serve costs you a delete + re-create and a new
# pluginId. This script polls the live URL and fails unless it comes back
# 200 text/html with bytes matching what was just pushed.
set -euo pipefail

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

# --- Build, if this is a React-archetype plugin ---------------------------
if [ -f "$src/package.json" ]; then
  archetype="react"
  command -v npm >/dev/null 2>&1 || {
    echo "deploy-plugin: plugins/$name has a package.json but npm is not on PATH." >&2; exit 1; }

  if ! grep -q '@sigmacomputing/plugin' "$src/package.json"; then
    echo "deploy-plugin: plugins/$name/package.json does not depend on" >&2
    echo "  @sigmacomputing/plugin -- it cannot talk to Sigma." >&2
    exit 1
  fi

  if [ ! -d "$src/node_modules" ]; then
    echo "Installing dependencies for $name ..." >&2
    ( cd "$src" && { [ -f package-lock.json ] && npm ci --silent || npm install --silent; } ) >&2
  fi
  echo "Building $name ..." >&2
  ( cd "$src" && npm run build --silent ) >&2

  publish_dir="$src/dist"
  [ -f "$publish_dir/index.html" ] || {
    echo "deploy-plugin: build produced no dist/index.html." >&2; exit 1; }

  # The failure this catches is invisible: with Vite's default `base: '/'` the
  # built page references /assets/index-xxx.js, which 404s under the Pages
  # subpath. The HTML loads, the bundle doesn't, and Sigma shows a blank
  # iframe with nothing in any log.
  if grep -qE '(src|href)="/(assets|static)/' "$publish_dir/index.html"; then
    echo "deploy-plugin: dist/index.html references assets at an ABSOLUTE path" >&2
    echo "  (/assets/... or /static/...), which will 404 under" >&2
    echo "  $HOST_URL/plugins/$name/ and render a blank iframe." >&2
    echo "  Set \`base: './'\` in vite.config.js (or \`homepage\` for CRA) and rebuild." >&2
    exit 1
  fi
else
  archetype="single"
  publish_dir="$src"
  [ -f "$src/index.html" ] || {
    echo "deploy-plugin: plugins/$name/index.html not found, and no package.json" >&2
    echo "  either -- so this is neither archetype." >&2; exit 1; }

  # Gates for the no-build path, run here because the kit does not track
  # deployed plugins: deploy is the last moment the content is private and the
  # only moment a check can stop a broken plugin getting a public URL.
  gate_fail=0
  if ! grep -q 'SigmaPlugin' "$src/index.html"; then
    echo "deploy-plugin: does not reference window.SigmaPlugin -- the only global" >&2
    echo "  the UMD bundle defines. client would be null and the plugin would" >&2
    echo "  silently render its fallback forever. See docs/plugin-api.md." >&2
    gate_fail=1
  fi
  if ! grep -q 'unpkg.com/@sigmacomputing/plugin' "$src/index.html"; then
    echo "deploy-plugin: does not load the Sigma plugin SDK from unpkg." >&2
    gate_fail=1
  fi
  [ "$gate_fail" -eq 0 ] || { echo "deploy-plugin: refusing to publish plugins/$name." >&2; exit 1; }
fi

if grep -rq '__PLUGIN_TITLE__' "$publish_dir" 2>/dev/null; then
  echo "deploy-plugin: still contains the __PLUGIN_TITLE__ placeholder." >&2
  exit 1
fi

# --- Get a clone of the host repo -----------------------------------------
if [ -d "$CLONE_DIR/.git" ]; then
  echo "Updating host clone at $CLONE_DIR ..." >&2
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
if git -C "$CLONE_DIR" diff --cached --quiet; then
  echo "No change to plugins/$name -- already deployed." >&2
else
  sha="$(git -C "$repo_root" rev-parse --short HEAD 2>/dev/null || echo unknown)"
  git -C "$CLONE_DIR" -c commit.gpgsign=false commit -q \
    -m "Deploy plugin $name ($archetype, sigma-plugin-kit $sha)"
  echo "Pushing to $HOST_REPO ..." >&2
  git -C "$CLONE_DIR" push -q origin main
fi

url="$HOST_URL/plugins/$name/index.html"

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
          echo "  serving after ${attempt} check(s): 200 $ctype, bytes match" >&2
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
