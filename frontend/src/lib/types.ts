export type TaskStatus = "pending" | "in_progress" | "in_review" | "completed" | "failed" | "cancelled"
export type TaskPriority = "low" | "medium" | "high" | "urgent"
export type TaskAssignee = "agent" | "human"

export interface Task {
  id: string
  title: string
  description: string
  status: TaskStatus
  priority: TaskPriority
  created_at: string
  updated_at: string
  created_by: string
  tags: string[]
  target_repo: string
  parent_id: string
  model: string
  plan_only: boolean
  depends_on: string[]
  deps_ready: boolean
  reply_pending: boolean
  role: string
  spawned_by: string
  project_id?: string
  directive_sk?: string
  directive_date?: string
  assignee: TaskAssignee
}

export type ProjectStatus = "active" | "paused" | "completed"

export type AutopilotMode = "daily" | "continuous"

export type CyclePauseReason = "time_expired" | "blocked" | "failures" | "manual" | ""

export type KPIDirection = "up" | "down" | "maintain"

export interface KPI {
  id: string
  label: string
  target: number
  current: number
  source: string
  direction: KPIDirection
  unit: string
}

export type PlanStatus = "proposed" | "approved" | "executing" | "completed"

export interface PlanItem {
  title: string
  description: string
  role: string
  priority: TaskPriority
}

export interface DailyPlan {
  sk: string
  plan_date: string
  status: PlanStatus
  reflection: string
  human_notes: string
  items: PlanItem[]
  task_ids: string[]
  created_at: string
  approved_at: string | null
  completed_at: string | null
  outcome_summary: Record<string, number> | null
}

export interface Project {
  id: string
  title: string
  spec: string
  status: ProjectStatus
  priority: TaskPriority
  target_repo: string
  created_at: string
  updated_at: string
  awaiting_next_directive: boolean
  active_directive_sk: string
  kpis: KPI[]
  /** Autopilot enabled; omitted on older API responses */
  autopilot?: boolean
  autopilot_mode?: AutopilotMode
  cycle_started_at?: string
  cycle_max_hours?: number
  cycle_paused?: boolean
  cycle_pause_reason?: CyclePauseReason
  cycle_feedback?: string
  next_check_at?: string
}

export interface ProjectListItem extends Project {
  task_total?: number
  task_done?: number
  last_directive_at?: string | null
}

export interface Directive {
  sk: string
  author: string
  content: string
  created_at: string
  task_ids: string[]
}

export interface TokenUsage {
  inputTokens: number
  outputTokens: number
  cacheReadTokens: number
  cacheWriteTokens: number
}

export interface TaskDetail extends Task {
  agent_output: string | null
  pr_url: string | null
  merged_at: string | null
  deployed_at: string | null
  runtime: number | null
  tokens: TokenUsage | null
  subtasks: Task[]
  comments: Comment[]
  parent?: { id: string; title: string } | null
  dep_tasks: { id: string; title: string; status: TaskStatus }[]
  spawned_tasks: Task[]
  spawned_by_task?: { id: string; title: string } | null
}

export interface Comment {
  author: string
  body: string
  created_at: string
}

export interface Counts {
  all: number
  pending: number
  in_progress: number
  in_review: number
  completed: number
  failed: number
  cancelled: number
  human: number
}
