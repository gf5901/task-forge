#!/usr/bin/env bash
# Run all available build checks in the current repository.
# Designed to be called by the agent after making changes to verify nothing is broken.
#
# Usage (from within the worktree):
#   bash /path/to/scripts/agent/build-check.sh
#
# Checks run (only if applicable to this repo):
#   - Python: pytest (if tests/ exists)
#   - Python: ruff / flake8 lint (if available)
#   - Frontend: npm run typecheck (tsc -b, if frontend/ exists)
#   - Frontend: eslint (if configured)
#   - Frontend: npm run build (if package.json has a build script)
#
# Exit codes:
#   0 — all checks passed (warnings are reported but do not fail)
#   1 — one or more checks failed (details printed above)
set -uo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'

FAILED=()
PASSED=()
SKIPPED=()

section() { echo -e "\n${CYAN}── $* ──────────────────────────────────────────${NC}"; }
ok()      { echo -e "${GREEN}✓${NC} $*"; PASSED+=("$*"); }
fail()    { echo -e "${RED}✗${NC} $*"; FAILED+=("$*"); }
skip()    { echo -e "${YELLOW}–${NC} $* (skipped — not applicable)"; SKIPPED+=("$*"); }

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$REPO_ROOT"

# ---------------------------------------------------------------------------
# Python checks
# ---------------------------------------------------------------------------
section "Python"

# Prefer the repo's own venv; fall back to whatever python3 is in PATH
VENV_PY=""
if [[ -f "$REPO_ROOT/.venv/bin/python3" ]]; then
    VENV_PY="$REPO_ROOT/.venv/bin/python3"
elif command -v python3 &>/dev/null; then
    VENV_PY="$(command -v python3)"
fi

if [[ -z "$VENV_PY" ]]; then
    skip "Python (no interpreter found)"
else
    # pytest
    if [[ -d "tests" ]]; then
        echo "Running pytest..."
        if "$VENV_PY" -m pytest tests/ -n auto -q --tb=short 2>&1; then
            ok "pytest"
        else
            fail "pytest (see output above)"
        fi
    else
        skip "pytest (no tests/ directory)"
    fi

    # ruff (preferred) or flake8
    if "$VENV_PY" -m ruff --version &>/dev/null 2>&1; then
        echo "Running ruff..."
        # Capture output so we can show it; ruff exits 1 on any violation
        RUFF_OUT=$("$VENV_PY" -m ruff check . 2>&1) && RUFF_RC=0 || RUFF_RC=$?
        if [[ -n "$RUFF_OUT" ]]; then
            echo "$RUFF_OUT"
        fi
        if [[ $RUFF_RC -eq 0 ]]; then
            ok "ruff lint"
        else
            fail "ruff lint (see output above)"
        fi
    elif "$VENV_PY" -m flake8 --version &>/dev/null 2>&1; then
        echo "Running flake8..."
        if "$VENV_PY" -m flake8 src/ --max-line-length=120 --ignore=E501,W503 2>&1; then
            ok "flake8 lint"
        else
            fail "flake8 lint (see output above)"
        fi
    else
        skip "Python lint (ruff/flake8 not installed)"
    fi
fi

# ---------------------------------------------------------------------------
# Frontend checks
# ---------------------------------------------------------------------------
section "Frontend"

if [[ ! -d "frontend" ]]; then
    skip "Frontend (no frontend/ directory)"
else
    cd frontend

    if [[ ! -d "node_modules" ]]; then
        # Hard-link copy from main checkout: fast (~4s) and isolated —
        # new packages installed here won't affect the main checkout.
        SCRIPT_NM="$(cd "$(dirname "$0")/../.." && pwd)/frontend/node_modules"
        if [[ -d "$SCRIPT_NM" ]]; then
            cp -al "$SCRIPT_NM" node_modules
            echo "Hard-linked node_modules from main checkout"
        else
            echo "node_modules missing — running pnpm install..."
            pnpm install --frozen-lockfile 2>&1 || true
        fi
    fi

    # TypeScript project build (tsc -b) — matches CI and pre-commit
    if [[ -f "tsconfig.json" ]] && grep -q '"typecheck"' package.json 2>/dev/null; then
        echo "Running npm run typecheck..."
        if npm run typecheck 2>&1; then
            ok "TypeScript (npm run typecheck)"
        else
            fail "TypeScript type errors (see output above)"
        fi
    elif [[ -f "tsconfig.json" ]] && command -v npx &>/dev/null; then
        echo "Running npx tsc -b..."
        if npx tsc -b --pretty false 2>&1; then
            ok "TypeScript (tsc -b)"
        else
            fail "TypeScript type errors (see output above)"
        fi
    else
        skip "tsc (no tsconfig.json or npm/npx not found)"
    fi

    # ESLint — check for config file using glob expansion or known filenames
    HAS_ESLINT_CONFIG=false
    for cfg in .eslintrc .eslintrc.js .eslintrc.cjs .eslintrc.json .eslintrc.yaml \
               .eslintrc.yml eslint.config.js eslint.config.mjs eslint.config.cjs; do
        [[ -f "$cfg" ]] && HAS_ESLINT_CONFIG=true && break
    done
    if $HAS_ESLINT_CONFIG || grep -q '"lint"' package.json 2>/dev/null; then
        echo "Running eslint..."
        # npm run lint exits 1 on errors, 0 on warnings-only
        LINT_OUT=$(npm run lint --silent 2>&1) && LINT_RC=0 || LINT_RC=$?
        if [[ -n "$LINT_OUT" ]]; then
            echo "$LINT_OUT"
        fi
        if [[ $LINT_RC -eq 0 ]]; then
            # Warn if there were warning lines in output even though exit was 0
            if echo "$LINT_OUT" | grep -qi "warning"; then
                ok "ESLint (passed with warnings — review output above)"
            else
                ok "ESLint"
            fi
        else
            fail "ESLint (see output above)"
        fi
    else
        skip "ESLint (no config found)"
    fi

    # Build (Vite / tsc)
    if grep -q '"build"' package.json 2>/dev/null; then
        echo "Running npm run build..."
        if npm run build 2>&1; then
            ok "npm run build"
        else
            fail "npm run build (see output above)"
        fi
    else
        skip "npm build (no build script in package.json)"
    fi

    cd "$REPO_ROOT"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "────────────────────────────────────────────────────"
echo -e "  ${GREEN}Passed${NC}:  ${#PASSED[@]}"
echo -e "  ${YELLOW}Skipped${NC}: ${#SKIPPED[@]}"
echo -e "  ${RED}Failed${NC}:  ${#FAILED[@]}"

if [[ ${#FAILED[@]} -gt 0 ]]; then
    echo ""
    echo -e "${RED}Failed checks:${NC}"
    for f in "${FAILED[@]}"; do
        echo "  - $f"
    done
    exit 1
fi

echo ""
echo -e "${GREEN}All checks passed.${NC}"
exit 0
