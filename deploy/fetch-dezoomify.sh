#!/usr/bin/env bash
# Download the Linux dezoomify-rs binary and place it next to the app.
# Called by install.sh and update.sh; also runnable on its own.
#
#   sudo bash deploy/fetch-dezoomify.sh            # -> /opt/gallery/dezoomify-rs
#   sudo bash deploy/fetch-dezoomify.sh /path/to/dezoomify-rs
#
# Note: upstream ships an x86_64 Linux build only. On arm64 (e.g. a Raspberry
# Pi) you'd need to build dezoomify-rs from source instead.
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/gallery}"
SVC_USER="${SVC_USER:-gallery}"
DEST="${1:-$APP_DIR/dezoomify-rs}"
URL="https://github.com/lovasoa/dezoomify-rs/releases/latest/download/dezoomify-rs-linux.tgz"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

echo "    fetching $URL"
curl -fsSL -o "$tmp/dz.tgz" "$URL"
tar -xzf "$tmp/dz.tgz" -C "$tmp"

# The archive contains a single `dezoomify-rs` binary; find it robustly.
src="$(find "$tmp" -type f -name dezoomify-rs -print -quit)"
[ -n "$src" ] || src="$(find "$tmp" -type f ! -name '*.tgz' -print -quit)"
[ -n "$src" ] || { echo "    ERROR: no binary found inside the archive" >&2; exit 1; }

install -m 0755 "$src" "$DEST"
if id -u "$SVC_USER" >/dev/null 2>&1; then
  chown "$SVC_USER:$SVC_USER" "$DEST" 2>/dev/null || true
fi

if "$DEST" --version >/dev/null 2>&1; then
  echo "    installed $("$DEST" --version 2>&1 | head -1) -> $DEST"
else
  echo "    installed -> $DEST"
fi
