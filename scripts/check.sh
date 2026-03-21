#!/usr/bin/env bash
# Health check for task-forge.
# Prints a summary of what's running, what's configured, and what's missing.
# Exit code 0 if everything looks healthy, 1 if any critical item is missing.
set -uo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; DIM='\033[2m'; NC='\033[0m'
ok()   { echo -e "  ${GREEN}✓${NC} $*"; }
warn() { echo -e "  ${YELLOW}!${NC} $*"; }
fail() { echo -e "  ${RED}✗${NC} $*"; ERRORS=$((ERRORS+1)); }
info() { echo -e "${CYAN}$*${NC}"; }

ERRORS=0

# ---------------------------------------------------------------------------
info "── Services ──────────────────────────────────────────"
# ---------------------------------------------------------------------------
if systemctl is-active taskbot-web.service &>/dev/null; then
    PID=$(systemctl show -p MainPID --value taskbot-web.service)
    ok "Web UI running via systemd (PID $PID)"
elif pgrep -f "run_web\.py" &>/dev/null; then
    PID=$(pgrep -f "run_web\.py" | head -1)
    warn "Web UI running via nohup (PID $PID) — migrate to systemd: sudo bash scripts/install-systemd.sh"
else
    fail "Web UI NOT running  →  sudo systemctl start taskbot-web"
fi

if systemctl is-active taskbot-discord.service &>/dev/null; then
    PID=$(systemctl show -p MainPID --value taskbot-discord.service)
    ok "Discord bot running via systemd (PID $PID)"
elif pgrep -f "python.*main\.py" &>/dev/null; then
    PID=$(pgrep -f "python.*main\.py" | head -1)
    warn "Discord bot running via nohup (PID $PID) — migrate to systemd: sudo bash scripts/install-systemd.sh"
else
    fail "Discord bot NOT running  →  sudo systemctl start taskbot-discord"
fi

# ---------------------------------------------------------------------------
info "── Cron ────────────────────────────────────────────"
# ---------------------------------------------------------------------------
if crontab -l 2>/dev/null | grep -q "run_task\.py"; then
    ok "Task runner cron installed (supplementary — poller is primary)"
else
    ok "Task runner cron not installed (poller handles dispatch)"
fi

if crontab -l 2>/dev/null | grep -q "run_heal\.py"; then
    ok "Healer cron installed"
else
    warn "Healer cron missing (optional but recommended)  →  bash scripts/install-cron.sh"
fi

if crontab -l 2>/dev/null | grep -q "disk-cleanup\.sh"; then
    ok "Disk cleanup cron installed"
else
    warn "Disk cleanup cron missing  →  bash scripts/install-cron.sh"
fi

# ---------------------------------------------------------------------------
info "── Environment (.env) ──────────────────────────────"
# ---------------------------------------------------------------------------
if [[ ! -f ".env" ]]; then
    fail ".env file missing  →  cp .env.example .env"
else
    ok ".env exists"
    # Source without executing side effects
    set +u
    # shellcheck disable=SC2046
    export $(grep -v '^#' .env | grep '=' | xargs) 2>/dev/null || true
    set -u

    [[ -n "${DISCORD_BOT_TOKEN:-}" ]]       && ok "DISCORD_BOT_TOKEN set"        || fail "DISCORD_BOT_TOKEN missing"
    [[ -n "${NOTIFICATION_CHANNEL_ID:-}" ]] && ok "NOTIFICATION_CHANNEL_ID set"  || warn "NOTIFICATION_CHANNEL_ID not set (bot won't post notifications)"
    [[ -n "${AUTH_SECRET_KEY:-}" ]]         && ok "AUTH_SECRET_KEY set"          || warn "AUTH_SECRET_KEY not set — sessions won't survive restarts"
    [[ -n "${AUTH_EMAIL:-}" ]]              && ok "AUTH_EMAIL set"               || warn "AUTH_EMAIL not set — web UI is open (no login required)"
    [[ -n "${GITHUB_WEBHOOK_SECRET:-}" ]]   && ok "GITHUB_WEBHOOK_SECRET set"    || warn "GITHUB_WEBHOOK_SECRET not set — auto-deploy webhook won't verify"
fi

# ---------------------------------------------------------------------------
info "── Tools ───────────────────────────────────────────"
# ---------------------------------------------------------------------------
VENV_PY="$PROJECT_DIR/.venv/bin/python3"
if [[ -f "$VENV_PY" ]]; then
    VER=$("$VENV_PY" --version 2>&1)
    ok "Python venv: $VER"
else
    fail "Python venv missing  →  bash scripts/setup.sh"
fi

AGENT_BIN="${AGENT_BIN:-agent}"
if command -v "$AGENT_BIN" &>/dev/null; then
    ok "Cursor agent CLI found: $(command -v "$AGENT_BIN")"
else
    fail "Cursor agent CLI ('$AGENT_BIN') not found — tasks cannot run"
fi

GH_BIN="${GH_BIN:-gh}"
if command -v "$GH_BIN" &>/dev/null; then
    if "$GH_BIN" auth status &>/dev/null 2>&1; then
        GHUSER=$("$GH_BIN" api user --jq '.login' 2>/dev/null || echo "unknown")
        ok "gh CLI authenticated as @$GHUSER"
    else
        warn "gh CLI installed but not authenticated  →  gh auth login"
    fi
else
    fail "gh CLI not found — PR creation will fail (AUTO_PR=true)"
fi

if command -v node &>/dev/null; then
    ok "Node.js: $(node --version)"
else
    warn "Node.js not found — frontend cannot be rebuilt"
fi

if command -v pnpm &>/dev/null; then
    ok "pnpm: $(pnpm --version)"
else
    warn "pnpm not found — run: curl -fsSL https://get.pnpm.io/install.sh | bash -"
fi

# ---------------------------------------------------------------------------
info "── Build artefacts ─────────────────────────────────"
# ---------------------------------------------------------------------------
[[ -d "frontend/dist" ]]          && ok "frontend/dist exists (React SPA built)"    || warn "frontend/dist missing  →  cd frontend && pnpm run build"
[[ -d "frontend/node_modules" ]]  && ok "frontend/node_modules exists"              || warn "frontend/node_modules missing  →  cd frontend && pnpm install"

# ---------------------------------------------------------------------------
info "── Disk ────────────────────────────────────────────"
# ---------------------------------------------------------------------------
DISK_PCT=$(df / --output=pcent | tail -1 | tr -d ' %')
DISK_AVAIL=$(df -h / --output=avail | tail -1 | tr -d ' ')
if [[ $DISK_PCT -lt 80 ]]; then
    ok "Disk usage: ${DISK_PCT}% (${DISK_AVAIL} free)"
elif [[ $DISK_PCT -lt 90 ]]; then
    warn "Disk usage: ${DISK_PCT}% (${DISK_AVAIL} free) — consider running scripts/disk-cleanup.sh"
else
    fail "Disk usage: ${DISK_PCT}% (${DISK_AVAIL} free) — CRITICAL: run scripts/disk-cleanup.sh NOW"
fi

# ---------------------------------------------------------------------------
info "── Health endpoint ─────────────────────────────────"
# ---------------------------------------------------------------------------
if curl -sf http://localhost:8080/api/health > /dev/null 2>&1; then
    ok "/api/health responding"
else
    warn "/api/health not responding"
fi

# ---------------------------------------------------------------------------
info "── Recent logs (last 5 lines each) ─────────────────"
# ---------------------------------------------------------------------------
for LOG in runner.log healer.log deploy.log; do
    if [[ -f "$LOG" ]]; then
        echo -e "  ${DIM}$LOG:${NC}"
        tail -5 "$LOG" | sed 's/^/    /'
    fi
done

# ---------------------------------------------------------------------------
echo ""
if [[ $ERRORS -eq 0 ]]; then
    echo -e "${GREEN}All checks passed.${NC}"
    exit 0
else
    echo -e "${RED}$ERRORS critical issue(s) found.${NC}"
    exit 1
fi
