# EC2 Setup Guide

How to provision a fresh Amazon Linux 2023 EC2 instance for task-forge. Covers the automated bootstrap, manual steps, and common pitfalls.

## Prerequisites

Before starting, ensure:

1. **EC2 instance** running Amazon Linux 2023 (arm64 or x86_64)
2. **IAM instance profile** (`AgentRole`) attached with policies for:
   - `dynamodb:*` on `arn:aws:dynamodb:us-west-2:*:table/agent-tasks` and its indexes
   - `secretsmanager:GetSecretValue`, `PutSecretValue`, `CreateSecret`, `DescribeSecret`, `UpdateSecret` on `arn:aws:secretsmanager:us-west-2:*:secret:task-forge/*`
   - `ssm:UpdateInstanceInformation` (for SSM agent heartbeats)
3. **DNS** — an A record for your API hostname (e.g. `tasks.example.com`) pointing to the instance's public IP
4. **Security group** — inbound SSH (22), HTTP (80), HTTPS (443)
5. **SSH access** as `ec2-user`

## Quick Start (Happy Path)

```bash
# 1. Clone the repo
git clone git@github.com:YOUR_ORG/task-forge.git ~/workspace/task-forge
cd ~/workspace/task-forge

# 2. Run bootstrap (installs everything, takes ~5 min)
bash scripts/bootstrap-ec2.sh

# 3. Authenticate GitHub CLI (interactive — can't be automated)
gh auth login

# 4. Install Cursor agent CLI (follow Cursor docs for headless install)
#    Binary should end up at ~/.local/bin/agent

# 5. Fill in .env (if Secrets Manager wasn't available during bootstrap)
#    Or pull from Secrets Manager if you already seeded it:
bash scripts/pull-env.sh

# 6. Start services
sudo systemctl start taskbot-web taskbot-discord taskbot-poller

# 7. Verify
bash scripts/verify-setup.sh
```

## What the Bootstrap Does

`scripts/bootstrap-ec2.sh` is idempotent — safe to re-run. It performs these steps in order:

| Step | What | Key detail |
|------|------|------------|
| 1 | System packages | python3, git, gcc, nginx, certbot, fail2ban, jq, htop, tmux |
| 2 | Node.js 22 | Via NodeSource RPM |
| 3 | pnpm | Standalone installer to `~/.local/share/pnpm` |
| 4 | GitHub CLI (gh) | From GitHub's RPM repo |
| 5 | App setup | Python venv, pip deps, frontend build, `.env` from `.env.example` |
| 5b | Secrets Manager | Auto-pulls `.env` values if AWS creds + secret exist |
| 6 | pre-commit hooks | Installs ruff, formatters, frontend lint hooks |
| 7 | SSH hardening | Pubkey only, no root login, max 3 auth tries |
| 8 | Kernel sysctl | Disable redirects, enable syncookies, log martians |
| 9 | SELinux | Set to enforcing |
| 10 | Audit rules | Identity, sudoers, SSH, cron, module, login auditing |
| 11 | AIDE | File integrity monitoring baseline |
| 12 | firewalld | Open ssh, http, https |
| 13 | fail2ban | sshd jail |
| 14 | nginx | Reverse proxy to :8080 |
| 15 | TLS | certbot with auto-renewal timer |
| 16 | systemd | `taskbot-web`, `taskbot-discord`, `taskbot-poller` services |
| 17 | Cron jobs | Task runner, healer, disk cleanup, heartbeat, PR reviewer |
| 18 | .env permissions | chmod 600 |

## Manual Steps (Cannot Be Automated)

These require interactive input or external action:

### 1. `gh auth login`

```bash
gh auth login
# Choose: GitHub.com → HTTPS → Paste a personal access token
# Token needs: repo, read:org, workflow
```

### 2. Cursor Agent CLI

The `agent` binary must be at `~/.local/bin/agent`. Install method depends on your Cursor license — follow their headless/CLI documentation. Verify with:

```bash
agent --version
```

### 3. Seed Secrets Manager (first instance only)

On the very first instance, there's no secret to pull. Fill in `.env` manually, then push:

```bash
# Edit .env with your secrets
vim .env

# Push to Secrets Manager for future instances
bash scripts/push-env.sh
```

Required `.env` values:

| Variable | Where to get it |
|----------|----------------|
| `DISCORD_BOT_TOKEN` | [Discord Developer Portal](https://discord.com/developers/applications) → Bot → Token |
| `NOTIFICATION_CHANNEL_ID` | Right-click channel in Discord → Copy Channel ID |
| `DISCORD_WEBHOOK_URL` | Server Settings → Integrations → Webhooks |
| `AUTH_EMAIL` | Your choice (web UI login) |
| `AUTH_PASSWORD` | Your choice (web UI login) |
| `AUTH_SECRET_KEY` | Generate: `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `GITHUB_WEBHOOK_SECRET` | Generate, then set in GitHub repo → Settings → Webhooks |
| `DYNAMO_TABLE` | `agent-tasks` |
| `AWS_REGION` | `us-west-2` |

## Secrets Manager Workflow

Secrets are stored as a single JSON blob in AWS Secrets Manager (`task-forge/env` in `us-west-2`). If you upgraded from an older tree name, your secret may still be `discord-task-bot/env` — pass that name as the first argument to `scripts/pull-env.sh` / `scripts/push-env.sh`, or set `SM_SECRET_NAME` during bootstrap.

```bash
# Push current .env to Secrets Manager (creates or updates)
bash scripts/push-env.sh

# Pull from Secrets Manager to .env (overlays onto .env.example template)
bash scripts/pull-env.sh

# Custom secret name
bash scripts/push-env.sh my-custom/secret-name
bash scripts/pull-env.sh my-custom/secret-name
```

The pull script preserves `.env.example` comments and structure. Commented-out keys (like `# DYNAMO_TABLE=agent-tasks`) are uncommented if the secret has a value for them. Extra keys not in `.env.example` are appended at the end.

The bootstrap script automatically tries `pull-env.sh` if AWS credentials are available.

## Verification

Run anytime to check the full setup:

```bash
bash scripts/verify-setup.sh
```

This checks 15 areas: system packages, Node/pnpm, Python venv, Cursor agent CLI, AWS/Secrets Manager, GitHub CLI auth, `.env` config, systemd services (including PATH), cron jobs, nginx/TLS/certbot renewal, firewall, security (fail2ban/SELinux/SSH), pre-commit hooks, frontend build, disk, and health endpoints.

Exit code 0 means all critical checks pass. Warnings are non-fatal.

## Lessons Learned / Gotchas

### Systemd PATH doesn't include `~/.local/bin`

Systemd services run with a minimal PATH (`/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin`). The Cursor `agent` CLI lives at `~/.local/bin/agent` and `pnpm` at `~/.local/share/pnpm/pnpm` — neither is in systemd's default PATH.

**Fix**: The service files include an explicit `Environment="PATH=..."` line. If you ever reinstall services, make sure you run `sudo bash scripts/install-systemd.sh` from the repo (the templates in `scripts/` have the fix).

The cron jobs don't have this problem — `install-cron.sh` sets `PATH=/home/ec2-user/.local/bin:...` on each entry.

### certbot renewal timer must be explicitly enabled

Amazon Linux 2023 ships a `certbot-renew.timer` systemd unit but does **not** enable it by default. Without it, the TLS cert expires silently after 90 days.

**Fix**: The bootstrap enables it. Verify with: `systemctl is-active certbot-renew.timer`

### IMDSv2 is required

AL2023 uses IMDSv2 (token-based) by default. Bare `curl http://169.254.169.254/...` calls return 401. The bootstrap and AWS SDK handle this automatically, but keep it in mind if you're debugging instance metadata.

### IAM instance profile required for DynamoDB + Secrets Manager

The EC2 instance needs an IAM role with DynamoDB and Secrets Manager access. Without it, the poller can't read tasks and `pull-env.sh` can't fetch secrets. The role is attached at instance launch via the instance profile — it can't be added later without stopping the instance (or using the AWS console/CLI from elsewhere).

### `.env` is never committed

`.env` is in `.gitignore`. It contains secrets. The source of truth for secrets is Secrets Manager — the `.env` file is a local materialization of it.

### Frontend is deployed separately

The React SPA is hosted on S3 + CloudFront (not by the EC2 FastAPI). `scripts/deploy.sh` (triggered by GitHub webhook on merge to `main`) only restarts backend services. See `docs/s3-cloudfront-deployment.md` and `docs/infra-deploy.md`.

## Service Management

```bash
# Start/stop/restart
sudo systemctl start taskbot-web taskbot-discord taskbot-poller
sudo systemctl stop taskbot-web taskbot-discord taskbot-poller
sudo systemctl restart taskbot-web

# Logs
journalctl -u taskbot-web -f
journalctl -u taskbot-poller -f --since "10 min ago"

# If port 8080 is stuck after a crash
fuser -k 8080/tcp
sudo systemctl restart taskbot-web
```

## Instance Replacement Checklist

When spinning up a new EC2 instance to replace an existing one:

1. Launch AL2023 instance with `AgentRole` instance profile
2. Update DNS A record for your API hostname
3. SSH in, clone repo, run `bash scripts/bootstrap-ec2.sh`
4. `gh auth login`
5. Install Cursor agent CLI
6. `bash scripts/pull-env.sh` (secrets come from Secrets Manager automatically)
7. `sudo systemctl start taskbot-web taskbot-discord taskbot-poller`
8. `bash scripts/verify-setup.sh` — confirm all green
9. Update `Ec2InstanceId` SST secret if the instance ID changed:
   ```bash
   # From your MacBook:
   cd infra && npx sst secret set Ec2InstanceId "i-NEW_ID" --stage production
   npx sst deploy --stage production
   ```
