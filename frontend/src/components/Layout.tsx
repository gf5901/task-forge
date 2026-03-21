import { useState, useEffect, useRef } from "react"
import { Outlet, useNavigate, useLocation, useSearchParams } from "react-router-dom"
import {
  Bot,
  ListTodo,
  Circle,
  Loader,
  CheckCircle2,
  XCircle,
  Eye,
  Plus,
  LogOut,
  Activity,
  FileText,
  DollarSign,
  FolderKanban,
  Settings,
  AlertTriangle,
  User,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { fetchTasks, logout, fetchBudget, fetchStats, fetchHealth } from "@/lib/api"
import type { BudgetStatus, StatsData, HealthStatus } from "@/lib/api"
import { usePullToRefresh } from "@/hooks/usePullToRefresh"
import type { Counts } from "@/lib/types"

const NAV_ITEMS = [
  { key: "all", label: "All", icon: ListTodo, color: "text-zinc-400" },
  { key: "human", label: "My Tasks", icon: User, color: "text-orange-400" },
  { key: "pending", label: "Pending", icon: Circle, color: "text-zinc-500" },
  { key: "in_progress", label: "In Progress", icon: Loader, color: "text-yellow-500" },
  { key: "in_review", label: "In Review", icon: Eye, color: "text-violet-400" },
  { key: "completed", label: "Completed", icon: CheckCircle2, color: "text-emerald-500" },
  { key: "failed", label: "Failed", icon: AlertTriangle, color: "text-amber-500" },
  { key: "cancelled", label: "Cancelled", icon: XCircle, color: "text-red-500" },
] as const

const MOBILE_NAV = [
  { key: "tasks", label: "Tasks", icon: ListTodo, path: "/tasks" },
  { key: "projects", label: "Projects", icon: FolderKanban, path: "/projects" },
  { key: "activity", label: "Activity", icon: Activity, path: "/activity" },
  { key: "stats", label: "Stats", icon: DollarSign, path: "/stats" },
  { key: "settings", label: "Settings", icon: Settings, path: "/settings" },
] as const

function fmtTok(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`
  return String(n)
}

export default function Layout() {
  const navigate = useNavigate()
  const location = useLocation()
  const [searchParams] = useSearchParams()
  const [counts, setCounts] = useState<Counts>({ all: 0, pending: 0, in_progress: 0, in_review: 0, completed: 0, failed: 0, cancelled: 0, human: 0 })
  const [budget, setBudget] = useState<BudgetStatus | null>(null)
  const [stats, setStats] = useState<StatsData | null>(null)
  const [health, setHealth] = useState<HealthStatus | null>(null)
  const mainRef = useRef<HTMLElement>(null)

  const isTaskList = location.pathname === "/tasks"
  const isProjectsRoot = location.pathname === "/projects"
  const activeStatus = searchParams.get("status") || "all"

  function refreshCounts() {
    fetchBudget().then(setBudget).catch(() => {})
    fetchStats().then(setStats).catch(() => {})
    fetchHealth().then(setHealth).catch(() => {})
    return fetchTasks("all")
      .then(({ counts: c }) => setCounts(c))
      .catch(() => {})
  }

  useEffect(() => { refreshCounts() }, [location.pathname, location.search])

  useEffect(() => {
    const id = setInterval(() => { fetchHealth().then(setHealth).catch(() => {}) }, 60_000)
    return () => clearInterval(id)
  }, [])

  const { pullY, refreshing } = usePullToRefresh({
    onRefresh: async () => { window.dispatchEvent(new CustomEvent("ptr:refresh")); await refreshCounts() },
    scrollRef: mainRef,
  })

  function handleNav(status: string) {
    navigate(`/tasks?status=${status}`)
  }

  async function handleLogout() {
    await logout()
    window.location.reload()
  }

  const healthOk = health?.status === "ok"
  const healthColor = health == null ? "bg-zinc-600" : healthOk ? "bg-emerald-500" : "bg-red-500"
  const healthLabel = health == null
    ? "Checking…"
    : healthOk
      ? `EC2 up ${health.uptime_seconds ? Math.floor(health.uptime_seconds / 3600) + "h" : ""} · ${health.disk_free_pct ?? "?"}% disk free`
      : `EC2 ${health.status}${health.error ? ": " + health.error : ""}`

  return (
    <div className="flex h-screen bg-zinc-950 text-zinc-100">
      {/* Desktop sidebar */}
      <aside className="hidden lg:flex w-56 shrink-0 flex-col border-r border-zinc-800/60 bg-zinc-950">
        <div className="flex items-center gap-2.5 px-4 h-12 border-b border-zinc-800/40">
          <Bot className="size-4 text-indigo-400" />
          <span className="text-[13px] font-semibold tracking-tight">Task Forge</span>
          <span className="ml-auto relative flex size-2" title={healthLabel}>
            {healthOk && <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-40" />}
            <span className={`relative inline-flex size-2 rounded-full ${healthColor}`} />
          </span>
        </div>

        <nav className="flex-1 px-2 py-2 space-y-px">
          {NAV_ITEMS.map(({ key, label, icon: Icon, color }) => {
            const active = isTaskList && activeStatus === key
            return (
              <button
                key={key}
                onClick={() => handleNav(key)}
                className={`flex w-full items-center gap-2 rounded-md px-2.5 py-[7px] text-[13px] transition-colors ${
                  active
                    ? "bg-zinc-800/70 text-zinc-100"
                    : "text-zinc-500 hover:bg-zinc-800/40 hover:text-zinc-300"
                }`}
              >
                <Icon className={`size-[15px] ${active ? color : "text-zinc-600"}`} />
                <span className="flex-1 text-left">{label}</span>
                <span className="text-[11px] tabular-nums text-zinc-600">
                  {counts[key as keyof Counts] || ""}
                </span>
              </button>
            )
          })}
        </nav>

        {stats && (
          <button
            type="button"
            onClick={() => navigate("/stats")}
            title="Open full usage & cost"
            className="mx-3 mb-2 px-2 py-2 rounded-md bg-zinc-900/50 border border-zinc-800/40 space-y-1.5 text-left w-[calc(100%-1.5rem)] transition-colors hover:bg-zinc-800/50 hover:border-zinc-700/50 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/40"
          >
            <div className="flex items-center gap-1.5 text-[11px] text-zinc-500">
              <DollarSign className="size-3" />
              <span>Today ~${stats.today.cost_usd.toFixed(2)}</span>
              {budget?.budget_enabled && (
                <span className="text-zinc-700">/ ${budget.daily_cap_usd.toFixed(2)}</span>
              )}
            </div>
            {budget?.budget_enabled && (
              <div className="h-1 rounded-full bg-zinc-800 overflow-hidden">
                <div
                  className={`h-full rounded-full transition-all ${
                    budget.remaining_usd <= 0
                      ? "bg-red-500"
                      : budget.spent_today_usd / budget.daily_cap_usd > 0.8
                        ? "bg-yellow-500"
                        : "bg-indigo-500"
                  }`}
                  style={{ width: `${Math.min((budget.spent_today_usd / budget.daily_cap_usd) * 100, 100)}%` }}
                />
              </div>
            )}
            <div className="text-[10px] text-zinc-600 tabular-nums">
              {fmtTok(stats.today.inputTokens)}↑ {fmtTok(stats.today.outputTokens)}↓
              {stats.today.cacheReadTokens > 0 && <span className="ml-1">({fmtTok(stats.today.cacheReadTokens)} cached)</span>}
            </div>
            {stats.daily.length > 1 && (() => {
              const last7 = stats.daily.slice(-7)
              const max = Math.max(...last7.map(x => x.cost_usd), 0.01)
              return (
              <div className="flex items-end gap-px h-5">
                {last7.map((d, i) => {
                  const pct = Math.max((d.cost_usd / max) * 100, 4)
                  return (
                    <div
                      key={i}
                      className="flex-1 rounded-sm bg-indigo-500/40 hover:bg-indigo-500/70 transition-colors"
                      style={{ height: `${pct}%` }}
                      title={`${d.date}: $${d.cost_usd.toFixed(2)}`}
                    />
                  )
                })}
              </div>
              )
            })()}
            <div className="text-[10px] text-zinc-700">
              All time ~${stats.all_time.cost_usd.toFixed(2)}
            </div>
            <div className="text-[10px] text-indigo-400/80 pt-0.5">View details →</div>
          </button>
        )}

        {health && (
          <div className="mx-3 mb-2 px-2 py-1.5 rounded-md bg-zinc-900/50 border border-zinc-800/40">
            <div className="flex items-center gap-1.5 text-[11px]">
              <span className={`inline-flex size-1.5 rounded-full ${healthColor}`} />
              <span className={healthOk ? "text-zinc-500" : "text-red-400"}>
                {healthOk ? "EC2 Online" : `EC2 ${health.status}`}
              </span>
            </div>
            {healthOk && (
              <div className="text-[10px] text-zinc-600 mt-0.5">
                {health.uptime_seconds ? `${Math.floor(health.uptime_seconds / 3600)}h uptime` : ""}
                {health.disk_free_pct != null && ` · ${health.disk_free_pct}% disk free`}
              </div>
            )}
            {!healthOk && health.error && (
              <div className="text-[10px] text-red-400/70 mt-0.5 truncate">{health.error}</div>
            )}
          </div>
        )}

        <div className="px-2 pb-2 space-y-1">
          <Button
            variant="ghost"
            className={`w-full justify-start gap-2 text-[13px] h-8 ${
              location.pathname === "/activity"
                ? "text-zinc-200 bg-zinc-800/50"
                : "text-zinc-500 hover:text-zinc-300"
            }`}
            onClick={() => navigate("/activity")}
          >
            <Activity className="size-4" />
            Activity
          </Button>
          <Button
            variant="ghost"
            className={`w-full justify-start gap-2 text-[13px] h-8 ${
              location.pathname === "/stats"
                ? "text-zinc-200 bg-zinc-800/50"
                : "text-zinc-500 hover:text-zinc-300"
            }`}
            onClick={() => navigate("/stats")}
          >
            <DollarSign className="size-4" />
            Usage &amp; cost
          </Button>
          <Button
            variant="ghost"
            className={`w-full justify-start gap-2 text-[13px] h-8 ${
              location.pathname.startsWith("/projects")
                ? "text-zinc-200 bg-zinc-800/50"
                : "text-zinc-500 hover:text-zinc-300"
            }`}
            onClick={() => navigate("/projects")}
          >
            <FolderKanban className="size-4" />
            Projects
          </Button>
          <Button
            variant="ghost"
            className={`w-full justify-start gap-2 text-[13px] h-8 ${
              location.pathname === "/spec"
                ? "text-zinc-200 bg-zinc-800/50"
                : "text-zinc-500 hover:text-zinc-300"
            }`}
            onClick={() => navigate("/spec")}
          >
            <FileText className="size-4" />
            Spec
          </Button>
          <Button
            variant="ghost"
            className="w-full justify-start gap-2 text-[13px] text-zinc-500 hover:text-zinc-300 h-8"
            onClick={() => navigate("/tasks/new")}
          >
            <Plus className="size-4" />
            New Task
          </Button>
          <Button
            variant="ghost"
            className={`w-full justify-start gap-2 text-[13px] h-8 ${
              location.pathname === "/settings"
                ? "text-zinc-200 bg-zinc-800/50"
                : "text-zinc-500 hover:text-zinc-300"
            }`}
            onClick={() => navigate("/settings")}
          >
            <Settings className="size-3.5" />
            Settings
          </Button>
          <Button
            variant="ghost"
            className="w-full justify-start gap-2 text-[13px] text-zinc-600 hover:text-zinc-400 h-8"
            onClick={handleLogout}
          >
            <LogOut className="size-3.5" />
            Sign out
          </Button>
        </div>
      </aside>

      {/* Mobile bottom nav bar */}
      <nav className="lg:hidden fixed inset-x-0 bottom-0 z-50 bg-zinc-950/95 backdrop-blur border-t border-zinc-800/60 safe-area-pb">
        <div className="flex items-stretch justify-around h-14">
          {MOBILE_NAV.map(({ key, label, icon: Icon, path }) => {
            const isActive = key === "tasks"
              ? location.pathname === "/tasks"
              : key === "projects"
                ? location.pathname.startsWith("/projects")
                : key === "stats"
                  ? location.pathname === "/stats"
                  : location.pathname === path
            const badge = 0
            return (
              <button
                key={key}
                onClick={() => navigate(path)}
                className={`flex flex-1 flex-col items-center justify-center gap-0.5 transition-colors ${
                  isActive ? "text-indigo-400" : "text-zinc-600"
                }`}
              >
                <span className="relative">
                  <Icon className="size-5" />
                  {badge > 0 && (
                    <span className="absolute -top-1.5 -right-2.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-orange-500 px-1 text-[9px] font-bold text-white">
                      {badge}
                    </span>
                  )}
                </span>
                <span className="text-[10px] font-medium">{label}</span>
              </button>
            )
          })}
        </div>
      </nav>

      {/* Main */}
      <main ref={mainRef} className="flex-1 overflow-y-auto pb-16 lg:pb-0">
        {/* Mobile health banner — only when unhealthy */}
        {health && !healthOk && (
          <div className="lg:hidden flex items-center gap-2 px-4 py-2 bg-red-950/60 border-b border-red-900/40">
            <span className={`inline-flex size-2 rounded-full ${healthColor}`} />
            <span className="text-[12px] text-red-300 truncate">{healthLabel}</span>
          </div>
        )}
        {/* Mobile health dot — when healthy, subtle top-right indicator */}
        {health && healthOk && (
          <div className="lg:hidden absolute top-2 right-3 z-40" title={healthLabel}>
            <span className="relative flex size-2">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-40" />
              <span className="relative inline-flex size-2 rounded-full bg-emerald-500" />
            </span>
          </div>
        )}
        {/* Pull-to-refresh indicator — mobile only */}
        <div
          className="lg:hidden flex items-center justify-center overflow-hidden transition-all duration-150 ease-out"
          style={{ height: refreshing ? 48 : pullY > 0 ? pullY * 0.6 : 0 }}
        >
          <Loader
            className={`size-4 text-indigo-400 transition-opacity ${
              pullY > 0 || refreshing ? "opacity-100" : "opacity-0"
            } ${refreshing ? "animate-spin" : ""}`}
            style={{ transform: refreshing ? undefined : `rotate(${(pullY / 72) * 180}deg)` }}
          />
        </div>
        <Outlet />
      </main>

      {/* Mobile FAB */}
      {isTaskList && (
        <button
          onClick={() => navigate("/tasks/new")}
          className="lg:hidden fixed bottom-20 right-5 z-50 flex h-12 w-12 items-center justify-center rounded-full bg-indigo-600 text-white shadow-lg shadow-indigo-600/25 hover:bg-indigo-500 active:scale-95 transition-all"
        >
          <Plus className="size-5" />
        </button>
      )}
      {isProjectsRoot && (
        <button
          onClick={() => navigate("/projects/new")}
          className="lg:hidden fixed bottom-20 right-5 z-50 flex h-12 w-12 items-center justify-center rounded-full bg-indigo-600 text-white shadow-lg shadow-indigo-600/25 hover:bg-indigo-500 active:scale-95 transition-all"
        >
          <Plus className="size-5" />
        </button>
      )}
    </div>
  )
}
