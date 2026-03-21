# Task Forge

**Task Forge** is a Discord bot and web UI for managing coding tasks. Pending work is dispatched to the **Cursor agent CLI** on an EC2 host (or your machine): the agent runs in isolated git worktrees, can open PRs, and results surface in Discord and the UI. **Task data lives in DynamoDB** (single-table design); the UI renders descriptions and agent output as markdown.

| Doc | Purpose |
|-----|---------|
| [docs/README.md](docs/README.md) | Index of all architecture and setup guides |
| [docs/ec2-setup.md](docs/ec2-setup.md) | Full EC2 bootstrap (`scripts/bootstrap-ec2.sh`), IAM, Secrets Manager |
| [docs/local-dev.md](docs/local-dev.md) | Run tests and develop the API/UI locally |
| [docs/dynamo-schema.md](docs/dynamo-schema.md) | DynamoDB keys, GSIs, record types |
| [CONTRIBUTING.md](CONTRIBUTING.md) | PRs, Python 3.9 rules, CI |
| [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) | Community guidelines |

## Architecture (overview)

- **Discord bot** (`main.py`) — slash commands; posts notifications to a channel.
- **Web UI** — React SPA (`frontend/`) served by FastAPI (`run_web.py`) or hosted on S3 + CloudFront; talks to `/api/*`.
- **Task runner** — `run_task.py` executes one task or comment reply; **`run_poller.py`** polls DynamoDB on an interval and spawns runners for `pending` tasks and `reply_pending` threads (see `POLL_INTERVAL`, `MAX_CONCURRENT_RUNNERS`).
- **Optional AWS stack** — SST in `infra/` deploys a Lambda API, DynamoDB (if not already created), scheduled Lambdas (digest, metrics, autopilot), etc. See [docs/infra-deploy.md](docs/infra-deploy.md).

Production EC2 typically runs **systemd** units: `taskbot-web`, `taskbot-discord`, `taskbot-poller` (install with `scripts/install-systemd.sh`). Cron is still used for auxiliary jobs (healer, disk cleanup, heartbeat) — see `scripts/install-cron.sh`.

## EC2 instance setup

**Recommended:** follow **[docs/ec2-setup.md](docs/ec2-setup.md)** — clone the repo, run `bash scripts/bootstrap-ec2.sh`, authenticate `gh`, install the Cursor `agent` CLI, configure `.env`, then `sudo systemctl start taskbot-web taskbot-discord taskbot-poller`.

Minimal manual path (same AMI/key/security-group assumptions as the full guide):

### 1. Launch instance

- **AMI**: Amazon Linux 2023  
- **Instance type**: `t3.micro` or larger  
- **Key pair**: PEM for SSH  
- **Security group**: SSH (22) from your IP; later HTTP/HTTPS if you use the web UI behind nginx  
- **IAM**: Instance profile with DynamoDB access to your table and (optional) Secrets Manager — see [docs/ec2-setup.md](docs/ec2-setup.md)

### 2. Connect

```bash
ssh -i your-key.pem ec2-user@<public-ip>
```

### 3. Install tooling

```bash
sudo dnf install -y 'dnf-command(config-manager)'
sudo dnf config-manager --add-repo https://cli.github.com/packages/rpm/gh-cli.repo
sudo dnf install -y gh cronie
sudo systemctl enable --now crond

# Authenticate GitHub CLI (needed for PR creation)
gh auth login

# Cursor agent CLI (install per your Cursor license; binary is often ~/.local/bin/agent)
curl https://cursor.com/install -fsS | bash
```

### 4. Clone and Python env

```bash
mkdir -p ~/workspace && cd ~/workspace
gh repo clone <your-org>/task-forge
cd task-forge

python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt

cp .env.example .env
chmod 600 .env
# Required: DISCORD_BOT_TOKEN, NOTIFICATION_CHANNEL_ID, DYNAMO_TABLE, AWS_REGION
# AWS credentials via instance profile (recommended) or ~/.aws/credentials
```

### 5. Poller + web + bot (systemd)

```bash
sudo bash scripts/install-systemd.sh
sudo systemctl start taskbot-web taskbot-discord taskbot-poller
```

Optional: `bash scripts/install-cron.sh` for healer / maintenance crons. Do **not** rely on a minute-cron loop for dispatch — **`run_poller.py` is the supported dispatcher**.

### 6. Verify

```bash
bash scripts/verify-setup.sh   # on a bootstrapped host
journalctl -u taskbot-poller -f --since "5 min ago"
```

## Discord Bot Setup

### 1. Create the application

1. Go to [discord.com/developers/applications](https://discord.com/developers/applications) and log in
2. Click **New Application**, give it a name (e.g. "Task Bot"), and accept the ToS
3. On the **General Information** page, note the **Application ID** — you won't need it in `.env`, but it's useful for debugging OAuth URLs

### 2. Create the bot user and copy the token

1. In the left sidebar, click **Bot**
2. Click **Reset Token** (or **Add Bot** if this is a brand-new app), then **Yes, do it!**
3. Copy the token immediately — you won't be able to see it again
4. Paste it into your `.env` file as `DISCORD_BOT_TOKEN`
5. Under **Privileged Gateway Intents**, leave all three toggles **off** — this bot only uses the default intents (guilds, guild messages)

> **Keep your token secret.** If it leaks, click **Reset Token** immediately to invalidate the old one.

### 3. Configure installation settings

1. In the left sidebar, click **Installation**
2. Under **Default Install Link**, select **None** (we'll generate our own invite URL)
3. Enable **Guild Install** so the bot can be added to servers

### 4. Generate an invite URL

1. In the left sidebar, click **OAuth2 → URL Generator**
2. Set **Integration Type** to **Guild Install**
3. Under **Scopes**, check:
   - `bot`
   - `applications.commands` (required for slash commands)
4. Under **Bot Permissions**, check:
   - `View Channels` — needed to read channel info and post notifications
   - `Send Messages` — needed to reply to commands and send notifications
   - `Embed Links` — needed for the rich embed formatting used in task cards
5. Copy the generated URL at the bottom of the page

### 5. Invite the bot to your server

1. Open the URL from the previous step in your browser
2. Select the Discord server you want to add the bot to (you need **Manage Server** permission on that server)
3. Review the permissions and click **Authorize**
4. Complete the CAPTCHA if prompted

The bot will appear offline in the member list until you start it (e.g. `sudo systemctl start taskbot-discord` after [EC2 instance setup](#ec2-instance-setup)).

### 6. Get the notification channel ID

Task status-change notifications (pending → in_progress → completed, etc.) are posted to a specific channel. To get its ID:

1. In Discord, go to **Settings → Advanced** and toggle **Developer Mode** on
2. Right-click (or long-press on mobile) the channel you want notifications sent to → **Copy Channel ID**
3. Paste it into your `.env` file as `NOTIFICATION_CHANNEL_ID`

### 7. Configure `.env`

Copy the example file and fill in every value:

```bash
cp .env.example .env
chmod 600 .env
```

| Variable | Required | Description |
|----------|----------|-------------|
| `DISCORD_BOT_TOKEN` | **Yes** | Bot token from step 2 |
| `NOTIFICATION_CHANNEL_ID` | **Yes** | Channel ID from step 6 |
| `DYNAMO_TABLE` | **Yes** (production) | DynamoDB table name (e.g. `agent-tasks`). Create the table via SST/`infra/` or AWS console — see [docs/dynamo-schema.md](docs/dynamo-schema.md). |
| `AWS_REGION` | **Yes** (production) | Region for DynamoDB (e.g. `us-west-2`). |
| `POLL_INTERVAL` | No | Seconds between poller scans for pending work (default: `15`). |
| `MAX_CONCURRENT_RUNNERS` | No | Parallel agent processes (default: `2`). |
| `LOG_LEVEL` | No | Python log level — `DEBUG`, `INFO`, `WARNING`, `ERROR` (default: `INFO`) |
| `AGENT_BIN` | No | Path to the Cursor agent CLI binary (default: `agent`) |
| `WORK_DIR` | No | Working directory where the agent runs (default: `/home/ec2-user`) |
| `TASK_TIMEOUT` | No | Max seconds per agent run before timeout (default: `900`) |
| `PLAN_TIMEOUT` | No | Max seconds for the planning pass (default: `120`). |
| `AUTO_PLAN` | No | Decompose tasks into subtasks before execution (default: `true`). |
| `AUTO_DOCS` | No | Run doc-update agent step after each task (default: `true`) |
| `AUTO_PR` | No | Auto-create PRs for agent changes (default: `true`) |
| `MODEL_FAST` / `MODEL_DEFAULT` / `MODEL_FULL` | No | Cursor CLI model names; see [docs/compound-engineering.md](docs/compound-engineering.md). |
| `GH_BIN` | No | Path to GitHub CLI binary (default: `gh`) |
| `CORS_ORIGINS` | No | Comma-separated browser origins allowed by the FastAPI API (set when the SPA is on another host). |
| `BUDGET_DAILY_USD` | No | Optional daily spend cap for agent usage (0 = unlimited). |
| `DISCORD_ADMIN_ROLE` | No | Discord role name required for mutating slash commands (empty = everyone). |
| `AUTH_EMAIL` | No | Email for web UI login. Auth is enabled only when both `AUTH_EMAIL` and `AUTH_PASSWORD` are set. |
| `AUTH_PASSWORD` | No | Password for web UI login. |
| `AUTH_SECRET_KEY` | No | Secret key for signing session cookies. A random key is generated at startup if not set (sessions won't survive restarts). Generate with `python -c "import secrets; print(secrets.token_hex(32))"` |
| `HTTPS_ONLY` | No | Set session cookies with `Secure` flag (default: `true`). Set to `false` when running over plain HTTP (e.g. local development). |

**Full list and comments:** [`.env.example`](.env.example).

### 8. Verify the bot is working

1. Start the bot — e.g. `sudo systemctl start taskbot-discord` or `python main.py` — you should see `Bot online as <your-bot-name>` in the logs
2. In your Discord server, type `/task-` — the autocomplete menu should show the bot's slash commands
3. Run `/task-create` with a title to confirm task creation works
4. Check the notification channel — a status embed should appear when the task's status changes

If slash commands don't appear, try `/task-sync` or wait a minute — Discord caches commands globally and it can take up to an hour for new commands to propagate. For instant updates during development, commands are synced per-guild on bot startup via `setup_hook`.

### Troubleshooting

| Problem | Fix |
|---------|-----|
| Slash commands don't appear | Wait up to 60 minutes for global sync, or re-run `/task-sync`. Make sure `applications.commands` scope was included in the invite URL. |
| Bot is online but commands say "interaction failed" | Check the bot logs for errors. Ensure `.env` values are correct and the bot has the required channel permissions. |
| Notifications aren't posted | Verify `NOTIFICATION_CHANNEL_ID` is set to a valid channel the bot can see. Check logs for "Notification channel not found" warnings. |
| "Privileged intent" error on startup | You accidentally enabled a privileged intent in the code but not in the portal, or vice versa. This bot doesn't need any — leave all three toggles off. |
| Token errors | Regenerate the token in the developer portal and update `.env`. Tokens can expire if you reset them. |

## Instance Security Hardening

See **[docs/ec2-hardening.md](docs/ec2-hardening.md)** for SSH, firewalld, fail2ban, SELinux, audit rules, AIDE, and kernel hardening. The bootstrap script (`scripts/bootstrap-ec2.sh`) applies most of these automatically.

## Web UI

A Linear-inspired dark-theme web dashboard for managing tasks. Runs alongside the Discord bot and shares the same task store.

```bash
source .venv/bin/activate
python run_web.py
```

Open `http://<your-ip>:8080`. The UI lets you create tasks, change status, view agent output, and filter by status. Tasks are sorted newest-first by default. All changes are visible to the Discord bot and the poller-backed runner as soon as they are written to DynamoDB.

### React SPA mode (default)

When `frontend/dist/` exists (built via `cd frontend && pnpm run build`), the backend serves the React SPA automatically. Static assets are served from `/assets/` and all other non-API paths return `index.html`. The React app communicates with the backend exclusively through the JSON API (`/api/*`).

To build the frontend:

```bash
cd frontend && pnpm install && pnpm run build
```

Task descriptions, agent output, and comments are rendered as formatted markdown in the browser using `react-markdown` with `remark-gfm` and `remark-breaks` (single newlines in agent output are preserved as line breaks).

The **New Task** form includes a **Target Repo** field (bare repo name, e.g. `my-app`). Known names come from `GET /api/repos`; you can type a new name — it resolves under your workspace layout (see [docs/compound-engineering.md](docs/compound-engineering.md)).

### Authentication

The web UI supports optional email/password authentication. Set `AUTH_EMAIL` and `AUTH_PASSWORD` in `.env` to enable it. When both are set:

- **SPA mode** — only `/api/*` routes (except `/api/auth/*`) require authentication. The React app checks auth via `GET /api/auth/me` and redirects to its own login page on 401.
- **Legacy mode** — all routes redirect to `/login`. Webhook endpoints (`/webhook/*`) are always exempt.

Session cookies are valid for 30 days. Set `HTTPS_ONLY=false` in `.env` when running over plain HTTP (e.g. local development); it defaults to `true`.

If authentication is not configured (either variable is blank), the UI runs in open-access mode with no login required.

To expose it on a custom domain (e.g. `tasks.example.com`), see the [Domain setup](#domain-setup) section below.

## Slash Commands

| Command | Description |
|---------|-------------|
| `/task-create` | Create a new task (title, description, priority, tags) |
| `/task-list` | List tasks, optionally filtered by status |
| `/task-view <id>` | View full task details + agent output |
| `/task-status <id> <status>` | Update a task's status |
| `/task-delete <id>` | Delete a task |
| `/task-sync` | Re-sync slash commands with Discord |

## How It Works

1. Create a task via `/task-create`, the web UI, or the Lambda API.
2. **`run_poller.py`** (or a manual `run_task.py` invocation) picks up `pending` tasks on a short interval (`POLL_INTERVAL`, default 15s).
3. The highest-priority runnable task is claimed and handed to the Cursor agent CLI inside a **git worktree** (see [docs/compound-engineering.md](docs/compound-engineering.md)).
4. Agent output and metadata are persisted to **DynamoDB**; the UI renders them as markdown.
5. Optional follow-up steps: documentation pass (`AUTO_DOCS`), PR creation (`AUTO_PR`).
6. Status changes trigger Discord notifications for the top-level task.

**Comment replies:** posting a comment can set `reply_pending`; the poller spawns `run_task.py --reply <id>`.

**Compound pipeline** details, model tiers, and worktree rules: [docs/compound-engineering.md](docs/compound-engineering.md).

- **Statuses:** `pending` → `in_progress` → `in_review` / `completed` / `failed` / `cancelled` (see `src/task_store.py`).
- **Priorities:** `low`, `medium`, `high`, `urgent`.

## Task record shape (DynamoDB / UI)

Tasks are stored in DynamoDB (`DYNAMO_TABLE`). The UI and API expose fields that correspond to the historical markdown task format. Example:

```markdown
---
id: a1b2c3d4
status: pending
priority: medium
created_at: 2026-03-15T12:00:00+00:00
updated_at: 2026-03-15T12:00:00+00:00
created_by: user#1234
tags: [backend, urgent]
role: fe_engineer
---

# My task title

Detailed description goes here.
```

Optional fields include `model`, `target_repo`, `parent_id`, `plan_only`, `project_id`, `directive_sk`, `reply_pending`, `assignee`, etc. Full schema: [docs/dynamo-schema.md](docs/dynamo-schema.md).

## Domain Setup

To serve the web UI at a custom domain like `tasks.example.com`:

### 1. Assign an Elastic IP

Attach an Elastic IP to the EC2 instance so the public IP doesn't change on reboot.

### 2. DNS record

In your DNS provider (Route 53, Cloudflare, etc.), add an **A record**:

| Name | Type | Value |
|------|------|-------|
| `tasks.example.com` | A | `<elastic-ip>` |

### 3. Install nginx + certbot

```bash
sudo dnf install -y nginx certbot python3-certbot-nginx
sudo systemctl enable --now nginx
```

### 4. Configure nginx reverse proxy

```bash
sudo tee /etc/nginx/conf.d/taskagent.conf > /dev/null << 'EOF'
server {
    listen 80;
    server_name tasks.example.com;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
EOF
sudo nginx -t && sudo systemctl reload nginx
```

### 5. TLS certificate

```bash
sudo certbot --nginx -d tasks.example.com
```

Certbot will automatically update the nginx config and set up auto-renewal.

### 6. Open port 443 in the security group

In the AWS console, add inbound rules to the instance's security group:

| Port | Protocol | Source |
|------|----------|--------|
| 80   | TCP      | `0.0.0.0/0` |
| 443  | TCP      | `0.0.0.0/0` |

After this, `https://tasks.example.com` will serve the web UI with TLS.

## AWS infrastructure (Lambda API, DynamoDB, crons)

SST configuration is in `infra/`. **Do not commit** account-specific domains, ARNs, or IDs. Copy `infra/.env.example` to `infra/.env` and fill in values listed in **`docs/infra-deploy.md`** before running `npx sst deploy`.

The React SPA is usually deployed to **S3 + CloudFront** via `.github/workflows/deploy-ui.yml`. Configure the repository secrets described in `docs/infra-deploy.md` (e.g. `VITE_API_BASE_URL`, `AWS_OIDC_ROLE_ARN`, `DEPLOY_UI_S3_BUCKET`, `DEPLOY_UI_CLOUDFRONT_ID`).

## Auto-Deploy (EC2 backend)

Merging to `main` can trigger an automatic **server** deploy via a GitHub webhook. The webhook sends a `POST` request to `/webhook/github`, which the FastAPI backend verifies using `GITHUB_WEBHOOK_SECRET` and then runs `scripts/deploy.sh`.

The deploy script pulls the latest code, installs Python dependencies, and restarts the web UI, Discord bot, and poller. It does **not** build the React SPA for S3 — see `docs/s3-cloudfront-deployment.md` and the workflow above.

All output is appended to `deploy.log` in the project root.

Set `GITHUB_WEBHOOK_SECRET` in `.env` to match the secret configured in the GitHub repository's webhook settings.
