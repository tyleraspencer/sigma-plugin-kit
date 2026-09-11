#!/usr/bin/env bash
# Publish a plugin to the public GitHub Pages host and wait until it serves.
#
# Usage:
#   bash scripts/deploy-plugin.sh <plugin-name>
#
# Prints ONLY the public URL on stdout (diagnostics go to stderr), so it
# composes:  URL=$(bash scripts/deploy-plugin.sh my-viz)
#
# Why a separate repo: Sigma renders a plugin by fetching its URL anonymously
# into an iframe. This toolkit is private, and a private repo's Pages output is
# not publicly fetchable -- so built plugins are pushed to a public host repo
# that contains nothing but plugin HTML.
#
# Override the target with:
#   SIGMA_PLUGIN_HOST_REPO   default tyleraspencer/sigma-plugins
#   SIGMA_PLUGIN_HOST_URL    default https://tyleraspencer.github.io/sigma-plugins
#   SIGMA_PLUGIN_HOST_CLONE  local clone path (default: a cache under ~/.cache)
#
# Deploy BEFORE registering. Sigma's PATCH cannot change a plugin's url, so a
# URL that turns out not to serve costs you a delete + re-create and a new
# pluginId. This script therefore polls the live URL and fails if it does not
# come back 200 text/html.
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
if [ ! -f "$src/index.html" ]; then
  echo "deploy-plugin: plugins/$name/index.html not found." >&2
  echo "  Available:" >&2
  ls "$repo_root/plugins" 2>/dev/null | sed 's/^/    /' >&2
  exit 1
fi

if grep -q '__PLUGIN_TITLE__' "$src/index.html"; then
  echo "deploy-plugin: plugins/$name/index.html still contains the" >&2
  echo "  __PLUGIN_TITLE__ placeholder -- scaffold it with new-plugin.sh or" >&2
  echo "  fill the title in before deploying." >&2
  exit 1
fi

command -v git >/dev/null 2>&1 || { echo "deploy-plugin: git is required." >&2; exit 1; }

# --- Get a clone of the host repo -----------------------------------------
if [ -d "$CLONE_DIR/.git" ]; then
  echo "Updating host clone at $CLONE_DIR ..." >&2
  git -C "$CLONE_DIR" fetch -q origin main
  # The clone is a deploy cache, not a working tree anyone edits, so a hard
  # reset is safe here and avoids a merge conflict blocking a deploy.
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

# The cache clone needs a commit identity of its own. Inherit this repo's,
# rather than depending on a global git config that may not exist -- on a host
# with no global user.email, the commit below fails with "Author identity
# unknown" after the push target is already set up. Override with
# SIGMA_GIT_NAME / SIGMA_GIT_EMAIL.
deploy_name="${SIGMA_GIT_NAME:-$(git -C "$repo_root" config user.name 2>/dev/null || true)}"
deploy_email="${SIGMA_GIT_EMAIL:-$(git -C "$repo_root" config user.email 2>/dev/null || true)}"
if [ -z "$deploy_name" ] || [ -z "$deploy_email" ]; then
  echo "deploy-plugin: no git identity available for the deploy commit." >&2
  echo "  Set one on this repo (git config --local user.email ...), or export" >&2
  echo "  SIGMA_GIT_NAME and SIGMA_GIT_EMAIL." >&2
  exit 1
fi
git -C "$CLONE_DIR" config user.name "$deploy_name"
git -C "$CLONE_DIR" config user.email "$deploy_email"

# --- Refuse to publish into a private repo --------------------------------
# This is the failure this whole two-repo split exists to prevent, so check it
# rather than trusting the default.
if command -v gh >/dev/null 2>&1; then
  vis=$(gh repo view "$HOST_REPO" --json visibility -q .visibility 2>/dev/null || echo "")
  if [ "$vis" = "PRIVATE" ]; then
    echo "deploy-plugin: $HOST_REPO is PRIVATE." >&2
    echo "  Sigma must fetch the plugin URL anonymously; a private repo's Pages" >&2
    echo "  output will not serve. Make it public, or point" >&2
    echo "  SIGMA_PLUGIN_HOST_REPO at a public repo." >&2
    exit 1
  fi
fi

# --- Copy and commit ------------------------------------------------------
dest="$CLONE_DIR/plugins/$name"
mkdir -p "$dest"
cp "$src/index.html" "$dest/index.html"
# Copy any sibling assets the plugin brought with it, without recursing into
# anything unexpected.
for extra in "$src"/*.css "$src"/*.js "$src"/*.svg "$src"/*.png; do
  [ -f "$extra" ] && cp "$extra" "$dest/"
done

git -C "$CLONE_DIR" add -A "plugins/$name"
if git -C "$CLONE_DIR" diff --cached --quiet; then
  echo "No change to plugins/$name -- already deployed." >&2
else
  sha="$(git -C "$repo_root" rev-parse --short HEAD 2>/dev/null || echo unknown)"
  git -C "$CLONE_DIR" -c commit.gpgsign=false commit -q \
    -m "Deploy plugin $name (sigma-plugin-kit $sha)"
  echo "Pushing to $HOST_REPO ..." >&2
  git -C "$CLONE_DIR" push -q origin main
fi

url="$HOST_URL/plugins/$name/index.html"

# --- Wait for Pages to serve it -------------------------------------------
# A fresh Pages build routinely takes 30-60s, and a brand-new path 404s until
# it lands. Poll rather than declaring success on a push.
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
        echo "  serving after ${attempt} check(s): 200 $ctype" >&2
        printf '%s\n' "$url"
        exit 0 ;;
      *)
        echo "deploy-plugin: served as '$ctype', not text/html -- Sigma will render" >&2
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
