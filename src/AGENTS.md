# Backend — Python / FastAPI

## Stack

Python 3.9, FastAPI, Starlette, uvicorn, discord.py, python-dotenv, itsdangerous, PyYAML.

## Python 3.9 Compatibility

This runs on Amazon Linux 2023 with Python 3.9. Do **not** use:

- `str | Path` — use `Union[str, Path]` from `typing`
- `list[str]`, `dict[str, int]` — use `List[str]`, `Dict[str, int]` from `typing`
- `match` statements — use `if/elif` chains
- Walrus operator in complex expressions — keep assignments simple

## File Structure

```
web.py          — FastAPI app: JSON API (/api/*), SPA serving, auth middleware
bot.py          — Discord bot with slash commands (discord.py)
task_store.py   — Shared task types (Task, enums, Comment)
dynamo_store.py — DynamoDB-backed task store (`DynamoTaskStore`)
projects_dynamo.py — DynamoDB helpers for project records (get_project, update_project, directives, PLAN# plans (date or UTC datetime suffix), MEMORY# agent notes; `resolve_memory_by_ref` for CLI lookup by sk/suffix)
objectives.py   — Daily KPI cycle: lean prompt + `./ctx` CLI + optional read-only repo worktree; `run_daily_cycle()`
context_cli.py  — Dynamo-backed context tool for agents (`./ctx spec|kpis|snapshots|tasks|proposals|human-tasks|plans|memory`); also exports `write_ctx_script()` to inject `./ctx` into any agent working dir
autopilot.py    — Autopilot plan proposal (daily vs continuous): lean prompt + `./ctx` CLI (`propose_daily_plan`; EC2 `run_task.py --propose-plan [--regenerate] [--plan-suffix]`)
pipeline.py     — Task orchestration: plan → execute → PR → cleanup; run_directive() for project directives
runner.py       — Entry point: slot locking, signal handling, delegates to pipeline
agent.py        — Agent CLI execution, output parsing, prompt building (includes project context injection)
roles.py        — Predefined agent roles (id, label, system prompt); get_role_prompt() used by runner
watcher.py      — Polls DynamoDB store for status changes, fires callbacks
```

## Task Store

Tasks live in DynamoDB (`agent-tasks` single-table design with GSIs), accessed via `dynamo_store.DynamoTaskStore`. Shared types and enums are in `task_store.py`. See `docs/dynamo-schema.md` for the full schema (all record types, GSIs, attributes, access patterns). Record fields include: `id`, `status`, `priority`, `created_at`, `updated_at`, `created_by`, `tags`, `parent_id`, `model`, `target_repo`, `reply_pending`, `role`, `project_id`, `directive_sk`, `directive_date`.

- Statuses: `pending`, `in_progress`, `in_review`, `completed`, `cancelled`
- Priorities: `low`, `medium`, `high`, `urgent`
- Model tiers: `fast`, `default`, `full`
- Agent output appended as `## Agent Output (timestamp)` sections
- Comments appended as `## Comment (timestamp) by author` sections

## Web API

All frontend communication goes through `/api/*` endpoints. Key routes:

- `GET /api/tasks?status=` — list tasks (excludes subtasks)
- `GET /api/tasks/{id}` — detail with subtasks, comments, agent output, PR URL
- `POST /api/tasks` — create task (`role` field selects a predefined agent role)
- `PATCH /api/tasks/{id}/status` — update status
- `POST /api/tasks/{id}/run` — trigger agent runner
- `POST /api/tasks/{id}/comment` — add comment; automatically triggers an agent reply (sets `reply_pending: true`, fires `run_task.py --reply` in background)
- `POST /api/tasks/{id}/reply` — manually trigger an agent reply
- `DELETE /api/tasks/{id}` — delete task
- `GET /api/roles` — list predefined agent roles (id, label, prompt)
- `GET /api/projects` — list projects (filterable by status)
- `GET /api/projects/{id}` — project detail with spec, directives, tasks, progress
- `POST /api/projects` — create project (title, spec, priority, target_repo)
- `PATCH /api/projects/{id}` — update spec, status, title, priority
- `DELETE /api/projects/{id}` — delete project + associated directives and tasks
- `POST /api/projects/{id}/directive` — post a directive; triggers directive decomposition on EC2
- `GET /api/projects/{id}/plans` — list recent daily autopilot plans
- `GET /api/projects/{id}/plans/{date}` — get plan detail with associated tasks
- `PATCH /api/projects/{id}/plans/{date}` — edit plan items (while proposed)
- `POST /api/projects/{id}/plans/{date}/approve` — approve plan and create tasks
- `POST /api/projects/{id}/plans/{date}/regenerate` — re-trigger plan proposal for today

Auth endpoints: `POST /api/auth/login`, `GET /api/auth/me`, `POST /api/auth/logout`.

## Agent Scripts

Helper scripts are in `scripts/agent/` — call these instead of re-implementing the logic:

```bash
# Confirm you're in an isolated worktree before making any changes (do this first)
bash scripts/agent/validate-worktree.sh

# Run all checks after making changes (pytest, ruff, tsc, eslint, npm build)
bash scripts/agent/build-check.sh

# Stage all changes, commit, push, and open a PR
bash scripts/agent/commit-pr.sh "task(abc123): short description"
# Or omit the message — it's auto-derived from the branch name
bash scripts/agent/commit-pr.sh
```

These scripts are located at `<repo-root>/scripts/agent/`. From within a worktree the path is the same since the worktree is a full checkout.

## Runner Pipeline

See `docs/compound-engineering.md` for the full pipeline. Key points:

- Each task runs in a git worktree at `/tmp/task-worktrees/task-<id>`
- Worktrees auto-install deps: hard-links `node_modules` from the main checkout (fast, isolated); falls back to `pnpm install` if unavailable
- `AUTO_PLAN=true` decomposes tasks into subtasks via a fast model
- Planning returns 1 subtask → runs directly (skips subtask overhead)
- `AUTO_DOCS=true` runs a doc-update pass after execution
- `AUTO_PR=true` commits, pushes, and opens a PR via `gh` CLI (agent-generated summaries)
- Model routing: `MODEL_FAST` for planning/docs, `MODEL_DEFAULT` for standard tasks, `MODEL_FULL` for complex work
- If a task has a `role` set, `build_prompt()` prepends `"You are acting as a <label>. <role prompt>"` before the task content — giving the agent a persona for that task
- If a task has a `project_id`, `build_prompt()` prepends the project's spec as a "Project context" section so the agent has the evolving product/technical spec
- Every stage is logged to `pipeline.log` (JSONL) with runtime tracking
- `GET /api/logs?task_id=` exposes logs to the UI

## Conventions

- All config from `.env` via `os.getenv()` — never hardcode values
- New dependencies must be added to `requirements.txt` with version pins
- After changing `web.py`, restart via `sudo systemctl restart taskbot-web`
- `runner.py` and `dynamo_store.py` don't need restarts (spawned per-task)
- Register new slash commands inside `TaskBot._register_commands()` using `@self.tree.command()`
