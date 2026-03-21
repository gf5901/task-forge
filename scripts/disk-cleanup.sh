#!/usr/bin/env bash
# Disk cleanup — prune stale worktrees, caches, old logs, and dead artifacts.
# Intended to run hourly via cron.
set -uo pipefail

# Override with TASK_FORGE_REPO (or legacy DISCORD_TASK_BOT_REPO) if your clone lives elsewhere.
REPO_DIR="${TASK_FORGE_REPO:-${DISCORD_TASK_BOT_REPO:-/home/ec2-user/workspace/task-forge}}"
WORKTREE_DIR="/tmp/task-worktrees"
MAX_AGE_HOURS=2

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Disk cleanup starting"

# --- Worktrees ---
if [[ -d "$WORKTREE_DIR" ]]; then
    for wt in "$WORKTREE_DIR"/task-*; do
        [[ -d "$wt" ]] || continue
        task_id=$(basename "$wt" | sed 's/^task-//')
        pidfile="/tmp/task-runner-${task_id}.pid"
        if [[ -f "$pidfile" ]]; then
            pid=$(cat "$pidfile" 2>/dev/null)
            if kill -0 "$pid" 2>/dev/null; then
                echo "  Skipping $wt — runner PID $pid still alive"
                continue
            fi
        fi
        age_min=$(( ($(date +%s) - $(stat -c %Y "$wt")) / 60 ))
        if [[ $age_min -gt $((MAX_AGE_HOURS * 60)) ]]; then
            echo "  Removing stale worktree: $wt (${age_min}m old)"
            rm -rf "$wt"
            branch_name=$(basename "$wt" | sed 's/^task-/task\//')
            git -C "$REPO_DIR" worktree prune 2>/dev/null || true
            git -C "$REPO_DIR" branch -D "$branch_name" 2>/dev/null || true
        fi
    done
fi

# --- Remote task branches merged or orphaned (>7 days old) ---
echo "  Pruning merged remote task branches..."
git -C "$REPO_DIR" fetch --prune 2>/dev/null || true
for ref in $(git -C "$REPO_DIR" branch -r --list 'origin/task/*' 2>/dev/null); do
    last_commit_epoch=$(git -C "$REPO_DIR" log -1 --format='%ct' "$ref" 2>/dev/null) || continue
    age_days=$(( ($(date +%s) - last_commit_epoch) / 86400 ))
    if [[ $age_days -gt 7 ]]; then
        branch_name="${ref#origin/}"
        echo "    Deleting remote $branch_name (${age_days}d since last commit)"
        git -C "$REPO_DIR" push origin --delete "$branch_name" 2>/dev/null || true
    fi
done

# --- Cursor agent versions (keep only the latest) ---
AGENT_VERSIONS_DIR="$HOME/.local/share/cursor-agent/versions"
if [[ -d "$AGENT_VERSIONS_DIR" ]]; then
    latest=$(ls -1t "$AGENT_VERSIONS_DIR" 2>/dev/null | head -1)
    for ver in "$AGENT_VERSIONS_DIR"/*/; do
        ver_name=$(basename "$ver")
        if [[ "$ver_name" != "$latest" ]]; then
            echo "  Removing old cursor-agent version: $ver_name"
            rm -rf "$ver"
        fi
    done
fi

# --- Cursor chat history (remove dirs older than 14 days) ---
CHATS_DIR="$HOME/.cursor/chats"
if [[ -d "$CHATS_DIR" ]]; then
    find "$CHATS_DIR" -mindepth 1 -maxdepth 1 -type d -mtime +14 -exec rm -rf {} + 2>/dev/null || true
fi

# --- Cron log rotation (truncate at 1MB, keep one .old backup) ---
for logfile in "$REPO_DIR"/healer.log "$REPO_DIR"/heartbeat.log "$REPO_DIR"/pr_reviewer.log "$REPO_DIR"/cleanup.log "$REPO_DIR"/pipeline.log; do
    if [[ -f "$logfile" ]]; then
        size=$(stat -c %s "$logfile" 2>/dev/null || echo 0)
        if [[ $size -gt 1048576 ]]; then
            echo "  Rotating $logfile ($(( size / 1024 ))KB)"
            cp "$logfile" "${logfile}.old"
            : > "$logfile"
        fi
    fi
done

# --- Pnpm store ---
if command -v pnpm &>/dev/null; then
    echo "  Pruning pnpm store..."
    pnpm store prune 2>/dev/null || true
fi

# --- Pip cache (clear if over 100MB) ---
pip_cache="$HOME/.cache/pip"
if [[ -d "$pip_cache" ]]; then
    size=$(du -sm "$pip_cache" 2>/dev/null | cut -f1)
    if [[ ${size:-0} -gt 100 ]]; then
        echo "  Clearing pip cache (${size}MB)"
        rm -rf "$pip_cache"
    fi
fi

# --- Compressed log archives older than 14 days ---
find "$REPO_DIR"/ -maxdepth 1 -name '*.log.*.gz' -mtime +14 -delete 2>/dev/null || true
find "$REPO_DIR"/ -maxdepth 1 -name '*.log.old' -mtime +14 -delete 2>/dev/null || true

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Disk cleanup done"
echo "  Disk usage: $(df -h / | tail -1 | awk '{print $3 "/" $2 " (" $5 " used)"}')"
