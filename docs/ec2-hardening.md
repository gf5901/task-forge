# EC2 Instance Security Hardening

Run these after the initial setup to lock down the EC2 instance. The bootstrap script (`scripts/bootstrap-ec2.sh`) applies most of these automatically.

## Current status

| Measure | Status | Notes |
|---------|--------|-------|
| SELinux enforcing | Applied | Runtime + `/etc/selinux/config` |
| firewalld | Applied | HTTP, HTTPS, SSH allowed; managed by `firewall-cmd` |
| fail2ban | Applied | sshd jail active |
| SSH hardening | Applied | Drop-in at `/etc/ssh/sshd_config.d/10-hardening.conf` |
| Kernel sysctl hardening | Applied | `/etc/sysctl.d/99-security.conf` |
| Audit rules | Applied | File watches active; reboot needed to clear legacy `-a never,task` and activate `-e 2` immutability |
| AIDE baseline | Applied | Run `sudo aide --check` to verify integrity |
| `.env` permissions | Applied | `chmod 600` |

## Post-reboot checklist

After the next instance reboot, verify:

1. `sudo auditctl -l` — should **not** show `-a never,task` (the old suppress-all rule)
2. `getenforce` — should return `Enforcing`
3. `sudo nft list ruleset` — should show the `inet filter` table with `policy drop`
4. `sudo systemctl is-active fail2ban` — should return `active`
5. `sudo fail2ban-client status sshd` — jail should be running

## Future improvements (not yet implemented)

- **Restrict passwordless sudo** — `/etc/sudoers.d/90-cloud-init-users` grants `ec2-user ALL=(ALL) NOPASSWD:ALL`. Consider limiting to specific commands or requiring authentication. Deferred because it could lock out remote administration.
- **Centralized log shipping** — Install CloudWatch Agent or ship logs to an external SIEM. Logs currently exist only on the instance.
- **ClamAV malware scanning** — Install and schedule periodic scans for any user-uploaded or agent-generated content.
- **Runner isolation** — The agent runs as `ec2-user` with broad home-directory access. Consider a dedicated service account with `ProtectHome=`, `NoNewPrivileges=`, and `CapabilityBoundingSet=` systemd directives.
- **Automated patching** — Configure `dnf-automatic` for security updates.

## SSH hardening

```bash
sudo tee /etc/ssh/sshd_config.d/10-hardening.conf > /dev/null << 'EOF'
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
```

## fail2ban (brute-force protection)

From the repo root (recommended — same config as production):

```bash
sudo bash scripts/install-fail2ban.sh
```

Or apply manually:

```bash
sudo dnf install -y fail2ban
sudo tee /etc/fail2ban/jail.local > /dev/null << 'EOF'
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
sudo systemctl enable --now fail2ban
```

## Host firewall (firewalld)

Fail2Ban installs `firewalld` as a dependency on Amazon Linux 2023. firewalld manages nftables rules and defaults to allowing only SSH. You must explicitly open HTTP/HTTPS for nginx:

```bash
sudo firewall-cmd --permanent --add-service=http
sudo firewall-cmd --permanent --add-service=https
sudo firewall-cmd --reload
# Verify:
sudo firewall-cmd --list-services   # should show: dhcpv6-client http https mdns ssh
```

> **Note:** If you previously used raw nftables rules (`/etc/nftables/hardening.nft`), firewalld overrides them. Do not mix firewalld and raw nftables — use `firewall-cmd` for all firewall changes.

## Kernel network hardening

```bash
sudo tee /etc/sysctl.d/99-security.conf > /dev/null << 'EOF'
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
sudo sysctl --system
```

## SELinux

```bash
sudo setenforce 1
sudo sed -i 's/^SELINUX=permissive/SELINUX=enforcing/' /etc/selinux/config
```

## Audit rules

```bash
sudo tee /etc/audit/rules.d/hardening.rules > /dev/null << 'EOF'
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
# Clear the default "suppress all" rule
echo "# Superseded by hardening.rules" | sudo tee /etc/audit/rules.d/audit.rules
sudo augenrules --load
```

## File integrity monitoring (AIDE)

```bash
sudo dnf install -y aide
sudo aide --init
sudo cp /var/lib/aide/aide.db.new.gz /var/lib/aide/aide.db.gz
# Run checks with: sudo aide --check
```
