# Task Forge — Product Spec

**Version:** 1.1  
**Last updated:** March 2026

---

## Overview

Task Forge is a personal AI engineering assistant that lets you describe software work in plain language and have it executed automatically. You submit a task — a bug fix, a feature, a design change, a refactor — and an AI agent reads your codebase, makes the changes, opens a pull request, and reports back. You can ask follow-up questions or request changes via comments, and the agent replies in context.

The system runs continuously in the background on a server, so tasks can be queued, prioritized, and executed while you're doing other things.

---

## Core Concepts

### Tasks

A task is a unit of work described in plain language. It has a title, an optional detailed description, a priority, and a target repository. Once created, a task moves through a lifecycle:

| Status | Meaning |
|--------|---------|
| **Pending** | Waiting to be picked up by the runner |
| **In Progress** | An agent is actively working on it |
| **In Review** | Work is done; a pull request has been opened |
| **Completed** | Done (no PR, or PR was merged/skipped) |
| **Cancelled** | Stopped before finishing |

Tasks are stored in a DynamoDB table, with each task record containing its metadata, description, agent output, and comment history.

### The Agent

Each task is executed by an AI agent (Cursor CLI) running inside an isolated copy of the target repository. The agent reads the codebase, implements the requested changes, and writes back to disk. It never touches your main working directory — all changes happen in a temporary worktree that is cleaned up after the pull request is created.

### Worktrees

Every task gets its own isolated git worktree — a full copy of the repository checked out to a fresh branch. This means multiple tasks can run in parallel on the same repo without interfering with each other, and the main checkout is never disturbed.

---

## Features

### Creating Tasks

Tasks can be created from the web UI or via Discord slash commands.

**From the web UI**, the create form accepts:

- **Title** — a short description of what needs to be done
- **Description** — optional additional context, requirements, or constraints
- **Priority** — Low, Medium, High, or Urgent (affects execution order)
- **Tags** — free-form labels for filtering and organization
- **Role** — the type of agent persona to use (e.g. Frontend Engineer, Backend Engineer, Product Designer). The agent receives a role-specific prompt that focuses its behavior.
- **Target Repo** — which repository to work in. Defaults to the task bot's own repo. Any repo in the workspace can be targeted.
- **Plan Only** — instead of executing the task immediately, the agent decomposes it into 3–10 independent subtasks that each run separately. Use this for large or complex work that would take too long in a single session.

Tasks run immediately on creation — there is no need to manually trigger them.

### The Compound Pipeline

When a task runs, it goes through a multi-stage pipeline:

1. **Plan** — A fast model reads the task and decomposes it into 2–5 numbered steps, each with a role assignment. Steps are executed as a single checklist by one agent in one session.
2. **Execute** — The agent works through the checklist in the isolated worktree. Child task records are created for each step for visibility in the UI.
3. **Update Docs** — After execution, a second fast-model pass updates any affected documentation files.
4. **Create PR** — Changes are committed, pushed to a branch, and a pull request is opened via the GitHub CLI. A PR description is auto-generated summarizing the diff.
5. **Verify PR** — A fast model reviews the diff to check it matches the task intent. Any concern is flagged in the task detail view (advisory only — does not block the PR).
6. **Cleanup** — The worktree is removed and the task is marked complete or in review.

Each stage is independently toggleable via environment configuration.

### Plan-Only Mode

For large tasks that would time out in a single session, "Plan Only" mode short-circuits the pipeline. The agent decomposes the task into 3–10 independent top-level tasks (not subtasks), each of which then runs through the full pipeline on its own. The parent task is marked completed once the breakdown is written. This is exposed as a toggle on the create form and as a "Break into Subtasks" button on cancelled tasks.

### Subtasks

When a task runs through the compound pipeline, child task records are created for each step in the plan. These are visible in the task detail view as a subtask list with individual statuses, roles, and model tiers. Subtasks link back to their parent.

### Task Dependencies

Tasks can depend on other tasks via the `depends_on` field. A task with unmet dependencies shows as "Blocked" in the UI — the Run button is disabled and a lock icon indicates which dependencies are still outstanding. When a dependency reaches completed or in-review status, any tasks waiting on it are automatically triggered to run. The task detail view shows the dependency list with current statuses and highlights blocking vs. satisfied dependencies.

### Comments and Agent Replies

Every task has a comment thread. After posting a comment, the agent automatically replies — no manual trigger needed. The agent receives the full task context (title, description, previous output) along with the latest comment, so it can answer questions accurately or suggest follow-up changes.

A spinning indicator appears while the agent is composing its reply.

### Rerun

Completed, in-review, and cancelled tasks can be reset to pending and re-executed from scratch with the Rerun button. This creates a fresh worktree on a new branch and runs the full pipeline again.

### Manual Status Control

Task status can be changed manually at any time via a dropdown on the task detail view. Setting a task to Cancelled while it is in progress will terminate the running agent process.

### Delete

Deleting a task removes the DynamoDB records, all subtask records, and asynchronously cleans up any associated git branches and worktrees. The UI returns immediately without waiting for git cleanup.

### Activity Log

The Activity page shows a real-time event log of all pipeline activity: when tasks started, which stage they're in, how long each stage took, which model was used, and token usage. Events can be filtered by task ID. Each log entry links to the relevant task.

### Token Usage

Token usage (input, output, and cached tokens) is tracked per task and displayed in the task detail view. This gives visibility into the cost and scope of each agent run.

### Deployment Status

After a PR is merged, the task detail view shows whether the changes have been deployed. A "Merged — deploying…" indicator appears when the PR is merged but deploy has not yet confirmed, and a "Deployed" badge with timestamp appears once the deploy completes.

---

## Roles

The agent can be configured to approach a task as a specific type of engineer or contributor. Roles inject a persona-specific system prompt that shapes the agent's focus and priorities. Available roles:

| Role | Focus |
|------|-------|
| Frontend Engineer | UI, components, styling, accessibility |
| Backend Engineer | APIs, data models, server-side logic |
| Fullstack Engineer | End-to-end feature work |
| Product Designer | UX, layout, visual design, design systems |
| Product Manager | Requirements, user stories, prioritization |
| DevOps Engineer | Infrastructure, CI/CD, deployment |
| Data Engineer | Pipelines, schemas, data modeling |
| Security Engineer | Auth, secrets, vulnerabilities |
| Technical Writer | Documentation, README, changelogs |
| Researcher | Analysis, investigation, summarization |
| Content Strategist | Copy, messaging, content structure |
| QA Engineer | Testing, edge cases, bug verification |
| Architect | System design, patterns, scalability |

During planning, the planner assigns a role to each step based on the nature of the work. This means a single task can have steps executed by different "specialists."

---

## Model Tiers

Three model tiers control the intelligence/cost trade-off:

| Tier | Use case |
|------|---------|
| **Fast** | Planning, doc updates, PR body generation, PR verification |
| **Default** | Standard task execution |
| **Full** | Complex tasks requiring deeper reasoning |

The planner assigns a model tier to each step. Steps are assembled into a single checklist and executed by one agent call, so the session's model tier is determined by the highest-tier step in the plan.

---

## Discord Integration

The Discord bot mirrors the web UI's functionality via slash commands. Tasks can be created, listed, and managed from any Discord channel. The bot reports task completion and PR creation back to the channel where the task was submitted.

---

## Access Control

The web UI is protected by email/password authentication. Login returns a JWT (JSON Web Token) that the frontend stores locally and sends as a Bearer token on every request. Tokens expire after 30 days. Login attempts are rate-limited to prevent brute-force attacks — credentials are compared in constant time, and repeated failures from the same email trigger a temporary lockout backed by DynamoDB with automatic TTL-based expiry.

---

## Infrastructure

- **API**: Hono (TypeScript) running on AWS Lambda behind API Gateway V2 (your deployed API hostname). Handles all `/api/*` endpoints, backed by DynamoDB.
- **Agent Execution**: Python / FastAPI on an EC2 instance. Handles GitHub webhook deploys, agent runner orchestration, and health checks. The Cursor CLI runs in isolated git worktrees on this server.
- **Frontend**: React SPA, hosted on S3 + CloudFront for global edge delivery
- **Task Storage**: DynamoDB single-table design with GSIs for status, repo, parent, and PR URL lookups
- **Monitoring**: Watchdog Lambda pings the EC2 health endpoint every 5 minutes; alerts to Discord if the server is down or disk is critically low
- **Scanning**: Repo Scanner Lambda runs hourly, checking configured GitHub repos for issues labeled `agent` and failing CI, and auto-creates tasks
- **Infrastructure as Code**: SST (v3) manages all AWS resources — DynamoDB, Lambdas, API Gateway, EventBridge schedules, Route 53 records, and ACM certificates
- **Auto-deploy**: Merging to `main` triggers backend deploy on EC2 via GitHub webhook, and frontend deploy to S3/CloudFront via GitHub Actions

---

## Budget Tracking

Daily agent costs are estimated from token usage (input, output, and cached tokens) and tracked per task. A configurable daily budget cap can be set — when the cap is reached, no new tasks are dispatched until the next day. The sidebar shows a live spend-vs-cap progress bar with color coding: indigo when healthy, yellow above 80%, red when exhausted.

---

## Automated Scanning

A Repo Scanner Lambda runs hourly and checks configured GitHub repositories for:

- **Issues** labeled with a configurable tag (e.g. `agent`) — each issue becomes a pending task
- **Failing CI** — if the default branch's latest check run is failing, a task is created to investigate and fix it

Duplicate tasks are prevented by tracking the source (issue URL or CI ref) in each task record. When a new task is created by the scanner, the EC2 runner is triggered immediately via SSM so execution begins without waiting for the next poll cycle.

---

## Health Monitoring

A Watchdog Lambda runs every 5 minutes and pings the EC2 health endpoint. If the server is unreachable or returns an error, or if disk usage exceeds configurable thresholds, an alert is sent to a Discord channel via webhook. This provides early warning of infrastructure problems without requiring manual monitoring.

---

## Limitations and Known Behaviors

- **Timeout**: Agent sessions have a maximum runtime (default 15 minutes). Long tasks may timeout and be marked cancelled. Use Plan Only mode to break large work into smaller pieces that each fit within the limit.
- **Concurrency**: By default, up to 2 tasks run simultaneously. Tasks beyond that queue as pending.
- **No undo**: There is no built-in way to revert changes an agent made. Changes live on a branch — review the PR before merging.
- **Quality depends on description**: The more specific and detailed the task description, the better the output. Vague tasks produce vague results.
- **Comment replies don't run in_progress**: If a task is actively running and you post a comment, the agent reply runs as text-only (in a temporary context) since the worktree is in use. Once the task finishes, future replies run in the worktree with full context.
