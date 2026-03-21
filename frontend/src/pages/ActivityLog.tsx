import { useState, useEffect } from "react"
import { Link, useSearchParams } from "react-router-dom"
import {
  Loader,
  Activity,
  Filter,
  X,
  Play,
  Check,
  XCircle,
  Clock,
  AlertTriangle,
  Diamond,
  ChevronRight,
  FileText,
  GitPullRequest,
  GitBranch,
  Trash2,
  CircleDot,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { fetchLogs } from "@/lib/api"
import type { LogEntry } from "@/lib/api"
import { timeAgo } from "@/lib/time"

const STAGE_COLORS: Record<string, string> = {
  pipeline: "text-indigo-400 bg-indigo-500/10",
  plan: "text-yellow-400 bg-yellow-500/10",
  execute: "text-emerald-400 bg-emerald-500/10",
  docs: "text-blue-400 bg-blue-500/10",
  pr: "text-purple-400 bg-purple-500/10",
  worktree: "text-zinc-400 bg-zinc-500/10",
  cleanup: "text-zinc-500 bg-zinc-500/10",
}

type IconComponent = React.ComponentType<{ className?: string }>

const EVENT_ICON_MAP: Record<string, IconComponent> = {
  task_start: Play,
  task_done: Check,
  task_failed: XCircle,
  task_timeout: Clock,
  task_error: AlertTriangle,
  planning_start: Diamond,
  planning_done: Diamond,
  planning_skip: ChevronRight,
  subtask_start: CircleDot,
  subtask_done: Check,
  subtask_failed: XCircle,
  subtask_timeout: Clock,
  subtask_error: AlertTriangle,
  execute_start: CircleDot,
  execute_done: Check,
  docs_start: FileText,
  docs_done: FileText,
  pr_start: GitPullRequest,
  pr_done: GitPullRequest,
  pr_skip: ChevronRight,
  worktree_created: GitBranch,
  worktree_cleaned: Trash2,
}

function EventIcon({ event }: { event: string }) {
  const isSuccess = event.endsWith("_done")
  const isFail =
    event.endsWith("_failed") || event.endsWith("_timeout") || event.endsWith("_error")
  const color = isSuccess
    ? "text-emerald-400"
    : isFail
    ? "text-red-400"
    : "text-zinc-500"

  const Icon = EVENT_ICON_MAP[event]
  if (!Icon) {
    return <span className={`text-[10px] font-mono ${color}`}>·</span>
  }
  return <Icon className={`size-3.5 shrink-0 ${color}`} />
}

export default function ActivityLog() {
  const [searchParams, setSearchParams] = useSearchParams()
  const filterTask = searchParams.get("task_id") || ""

  const [entries, setEntries] = useState<LogEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [taskFilter, setTaskFilter] = useState(filterTask)

  useEffect(() => {
    fetchLogs(filterTask || undefined, 200)
      .then(({ entries: e }) => { setLoading(false); setEntries(e) })
      .catch(() => { setLoading(false); setEntries([]) })
  }, [filterTask])

  function applyFilter() {
    if (taskFilter.trim()) {
      setSearchParams({ task_id: taskFilter.trim() })
    } else {
      setSearchParams({})
    }
  }

  function clearFilter() {
    setTaskFilter("")
    setSearchParams({})
  }

  return (
    <div className="mx-auto max-w-4xl px-3 sm:px-6 py-4 sm:py-6 space-y-4">
      <div className="flex items-center gap-3">
        <Activity className="size-4 text-zinc-500" />
        <h1 className="text-lg font-semibold text-zinc-100">Pipeline Activity</h1>
      </div>

      {/* Filter bar */}
      <div className="flex items-center gap-2">
        <Filter className="size-3.5 text-zinc-600 shrink-0" />
        <Input
          value={taskFilter}
          onChange={(e) => setTaskFilter(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && applyFilter()}
          placeholder="Filter by task ID…"
          className="h-8 w-full max-w-48 bg-zinc-900/50 border-zinc-700/60 text-sm text-zinc-300 placeholder:text-zinc-600"
        />
        {filterTask && (
          <Button variant="ghost" size="sm" onClick={clearFilter} className="h-8 px-2 text-zinc-500">
            <X className="size-3.5" />
          </Button>
        )}
      </div>

      {loading ? (
        <div className="flex items-center justify-center h-48">
          <Loader className="size-5 animate-spin text-zinc-600" />
        </div>
      ) : entries.length === 0 ? (
        <div className="text-center py-16">
          <p className="text-sm text-zinc-500">No pipeline activity yet.</p>
          <p className="text-xs text-zinc-600 mt-1">Events will appear here when tasks run.</p>
        </div>
      ) : (
        <div className="space-y-px">
          {entries.map((entry, i) => (
            <div
              key={i}
              className="flex items-start gap-2.5 px-2 py-2 rounded-md hover:bg-zinc-800/30 group"
            >
              {/* Icon */}
              <div className="mt-0.5 shrink-0">
                <EventIcon event={entry.event} />
              </div>

              {/* Main content */}
              <div className="flex-1 min-w-0 space-y-0.5">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className={`text-[11px] font-medium px-1.5 py-0.5 rounded shrink-0 ${STAGE_COLORS[entry.stage] || "text-zinc-500 bg-zinc-800/40"}`}>
                    {entry.stage}
                  </span>
                  <span className="text-[12px] text-zinc-300 break-words min-w-0">{entry.message}</span>
                </div>
                <div className="flex items-center gap-2 text-[11px] text-zinc-600 flex-wrap">
                  <Link
                    to={`/tasks/${entry.task_id}`}
                    className="font-mono hover:text-zinc-400 transition-colors truncate max-w-[120px] sm:max-w-none"
                  >
                    {entry.task_id}
                  </Link>
                  {entry.extra?.parent_id && (
                    <>
                      <ChevronRight className="size-3 text-zinc-700 shrink-0" />
                      <Link
                        to={`/tasks/${entry.extra.parent_id}`}
                        className="font-mono hover:text-zinc-400 transition-colors truncate max-w-[120px] sm:max-w-none"
                      >
                        {entry.extra.parent_id}
                      </Link>
                    </>
                  )}
                  {entry.extra?.model && (
                    <span className="text-zinc-700 hidden sm:inline">model: {entry.extra.model}</span>
                  )}
                  {entry.extra?.runtime && (
                    <span className="text-zinc-700 inline-flex items-center gap-0.5">
                      <Clock className="size-2.5" />{entry.extra.runtime}s
                    </span>
                  )}
                  {entry.extra?.inputTokens != null && (
                    <span className="text-zinc-700 tabular-nums" title={`In: ${entry.extra.inputTokens} · Out: ${entry.extra.outputTokens}${entry.extra.cacheReadTokens ? ` · Cache: ${entry.extra.cacheReadTokens}` : ''}`}>
                      {Number(entry.extra.inputTokens) >= 1000
                        ? `${(Number(entry.extra.inputTokens) / 1000).toFixed(1)}k`
                        : entry.extra.inputTokens}↑ {Number(entry.extra.outputTokens) >= 1000
                        ? `${(Number(entry.extra.outputTokens) / 1000).toFixed(1)}k`
                        : entry.extra.outputTokens}↓ tok
                    </span>
                  )}
                </div>
              </div>

              {/* Timestamp — always visible, right-aligned */}
              <span className="text-[11px] text-zinc-700 tabular-nums shrink-0 mt-0.5">
                {timeAgo(entry.ts)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
