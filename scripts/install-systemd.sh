#!/usr/bin/env bash
# Install systemd unit files for taskbot-web, taskbot-discord, and taskbot-poller.
# Must be run as root or with sudo.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Installing systemd unit files..."
cp "$SCRIPT_DIR/taskbot-web.service" /etc/systemd/system/
cp "$SCRIPT_DIR/taskbot-discord.service" /etc/systemd/system/
cp "$SCRIPT_DIR/taskbot-poller.service" /etc/systemd/system/

systemctl daemon-reload

systemctl enable taskbot-web.service
systemctl enable taskbot-discord.service
systemctl enable taskbot-poller.service

echo "Systemd units installed and enabled."
echo ""
echo "To start the services:"
echo "     sudo systemctl start taskbot-web"
echo "     sudo systemctl start taskbot-discord"
echo "     sudo systemctl start taskbot-poller"
echo "  Check status:"
echo "     systemctl status taskbot-web taskbot-discord taskbot-poller"
