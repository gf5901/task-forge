#!/usr/bin/env bash
# First-time setup for task-forge.
# Safe to re-run — skips steps that are already done.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${CYAN}[setup]${NC} $*"; }
ok()      { echo -e "${GREEN}[ok]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[warn]${NC}  $*"; }
die()     { echo -e "${RED}[error]${NC} $*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# 1. Python venv
# ---------------------------------------------------------------------------
info "Checking Python venv..."
PYTHON=$(command -v python3.9 || command -v python3 || true)
[[ -z "$PYTHON" ]] && die "python3 not found"

if [[ ! -f ".venv/bin/python3" ]]; then
    info "Creating venv with $PYTHON"
    "$PYTHON" -m venv .venv
    ok "Venv created"
else
    ok "Venv already exists"
fi

VENV_PY="$PROJECT_DIR/.venv/bin/python3"
PIP="$PROJECT_DIR/.venv/bin/pip"

info "Installing Python dependencies..."
"$PIP" install -q -r requirements.txt
ok "Python deps installed"

# ---------------------------------------------------------------------------
# 2. Frontend
# ---------------------------------------------------------------------------
info "Checking pnpm / frontend..."
if ! command -v pnpm &>/dev/null; then
    warn "pnpm not found — installing via standalone installer..."
    curl -fsSL https://get.pnpm.io/install.sh | bash - >> /tmp/pnpm-install.log 2>&1
    export PNPM_HOME="$HOME/.local/share/pnpm"
    export PATH="$PNPM_HOME:$PATH"
fi

if ! command -v pnpm &>/dev/null; then
    warn "pnpm still not found — skipping frontend build. Install manually: https://pnpm.io/installation"
elif [[ ! -d "frontend/node_modules" ]]; then
    info "Installing frontend pnpm deps..."
    cd frontend && pnpm install --frozen-lockfile && cd "$PROJECT_DIR"
    ok "pnpm deps installed"
else
    ok "frontend/node_modules already exists"
fi

if command -v pnpm &>/dev/null && [[ ! -d "frontend/dist" ]]; then
    info "Building frontend..."
    cd frontend && pnpm run build && cd "$PROJECT_DIR"
    ok "Frontend built"
elif [[ -d "frontend/dist" ]]; then
    ok "frontend/dist already exists"
fi

# ---------------------------------------------------------------------------
# 3. .env file
# ---------------------------------------------------------------------------
info "Checking .env..."
if [[ ! -f ".env" ]]; then
    cp .env.example .env
    warn ".env created from .env.example — fill in the required values before starting:"
    warn "  DISCORD_BOT_TOKEN, NOTIFICATION_CHANNEL_ID, AUTH_EMAIL, AUTH_PASSWORD,"
    warn "  AUTH_SECRET_KEY, GITHUB_WEBHOOK_SECRET"
    warn "  Run: python3 -c \"import secrets; print(secrets.token_hex(32))\" to generate secrets."
else
    ok ".env already exists"
fi

# ---------------------------------------------------------------------------
# 4. Check external tools
# ---------------------------------------------------------------------------
info "Checking external tools..."
MISSING=()

if ! command -v agent &>/dev/null && [[ ! -f "$HOME/.local/bin/agent" ]]; then
    MISSING+=("Cursor agent CLI ('agent' binary not found in PATH or ~/.local/bin)")
fi

if ! command -v gh &>/dev/null; then
    MISSING+=("GitHub CLI (gh) — required for PR creation. Install: https://cli.github.com")
else
    if ! gh auth status &>/dev/null 2>&1; then
        warn "gh is installed but not authenticated — run: gh auth login"
    else
        ok "gh authenticated"
    fi
fi

if [[ ${#MISSING[@]} -gt 0 ]]; then
    warn "Missing tools (AUTO_PR / agent execution will fail until these are installed):"
    for m in "${MISSING[@]}"; do
        warn "  - $m"
    done
fi

# ---------------------------------------------------------------------------
# 5. System-level config (requires sudo)
# ---------------------------------------------------------------------------
info "Checking journald retention..."
if grep -q '^SystemMaxUse=' /etc/systemd/journald.conf 2>/dev/null; then
    ok "journald SystemMaxUse already set"
else
    if sudo -n true 2>/dev/null; then
        sudo sed -i 's/^#SystemMaxUse=$/SystemMaxUse=50M/' /etc/systemd/journald.conf
        sudo systemctl restart systemd-journald
        ok "journald capped at 50M"
    else
        warn "journald SystemMaxUse not set (needs sudo). Run manually:"
        warn "  sudo sed -i 's/^#SystemMaxUse=$/SystemMaxUse=50M/' /etc/systemd/journald.conf"
        warn "  sudo systemctl restart systemd-journald"
    fi
fi

# ---------------------------------------------------------------------------
# 6. Cron jobs (auxiliary — poller handles task dispatch)
# ---------------------------------------------------------------------------
info "Checking cron jobs..."
if crontab -l 2>/dev/null | grep -q "run_heal.py"; then
    ok "Healer cron already installed"
else
    warn "Healer cron not installed. Run: bash scripts/install-cron.sh"
fi

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo ""
echo -e "${GREEN}Setup complete.${NC}"
echo ""
echo "Next steps:"
echo "  1. Edit .env and fill in all required values"
echo "  2. sudo bash scripts/install-systemd.sh  # install systemd services"
echo "  3. sudo systemctl start taskbot-web taskbot-discord taskbot-poller"
echo "  4. bash scripts/install-cron.sh           # healer, disk cleanup (optional)"
echo "  5. bash scripts/check.sh                  # verify everything is ready"
