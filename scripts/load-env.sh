#!/usr/bin/env bash
# Print `export SIGMA_...=...` lines for the SIGMA_* keys in .env, for eval.
#
# Usage:
#   eval "$(scripts/load-env.sh)"          # repo-root .env
#   eval "$(scripts/load-env.sh path/to/.env)"
#
# scripts/api/get-token.sh calls this automatically when SIGMA_BASE_URL /
# SIGMA_CLIENT_ID / SIGMA_CLIENT_SECRET are not already exported and a .env
# exists at the repo root -- the CLI/local counterpart to Claude Code web,
# which injects those three directly into the environment.
#
# Prefer `eval "$(scripts/api/browser-login.sh)"` over a .env: it needs no
# admin-provisioned client credential and stores only a refresh token, in the
# OS keychain. A .env here is for an org that has issued you a real
# client_credentials pair. .env is gitignored; never commit one.
#
# Only SIGMA_*-prefixed keys are emitted, and every value is single-quoted,
# so a malformed or hostile .env cannot inject shell into the caller's eval.
set -euo pipefail

env_file="${1:-}"
if [ -z "$env_file" ]; then
  env_file="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.env"
fi

if [ ! -f "$env_file" ]; then
  echo "load-env.sh: no .env at $env_file" >&2
  exit 1
fi

# Read with `-r` (no backslash mangling) and tolerate a missing trailing
# newline on the last line (`|| [ -n "$line" ]`).
while IFS= read -r line || [ -n "$line" ]; do
  # Strip a UTF-8 BOM on the first line, CRs from a CRLF file, and
  # surrounding whitespace.
  line="${line#$'\xef\xbb\xbf'}"
  line="${line%$'\r'}"
  line="${line#"${line%%[![:space:]]*}"}"
  line="${line%"${line##*[![:space:]]}"}"

  case "$line" in
    ''|'#'*) continue ;;
  esac
  line="${line#export }"

  key="${line%%=*}"
  val="${line#*=}"
  case "$line" in *=*) ;; *) continue ;; esac
  case "$key" in SIGMA_*) ;; *) continue ;; esac

  # Drop one layer of surrounding quotes, as dotenv loaders do.
  case "$val" in
    \"*\") val="${val#\"}"; val="${val%\"}" ;;
    \'*\') val="${val#\'}"; val="${val%\'}" ;;
  esac

  # Single-quote the value, escaping any embedded single quote, so the
  # emitted line is inert under eval no matter what the value contains.
  printf "export %s='%s'\n" "$key" "$(printf '%s' "$val" | sed "s/'/'\\\\''/g")"
done < "$env_file"
