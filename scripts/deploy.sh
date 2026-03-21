#!/usr/bin/env bash
# Deploy script — called by the GitHub webhook on push to main.
# Pulls latest code, restarts the bot and web UI via systemd.
# NOTE: The React SPA frontend is deployed separately to S3/CloudFront via
# GitHub Actions — do NOT rebuild it here.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

LOG="$PROJECT_DIR/deploy.log"
VENV_PY="$PROJECT_DIR/.venv/bin/python3"
HEALTH_URL="http://localhost:8080/api/health"
DISCORD_WEBHOOK_URL="${DISCORD_WEBHOOK_URL:-}"

_log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*" >> "$LOG"; }
_alert() {
    _log "ALERT: $1"
    if [[ -n "$DISCORD_WEBHOOK_URL" ]]; then
        curl -sf -H "Content-Type: application/json" \
            -d "{\"content\":\"⚠️ **Deploy alert:** $1\"}" \
            "$DISCORD_WEBHOOK_URL" >/dev/null 2>&1 || true
    fi
}

_log "--- Deploy started ---"

PREV_COMMIT=$(git rev-parse HEAD)

git pull --ff-only origin main >> "$LOG" 2>&1

"$VENV_PY" -m pip install -q -r requirements.txt >> "$LOG" 2>&1

# Wait for in-flight task runners to finish (up to 60s)
DRAIN_TIMEOUT=60
DRAIN_START=$(date +%s)
while ls /tmp/task-runner-*.pid 2>/dev/null | head -1 | grep -q .; do
    elapsed=$(( $(date +%s) - DRAIN_START ))
    if [[ $elapsed -ge $DRAIN_TIMEOUT ]]; then
        _log "Drain timeout (${DRAIN_TIMEOUT}s) — proceeding with restart"
        break
    fi
    _log "Waiting for in-flight runners to finish... (${elapsed}s/${DRAIN_TIMEOUT}s)"
    sleep 5
done

# Determine restart method: systemd if available, otherwise nohup fallback
USE_SYSTEMD=false
if systemctl is-enabled taskbot-web.service &>/dev/null 2>&1; then
    USE_SYSTEMD=true
fi

if $USE_SYSTEMD; then
    sudo systemctl restart taskbot-web.service
    _log "Web UI restarted via systemd"
    sudo systemctl restart taskbot-discord.service
    _log "Discord bot restarted via systemd"
    sudo systemctl restart taskbot-poller.service 2>/dev/null || true
    _log "Poller restarted via systemd"
else
    if pgrep -f "run_web.py" > /dev/null; then
        kill "$(pgrep -f 'run_web.py')" 2>/dev/null || true
        sleep 2
    fi
    nohup "$VENV_PY" run_web.py >> /tmp/web_ui.log 2>&1 &
    _log "Web UI restarted via nohup (PID $!)"

    if pgrep -f "python main.py" > /dev/null; then
        kill "$(pgrep -f 'python main.py')" 2>/dev/null || true
        sleep 2
    fi
    nohup "$VENV_PY" main.py >> /tmp/bot.log 2>&1 &
    _log "Discord bot restarted via nohup (PID $!)"
fi

# Health check — verify the new code starts correctly
sleep 3
if curl -sf "$HEALTH_URL" > /dev/null 2>&1; then
    _log "Health check passed"
else
    _log "Health check FAILED — rolling back"
    _alert "Deploy failed health check, rolling back to $PREV_COMMIT"

    git checkout "$PREV_COMMIT" >> "$LOG" 2>&1
    "$VENV_PY" -m pip install -q -r requirements.txt >> "$LOG" 2>&1

    if $USE_SYSTEMD; then
        sudo systemctl restart taskbot-web.service
        sudo systemctl restart taskbot-discord.service
        sudo systemctl restart taskbot-poller.service 2>/dev/null || true
    else
        kill "$(pgrep -f 'run_web.py')" 2>/dev/null || true
        sleep 1
        nohup "$VENV_PY" run_web.py >> /tmp/web_ui.log 2>&1 &
        kill "$(pgrep -f 'python main.py')" 2>/dev/null || true
        sleep 1
        nohup "$VENV_PY" main.py >> /tmp/bot.log 2>&1 &
    fi

    _log "Rolled back to $PREV_COMMIT"
    exit 1
fi

_log "--- Deploy completed ---"
