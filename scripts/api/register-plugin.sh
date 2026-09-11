#!/usr/bin/env bash
# Register and manage custom-viz plugins in a Sigma org via /v2/plugins.
#
# Usage:
#   scripts/api/register-plugin.sh create <name> <url> [description]
#   scripts/api/register-plugin.sh list [--name <substring>]
#   scripts/api/register-plugin.sh get <plugin-id>
#   scripts/api/register-plugin.sh rename <plugin-id> <new-name> [description]
#   scripts/api/register-plugin.sh delete <plugin-id>
#   scripts/api/register-plugin.sh id-for <name>        # exact name -> pluginId
#
# `create` prints ONLY the pluginId on stdout (diagnostics go to stderr), so it
# composes:  PID=$(scripts/api/register-plugin.sh create "My Viz" "$URL")
#
# Auth, the Accept header and 401 retry come from _env.sh's sigma_curl.
# POST/PATCH/DELETE need Admin, or an account type with "Manage plugins".
#
# THE URL IS IMMUTABLE. Sigma's PATCH endpoint cannot change `url` -- only
# `name`, `description` and `devUrl`. Changing where a plugin is hosted means
# delete + re-create, which mints a NEW pluginId and silently breaks every
# workbook already referencing the old one. So get the hosted URL right the
# first time: deploy first, verify it serves publicly, then register.
#
# `create` refuses a URL that isn't publicly fetchable as HTML, because that
# failure is otherwise invisible until a workbook renders a blank iframe.
# Override with SIGMA_SKIP_URL_CHECK=1 (e.g. registering a localhost devUrl).
set -euo pipefail
source "$(dirname "$0")/_env.sh"

if ! command -v jq >/dev/null 2>&1; then
  echo "register-plugin: jq is required (used to parse /v2/plugins responses)." >&2
  exit 1
fi

json_body() { # json_body key value [key value ...] -> compact JSON object
  "$SIGMA_PYTHON" -c '
import json, sys
args = sys.argv[1:]
print(json.dumps(dict(zip(args[::2], args[1::2]))))
' "$@"
}

check_url() { # check_url <url>; 0 if it serves HTML publicly
  local url="$1" out status ctype
  # -L: Pages 301s bare directory URLs. Separate connect/max timeouts so a
  # black-holed host fails in seconds rather than hanging the whole pipeline.
  out=$(curl -sSL --connect-timeout 5 --max-time 20 \
          -o /dev/null -w '%{http_code} %{content_type}' "$url" 2>/dev/null) || return 1
  status="${out%% *}"
  ctype="${out#* }"
  [ "$status" = "200" ] || { echo "  HTTP $status" >&2; return 1; }
  case "$ctype" in
    text/html*) return 0 ;;
    *) echo "  served as '$ctype', not text/html" >&2; return 1 ;;
  esac
}

cmd="${1:-}"
case "$cmd" in
  create)
    name="${2:?usage: register-plugin.sh create <name> <url> [description]}"
    url="${3:?usage: register-plugin.sh create <name> <url> [description]}"
    desc="${4:-$name}"

    if [ "${SIGMA_SKIP_URL_CHECK:-0}" != "1" ]; then
      echo "Checking $url is publicly fetchable as HTML..." >&2
      if ! check_url "$url"; then
        echo "" >&2
        echo "register-plugin: refusing to register an unreachable URL." >&2
        echo "  Sigma fetches this URL anonymously to render the plugin in an iframe." >&2
        echo "  Common causes:" >&2
        echo "    - the hosting repo is private (Pages output is not public)" >&2
        echo "    - Pages hasn't finished building yet -- retry in a minute" >&2
        echo "    - the URL points at jsDelivr, which serves .html as text/plain" >&2
        echo "  Because PATCH cannot change a plugin's url, registering a wrong one" >&2
        echo "  means delete + re-create and a new pluginId. Fix the URL first." >&2
        echo "  Override with SIGMA_SKIP_URL_CHECK=1 if this is deliberate." >&2
        exit 1
      fi
      echo "  ok -- 200 text/html" >&2
    fi

    body=$(json_body name "$name" description "$desc" url "$url")
    set +e
    response=$(sigma_curl -X POST -H "Content-Type: application/json" \
      --data-binary "$body" "$SIGMA_BASE_URL/v2/plugins")
    rc=$?
    set -e
    if [ "$rc" -ne 0 ]; then
      echo "register-plugin: create failed:" >&2
      echo "$response" >&2
      case "$response" in
        *orbidden*|*ermission*|*nauthorized*)
          echo "" >&2
          echo "  /v2/plugins write access needs Admin or the 'Manage plugins' permission." >&2
          echo "  An org admin may need to register this for you, or grant the permission." >&2 ;;
      esac
      exit "$rc"
    fi
    pid=$(printf '%s' "$response" | jq -r '.pluginId // empty')
    if [ -z "$pid" ]; then
      echo "register-plugin: no pluginId in response:" >&2
      echo "$response" >&2
      exit 1
    fi
    echo "Registered '$name' -> $pid" >&2
    printf '%s\n' "$pid"
    ;;

  list)
    filter=""
    if [ "${2:-}" = "--name" ]; then
      filter="${3:?usage: register-plugin.sh list --name <substring>}"
    fi
    # pageSize maxes at 1000; one page is plenty for an org's plugin list.
    response=$(sigma_curl "$SIGMA_BASE_URL/v2/plugins?pageSize=1000")
    printf '%s' "$response" | jq --arg f "$filter" '
      [ (.entries // [])[]
        | select($f == "" or (.name | ascii_downcase | contains($f | ascii_downcase))) ]
      | sort_by(.name)
      | map({pluginId, name, url})'
    ;;

  get)
    pid="${2:?usage: register-plugin.sh get <plugin-id>}"
    sigma_curl "$SIGMA_BASE_URL/v2/plugins/$pid"
    ;;

  id-for)
    want="${2:?usage: register-plugin.sh id-for <name>}"
    response=$(sigma_curl "$SIGMA_BASE_URL/v2/plugins?pageSize=1000")
    pid=$(printf '%s' "$response" \
      | jq -r --arg n "$want" 'first((.entries // [])[] | select(.name == $n) | .pluginId) // empty')
    if [ -z "$pid" ]; then
      echo "register-plugin: no plugin named exactly '$want'." >&2
      echo "  Registered names:" >&2
      printf '%s' "$response" | jq -r '(.entries // [])[] | "    " + .name' >&2
      exit 1
    fi
    printf '%s\n' "$pid"
    ;;

  rename)
    pid="${2:?usage: register-plugin.sh rename <plugin-id> <new-name> [description]}"
    newname="${3:?usage: register-plugin.sh rename <plugin-id> <new-name> [description]}"
    if [ -n "${4:-}" ]; then
      body=$(json_body name "$newname" description "$4")
    else
      body=$(json_body name "$newname")
    fi
    sigma_curl -X PATCH -H "Content-Type: application/json" \
      --data-binary "$body" "$SIGMA_BASE_URL/v2/plugins/$pid"
    ;;

  delete)
    pid="${2:?usage: register-plugin.sh delete <plugin-id>}"
    echo "register-plugin: DELETE is permanent. Workbook elements referencing" >&2
    echo "  $pid will survive but stop rendering, and re-creating the plugin" >&2
    echo "  produces a different pluginId that those workbooks will not pick up." >&2
    echo "  Set SIGMA_CONFIRM_DELETE=1 to proceed." >&2
    if [ "${SIGMA_CONFIRM_DELETE:-0}" != "1" ]; then
      exit 2
    fi
    sigma_curl -X DELETE "$SIGMA_BASE_URL/v2/plugins/$pid"
    ;;

  *)
    sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//' >&2
    exit 2 ;;
esac
