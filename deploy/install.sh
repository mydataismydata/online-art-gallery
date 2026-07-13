#!/usr/bin/env bash
# One-shot installer for The Gallery on Debian/Ubuntu. Idempotent — safe to
# re-run to repair or upgrade an install. Run with sudo.
#
#   sudo bash deploy/install.sh
#
# Or straight from GitHub on a fresh box (no clone needed first):
#   curl -fsSL https://raw.githubusercontent.com/mydataismydata/online-art-gallery/main/deploy/install.sh | sudo bash
#
# Override any path/user via env: APP_DIR, DATA_DIR, SVC_USER, REPO_URL.
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/gallery}"
DATA_DIR="${DATA_DIR:-/var/lib/gallery}"
SVC_USER="${SVC_USER:-gallery}"
REPO_URL="${REPO_URL:-https://github.com/mydataismydata/online-art-gallery.git}"

[ "$(id -u)" -eq 0 ] || { echo "Please run with sudo: sudo bash deploy/install.sh" >&2; exit 1; }

# Run a command as the service user, with a writable HOME so git/pip don't warn.
run_as() { sudo -u "$SVC_USER" env HOME="$DATA_DIR" "$@"; }

echo "==> System packages"
apt-get update -qq
apt-get install -y -qq python3-venv python3-pip git curl

echo "==> Service user + directories"
id -u "$SVC_USER" >/dev/null 2>&1 || \
  useradd --system --home-dir "$DATA_DIR" --shell /usr/sbin/nologin "$SVC_USER"
mkdir -p "$APP_DIR" "$DATA_DIR"
chown "$SVC_USER:$SVC_USER" "$APP_DIR" "$DATA_DIR"

echo "==> Code ($APP_DIR)"
if [ -d "$APP_DIR/.git" ]; then
  run_as git -C "$APP_DIR" pull --ff-only
else
  run_as git clone "$REPO_URL" "$APP_DIR"
fi

echo "==> Python virtualenv + dependencies"
[ -x "$APP_DIR/.venv/bin/python" ] || run_as python3 -m venv "$APP_DIR/.venv"
run_as "$APP_DIR/.venv/bin/pip" install -q --upgrade pip
run_as "$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

echo "==> dezoomify-rs (Google Arts & Culture helper)"
APP_DIR="$APP_DIR" SVC_USER="$SVC_USER" bash "$APP_DIR/deploy/fetch-dezoomify.sh"

echo "==> systemd service"
cp "$APP_DIR/deploy/gallery.service" /etc/systemd/system/gallery.service
systemctl daemon-reload
systemctl enable --now gallery

echo
echo "==> Done."
systemctl --no-pager --lines=0 status gallery || true
echo
echo "Local check:"
curl -s -o /dev/null -w "  http://127.0.0.1:8000/ -> HTTP %{http_code}\n" http://127.0.0.1:8000/ || true
echo
echo "Next — expose it on your tailnet (see DEPLOY.md Part 3):"
echo "  sudo tailscale serve --bg 8000   &&   tailscale serve status"
