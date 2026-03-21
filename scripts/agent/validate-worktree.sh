#!/usr/bin/env bash
# Validate that the current working directory is an isolated task worktree,
# NOT the main project checkout.
#
# The agent should run this before making any file changes to confirm it is
# operating in the correct isolated environment.
#
# Usage (from within the worktree):
#   bash /path/to/scripts/agent/validate-worktree.sh
#
# Exit codes:
#   0 — working directory is a valid task worktree
#   1 — working directory is the main checkout or not a git repo (abort!)
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

CWD="$(pwd)"
WORKTREE_BASE="${WORKTREE_BASE:-/tmp/task-worktrees}"

# Must be inside a git repo
if ! git rev-parse --is-inside-work-tree &>/dev/null; then
    echo -e "${RED}ERROR: Not inside a git repository.${NC}" >&2
    echo "  cwd: $CWD" >&2
    exit 1
fi

REPO_ROOT="$(git rev-parse --show-toplevel)"

# Must be inside the worktree base, not the main checkout
if [[ "$REPO_ROOT" != "$WORKTREE_BASE"/* ]]; then
    echo -e "${RED}ERROR: Working directory is NOT a task worktree.${NC}" >&2
    echo "  cwd:        $CWD" >&2
    echo "  repo root:  $REPO_ROOT" >&2
    echo "  expected:   $WORKTREE_BASE/task-*" >&2
    echo "" >&2
    echo "Do NOT make changes here — this is the main checkout." >&2
    exit 1
fi

# Branch must be a task branch
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [[ "$BRANCH" != task/* ]]; then
    echo -e "${YELLOW}WARN: Branch '$BRANCH' does not look like a task branch (task/<id>-<slug>).${NC}"
fi

echo -e "${GREEN}OK: Running in worktree${NC}"
echo "  path:   $REPO_ROOT"
echo "  branch: $BRANCH"
