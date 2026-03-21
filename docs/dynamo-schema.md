# DynamoDB Schema — `agent-tasks` table

Single-table design. All record types share one table with `pk`/`sk` composite keys.

## Table Settings

| Setting | Value |
|---------|-------|
| Billing | PAY_PER_REQUEST (on-demand) |
| PITR | Enabled |
| TTL attribute | `ttl` (epoch seconds) |

## Global Secondary Indexes

| Name | PK | SK | Purpose |
|------|----|----|---------|
| status-index | `status` | `priority_sort_created` | Tasks by status, sorted by priority then created |
| repo-index | `target_repo` | `priority_sort_created` | Tasks by repo |
| parent-index | `parent_id` | `priority_sort_created` | Subtasks of a parent |
| pr-index | `pr_url` | `pk` | Task lookup by PR URL |
| project-index | `project_id` | `priority_sort_created` | Tasks linked to a project |
| project-list-index | `proj_status` | `project_updated` | Projects by status |

All GSIs use ALL projection (default).

## `priority_sort_created` format

`{n}#{created_at}` where n: 0=urgent, 1=high, 2=medium, 3=low. Example: `1#2025-03-20T14:30:00+00:00`. Sorting ascending gives urgent→low, newest→oldest within each priority.

---

## Record Types

### Task META — `pk=TASK#{id}  sk=META`

Core task record. `id` is 8-char hex.

| Attribute | Type | Description |
|-----------|------|-------------|
| task_id | string | Task ID |
| title | string | Task title |
| description | string | Markdown body |
| status | string | `pending` · `in_progress` · `in_review` · `completed` · `failed` · `cancelled` |
| priority | string | `low` · `medium` · `high` · `urgent` |
| priority_sort_created | string | GSI sort key (see above) |
| created_at | string | ISO 8601 |
| updated_at | string | ISO 8601 |
| created_by | string | Creator name |
| tags | list\<string\> | Tags |
| target_repo | string? | Repo name (bare, not path) |
| parent_id | string? | Parent task ID |
| model | string? | `fast` · `default` · `full` |
| plan_only | bool? | Decompose only, don't execute |
| depends_on | list\<string\>? | Dependency task IDs |
| session_id | string? | Cursor agent session ID |
| reply_pending | bool? | Agent reply in progress |
| role | string? | Agent role ID |
| spawned_by | string? | Task that created this one |
| project_id | string? | Linked project ID |
| directive_sk | string? | `DIR#<iso>` or `PLAN#<date>` |
| directive_date | string? | Date string for directive/plan |
| assignee | string? | `agent` (default) · `human` |
| pr_url | string? | Pull request URL |
| merged_at | string? | PR merge timestamp |
| deployed_at | string? | Deploy timestamp |
| cancelled_by | string? | `user` · `directive` |

**GSIs:** status-index (always), repo-index (if target_repo), parent-index (if parent_id), pr-index (if pr_url), project-index (if project_id).

**Key access patterns:**
- Get by ID: `GetItem(pk=TASK#id, sk=META)`
- List by status: `Query status-index(status=X)` — sorted by priority then created_at
- Subtasks: `Query parent-index(parent_id=X)`
- By project: `Query project-index(project_id=X)`
- By PR: `Query pr-index(pr_url=X)`
- Pending pick: `Query status-index(status=pending)` → filter paused projects, awaiting batches

---

### Task OUTPUT — `pk=TASK#{id}  sk=OUTPUT#{iso}`

Agent output sections appended during execution.

| Attribute | Type | Description |
|-----------|------|-------------|
| section | string | Section heading (e.g. "Agent Output", "Doc Update") |
| body | string | Markdown content |
| created_at | string | ISO 8601 |

**Access:** `Query pk, sk begins_with "OUTPUT#"` — `ScanIndexForward=false, Limit=1` for latest.

---

### Task COMMENT — `pk=TASK#{id}  sk=COMMENT#{iso_micro}`

Comments from humans and agent. Microsecond precision in sk for ordering.

| Attribute | Type | Description |
|-----------|------|-------------|
| author | string | Human name or `"agent"` |
| body | string | Markdown content |
| created_at | string | ISO 8601 |

**Access:** `Query pk, sk begins_with "COMMENT#"` — `ScanIndexForward=true` (chronological).

---

### Task PLAN — `pk=TASK#{id}  sk=PLAN#{iso}`

Plan decomposition results (subtask listings).

| Attribute | Type | Description |
|-----------|------|-------------|
| section | string | Section heading |
| body | string | Markdown content |
| created_at | string | ISO 8601 |

---

### Task LOG — `pk=TASK#{id}  sk=LOG#{iso}`

Pipeline events (structured logging).

| Attribute | Type | Description |
|-----------|------|-------------|
| event | string | `task_start` · `plan_done` · `execute_done` · `pr_created` · `reply_done` · … |
| stage | string? | `execute` · `pipeline` · `plan` · `pr` · … |
| message | string? | Human-readable message |
| created_at | string | ISO 8601 |
| runtime | number? | Stage duration in seconds |
| model | string? | Model used |
| parent_id | string? | Parent task ID |
| priority | string? | Task priority |
| inputTokens | number? | Token usage |
| outputTokens | number? | Token usage |
| cacheReadTokens | number? | Token usage |
| cacheWriteTokens | number? | Token usage |

**Access:** `Query pk, sk begins_with "LOG#"` — `ScanIndexForward=false` for most recent first. All-logs scan: `Scan` with `sk begins_with "LOG#"`, sort by `created_at`.

---

### PROJECT — `pk=PROJECT#{id}  sk=PROJECT`

Project metadata. `id` is 8-char hex.

| Attribute | Type | Description |
|-----------|------|-------------|
| project_id | string | Project ID |
| title | string | Project title |
| spec | string | Product/technical spec (markdown) |
| proj_status | string | `active` · `paused` · `completed` |
| priority | string | `low` · `medium` · `high` · `urgent` |
| target_repo | string? | Repo name |
| created_at | string | ISO 8601 |
| updated_at | string | ISO 8601 |
| project_updated | string | Duplicate of updated_at for project-list-index SK |
| awaiting_next_directive | bool? | All tasks in current batch are terminal |
| active_directive_sk | string? | Currently executing `DIR#` or `PLAN#` sk |
| kpis | list\<KPI\>? | KPI definitions (id, name, source, metric, target, direction, current) |
| autopilot | bool? | Enable autopilot plan proposals |
| autopilot_mode | string? | `daily` (human approve, 07 UTC) or `continuous` (auto-approve, hourly tick while cycle active) |
| cycle_started_at | string? | ISO start of current continuous cycle |
| cycle_max_hours | number? | Wall-clock hours per cycle (default 24) |
| cycle_paused | bool? | Waiting for human review / manual stop |
| cycle_pause_reason | string? | `time_expired` · `blocked` · `failures` · `manual` |
| cycle_feedback | string? | Human notes for next planner pass |
| next_check_at | string? | Agent-requested deferral (ISO); empty if none |

**GSI:** project-list-index (`proj_status`, `project_updated`).

**Access:**
- Get: `GetItem(pk=PROJECT#id, sk=PROJECT)`
- List active: `Query project-list-index(proj_status=active)` — sorted by updated desc

---

### Directive — `pk=PROJECT#{id}  sk=DIR#{iso}`

Human-authored daily directives.

| Attribute | Type | Description |
|-----------|------|-------------|
| author | string | Who posted it |
| content | string | Directive text |
| created_at | string | ISO 8601 |
| task_ids | list\<string\> | Task IDs created from decomposition |

**Access:** `Query pk, sk begins_with "DIR#"` — `ScanIndexForward=true`.

---

### SNAPSHOT — `pk=PROJECT#{id}  sk=SNAPSHOT#{YYYY-MM-DD}`

Daily metric readings from the Metrics Lambda.

| Attribute | Type | Description |
|-----------|------|-------------|
| date | string | YYYY-MM-DD |
| kpi_readings | map | `{kpi_id: number|null}` |
| reflection | string? | Agent reflection from daily cycle |
| created_at | string | ISO 8601 |

**Access:** `Query pk, sk begins_with "SNAPSHOT#"` — `ScanIndexForward=false, Limit=14`.

---

### PROP (Proposal) — `pk=PROJECT#{id}  sk=PROP#{date}#{id}`

Proposals from the autonomous daily cycle. 7-day TTL.

| Attribute | Type | Description |
|-----------|------|-------------|
| status | string | `pending` · `approved` · `rejected` |
| action | string | What to do |
| rationale | string | Why |
| domain | string | `code` · `content` · `seo` · … |
| target_kpi | string | KPI ID this targets |
| created_at | string | ISO 8601 |
| ttl | number | Epoch seconds — 7 days from creation |
| feedback | string? | Rejection feedback |
| task_id | string? | Task created on approval |
| outcome | string? | Task outcome after completion |

**Access:** `Query pk, sk begins_with "PROP#"` — optional filter on `status`.

---

### MEMORY — `pk=PROJECT#{id}  sk=MEMORY#<iso8601-microseconds>`

Durable notes written by the daily-cycle agent via `./ctx memory save` (max 50 per project; oldest pruned after each save). No TTL.

| Attribute | Type | Description |
|-----------|------|-------------|
| content | string | Insight text (max 2000 chars) |
| cycle_date | string | YYYY-MM-DD when saved |
| created_at | string | ISO 8601 with microseconds |

**Access:** `Query pk, sk begins_with "MEMORY#"` — `ScanIndexForward=false` for newest first.

---

### PLAN (Autopilot Plan) — `pk=PROJECT#{id}  sk=PLAN#…`

Autopilot plans. Sort key is either legacy `PLAN#YYYY-MM-DD` (one per calendar day) or `PLAN#YYYY-MM-DDTHH:MM:SS` (UTC, multiple per day in continuous mode). `plan_date` is always calendar `YYYY-MM-DD` for grouping.

| Attribute | Type | Description |
|-----------|------|-------------|
| plan_date | string | YYYY-MM-DD (calendar day for the plan) |
| status | string | `proposed` · `approved` · `executing` · `completed` |
| reflection | string? | Agent's reasoning |
| human_notes | string? | Human notes on approval |
| items | list\<PlanItem\> | Work items: `{title, description, role, priority}` |
| task_ids | list\<string\> | Task IDs created on approval |
| created_at | string | ISO 8601 |
| updated_at | string | ISO 8601 |
| approved_at | string? | When approved |
| completed_at | string? | When all tasks finished |
| outcome_summary | map? | `{completed: N, in_review: N, failed: N, cancelled: N}` |

**Access:**
- Get: `GetItem(pk=PROJECT#id, sk=PLAN#<suffix>)` — suffix is date or `YYYY-MM-DDTHH:MM:SS`
- List: `Query pk, sk begins_with "PLAN#"` — `ScanIndexForward=false, Limit=14`

---

### CONFIG — `pk=CONFIG#GLOBAL  sk=SETTINGS`

Runtime configuration (single record).

| Attribute | Type | Description |
|-----------|------|-------------|
| max_concurrent_runners | number? | 1–4, overrides env var |
| min_spawn_interval | number? | Seconds between poller spawns |
| task_timeout | number? | Task execution timeout in seconds |
| budget_daily_usd | number? | Daily budget cap |

---

### RATELIMIT — `pk=RATELIMIT#{ip}  sk=LOGIN`

Login rate limiting. Auto-expires via TTL.

| Attribute | Type | Description |
|-----------|------|-------------|
| attempts | number | Failed login count in window |
| ttl | number | Epoch seconds (15-minute window) |
