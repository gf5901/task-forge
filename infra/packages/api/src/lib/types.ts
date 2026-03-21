export type TaskStatus = "pending" | "in_progress" | "in_review" | "completed" | "cancelled" | "failed";
export type TaskPriority = "low" | "medium" | "high" | "urgent";
export type TaskAssignee = "agent" | "human";

export const TASK_STATUSES: TaskStatus[] = [
  "pending",
  "in_progress",
  "in_review",
  "completed",
  "cancelled",
  "failed",
];

export const TASK_PRIORITIES: TaskPriority[] = ["low", "medium", "high", "urgent"];

export interface Task {
  id: string;
  title: string;
  description: string;
  status: TaskStatus;
  priority: TaskPriority;
  created_at: string;
  updated_at: string;
  created_by: string;
  tags: string[];
  target_repo: string;
  parent_id: string;
  model: string;
  plan_only: boolean;
  depends_on: string[];
  session_id: string;
  reply_pending: boolean;
  role: string;
  spawned_by: string;
  project_id: string;
  directive_sk: string;
  directive_date: string;
  assignee: TaskAssignee;
}

export type ProjectStatus = "active" | "paused" | "completed";

export type KPIDirection = "up" | "down" | "maintain";

export interface KPI {
  id: string;
  label: string;
  target: number;
  current: number;
  source: string;
  direction: KPIDirection;
  unit: string;
}

export interface Project {
  id: string;
  title: string;
  spec: string;
  status: ProjectStatus;
  priority: TaskPriority;
  target_repo: string;
  created_at: string;
  updated_at: string;
  awaiting_next_directive: boolean;
  active_directive_sk: string;
  kpis: KPI[];
  /** When true, daily PLAN# proposal runs (7 AM UTC cron) for approve-then-execute workflow */
  autopilot: boolean;
}

export type PlanStatus = "proposed" | "approved" | "executing" | "completed";

export interface PlanItem {
  title: string;
  description: string;
  role: string;
  priority: TaskPriority;
}

export interface DailyPlan {
  sk: string;
  plan_date: string;
  status: PlanStatus;
  reflection: string;
  human_notes: string;
  items: PlanItem[];
  task_ids: string[];
  created_at: string;
  approved_at: string | null;
  completed_at: string | null;
  outcome_summary: Record<string, number> | null;
}

export interface Directive {
  sk: string;
  author: string;
  content: string;
  created_at: string;
  task_ids: string[];
}

export interface TaskDetail extends Task {
  agent_output: string | null;
  pr_url: string | null;
  merged_at: string | null;
  deployed_at: string | null;
  deps_ready: boolean;
  subtasks: TaskListItem[];
  dep_tasks: { id: string; title: string; status: string }[];
  comments: Comment[];
  parent: { id: string; title: string } | null;
  spawned_tasks: TaskListItem[];
  spawned_by_task: { id: string; title: string } | null;
  runtime: number | null;
  tokens: TokenCounts | null;
}

export interface TaskListItem extends Task {
  deps_ready: boolean;
}

export interface Comment {
  author: string;
  body: string;
  created_at: string;
}

export interface TokenCounts {
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheWriteTokens: number;
}

export interface StatusCounts {
  all: number;
  pending: number;
  in_progress: number;
  in_review: number;
  completed: number;
  cancelled: number;
  failed: number;
  human: number;
}

export interface LogEntry {
  ts: string;
  task_id: string;
  event: string;
  stage: string;
  message: string;
  extra?: Record<string, unknown>;
}

export interface Role {
  id: string;
  label: string;
  prompt: string;
}
