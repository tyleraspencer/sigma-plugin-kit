# Sourced, not run: turning plugins/<name>/src into plugins/<name>/dist.
#
#   . "$repo_root/scripts/_plugin-build.sh"
#   plugin_source_hash plugins/my-viz          # what can change the bundle
#   plugin_build_if_stale plugins/my-viz my-viz
#
# WHY this is shared rather than inlined in deploy-plugin.sh. The bind harness
# drives the BUILT bundle, because that is what Sigma loads -- so the harness
# needs a dist/ before it can say anything. It used to run at step 3 while the
# only `npm run build` in the pipeline lived at step 4, so on a first build the
# harness printed "build it first" and no-opped, and the pipeline sailed past
# it. The one gate that proves a plugin renders BOUND data never fired on the
# one run where the plugin was new.
#
# Fixing it by giving the harness its own build would put two build paths in
# the pipeline, and the moment they disagree the harness is testing a bundle
# nobody ships. So both callers go through here instead.

# shellcheck source=scripts/_deps-cache.sh
. "$(dirname "${BASH_SOURCE[0]}")/_deps-cache.sh"

# Hash of everything that can change the built bundle. dist/ and node_modules
# are excluded deliberately -- they are outputs, and node_modules is shared by
# hard link with other plugins.
plugin_source_hash() {
  "${SIGMA_PYTHON:-python3}" - "$1" <<'PY' 2>/dev/null
import hashlib, pathlib, sys
root = pathlib.Path(sys.argv[1])
h = hashlib.sha256()
paths = []
for p in sorted(root.rglob("*")):
    rel = p.relative_to(root)
    parts = rel.parts
    if parts and parts[0] in ("node_modules", "dist", ".git"):
        continue
    if p.is_file():
        paths.append((str(rel).replace("\\", "/"), p))
for rel, p in paths:
    h.update(rel.encode())
    h.update(b"\0")
    h.update(p.read_bytes())
    h.update(b"\0")
print(h.hexdigest()[:16])
PY
}

# plugin_version_assets <dist-dir>
#
# Rewrites dist/index.html's asset references from `./assets/index.js` to
# `./assets/index.js?v=<content hash>`. The FILENAME stays stable; only the
# reference carries the version.
#
# Both halves are load-bearing, and they fix opposite failures:
#
#   * Stable filename (vite.config.js) -- GitHub Pages serves index.html with
#     `Cache-Control: max-age=600`, and a deploy replaces dist/assets
#     wholesale. With a hashed name, a browser holding the previous index.html
#     asks for a bundle that has just been deleted: 404, nothing mounts, an
#     empty iframe and Sigma's loading bar spinning forever.
#   * Versioned reference (here) -- with a stable name AND a stable reference,
#     a browser that already has `assets/index.js` cached serves the OLD
#     bundle from a NEW index.html for up to ten minutes. That is not a blank
#     iframe; it is the previous build rendering as though the deploy never
#     happened, which is worse, because it reads as a bug in the code you just
#     wrote. It cost a debugging session on cohort-retention.
#
# Together the worst case becomes "index.html is up to ten minutes old, and
# every byte it names is exactly the build it came from" -- consistent, never
# a 404, and self-healing.
#
# Idempotent: an existing ?v= is stripped before the hash is recomputed, so
# running this twice over one dist changes nothing.
plugin_version_assets() {
  "${SIGMA_PYTHON:-python3}" - "$1" <<'VERSION_ASSETS_PY' 2>/dev/null
import hashlib, pathlib, re, sys

dist = pathlib.Path(sys.argv[1])
html = dist / "index.html"
if not html.is_file():
    sys.exit(0)
text = html.read_text()

PATTERN = re.compile(
    r"""(?P<attr>src|href)=(?P<q>["'])(?P<ref>\.?/?assets/[^"']+)(?P=q)""")


def stamp(m):
    bare = m.group("ref").split("?", 1)[0]
    target = dist / bare.lstrip("./")
    if not target.is_file():
        # Leave a reference we cannot resolve exactly as the build wrote it.
        # Inventing a version for a file that is not there would turn a
        # missing asset into a 404 with a query string on the end.
        return m.group(0)
    v = hashlib.sha256(target.read_bytes()).hexdigest()[:8]
    q = m.group("q")
    return "%s=%s%s?v=%s%s" % (m.group("attr"), q, bare, v, q)


out = PATTERN.sub(stamp, text)
if out != text:
    html.write_text(out)
VERSION_ASSETS_PY
}

# Where the last successful build's source hash is recorded. Kept in the cache
# rather than in the plugin directory on purpose: plugin_source_hash walks that
# directory, so a stamp living inside it would change the hash it is stamping.
_plugin_build_stamp() {
  printf '%s\n' "${SIGMA_PLUGIN_KIT_CACHE:-${XDG_CACHE_HOME:-$HOME/.cache}/sigma-plugin-kit}/builds/$2.hash"
}

# plugin_build_if_stale <src-dir> <name> [<source-hash>]
#
# Builds when there is no dist/, or when the sources have changed since the
# dist that is there. Both conditions matter, and for different reasons: the
# first is the missing-bundle case the harness used to trip over, and the
# second is worse -- a stale dist makes every downstream check pass against
# code nobody edited, which is a gate that is confidently wrong rather than
# merely absent.
#
# Pass the hash if the caller already computed one, so a single run doesn't
# walk the source tree twice.
plugin_build_if_stale() {
  local src="$1" name="$2" src_hash="${3:-}"
  local stamp; stamp="$(_plugin_build_stamp "$src" "$name")"
  [ -n "$src_hash" ] || src_hash="$(plugin_source_hash "$src")"

  if [ -z "${SIGMA_FORCE_BUILD:-}" ] && [ -f "$src/dist/index.html" ] \
     && [ -n "$src_hash" ] && [ -f "$stamp" ] \
     && [ "$(cat "$stamp" 2>/dev/null)" = "$src_hash" ]; then
    # Also on the skip path, so the invariant is "every dist this function
    # returns 0 for has versioned references" no matter which branch got
    # there. It is one hash of one file, and it is what brings a dist built
    # before plugin_version_assets existed up to date without a rebuild.
    plugin_version_assets "$src/dist"
    echo "  dist/ is current for $name -- skipped the build." >&2
    return 0
  fi

  local dep_key; dep_key="$(deps_cache_key "$src" || true)"
  if [ ! -d "$src/node_modules" ]; then
    if [ -n "$dep_key" ] && deps_cache_link "$dep_key" "$src/node_modules"; then
      echo "Linked dependencies for $name from the shared store." >&2
    else
      echo "Installing dependencies for $name ..." >&2
      ( cd "$src" && { [ -f package-lock.json ] && npm ci --silent || npm install --silent; } ) >&2
    fi
  fi
  # Populate the store from the first plugin that installs this dependency set,
  # so the next scaffold with the same deps links instead of installing.
  if [ -n "$dep_key" ] && [ -d "$src/node_modules" ]; then
    deps_cache_save "$dep_key" "$src/node_modules" || true
  fi

  # Check the exit status explicitly rather than leaning on `set -e`: callers
  # invoke this inside an `if`, which suppresses errexit for everything in the
  # function. Without this a failed build falls straight through to the dist/
  # check, finds the PREVIOUS build's index.html still sitting there, and
  # stamps it as current -- the stale-bundle case this whole file exists to
  # prevent, reintroduced by the guard that was supposed to prevent it.
  echo "Building $name ..." >&2
  if ! ( cd "$src" && npm run build --silent ) >&2; then
    echo "plugin-build: npm run build failed for $name." >&2
    return 1
  fi

  [ -f "$src/dist/index.html" ] || {
    echo "plugin-build: build produced no dist/index.html." >&2; return 1; }

  # Version the asset references before anything else reads this dist/. The
  # bind harness and the deploy consume exactly these bytes, and a dist that
  # gets versioned only on the deploy path is a dist the harness never tested.
  plugin_version_assets "$src/dist"

  # Stamped only after a build that actually produced a bundle, so a failed
  # build can never leave a stamp claiming the dist is current.
  mkdir -p "$(dirname "$stamp")" 2>/dev/null || true
  printf '%s\n' "$src_hash" > "$stamp" 2>/dev/null || true
}
