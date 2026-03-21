#!/usr/bin/env bash
# Install (or update) cron jobs for task-forge.
# Safe to re-run — existing entries are replaced, not duplicated.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV_PY="$PROJECT_DIR/.venv/bin/python3"
PATH_PREFIX="PATH=/home/ec2-user/.local/bin:/usr/local/bin:/usr/bin:/bin"

if [[ ! -f "$VENV_PY" ]]; then
    echo "Error: venv not found at $VENV_PY — run bash scripts/setup.sh first" >&2
    exit 1
fi

RUNNER_ENTRY="*/10 * * * * $PATH_PREFIX $VENV_PY $PROJECT_DIR/run_task.py >> $PROJECT_DIR/runner.log 2>&1"
HEALER_ENTRY="*/30 * * * * $PATH_PREFIX $VENV_PY $PROJECT_DIR/run_heal.py >> $PROJECT_DIR/healer.log 2>&1"
CLEANUP_ENTRY="0 * * * * $PATH_PREFIX bash $PROJECT_DIR/scripts/disk-cleanup.sh >> $PROJECT_DIR/cleanup.log 2>&1"
HEARTBEAT_ENTRY="*/15 * * * * $PATH_PREFIX $VENV_PY $PROJECT_DIR/run_heartbeat.py >> $PROJECT_DIR/heartbeat.log 2>&1"
PR_REVIEWER_ENTRY="*/30 * * * * $PATH_PREFIX $VENV_PY $PROJECT_DIR/run_pr_reviewer.py >> $PROJECT_DIR/pr_reviewer.log 2>&1"

# Read current crontab (empty string if none)
CURRENT=$(crontab -l 2>/dev/null || true)

# Strip any existing task-forge runner/healer lines, then append fresh ones
# grep returns exit 1 when no lines match, which would abort under set -e; use || true
NEW=$(echo "$CURRENT" \
    | grep -v "run_task\.py" \
    | grep -v "run_heal\.py" \
    | grep -v "disk-cleanup\.sh" \
    | grep -v "run_heartbeat\.py" \
    | grep -v "run_pr_reviewer\.py" \
    || true)

NEW="${NEW}
${RUNNER_ENTRY}
${HEALER_ENTRY}
${CLEANUP_ENTRY}
${HEARTBEAT_ENTRY}
${PR_REVIEWER_ENTRY}"

echo "$NEW" | crontab -

echo "Cron jobs installed:"
echo "  every 10 min  → run_task.py        (task runner)"
echo "  every 30 min  → run_heal.py        (self-healer)"
echo "  every hour    → disk-cleanup.sh    (worktree/cache pruner)"
echo "  every 15 min  → run_heartbeat.py   (heartbeat dispatcher)"
echo "  every 30 min  → run_pr_reviewer.py (PR review agent)"
echo ""
echo "Current crontab:"
crontab -l
