import type {
  Task,
  TaskDetail,
  Counts,
  Project,
  ProjectListItem,
  Directive,
  ProjectStatus,
  TaskPriority,
  KPI,
  DailyPlan,
  PlanItem,
} from "./types"

const BASE = (import.meta.env.VITE_API_BASE_URL ?? "") + "/api"

const TOKEN_KEY = "auth_token"

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token)
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY)
}

async function request<T>(path: string, opts?: RequestInit): Promise<T> {
  const token = getToken()
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...((opts?.headers as Record<string, string>) ?? {}),
  }
  if (token) {
    headers["Authorization"] = `Bearer ${token}`
  }

  const res = await fetch(`${BASE}${path}`, {
    cache: "no-store",
    ...opts,
    headers,
  })
  if (res.status === 401 && !path.startsWith("/auth/")) {
    clearToken()
    window.location.reload()
    throw new Error("Session expired")
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.error || `Request failed: ${res.status}`)
  }
  return res.json()
}

export async function fetchTasks(status = "all", limit = 25, offset = 0) {
  const params = new URLSearchParams({ status, limit: String(limit), offset: String(offset) })
  return request<{ tasks: Task[]; total: number; counts: Counts }>(`/tasks?${params}`)
}

export async function fetchTask(id: string) {
  return request<TaskDetail>(`/tasks/${id}`)
}

export async function createTask(data: {
  title: string
  description?: string
  priority?: string
  tags?: string
  target_repo?: string
  plan_only?: boolean
  role?: string
  model?: string
  assignee?: string
}) {
  return request<Task>("/tasks", { method: "POST", body: JSON.stringify(data) })
}

export async function fetchRoles() {
  return request<{ roles: { id: string; label: string; prompt: string }[] }>("/roles")
}

export async function updateTaskStatus(id: string, status: string) {
  return request<Task>(`/tasks/${id}/status`, {
    method: "PATCH",
    body: JSON.stringify({ status }),
  })
}

export async function runTask(id: string) {
  return request<{ ok: boolean }>(`/tasks/${id}/run`, { method: "POST" })
}

export async function rerunTask(id: string) {
  return request<{ ok: boolean }>(`/tasks/${id}/rerun`, { method: "POST" })
}

export async function addComment(id: string, body: string) {
  return request<{ author: string; body: string; created_at: string }>(
    `/tasks/${id}/comment`,
    { method: "POST", body: JSON.stringify({ body }) },
  )
}

export async function triggerReply(id: string) {
  return request<{ ok: boolean; message: string }>(`/tasks/${id}/reply`, { method: "POST" })
}

export async function replanTask(id: string) {
  return request<{ ok: boolean; message: string }>(`/tasks/${id}/replan`, { method: "POST" })
}

export async function deleteTask(id: string) {
  return request<{ ok: boolean }>(`/tasks/${id}`, { method: "DELETE" })
}

export async function fetchRepos() {
  return request<{ repos: string[] }>("/repos")
}

export async function fetchCounts() {
  return request<Counts>("/counts")
}

export async function triggerHeal() {
  return request<{ stale_reset: number; prs_created: number; cancelled_recovered: number; total: number }>("/heal", { method: "POST" })
}

export async function login(email: string, password: string) {
  const res = await request<{ ok: boolean; auth_enabled?: boolean; token?: string }>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  })
  if (res.token) {
    setToken(res.token)
  }
  return res
}

export async function checkAuth() {
  return request<{ authenticated: boolean; auth_enabled: boolean; email?: string }>("/auth/me")
}

export async function logout() {
  await request<{ ok: boolean }>("/auth/logout", { method: "POST" }).catch(() => {})
  clearToken()
}

export interface LogEntry {
  ts: string
  task_id: string
  event: string
  stage: string
  message: string
  extra?: Record<string, string | number>
}

export async function fetchLogs(taskId?: string, limit = 200, offset = 0) {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) })
  if (taskId) params.set("task_id", taskId)
  return request<{ entries: LogEntry[]; count: number }>(`/logs?${params}`)
}

export interface BudgetStatus {
  daily_cap_usd: number
  spent_today_usd: number
  remaining_usd: number
  budget_enabled: boolean
}

export async function fetchBudget() {
  return request<BudgetStatus>("/budget")
}

export interface Settings {
  max_concurrent_runners: number
  min_spawn_interval: number
  task_timeout: number
  budget_daily_usd: number
}

export async function fetchSettings() {
  return request<Settings>("/settings")
}

export async function patchSettings(patch: Partial<Settings>) {
  return request<Settings>("/settings", {
    method: "PATCH",
    body: JSON.stringify(patch),
  })
}

export interface StatsData {
  today: { inputTokens: number; outputTokens: number; cacheReadTokens: number; cacheWriteTokens: number; cost_usd: number }
  all_time: { inputTokens: number; outputTokens: number; cacheReadTokens: number; cacheWriteTokens: number; cost_usd: number }
  daily: { date: string; cost_usd: number; tokens: number }[]
}

export async function fetchStats() {
  return request<StatsData>("/stats")
}

export interface HealthStatus {
  status: string
  uptime_seconds?: number
  disk_free_pct?: number
  disk_free_bytes?: number
  disk_total_bytes?: number
  last_runner_ts?: string
  error?: string
}

export async function fetchHealth(): Promise<HealthStatus> {
  const token = getToken()
  const headers: Record<string, string> = { "Content-Type": "application/json" }
  if (token) headers["Authorization"] = `Bearer ${token}`
  try {
    const res = await fetch(`${BASE}/health`, { headers, cache: "no-store" })
    return await res.json()
  } catch {
    return { status: "unreachable", error: "Network error" }
  }
}

export async function fetchProjects(status?: ProjectStatus) {
  const q = status ? `?status=${encodeURIComponent(status)}` : ""
  return request<{ projects: ProjectListItem[] }>(`/projects${q}`)
}

export async function fetchProjectDetail(id: string) {
  return request<{
    project: Project
    directives: Directive[]
    tasks: Task[]
    progress: { total: number; done: number }
  }>(`/projects/${id}`)
}

export async function createProject(data: {
  title: string
  spec?: string
  priority?: TaskPriority
  target_repo?: string
  status?: ProjectStatus
  autopilot?: boolean
}) {
  return request<Project>("/projects", {
    method: "POST",
    body: JSON.stringify(data),
  })
}

/** Draft or refine project spec via Lambda + Bedrock (batch, not streaming). */
export async function generateProjectSpec(prompt: string, existingSpec?: string) {
  return request<{ spec: string }>("/projects/generate-spec", {
    method: "POST",
    body: JSON.stringify({
      prompt,
      ...(existingSpec !== undefined && existingSpec !== ""
        ? { existing_spec: existingSpec }
        : {}),
    }),
  })
}

/** Generate KPI suggestions for an existing project via Bedrock. */
export async function generateProjectKPIs(projectId: string) {
  return request<{ kpis: KPI[] }>(`/projects/${projectId}/generate-kpis`, {
    method: "POST",
    body: JSON.stringify({}),
  })
}

export async function patchProject(
  id: string,
  body: Partial<{
    title: string
    spec: string
    status: ProjectStatus
    priority: TaskPriority
    target_repo: string
    kpis: KPI[]
    autopilot: boolean
  }>,
) {
  return request<Project>(`/projects/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  })
}

export async function deleteProject(id: string) {
  return request<{ ok: boolean }>(`/projects/${id}`, { method: "DELETE" })
}

export async function postProjectDirective(id: string, content: string) {
  return request<{ ok: boolean; directive: Directive }>(`/projects/${id}/directive`, {
    method: "POST",
    body: JSON.stringify({ content }),
  })
}

// ---------------------------------------------------------------------------
// Snapshots, Proposals, Human Requests
// ---------------------------------------------------------------------------

export interface Snapshot {
  sk: string
  date: string
  kpi_readings: Record<string, number | null>
  reflection: string | null
  created_at: string
}

export interface Proposal {
  sk: string
  action: string
  rationale: string
  domain: string
  target_kpi: string
  status: string
  feedback: string | null
  task_id: string | null
  outcome: string | null
  created_at: string
}

export async function fetchSnapshots(projectId: string, days = 14) {
  return request<{ snapshots: Snapshot[] }>(`/projects/${projectId}/snapshots?days=${days}`)
}

export async function fetchProposals(projectId: string, status?: string) {
  const q = status ? `?status=${encodeURIComponent(status)}` : ""
  return request<{ proposals: Proposal[] }>(`/projects/${projectId}/proposals${q}`)
}

export async function approveProposal(projectId: string, propSk: string) {
  return request<{ ok: boolean; task_id: string }>(
    `/projects/${projectId}/proposals/${encodeURIComponent(propSk)}`,
    { method: "PATCH", body: JSON.stringify({ status: "approved" }) },
  )
}

export async function rejectProposal(projectId: string, propSk: string, feedback: string) {
  return request<{ ok: boolean }>(
    `/projects/${projectId}/proposals/${encodeURIComponent(propSk)}`,
    { method: "PATCH", body: JSON.stringify({ status: "rejected", feedback }) },
  )
}

export async function fetchPlans(projectId: string, limit = 14) {
  return request<{ plans: DailyPlan[] }>(`/projects/${projectId}/plans?limit=${limit}`)
}

export async function fetchPlanDetail(projectId: string, dateStr: string) {
  return request<{ plan: DailyPlan; tasks: Task[] }>(
    `/projects/${projectId}/plans/${encodeURIComponent(dateStr)}`,
  )
}

export async function patchPlanItems(
  projectId: string,
  dateStr: string,
  body: { items: PlanItem[]; reflection?: string },
) {
  return request<{ plan: DailyPlan }>(`/projects/${projectId}/plans/${encodeURIComponent(dateStr)}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  })
}

export async function approvePlan(projectId: string, dateStr: string, humanNotes = "") {
  return request<{ ok: boolean; plan: DailyPlan; task_ids: string[] }>(
    `/projects/${projectId}/plans/${encodeURIComponent(dateStr)}/approve`,
    { method: "POST", body: JSON.stringify({ human_notes: humanNotes }) },
  )
}

export async function regeneratePlan(projectId: string, dateStr: string) {
  return request<{ ok: boolean }>(
    `/projects/${projectId}/plans/${encodeURIComponent(dateStr)}/regenerate`,
    { method: "POST", body: JSON.stringify({}) },
  )
}
