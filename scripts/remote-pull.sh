#!/usr/bin/env bash
# Pull the latest main branch on the EC2 instance via SSH.
# Usage: scripts/remote-pull.sh [--restart]
#   --restart  Also restart systemd services after pulling
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Load EC2_SSH_HOST from .env if present
if [[ -f "$PROJECT_DIR/.env" ]]; then
    EC2_SSH_HOST=$(grep -E '^EC2_SSH_HOST=' "$PROJECT_DIR/.env" | cut -d= -f2- | tr -d '"' || true)
fi
EC2_SSH_HOST="${EC2_SSH_HOST:?EC2_SSH_HOST not set — add it to .env (e.g. EC2_SSH_HOST=ec2-user@your-host)}"

REMOTE_HOST="$EC2_SSH_HOST"
# Optional: EC2_REPO_DIR in .env (default Amazon Linux path below)
if [[ -f "$PROJECT_DIR/.env" ]]; then
    EC2_REPO_DIR=$(grep -E '^EC2_REPO_DIR=' "$PROJECT_DIR/.env" | cut -d= -f2- | tr -d '"' || true)
fi
REMOTE_DIR="${EC2_REPO_DIR:-/home/ec2-user/workspace/task-forge}"

RESTART=false
for arg in "$@"; do
    case "$arg" in
        --restart) RESTART=true ;;
        *) echo "Unknown argument: $arg"; exit 1 ;;
    esac
done

echo "Pulling latest main on $REMOTE_HOST..."
ssh "$REMOTE_HOST" "cd $REMOTE_DIR && git pull --ff-only origin main"

if $RESTART; then
    echo "Restarting services..."
    ssh "$REMOTE_HOST" "sudo systemctl restart taskbot-web taskbot-discord taskbot-poller"
    echo "Services restarted."
fi

echo "Done."
