#!/usr/bin/env bash
# Bootstrap a fresh Amazon Linux 2023 EC2 instance for task-forge.
# Installs all OS-level dependencies, configures nginx + TLS, firewalld,
# fail2ban, systemd services, and runs the app-level setup.
#
# Usage (run as ec2-user from the cloned repo):
#   bash scripts/bootstrap-ec2.sh
#
# Idempotent — safe to re-run. Skips steps that are already done.
# After completion, edit .env with your secrets and start the services.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

# Set EC2_DOMAIN to the hostname nginx should serve (e.g. tasks.example.com).
DOMAIN="${EC2_DOMAIN:-tasks.example.com}"

RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${CYAN}[bootstrap]${NC} $*"; }
ok()    { echo -e "${GREEN}[ok]${NC}        $*"; }
warn()  { echo -e "${YELLOW}[warn]${NC}      $*"; }
die()   { echo -e "${RED}[error]${NC}     $*" >&2; exit 1; }
need_sudo() { sudo -n true 2>/dev/null || die "sudo required — run 'sudo -v' first or use a user with passwordless sudo"; }

need_sudo

# ============================================================================
# 1. System packages
# ============================================================================
info "Installing system packages..."
PKGS=(
    python3.9 python3.9-devel python3.9-pip
    git gcc cronie
    nginx certbot python3-certbot-nginx
    fail2ban audit aide
    jq htop tmux
)

NEEDED=()
for pkg in "${PKGS[@]}"; do
    if ! rpm -q "$pkg" &>/dev/null; then
        NEEDED+=("$pkg")
    fi
done

if [[ ${#NEEDED[@]} -gt 0 ]]; then
    info "Installing: ${NEEDED[*]}"
    sudo dnf install -y "${NEEDED[@]}"
    ok "System packages installed"
else
    ok "System packages already installed"
fi

# ============================================================================
# 2. Node.js 22 (via NodeSource)
# ============================================================================
info "Checking Node.js..."
if ! command -v node &>/dev/null || [[ "$(node -v | cut -d. -f1 | tr -d v)" -lt 22 ]]; then
    info "Installing Node.js 22..."
    curl -fsSL https://rpm.nodesource.com/setup_22.x | sudo bash -
    sudo dnf install -y nodejs
    ok "Node.js $(node -v) installed"
else
    ok "Node.js $(node -v) already installed"
fi

# ============================================================================
# 3. pnpm
# ============================================================================
info "Checking pnpm..."
export PNPM_HOME="${PNPM_HOME:-$HOME/.local/share/pnpm}"
export PATH="$PNPM_HOME:$PATH"
if ! command -v pnpm &>/dev/null; then
    info "Installing pnpm..."
    curl -fsSL https://get.pnpm.io/install.sh | bash -
    ok "pnpm installed"
else
    ok "pnpm $(pnpm -v) already installed"
fi

# ============================================================================
# 4. GitHub CLI (gh)
# ============================================================================
info "Checking GitHub CLI..."
if ! command -v gh &>/dev/null; then
    info "Installing gh..."
    sudo dnf install -y 'dnf-command(config-manager)'
    sudo dnf config-manager --add-repo https://cli.github.com/packages/rpm/gh-cli.repo
    sudo dnf install -y gh
    ok "gh $(gh --version | head -1) installed"
else
    ok "gh already installed"
fi

if ! gh auth status &>/dev/null 2>&1; then
    warn "gh not authenticated — run: gh auth login"
fi

# ============================================================================
# 5. App-level setup (venv, deps, frontend, .env)
# ============================================================================
info "Running app-level setup..."
bash "$SCRIPT_DIR/setup.sh"

# ============================================================================
# 5b. Pull secrets from AWS Secrets Manager (if available)
# ============================================================================
info "Checking for secrets in AWS Secrets Manager..."
if aws sts get-caller-identity --region "${AWS_REGION:-us-west-2}" &>/dev/null; then
    SM_SECRET="${SM_SECRET_NAME:-task-forge/env}"
    if aws secretsmanager describe-secret --secret-id "$SM_SECRET" --region "${AWS_REGION:-us-west-2}" &>/dev/null; then
        info "Found secret '$SM_SECRET' — pulling..."
        bash "$SCRIPT_DIR/pull-env.sh" "$SM_SECRET"
        ok "Secrets applied to .env from Secrets Manager"
    else
        warn "AWS credentials available but secret '$SM_SECRET' not found — using .env.example defaults"
        warn "  To create: fill in .env, then run: bash scripts/push-env.sh"
    fi
else
    warn "No AWS credentials — skipping Secrets Manager. Fill in .env manually."
    warn "  After configuring AWS: bash scripts/push-env.sh  (seed)  /  bash scripts/pull-env.sh  (pull)"
fi

# ============================================================================
# 6. pre-commit hooks
# ============================================================================
info "Checking pre-commit..."
if [[ -f ".pre-commit-config.yaml" ]]; then
    PRECOMMIT="$PROJECT_DIR/.venv/bin/pre-commit"
    if [[ ! -f "$PRECOMMIT" ]]; then
        "$PROJECT_DIR/.venv/bin/pip" install -q pre-commit
    fi
    "$PRECOMMIT" install --allow-missing-config 2>/dev/null || true
    ok "pre-commit hooks installed"
fi

# ============================================================================
# 7. SSH hardening
# ============================================================================
info "Configuring SSH hardening..."
SSH_HARDENING="/etc/ssh/sshd_config.d/10-hardening.conf"
if sudo test -f "$SSH_HARDENING"; then
    ok "SSH hardening config already exists"
else
    sudo tee "$SSH_HARDENING" >/dev/null << 'EOF'
PermitRootLogin no
MaxAuthTries 3
MaxSessions 5
LoginGraceTime 30
ClientAliveInterval 300
ClientAliveCountMax 2
AllowUsers ec2-user
X11Forwarding no
AllowTcpForwarding no
AllowAgentForwarding no
PubkeyAuthentication yes
PasswordAuthentication no
PermitEmptyPasswords no
KbdInteractiveAuthentication no
EOF
    sudo sshd -t && sudo systemctl reload sshd
    ok "SSH hardening applied"
fi

# ============================================================================
# 8. Kernel network hardening (sysctl)
# ============================================================================
info "Configuring kernel network hardening..."
SYSCTL_CONF="/etc/sysctl.d/99-security.conf"
if sudo test -f "$SYSCTL_CONF"; then
    ok "Sysctl hardening already exists"
else
    sudo tee "$SYSCTL_CONF" >/dev/null << 'EOF'
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.default.accept_redirects = 0
net.ipv4.conf.all.send_redirects = 0
net.ipv6.conf.all.accept_redirects = 0
net.ipv6.conf.default.accept_redirects = 0
net.ipv4.conf.all.log_martians = 1
net.ipv4.icmp_echo_ignore_broadcasts = 1
net.ipv4.conf.all.accept_source_route = 0
net.ipv4.conf.default.accept_source_route = 0
net.ipv6.conf.all.accept_source_route = 0
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.default.rp_filter = 1
net.ipv4.tcp_syncookies = 1
net.ipv4.ip_forward = 0
net.ipv6.conf.all.forwarding = 0
EOF
    sudo sysctl --system >/dev/null
    ok "Sysctl hardening applied"
fi

# ============================================================================
# 9. SELinux enforcing
# ============================================================================
info "Configuring SELinux..."
if command -v getenforce &>/dev/null; then
    CURRENT_SE=$(getenforce)
    if [[ "$CURRENT_SE" == "Enforcing" ]]; then
        ok "SELinux already enforcing"
    else
        sudo setenforce 1 2>/dev/null || warn "setenforce failed (may need reboot)"
        sudo sed -i 's/^SELINUX=permissive/SELINUX=enforcing/' /etc/selinux/config 2>/dev/null || true
        sudo sed -i 's/^SELINUX=disabled/SELINUX=enforcing/' /etc/selinux/config 2>/dev/null || true
        ok "SELinux set to enforcing (may need reboot to fully activate)"
    fi
    if ! getsebool httpd_can_network_connect 2>/dev/null | grep -q "on"; then
        sudo setsebool -P httpd_can_network_connect 1
        ok "SELinux: httpd_can_network_connect enabled (nginx proxy)"
    else
        ok "SELinux: httpd_can_network_connect already on"
    fi
else
    warn "SELinux not available on this system"
fi

# ============================================================================
# 10. Audit rules
# ============================================================================
info "Configuring audit rules..."
AUDIT_RULES="/etc/audit/rules.d/hardening.rules"
if sudo test -f "$AUDIT_RULES"; then
    ok "Audit hardening rules already exist"
else
    if command -v augenrules &>/dev/null; then
        sudo tee "$AUDIT_RULES" >/dev/null << 'EOF'
-D
-b 8192
-f 1
-w /etc/passwd -p wa -k identity
-w /etc/group -p wa -k identity
-w /etc/shadow -p wa -k identity
-w /etc/gshadow -p wa -k identity
-w /etc/sudoers -p wa -k sudoers
-w /etc/sudoers.d/ -p wa -k sudoers
-w /usr/bin/sudo -p x -k priv_esc
-w /usr/bin/su -p x -k priv_esc
-w /etc/ssh/sshd_config -p wa -k sshd_config
-w /etc/ssh/sshd_config.d/ -p wa -k sshd_config
-w /etc/crontab -p wa -k cron
-w /etc/cron.d/ -p wa -k cron
-w /var/spool/cron/ -p wa -k cron
-w /sbin/modprobe -p x -k modules
-w /sbin/insmod -p x -k modules
-w /var/log/lastlog -p wa -k logins
-w /var/log/wtmp -p wa -k logins
-e 2
EOF
        echo "# Superseded by hardening.rules" | sudo tee /etc/audit/rules.d/audit.rules >/dev/null
        sudo augenrules --load 2>/dev/null || warn "augenrules --load failed (reboot to activate)"
        ok "Audit rules installed"
    else
        sudo dnf install -y audit
        warn "audit installed — re-run this script to apply rules"
    fi
fi

# ============================================================================
# 11. File integrity monitoring (AIDE)
# ============================================================================
info "Configuring AIDE..."
if command -v aide &>/dev/null; then
    ok "AIDE already installed"
else
    sudo dnf install -y aide
    ok "AIDE installed"
fi

if sudo test -f /var/lib/aide/aide.db.gz; then
    ok "AIDE baseline already exists"
else
    info "Initializing AIDE baseline (this takes a minute)..."
    sudo aide --init 2>/dev/null
    sudo cp /var/lib/aide/aide.db.new.gz /var/lib/aide/aide.db.gz 2>/dev/null || true
    ok "AIDE baseline created"
fi

# ============================================================================
# 12. Firewalld
# ============================================================================
info "Configuring firewalld..."
if ! systemctl is-active firewalld &>/dev/null; then
    sudo systemctl enable --now firewalld
fi
sudo firewall-cmd --permanent --add-service=ssh 2>/dev/null || true
sudo firewall-cmd --permanent --add-service=http 2>/dev/null || true
sudo firewall-cmd --permanent --add-service=https 2>/dev/null || true
sudo firewall-cmd --reload
ok "Firewalld: ssh, http, https open"

# ============================================================================
# 13. Fail2ban
# ============================================================================
info "Configuring fail2ban..."
sudo bash "$SCRIPT_DIR/install-fail2ban.sh"
ok "Fail2ban configured"

# ============================================================================
# 14. Nginx reverse proxy
# ============================================================================
info "Configuring nginx..."
NGINX_CONF="/etc/nginx/conf.d/taskagent.conf"
if [[ ! -f "$NGINX_CONF" ]]; then
    sudo tee "$NGINX_CONF" >/dev/null << EOF
server {
    listen 80;
    server_name $DOMAIN;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF
    sudo nginx -t && sudo systemctl enable --now nginx && sudo systemctl reload nginx
    ok "Nginx configured for $DOMAIN"
else
    ok "Nginx config already exists"
fi

# ============================================================================
# 15. TLS certificate (certbot)
# ============================================================================
info "Checking TLS certificate..."
if sudo test -d "/etc/letsencrypt/live/$DOMAIN"; then
    ok "TLS cert already exists for $DOMAIN"
else
    info "Requesting TLS cert for $DOMAIN..."
    warn "This requires DNS for $DOMAIN to point to this instance's public IP."
    sudo certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --register-unsafely-without-email || {
        warn "Certbot failed — DNS may not be propagated yet. Run manually later:"
        warn "  sudo certbot --nginx -d $DOMAIN"
    }
fi

# Enable auto-renewal timer (certbot package ships a systemd timer but doesn't enable it)
if systemctl list-unit-files certbot-renew.timer &>/dev/null; then
    sudo systemctl enable --now certbot-renew.timer
    ok "Certbot auto-renewal timer enabled"
fi

# ============================================================================
# 16. Systemd services
# ============================================================================
info "Installing systemd services..."
sudo bash "$SCRIPT_DIR/install-systemd.sh"
ok "Systemd services installed"

# ============================================================================
# 17. Cron jobs
# ============================================================================
info "Installing cron jobs..."
bash "$SCRIPT_DIR/install-cron.sh"
ok "Cron jobs installed"

# ============================================================================
# 18. .env file permissions
# ============================================================================
if [[ -f "$PROJECT_DIR/.env" ]]; then
    chmod 600 "$PROJECT_DIR/.env"
    ok ".env permissions set to 600"
fi

# ============================================================================
# Done
# ============================================================================
echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN} EC2 Bootstrap Complete${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "What was set up:"
echo "  - Python 3.9 + venv + pip deps"
echo "  - Node.js 22 + pnpm + frontend build"
echo "  - GitHub CLI (gh)"
echo "  - SSH hardening (pubkey only, no root login)"
echo "  - Kernel sysctl hardening (network, redirects, syncookies)"
echo "  - SELinux enforcing"
echo "  - Audit rules (identity, sudoers, SSH, cron, modules, logins)"
echo "  - AIDE file integrity baseline"
echo "  - firewalld (ssh, http, https)"
echo "  - fail2ban (sshd jail)"
echo "  - nginx reverse proxy → :8080"
echo "  - TLS via certbot ($DOMAIN)"
echo "  - systemd services (taskbot-web, taskbot-discord, taskbot-poller)"
echo "  - cron jobs (runner, healer, cleanup, heartbeat, pr-reviewer)"
echo "  - pre-commit hooks"
echo "  - .env permissions (600)"
echo ""
echo "Next steps:"
echo "  1. Edit .env and fill in all required secrets"
echo "  2. Authenticate GitHub CLI:  gh auth login"
echo "  3. Start services:"
echo "       sudo systemctl start taskbot-web"
echo "       sudo systemctl start taskbot-discord"
echo "       sudo systemctl start taskbot-poller"
echo "  4. Verify:  curl -s https://$DOMAIN/api/health"
