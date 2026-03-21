#!/usr/bin/env bash
# Install and enable fail2ban with sshd jail (Amazon Linux 2023 + nftables).
# Idempotent: safe to re-run. See README.md "Instance Security Hardening".

set -euo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Run with sudo: sudo bash scripts/install-fail2ban.sh"
  exit 1
fi

dnf install -y fail2ban

tee /etc/fail2ban/jail.local >/dev/null << 'EOF'
[DEFAULT]
bantime = 3600
findtime = 600
maxretry = 3
banaction = nftables-multiport

[sshd]
enabled = true
port = ssh
filter = sshd
logpath = /var/log/secure
maxretry = 3
EOF

systemctl enable --now fail2ban
fail2ban-client reload 2>/dev/null || true

echo "fail2ban status:"
systemctl is-active fail2ban
fail2ban-client status
