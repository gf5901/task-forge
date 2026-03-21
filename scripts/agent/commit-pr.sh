#!/usr/bin/env bash
# Stage all changes, commit, push, and open a PR from the current worktree.
# Mirrors what commit_and_create_pr() in src/runner.py does, so the agent
# can trigger this step manually if needed.
#
# Usage (from within the worktree):
#   bash /path/to/scripts/agent/commit-pr.sh "task(abc123): short description"
#   bash /path/to/scripts/agent/commit-pr.sh  # auto-derives message from branch name
#
# Environment:
#   GH_BIN      — path to gh CLI (default: gh)
#   BASE_BRANCH — PR base branch (default: auto-detected from remote HEAD)
#
# Exit codes:
#   0 — PR created (URL printed on last line)
#   2 — nothing to commit (clean worktree)
#   1 — push or PR creation failed
set -euo pipefail

GH_BIN="${GH_BIN:-gh}"
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

# ---------------------------------------------------------------------------
# Guard: must be in a worktree, not the main checkout
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [[ -f "$SCRIPT_DIR/validate-worktree.sh" ]]; then
    bash "$SCRIPT_DIR/validate-worktree.sh" || exit 1
fi

# ---------------------------------------------------------------------------
# Guard: gh must be available and authenticated
# ---------------------------------------------------------------------------
if ! command -v "$GH_BIN" &>/dev/null; then
    echo -e "${RED}ERROR: gh CLI not found (GH_BIN=$GH_BIN).${NC}" >&2
    echo "Install from https://cli.github.com or set GH_BIN to the correct path." >&2
    exit 1
fi
if ! "$GH_BIN" auth status &>/dev/null 2>&1; then
    echo -e "${RED}ERROR: gh CLI is not authenticated.${NC}" >&2
    echo "Run: gh auth login" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Check for changes
# ---------------------------------------------------------------------------
if [[ -z "$(git status --porcelain)" ]]; then
    echo -e "${YELLOW}Nothing to commit — worktree is clean.${NC}"
    exit 2
fi

echo -e "${CYAN}Changes to commit:${NC}"
git status --short

# ---------------------------------------------------------------------------
# Commit message
# ---------------------------------------------------------------------------
BRANCH="$(git rev-parse --abbrev-ref HEAD)"

if [[ -n "${1:-}" ]]; then
    COMMIT_MSG="$1"
else
    # Derive from branch name: task/<8-char-id>-<slug> → task(<id>): <slug>
    # Task IDs are 8-char hex (no hyphens), so the first segment before '-' is the ID.
    if [[ "$BRANCH" =~ ^task/([a-f0-9]{8})-(.+)$ ]]; then
        TASK_ID="${BASH_REMATCH[1]}"
        SLUG="${BASH_REMATCH[2]//-/ }"
        COMMIT_MSG="task($TASK_ID): $SLUG"
    else
        COMMIT_MSG="chore: agent changes on $BRANCH"
    fi
fi

# ---------------------------------------------------------------------------
# Commit — guard against empty commit (e.g. only .gitignored files changed)
# ---------------------------------------------------------------------------
git add -A
if [[ -z "$(git diff --cached --name-only)" ]]; then
    echo -e "${YELLOW}Nothing staged after git add -A — worktree may only have ignored changes.${NC}"
    exit 2
fi
git commit -m "$COMMIT_MSG"
echo -e "${GREEN}Committed:${NC} $COMMIT_MSG"

# ---------------------------------------------------------------------------
# Push
# ---------------------------------------------------------------------------
echo "Pushing $BRANCH to origin..."
git push -u origin "$BRANCH"
echo -e "${GREEN}Pushed.${NC}"

# ---------------------------------------------------------------------------
# Detect base branch
# ---------------------------------------------------------------------------
if [[ -n "${BASE_BRANCH:-}" ]]; then
    DEFAULT_BRANCH="$BASE_BRANCH"
else
    # git remote show can be slow; try symbolic-ref first
    DEFAULT_BRANCH="$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null \
        | sed 's|refs/remotes/origin/||')" \
        || DEFAULT_BRANCH="$(git remote show origin 2>/dev/null \
            | awk '/HEAD branch/ {print $NF}')" \
        || DEFAULT_BRANCH="main"
fi

# ---------------------------------------------------------------------------
# Generate PR body from diff stat (full stat, not truncated)
# ---------------------------------------------------------------------------
DIFF_STAT="$(git diff "origin/$DEFAULT_BRANCH"...HEAD --stat 2>/dev/null \
    || echo '(diff unavailable)')"

# Use printf to avoid issues with special characters in variables
PR_BODY="$(printf '## Summary\n\nAgent-authored changes for `%s`.\n\n## Changes\n\n```\n%s\n```\n\n## Testing\n\n- Run `bash scripts/agent/build-check.sh` in the worktree to verify checks pass.' \
    "$BRANCH" "$DIFF_STAT")"

# ---------------------------------------------------------------------------
# Create PR
# ---------------------------------------------------------------------------
echo "Creating PR against $DEFAULT_BRANCH..."
PR_URL="$("$GH_BIN" pr create \
    --title "$COMMIT_MSG" \
    --body "$PR_BODY" \
    --base "$DEFAULT_BRANCH" \
    --head "$BRANCH")"

echo ""
echo -e "${GREEN}PR created:${NC} $PR_URL"
