#!/usr/bin/env bash
# Verify that an EC2 instance is fully bootstrapped for task-forge.
# Read-only — does not change anything. Safe to run anytime.
#
# Usage:  bash scripts/verify-setup.sh
#
# Exit code 0 = all critical checks pass, 1 = one or more failures.
set -uo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

DOMAIN="${EC2_DOMAIN:-tasks.example.com}"

RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; DIM='\033[2m'; BOLD='\033[1m'; NC='\033[0m'
ok()   { echo -e "  ${GREEN}✓${NC} $*"; }
warn() { echo -e "  ${YELLOW}!${NC} $*"; WARNINGS=$((WARNINGS+1)); }
fail() { echo -e "  ${RED}✗${NC} $*"; ERRORS=$((ERRORS+1)); }
section() { echo -e "\n${CYAN}${BOLD}── $* ──${NC}"; }

ERRORS=0
WARNINGS=0

# ============================================================================
section "System Packages"
# ============================================================================
if command -v python3.9 &>/dev/null || command -v python3 &>/dev/null; then
    PY_VER=$(python3 --version 2>&1)
    ok "Python: $PY_VER"
else
    fail "Python 3.9 not found  →  sudo dnf install -y python3"
fi
for pkg in git gcc nginx certbot fail2ban jq; do
    if rpm -q "$pkg" &>/dev/null; then
        ok "$pkg installed"
    else
        fail "$pkg not installed  →  sudo dnf install -y $pkg"
    fi
done

# ============================================================================
section "Node.js & pnpm"
# ============================================================================
if command -v node &>/dev/null; then
    NODE_MAJOR=$(node -v | cut -d. -f1 | tr -d v)
    if [[ "$NODE_MAJOR" -ge 22 ]]; then
        ok "Node.js $(node -v)"
    else
        warn "Node.js $(node -v) — v22+ recommended"
    fi
else
    fail "Node.js not found  →  curl -fsSL https://rpm.nodesource.com/setup_22.x | sudo bash - && sudo dnf install -y nodejs"
fi

if command -v pnpm &>/dev/null; then
    ok "pnpm $(pnpm -v)"
else
    fail "pnpm not found  →  curl -fsSL https://get.pnpm.io/install.sh | bash -"
fi

# ============================================================================
section "Python Venv & Dependencies"
# ============================================================================
VENV_PY="$PROJECT_DIR/.venv/bin/python3"
if [[ -f "$VENV_PY" ]]; then
    ok "venv exists: $("$VENV_PY" --version 2>&1)"
else
    fail "venv missing  →  bash scripts/setup.sh"
fi

if [[ -f "$VENV_PY" ]]; then
    MISSING_PKGS=()
    # pip-name → import-name mapping for packages where they differ
    declare -A IMPORT_MAP=(
        [fastapi]=fastapi [uvicorn]=uvicorn [boto3]=boto3
        [discord.py]=discord [python-frontmatter]=frontmatter
        [pydantic]=pydantic [python-dotenv]=dotenv
    )
    for dep in "${!IMPORT_MAP[@]}"; do
        MOD="${IMPORT_MAP[$dep]}"
        if ! "$VENV_PY" -c "import $MOD" &>/dev/null; then
            MISSING_PKGS+=("$dep")
        fi
    done
    if [[ ${#MISSING_PKGS[@]} -eq 0 ]]; then
        ok "Key Python packages present"
    else
        fail "Missing Python packages: ${MISSING_PKGS[*]}  →  .venv/bin/pip install -r requirements.txt"
    fi
fi

# ============================================================================
section "Cursor Agent CLI"
# ============================================================================
AGENT_BIN="${AGENT_BIN:-agent}"
AGENT_PATH=$(command -v "$AGENT_BIN" 2>/dev/null || echo "")
if [[ -n "$AGENT_PATH" ]]; then
    ok "agent CLI found: $AGENT_PATH"
else
    if [[ -f "$HOME/.local/bin/agent" ]]; then
        ok "agent CLI found: $HOME/.local/bin/agent (not in current PATH but available via ~/.local/bin)"
        AGENT_PATH="$HOME/.local/bin/agent"
    else
        fail "Cursor agent CLI not found — tasks cannot execute"
    fi
fi

# ============================================================================
section "AWS & Secrets Manager"
# ============================================================================
SM_REGION="${AWS_REGION:-us-west-2}"
SM_SECRET="${SM_SECRET_NAME:-task-forge/env}"
if aws sts get-caller-identity --region "$SM_REGION" &>/dev/null; then
    AWS_ACCT=$(aws sts get-caller-identity --region "$SM_REGION" --query 'Account' --output text 2>/dev/null)
    ok "AWS credentials valid (account $AWS_ACCT)"
    if aws secretsmanager describe-secret --secret-id "$SM_SECRET" --region "$SM_REGION" &>/dev/null; then
        ok "Secret '$SM_SECRET' exists in $SM_REGION"
    else
        warn "Secret '$SM_SECRET' not found — run: bash scripts/push-env.sh"
    fi
else
    warn "No AWS credentials — Secrets Manager unavailable. Attach an IAM instance profile or run 'aws configure'."
fi

# ============================================================================
section "GitHub CLI"
# ============================================================================
GH_BIN="${GH_BIN:-gh}"
if command -v "$GH_BIN" &>/dev/null; then
    ok "gh installed: $("$GH_BIN" --version | head -1)"
    if "$GH_BIN" auth status &>/dev/null 2>&1; then
        GHUSER=$("$GH_BIN" api user --jq '.login' 2>/dev/null || echo "unknown")
        ok "gh authenticated as @$GHUSER"
    else
        fail "gh not authenticated  →  gh auth login"
    fi
else
    fail "gh not found  →  sudo dnf install -y gh"
fi

# ============================================================================
section "Environment (.env)"
# ============================================================================
if [[ ! -f "$PROJECT_DIR/.env" ]]; then
    fail ".env missing  →  cp .env.example .env && chmod 600 .env"
else
    PERMS=$(stat -c '%a' "$PROJECT_DIR/.env")
    if [[ "$PERMS" == "600" ]]; then
        ok ".env exists (mode $PERMS)"
    else
        warn ".env exists but mode is $PERMS (should be 600)  →  chmod 600 .env"
    fi

    set +u
    # shellcheck disable=SC2046
    export $(grep -v '^#' .env | grep '=' | xargs) 2>/dev/null || true
    set -u

    [[ -n "${DISCORD_BOT_TOKEN:-}" ]]       && ok "DISCORD_BOT_TOKEN set"        || fail "DISCORD_BOT_TOKEN not set"
    [[ -n "${NOTIFICATION_CHANNEL_ID:-}" ]] && ok "NOTIFICATION_CHANNEL_ID set"  || warn "NOTIFICATION_CHANNEL_ID not set (bot won't post notifications)"
    [[ -n "${AUTH_EMAIL:-}" ]]              && ok "AUTH_EMAIL set"               || warn "AUTH_EMAIL not set — web UI has no login"
    [[ -n "${AUTH_PASSWORD:-}" ]]           && ok "AUTH_PASSWORD set"            || warn "AUTH_PASSWORD not set — web UI has no login"
    [[ -n "${AUTH_SECRET_KEY:-}" ]]         && ok "AUTH_SECRET_KEY set"          || warn "AUTH_SECRET_KEY not set — sessions won't survive restarts"
    [[ -n "${GITHUB_WEBHOOK_SECRET:-}" ]]   && ok "GITHUB_WEBHOOK_SECRET set"    || warn "GITHUB_WEBHOOK_SECRET not set — auto-deploy won't verify payloads"

    if [[ "${TASK_BACKEND:-file}" == "dynamo" ]]; then
        ok "TASK_BACKEND=dynamo"
        [[ -n "${DYNAMO_TABLE:-}" ]] && ok "DYNAMO_TABLE=${DYNAMO_TABLE}" || fail "DYNAMO_TABLE not set (required when TASK_BACKEND=dynamo)"
    else
        warn "TASK_BACKEND=${TASK_BACKEND:-file} — using local file store (set dynamo for production)"
    fi
fi

# ============================================================================
section "Systemd Services"
# ============================================================================
for svc in taskbot-web taskbot-discord taskbot-poller; do
    UNIT="/etc/systemd/system/${svc}.service"
    if [[ -f "$UNIT" ]]; then
        if systemctl is-enabled "$svc" &>/dev/null; then
            if systemctl is-active "$svc" &>/dev/null; then
                PID=$(systemctl show -p MainPID --value "$svc")
                ok "$svc: running (PID $PID)"
            else
                warn "$svc: installed & enabled but not running  →  sudo systemctl start $svc"
            fi
        else
            warn "$svc: installed but not enabled  →  sudo systemctl enable $svc"
        fi

        if grep -q 'Environment="PATH=.*\.local/bin' "$UNIT" 2>/dev/null; then
            ok "$svc: PATH includes ~/.local/bin (agent reachable)"
        else
            fail "$svc: PATH missing ~/.local/bin — agent binary won't be found  →  sudo bash scripts/install-systemd.sh"
        fi
    else
        fail "$svc: unit file missing  →  sudo bash scripts/install-systemd.sh"
    fi
done

# ============================================================================
section "Cron Jobs"
# ============================================================================
CRONTAB=$(crontab -l 2>/dev/null || true)
for entry in "run_heal.py:Healer" "disk-cleanup.sh:Disk cleanup" "run_heartbeat.py:Heartbeat" "run_pr_reviewer.py:PR reviewer"; do
    PATTERN="${entry%%:*}"
    LABEL="${entry#*:}"
    if echo "$CRONTAB" | grep -q "$PATTERN"; then
        ok "$LABEL cron installed"
    else
        warn "$LABEL cron missing  →  bash scripts/install-cron.sh"
    fi
done

if echo "$CRONTAB" | grep -q "run_task\.py"; then
    ok "Task runner cron installed (supplementary — poller is primary dispatcher)"
fi

if systemctl is-active crond &>/dev/null; then
    ok "crond service active"
else
    fail "crond not running  →  sudo systemctl enable --now crond"
fi

# ============================================================================
section "Nginx & TLS"
# ============================================================================
if systemctl is-active nginx &>/dev/null; then
    ok "nginx running"
else
    fail "nginx not running  →  sudo systemctl enable --now nginx"
fi

if sudo nginx -t &>/dev/null 2>&1; then
    ok "nginx config valid"
else
    fail "nginx config invalid  →  sudo nginx -t"
fi

NGINX_CONF="/etc/nginx/conf.d/taskagent.conf"
if [[ -f "$NGINX_CONF" ]]; then
    ok "taskagent.conf exists"
    if grep -q "proxy_pass.*8080" "$NGINX_CONF" 2>/dev/null; then
        ok "nginx proxying to :8080"
    else
        warn "nginx conf exists but may not proxy to :8080"
    fi
    if grep -q "ssl_certificate" "$NGINX_CONF" 2>/dev/null; then
        ok "TLS configured in nginx"
    else
        warn "No TLS in nginx config  →  sudo certbot --nginx -d $DOMAIN"
    fi
else
    fail "taskagent.conf missing  →  run bootstrap or configure nginx manually"
fi

if sudo test -d "/etc/letsencrypt/live/$DOMAIN" 2>/dev/null; then
    EXPIRY=$(sudo openssl x509 -enddate -noout -in "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" 2>/dev/null | cut -d= -f2)
    EXPIRY_EPOCH=$(date -d "$EXPIRY" +%s 2>/dev/null || echo 0)
    NOW_EPOCH=$(date +%s)
    DAYS_LEFT=$(( (EXPIRY_EPOCH - NOW_EPOCH) / 86400 ))
    if [[ $DAYS_LEFT -gt 30 ]]; then
        ok "TLS cert valid ($DAYS_LEFT days remaining)"
    elif [[ $DAYS_LEFT -gt 0 ]]; then
        warn "TLS cert expires in $DAYS_LEFT days  →  sudo certbot renew"
    else
        fail "TLS cert expired  →  sudo certbot renew"
    fi
else
    warn "No TLS cert for $DOMAIN  →  sudo certbot --nginx -d $DOMAIN"
fi

if systemctl is-active certbot-renew.timer &>/dev/null; then
    ok "Certbot auto-renewal timer active"
else
    fail "Certbot renewal timer not active — cert will expire  →  sudo systemctl enable --now certbot-renew.timer"
fi

# ============================================================================
section "Firewall"
# ============================================================================
if systemctl is-active firewalld &>/dev/null; then
    ok "firewalld running"
    FW_SERVICES=$(sudo firewall-cmd --list-services 2>/dev/null || echo "")
    for svc in ssh http https; do
        if echo "$FW_SERVICES" | grep -qw "$svc"; then
            ok "firewall allows $svc"
        else
            fail "firewall missing $svc  →  sudo firewall-cmd --permanent --add-service=$svc && sudo firewall-cmd --reload"
        fi
    done
else
    warn "firewalld not running"
fi

# ============================================================================
section "Security"
# ============================================================================
if systemctl is-active fail2ban &>/dev/null; then
    JAILS=$(sudo fail2ban-client status 2>/dev/null | grep "Jail list" | cut -d: -f2 | tr -d '[:space:]')
    ok "fail2ban active (jails: ${JAILS:-none})"
else
    warn "fail2ban not running  →  sudo systemctl enable --now fail2ban"
fi

if command -v getenforce &>/dev/null; then
    SE_STATUS=$(getenforce 2>/dev/null)
    if [[ "$SE_STATUS" == "Enforcing" ]]; then
        ok "SELinux enforcing"
    elif [[ "$SE_STATUS" == "Permissive" ]]; then
        warn "SELinux permissive  →  sudo setenforce 1"
    else
        warn "SELinux disabled"
    fi
fi

SSH_HARDENING="/etc/ssh/sshd_config.d/10-hardening.conf"
if sudo test -f "$SSH_HARDENING" 2>/dev/null; then
    ok "SSH hardening config present"
else
    warn "SSH hardening not applied  →  re-run bootstrap"
fi

# ============================================================================
section "Pre-commit Hooks"
# ============================================================================
if [[ -f "$PROJECT_DIR/.git/hooks/pre-commit" ]]; then
    ok "pre-commit hooks installed"
else
    warn "pre-commit hooks not installed  →  .venv/bin/pre-commit install"
fi

# ============================================================================
section "Frontend Build"
# ============================================================================
[[ -d "frontend/node_modules" ]] && ok "frontend/node_modules exists"   || warn "frontend/node_modules missing  →  cd frontend && pnpm install"
[[ -d "frontend/dist" ]]         && ok "frontend/dist exists"           || warn "frontend/dist missing  →  cd frontend && pnpm run build"

# ============================================================================
section "Disk"
# ============================================================================
DISK_PCT=$(df / --output=pcent | tail -1 | tr -d ' %')
DISK_AVAIL=$(df -h / --output=avail | tail -1 | tr -d ' ')
if [[ $DISK_PCT -lt 80 ]]; then
    ok "Disk: ${DISK_PCT}% used (${DISK_AVAIL} free)"
elif [[ $DISK_PCT -lt 90 ]]; then
    warn "Disk: ${DISK_PCT}% used (${DISK_AVAIL} free)  →  bash scripts/disk-cleanup.sh"
else
    fail "Disk: ${DISK_PCT}% used (${DISK_AVAIL} free)  →  CRITICAL: bash scripts/disk-cleanup.sh"
fi

# ============================================================================
section "Health Endpoint"
# ============================================================================
if curl -sf http://localhost:8080/api/health >/dev/null 2>&1; then
    ok "/api/health responding on :8080"
elif systemctl is-active taskbot-web &>/dev/null; then
    warn "/api/health not responding (service is running — may still be starting)"
else
    warn "/api/health not responding (web service not running)"
fi

if curl -sf --max-time 5 "https://$DOMAIN/api/health" >/dev/null 2>&1; then
    ok "https://$DOMAIN/api/health responding"
else
    warn "https://$DOMAIN/api/health not responding"
fi

# ============================================================================
# Summary
# ============================================================================
echo ""
echo -e "${BOLD}════════════════════════════════════════════════════${NC}"
if [[ $ERRORS -eq 0 && $WARNINGS -eq 0 ]]; then
    echo -e "${GREEN}${BOLD} All checks passed.${NC}"
elif [[ $ERRORS -eq 0 ]]; then
    echo -e "${GREEN}${BOLD} All critical checks passed${NC} ${YELLOW}($WARNINGS warning(s))${NC}"
else
    echo -e "${RED}${BOLD} $ERRORS critical failure(s)${NC}"
    [[ $WARNINGS -gt 0 ]] && echo -e " ${YELLOW}$WARNINGS warning(s)${NC}"
fi
echo -e "${BOLD}════════════════════════════════════════════════════${NC}"

[[ $ERRORS -eq 0 ]]
