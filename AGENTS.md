# Task Forge

Discord bot + web UI that manages coding tasks executed by AI agents via the Cursor CLI.

## Stack

- **Backend**: Python 3.9, FastAPI, uvicorn (EC2); Hono TypeScript API on Lambda (API Gateway)
- **Frontend**: React, TypeScript, Vite, Tailwind CSS v4, shadcn/ui, Lucide icons
- **Task storage**: DynamoDB (`agent-tasks` table) — single-table design with GSIs. See `docs/dynamo-schema.md`.
- **Agent execution**: Cursor CLI (`agent -p --force`) in isolated git worktrees
- **Infrastructure**: Amazon Linux 2023, nginx, certbot, firewalld, Fail2Ban, systemd, SST (IaC), Lambda, API Gateway, DynamoDB

## Key Constraints

- **Python 3.9** — no `X | Y` unions, no `list[str]`, no `match`. Use `typing` imports.
- **All config in `.env`** — never hardcode tokens, secrets, or paths. See `.env.example`.
- **Tasks run in worktrees** at `/tmp/task-worktrees/task-<id>`, branched from `origin/main`. The main checkout is never disturbed.
- **`AUTH_SECRET_KEY`** must be set in `.env` for sessions to survive restarts.

## Directory Layout

```
src/              → Python backend (FastAPI, bot, runner, task store) — see src/AGENTS.md
frontend/         → React SPA — see frontend/AGENTS.md
infra/            → SST IaC: DynamoDB, Lambda API (Hono/TypeScript), Watchdog, Digest, Metrics, Autopilot (`docs/infra-deploy.md` for deploy env vars)
docs/             → Architecture docs (compound pipeline, etc.)
scripts/          → Deploy and utility scripts
.cursor/rules/    → Cursor IDE-specific rules (glob-scoped, frontmatter)
```

## Pipeline Overview

Tasks flow through: **create worktree → plan subtasks → execute → update docs → commit & PR → cleanup**. Each stage is optional and togglable. See `docs/compound-engineering.md` for details.

**Directive pipeline**: Projects have evolving specs and accept daily directives. `POST /api/projects/:id/directive` triggers `run_task.py --directive` on EC2, which decomposes the directive + spec into independent tasks (plan-only style). Tasks carry `project_id`, `directive_sk`, and `directive_date`. When all tasks in a directive batch reach terminal status, the project is flagged `awaiting_next_directive`. The runner skips tasks for paused projects and completed directive batches.

**Autonomous objectives pipeline**: Projects with KPIs get a daily autonomous cycle. The Metrics Lambda (`infra/packages/metrics/`) runs at 6 AM UTC, fetches metrics (PageSpeed Insights, GitHub, optionally GA4/Search Console), writes SNAPSHOT records, and triggers `run_task.py --daily-cycle <project_id>` on EC2 via SSM. The daily cycle (`src/objectives.py`) loads metric history, proposals, and recent tasks, then calls the agent (opus) with a reflection prompt. The agent returns structured proposals and human requests. Proposals queue for human approval in the web UI; approved proposals become tasks in the existing pipeline. See `docs/autonomous-objectives.md`.

**Autopilot pipeline**: Projects with `autopilot: true` use `autopilot_mode` **`daily`** (default) or **`continuous`**. The Autopilot Lambda (`infra/packages/autopilot/`) runs **hourly**; **daily** projects are only dispatched at **07:00 UTC** (filtered in Lambda; EC2 enforces the same). **Continuous** projects run while a cycle is active (`cycle_started_at`, `cycle_max_hours`, not `cycle_paused`). `run_task.py --propose-plan <project_id> [--regenerate] [--plan-suffix]` on EC2 calls `src/autopilot.py`, which uses the agent (opus) and `./ctx`. **Daily**: `PLAN#YYYY-MM-DD`, human approves in the UI. **Continuous**: `PLAN#YYYY-MM-DDTHH:MM:SS` UTC, auto-approved tasks, pauses + Discord on time window, failures, or human-only backlog. Plan tasks use `directive_sk=PLAN#…`. Directives cancel pending plan tasks. The same hourly Lambda also triggers **PM sweeps** for projects with `reply_pending: true` — processing queued chat messages and human-assigned task replies in a single agent session (`run_task.py --pm-reply <project_id>`).

Every pipeline event is logged to `pipeline.log` (structured JSONL) with timestamps, task IDs, stages, runtimes, and model info. The Activity page (`/activity`) exposes this in the UI.

## Task dispatch — EC2 polling

The Lambda API does **not** trigger task execution directly. It writes to DynamoDB (creates tasks as `pending`, sets `reply_pending: true` for comments) and returns. A polling daemon on EC2 (`run_poller.py`) checks DynamoDB every `POLL_INTERVAL` seconds (default 15) and spawns `run_task.py` subprocesses for pending tasks and reply-pending comments (excluding human-assigned tasks with a project, which are handled by the PM sweep).

- **EC2**: Set `DYNAMO_TABLE=agent-tasks` in `.env`. Run `run_poller.py` as a systemd service.
- **SSM**: Used for `cancelRunner` (SIGTERM to kill a running agent), directive decomposition, daily cycle triggers, autopilot plan proposals, and PM sweeps. `EC2_INSTANCE_ID` (or SST secret `Ec2InstanceId`) is required for these operations.
- **Activity**: Pipeline events are written to DynamoDB, so the Activity page (which reads from Dynamo via the API) shows task_start, execute_done, etc.

## Python tests

- Install dev deps: `pip install -r requirements.txt`
- **Faster runs** (used in CI and `scripts/agent/build-check.sh`): `pytest tests/ -n auto` (`pytest-xdist`). Sequential for debugging: `pytest tests/ -n0` or `pytest tests/ --pdb`

## Boundaries

- Do not modify `.env` (contains secrets)
- Do not commit `deploy.log`, `frontend/dist/`, or `frontend/node_modules/`
- Do not push directly to `main` — always use PRs
- Restart the web UI after changing `src/web.py` or frontend build
