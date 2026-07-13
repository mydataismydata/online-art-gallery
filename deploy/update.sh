#!/usr/bin/env bash
# Update an existing Gallery install: pull latest code, refresh Python deps,
# ensure the dezoomify-rs helper is present, and restart the service.
# Run with sudo.
#
#   sudo bash /opt/gallery/deploy/update.sh
#
# Set FORCE_DEZOOMIFY=1 to re-download dezoomify-rs even if already present.
# Override paths/user via env: APP_DIR, DATA_DIR, SVC_USER.
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/gallery}"
DATA_DIR="${DATA_DIR:-/var/lib/gallery}"
SVC_USER="${SVC_USER:-gallery}"

[ "$(id -u)" -eq 0 ] || { echo "Please run with sudo: sudo bash $APP_DIR/deploy/update.sh" >&2; exit 1; }
run_as() { sudo -u "$SVC_USER" env HOME="$DATA_DIR" "$@"; }

echo "==> Pulling latest code"
run_as git -C "$APP_DIR" pull --ff-only

echo "==> Updating Python dependencies"
run_as "$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

echo "==> Ensuring dezoomify-rs is present"
if [ -x "$APP_DIR/dezoomify-rs" ] && [ -z "${FORCE_DEZOOMIFY:-}" ]; then
  echo "    already installed (set FORCE_DEZOOMIFY=1 to refresh)"
else
  APP_DIR="$APP_DIR" SVC_USER="$SVC_USER" bash "$APP_DIR/deploy/fetch-dezoomify.sh"
fi

echo "==> Restarting service"
systemctl restart gallery
sleep 1
systemctl --no-pager --lines=0 status gallery || true
echo "==> Done."
