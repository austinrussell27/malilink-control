#!/usr/bin/env bash
# Installs Malinois Link Control itself on a Raspberry Pi (Raspberry Pi OS Lite, Debian trixie, arm64).
# Usage: sudo ./scripts/install-app.sh      (run from the project folder)
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
SRC="$(cd "$(dirname "$0")/.." && pwd)"
DEST=/opt/malilink-control
apt-get update
apt-get install -y python3 python3-venv python3-pip git
mkdir -p "$DEST" /var/lib/malilink-control
if [ "$SRC" != "$DEST" ]; then cp -a "$SRC"/. "$DEST"/; rm -rf "$DEST/.venv" "$DEST/var"; fi
python3 -m venv "$DEST/.venv"
"$DEST/.venv/bin/pip" install -q -r "$DEST/requirements.txt"
cp "$DEST/systemd/malilink-control.service" /etc/systemd/system/
[ -f /etc/default/malilink-control ] || cat > /etc/default/malilink-control <<'ENV'
# MALILINK_BIND=100.x.y.z     # your Tailscale IP to listen only on the tailnet
# MALILINK_PORT=8080
# MALILINK_PASSWORD=change-me # HTTP basic auth (user: admin)
# MALILINK_DEMO=1             # simulated dashboard data
# MALILINK_DRY_RUN=1          # never touch apt/systemctl/usr/local/etc
ENV
systemctl daemon-reload
systemctl enable --now malilink-control
echo "Malinois Link Control is running on port 8080 — open http://$(hostname):8080 over Tailscale."
