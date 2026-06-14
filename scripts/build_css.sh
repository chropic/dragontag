#!/usr/bin/env bash
# Build the vendored Tailwind stylesheet served at /static/app.css.
#
# The UI is fully self-hosted (no CDN), so the stylesheet is compiled ahead of
# time from the templates and committed to the repo. Re-run this whenever you
# add/remove Tailwind utility classes in dragontag/app/web/templates/.
#
# Downloads the Tailwind standalone CLI (no Node required) into build_tmp/ if it
# isn't already there.
set -euo pipefail
cd "$(dirname "$0")/.."

TW_VERSION="v3.4.4"
TW_BIN="build_tmp/tailwindcss"
OUT="dragontag/app/web/static/app.css"

if [[ ! -x "$TW_BIN" ]]; then
  mkdir -p build_tmp
  echo "Downloading Tailwind CLI ${TW_VERSION}..."
  curl -sSL -o "$TW_BIN" \
    "https://github.com/tailwindlabs/tailwindcss/releases/download/${TW_VERSION}/tailwindcss-linux-x64"
  chmod +x "$TW_BIN"
fi

"$TW_BIN" -c tailwind.config.js -i app.input.css -o "$OUT" --minify
echo "Wrote $OUT"
