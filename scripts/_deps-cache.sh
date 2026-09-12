# Sourced, not run: installed node_modules trees, shared between plugins by
# hard link so the second plugin with the same dependencies costs ~1.5s and
# ~0MB instead of ~7s and 40MB.
#
#   . "$repo_root/scripts/_deps-cache.sh"
#   key="$(deps_cache_key plugins/my-viz)"
#   deps_cache_link "$key" plugins/my-viz/node_modules   # scaffold: reuse
#   deps_cache_save "$key" plugins/my-viz/node_modules   # after npm install
#
# Keyed by the dependency SET, not by package.json, whose `name` differs per
# plugin. Add a dependency and you get a different key and a normal install.
#
# Sharing inodes is safe because npm replaces package directories rather than
# editing files in place, so installing in one plugin cannot corrupt another's.
#
# Not a package manager, and not load-bearing: every function fails soft, and
# the caller falls back to `npm install`, which is always correct. Deleting
# $SIGMA_PLUGIN_KIT_CACHE/deps is safe; SIGMA_SKIP_DEP_CACHE=1 turns it off.

deps_cache_root() {
  printf '%s\n' "${SIGMA_PLUGIN_KIT_CACHE:-${XDG_CACHE_HOME:-$HOME/.cache}/sigma-plugin-kit}/deps"
}

# deps_cache_key <plugin-dir> -> a hash of its dependency set, or empty
deps_cache_key() {
  [ -z "${SIGMA_SKIP_DEP_CACHE:-}" ] || return 1
  [ -f "$1/package.json" ] || return 1
  "${SIGMA_PYTHON:-python3}" - "$1/package.json" <<'PY' 2>/dev/null
import hashlib, json, sys
try:
    pkg = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    sys.exit(1)
# Only the dependency set. `name` and `version` are per-plugin and must not
# shard the store; anything else in package.json cannot change what npm
# installs.
deps = {k: pkg.get(k) or {} for k in ("dependencies", "devDependencies")}
blob = json.dumps(deps, sort_keys=True, separators=(",", ":"))
print(hashlib.sha256(blob.encode()).hexdigest()[:16])
PY
}

# _deps_hardlink_tree <src-dir> <dest-dir>; 0 if the tree was linked
# `cp -al` is the GNU spelling and recent macOS has it too, but neither is
# guaranteed -- `pax -rwl` is the POSIX one and is what Git Bash ships. Try
# both, and let the caller fall back to npm if neither works.
_deps_hardlink_tree() {
  local src="$1" dest="$2"
  [ -d "$src" ] || return 1
  rm -rf "$dest" 2>/dev/null || true
  if cp -al "$src" "$dest" 2>/dev/null; then
    return 0
  fi
  rm -rf "$dest" 2>/dev/null || true
  if mkdir -p "$dest" 2>/dev/null && ( cd "$src" && pax -rwl . "$dest" ) 2>/dev/null; then
    return 0
  fi
  rm -rf "$dest" 2>/dev/null || true
  return 1
}

# deps_cache_link <key> <dest-node_modules>; 0 if it was populated from cache
deps_cache_link() {
  [ -z "${SIGMA_SKIP_DEP_CACHE:-}" ] || return 1
  [ -n "${1:-}" ] || return 1
  local store
  store="$(deps_cache_root)/$1"
  # .complete is written last by deps_cache_save, so a store entry left behind
  # by an interrupted save is never linked into a plugin.
  [ -f "$store/.complete" ] || return 1
  _deps_hardlink_tree "$store/node_modules" "$2" || return 1
  return 0
}

# deps_cache_save <key> <src-node_modules>; 0 if the store now holds this set
deps_cache_save() {
  [ -z "${SIGMA_SKIP_DEP_CACHE:-}" ] || return 1
  [ -n "${1:-}" ] || return 1
  local store
  store="$(deps_cache_root)/$1"
  [ -f "$store/.complete" ] && return 0   # already stored
  [ -d "$2" ] || return 1
  mkdir -p "$store" 2>/dev/null || return 1
  _deps_hardlink_tree "$2" "$store/node_modules" || { rm -rf "$store"; return 1; }
  : > "$store/.complete" 2>/dev/null || { rm -rf "$store"; return 1; }
  return 0
}
