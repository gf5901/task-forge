import { useState, useEffect, useMemo } from "react"
import { Link, useSearchParams } from "react-router-dom"
import {
  Loader,
  Activity,
  Search,
  X,
  Play,
  Check,
  XCircle,
  Clock,
  AlertTriangle,
  Sparkles,
  ChevronRight,
  FileText,
  GitPullRequest,
  GitBranch,
  Trash2,
  CircleDot,
  Cpu,
  Zap,
  ArrowRight,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { fetchLogs } from "@/lib/api"
import type { LogEntry } from "@/lib/api"
import { timeAgo } from "@/lib/time"

// ── Visual config ──────────────────────────────────────────────

const STAGE_META: Record<string, { label: string; color: string; bg: string; ring: string }> = {
  pipeline: { label: "Pipeline", color: "text-indigo-400", bg: "bg-indigo-500/10", ring: "ring-indigo-500/20" },
  plan:     { label: "Plan",     color: "text-amber-400",  bg: "bg-amber-500/10",  ring: "ring-amber-500/20" },
  execute:  { label: "Execute",  color: "text-emerald-400",bg: "bg-emerald-500/10", ring: "ring-emerald-500/20" },
  docs:     { label: "Docs",     color: "text-sky-400",    bg: "bg-sky-500/10",     ring: "ring-sky-500/20" },
  pr:       { label: "PR",       color: "text-violet-400", bg: "bg-violet-500/10",  ring: "ring-violet-500/20" },
  worktree: { label: "Worktree", color: "text-zinc-400",   bg: "bg-zinc-500/10",    ring: "ring-zinc-500/20" },
  cleanup:  { label: "Cleanup",  color: "text-zinc-500",   bg: "bg-zinc-500/10",    ring: "ring-zinc-500/20" },
}

const DEFAULT_STAGE_META = { label: "", color: "text-zinc-500", bg: "bg-zinc-800/40", ring: "ring-zinc-700/30" }

type LucideIcon = React.ComponentType<{ className?: string }>

const EVENT_ICONS: Record<string, LucideIcon> = {
  task_start: Play,
  task_done: Check,
  task_failed: XCircle,
  task_timeout: Clock,
  task_error: AlertTriangle,
  planning_start: Sparkles,
  planning_done: Sparkles,
  planning_skip: ChevronRight,
  subtask_start: CircleDot,
  subtask_done: Check,
  subtask_failed: XCircle,
  subtask_timeout: Clock,
  subtask_error: AlertTriangle,
  execute_start: Zap,
  execute_done: Check,
  docs_start: FileText,
  docs_done: FileText,
  pr_start: GitPullRequest,
  pr_done: GitPullRequest,
  pr_skip: ChevronRight,
  worktree_created: GitBranch,
  worktree_cleaned: Trash2,
}

const EVENT_LABELS: Record<string, string> = {
  task_start: "Task started",
  task_done: "Task completed",
  task_failed: "Task failed",
  task_timeout: "Task timed out",
  task_error: "Task error",
  planning_start: "Planning started",
  planning_done: "Planning complete",
  planning_skip: "Planning skipped",
  subtask_start: "Subtask started",
  subtask_done: "Subtask completed",
  subtask_failed: "Subtask failed",
  subtask_timeout: "Subtask timed out",
  subtask_error: "Subtask error",
  execute_start: "Execution started",
  execute_done: "Execution finished",
  docs_start: "Updating docs",
  docs_done: "Docs updated",
  pr_start: "Creating PR",
  pr_done: "PR created",
  pr_skip: "PR skipped",
  worktree_created: "Worktree created",
  worktree_cleaned: "Worktree cleaned",
}

function isPipelineEvent(event: string): boolean {
  return event === "task_start" || event === "task_done" || event === "task_failed" || event === "task_timeout" || event === "task_error"
}

// ── Components ─────────────────────────────────────────────────

function EventIcon({ event }: { event: string }) {
  const isSuccess = event.endsWith("_done") || event === "worktree_cleaned"
  const isFail = event.endsWith("_failed") || event.endsWith("_timeout") || event.endsWith("_error")
  const isPipeline = isPipelineEvent(event)

  let ringColor = "ring-zinc-700/40"
  let bgColor = "bg-zinc-900"
  let iconColor = "text-zinc-500"

  if (event === "task_start") {
    ringColor = "ring-indigo-500/30"
    bgColor = "bg-indigo-500/10"
    iconColor = "text-indigo-400"
  } else if (event === "task_done") {
    ringColor = "ring-emerald-500/30"
    bgColor = "bg-emerald-500/10"
    iconColor = "text-emerald-400"
  } else if (isFail) {
    ringColor = "ring-red-500/30"
    bgColor = "bg-red-500/10"
    iconColor = "text-red-400"
  } else if (isSuccess) {
    iconColor = "text-emerald-400"
  }

  const Icon = EVENT_ICONS[event]
  const size = isPipeline ? "size-8" : "size-6"
  const iconSize = isPipeline ? "size-4" : "size-3"

  return (
    <div className={`${size} rounded-full ${bgColor} ring-1 ${ringColor} flex items-center justify-center shrink-0`}>
      {Icon ? (
        <Icon className={`${iconSize} ${iconColor}`} />
      ) : (
        <CircleDot className={`${iconSize} ${iconColor}`} />
      )}
    </div>
  )
}

function StageBadge({ stage }: { stage: string }) {
  const meta = STAGE_META[stage] || DEFAULT_STAGE_META
  const label = meta.label || stage
  if (!label) return null
  return (
    <span className={`inline-flex items-center text-[10px] font-semibold uppercase tracking-wide px-1.5 py-0.5 rounded ${meta.color} ${meta.bg} ring-1 ${meta.ring}`}>
      {label}
    </span>
  )
}

function TokenChip({ entry }: { entry: LogEntry }) {
  if (entry.extra?.inputTokens == null) return null
  const inTok = Number(entry.extra.inputTokens)
  const outTok = Number(entry.extra.outputTokens)
  const fmt = (n: number) => n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n)
  return (
    <span
      className="inline-flex items-center gap-1 text-[10px] tabular-nums text-zinc-600 bg-zinc-800/60 rounded px-1.5 py-0.5"
      title={`In: ${inTok.toLocaleString()} · Out: ${outTok.toLocaleString()}${entry.extra.cacheReadTokens ? ` · Cache: ${Number(entry.extra.cacheReadTokens).toLocaleString()}` : ""}`}
    >
      <Cpu className="size-2.5 text-zinc-600" />
      {fmt(inTok)}↑ {fmt(outTok)}↓
    </span>
  )
}

function RuntimeChip({ seconds }: { seconds: number }) {
  const display = seconds >= 60
    ? `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`
    : `${seconds.toFixed(1)}s`
  return (
    <span className="inline-flex items-center gap-1 text-[10px] tabular-nums text-zinc-600 bg-zinc-800/60 rounded px-1.5 py-0.5">
      <Clock className="size-2.5" />
      {display}
    </span>
  )
}

function ModelChip({ model }: { model: string }) {
  return (
    <span className="inline-flex items-center gap-1 text-[10px] text-zinc-600 bg-zinc-800/60 rounded px-1.5 py-0.5">
      {model}
    </span>
  )
}

function EntryMessage({ entry }: { entry: LogEntry }) {
  const label = EVENT_LABELS[entry.event]
  const hasCustomMessage = entry.message && entry.message !== label

  if (isPipelineEvent(entry.event)) {
    return (
      <div className="space-y-0.5">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-[13px] font-medium text-zinc-200">
            {label || entry.event}
          </span>
          {hasCustomMessage && (
            <>
              <ArrowRight className="size-3 text-zinc-700" />
              <span className="text-[13px] text-zinc-400">{entry.message}</span>
            </>
          )}
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <Link
            to={`/tasks/${entry.task_id}`}
            className="text-[11px] font-mono text-zinc-600 hover:text-indigo-400 transition-colors"
          >
            {entry.task_id}
          </Link>
          {entry.extra?.parent_id && (
            <>
              <span className="text-zinc-800">·</span>
              <Link
                to={`/tasks/${entry.extra.parent_id}`}
                className="text-[11px] font-mono text-zinc-600 hover:text-indigo-400 transition-colors"
              >
                parent: {entry.extra.parent_id}
              </Link>
            </>
          )}
          {entry.extra?.model && <ModelChip model={String(entry.extra.model)} />}
          {entry.extra?.runtime != null && <RuntimeChip seconds={Number(entry.extra.runtime)} />}
          <TokenChip entry={entry} />
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-0.5">
      <div className="flex items-center gap-2 flex-wrap">
        <StageBadge stage={entry.stage} />
        <span className="text-[12px] text-zinc-300">
          {label || entry.event}
        </span>
        {hasCustomMessage && (
          <span className="text-[12px] text-zinc-500">{entry.message}</span>
        )}
      </div>
      <div className="flex items-center gap-2 flex-wrap">
        <Link
          to={`/tasks/${entry.task_id}`}
          className="text-[11px] font-mono text-zinc-600 hover:text-indigo-400 transition-colors"
        >
          {entry.task_id}
        </Link>
        {entry.extra?.parent_id && (
          <>
            <span className="text-zinc-800">·</span>
            <Link
              to={`/tasks/${entry.extra.parent_id}`}
              className="text-[11px] font-mono text-zinc-600 hover:text-indigo-400 transition-colors"
            >
              parent: {entry.extra.parent_id}
            </Link>
          </>
        )}
        {entry.extra?.model && <ModelChip model={String(entry.extra.model)} />}
        {entry.extra?.runtime != null && <RuntimeChip seconds={Number(entry.extra.runtime)} />}
        <TokenChip entry={entry} />
      </div>
    </div>
  )
}

// ── Main ───────────────────────────────────────────────────────

export default function ActivityLog() {
  const [searchParams, setSearchParams] = useSearchParams()
  const filterTask = searchParams.get("task_id") || ""

  const [entries, setEntries] = useState<LogEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [taskFilter, setTaskFilter] = useState(filterTask)

  useEffect(() => {
    let cancelled = false
    fetchLogs(filterTask || undefined, 200)
      .then(({ entries: e }) => { if (!cancelled) { setLoading(false); setEntries(e) } })
      .catch(() => { if (!cancelled) { setLoading(false); setEntries([]) } })
    return () => { cancelled = true }
  }, [filterTask])

  const grouped = useMemo(() => groupByTimeBucket(entries), [entries])

  function applyFilter() {
    setLoading(true)
    if (taskFilter.trim()) {
      setSearchParams({ task_id: taskFilter.trim() })
    } else {
      setSearchParams({})
    }
  }

  function clearFilter() {
    setLoading(true)
    setTaskFilter("")
    setSearchParams({})
  }

  return (
    <div className="mx-auto max-w-3xl px-3 sm:px-6 py-4 sm:py-6 space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <div className="size-7 rounded-lg bg-indigo-500/10 ring-1 ring-indigo-500/20 flex items-center justify-center">
            <Activity className="size-3.5 text-indigo-400" />
          </div>
          <h1 className="text-base font-semibold text-zinc-100">Activity</h1>
        </div>
      </div>

      {/* Search / filter */}
      <div className="relative">
        <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 size-3.5 text-zinc-600 pointer-events-none" />
        <Input
          value={taskFilter}
          onChange={(e) => setTaskFilter(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && applyFilter()}
          placeholder="Filter by task ID…"
          className="h-8 pl-8 pr-8 w-full max-w-xs bg-zinc-900/60 border-zinc-800 text-sm text-zinc-300 placeholder:text-zinc-600 focus:border-indigo-500/40 focus:ring-1 focus:ring-indigo-500/20"
        />
        {filterTask && (
          <Button
            variant="ghost"
            size="sm"
            onClick={clearFilter}
            className="absolute right-1 top-1/2 -translate-y-1/2 h-6 w-6 p-0 text-zinc-500 hover:text-zinc-300"
          >
            <X className="size-3.5" />
          </Button>
        )}
      </div>

      {/* Content */}
      {loading ? (
        <div className="flex items-center justify-center h-48">
          <Loader className="size-5 animate-spin text-zinc-600" />
        </div>
      ) : entries.length === 0 ? (
        <div className="text-center py-20">
          <div className="size-10 rounded-full bg-zinc-800/60 ring-1 ring-zinc-700/40 flex items-center justify-center mx-auto mb-3">
            <Activity className="size-5 text-zinc-600" />
          </div>
          <p className="text-sm text-zinc-500">No pipeline activity yet</p>
          <p className="text-xs text-zinc-600 mt-1">Events will appear here when tasks run.</p>
        </div>
      ) : (
        <div className="space-y-6">
          {grouped.map((group) => (
            <div key={group.label}>
              <div className="sticky top-0 z-10 backdrop-blur-sm bg-zinc-950/80 pb-2 pt-1 -mx-1 px-1">
                <span className="text-[11px] font-medium uppercase tracking-wider text-zinc-600">
                  {group.label}
                </span>
              </div>
              <div className="relative">
                {/* Timeline line */}
                <div className="absolute left-3 sm:left-[15px] top-4 bottom-4 w-px bg-zinc-800/80" />

                <div className="space-y-0">
                  {group.entries.map((entry, i) => {
                    const pipeline = isPipelineEvent(entry.event)
                    return (
                      <div
                        key={`${entry.ts}-${entry.task_id}-${entry.event}-${i}`}
                        className={`relative flex items-start gap-3 group transition-colors rounded-lg ${
                          pipeline
                            ? "py-2.5 px-1.5 -mx-1.5 hover:bg-zinc-800/20"
                            : "py-1.5 px-1.5 -mx-1.5 hover:bg-zinc-800/20"
                        }`}
                      >
                        {/* Icon on timeline */}
                        <div className="relative z-[1] shrink-0">
                          <EventIcon event={entry.event} />
                        </div>

                        {/* Content */}
                        <div className="flex-1 min-w-0 pt-px">
                          <EntryMessage entry={entry} />
                        </div>

                        {/* Timestamp */}
                        <span className="text-[11px] text-zinc-700 tabular-nums shrink-0 pt-0.5 opacity-60 group-hover:opacity-100 transition-opacity">
                          {timeAgo(entry.ts)}
                        </span>
                      </div>
                    )
                  })}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Helpers ─────────────────────────────────────────────────────

function groupByTimeBucket(entries: LogEntry[]): { label: string; entries: LogEntry[] }[] {
  if (entries.length === 0) return []

  const now = Date.now()
  const buckets: Map<string, LogEntry[]> = new Map()

  for (const entry of entries) {
    const ts = new Date(entry.ts).getTime()
    const diffMs = now - ts
    const diffHours = diffMs / (1000 * 60 * 60)
    const diffDays = diffMs / (1000 * 60 * 60 * 24)

    let label: string
    if (diffHours < 1) label = "Last hour"
    else if (diffHours < 24) label = "Today"
    else if (diffDays < 2) label = "Yesterday"
    else if (diffDays < 7) label = "This week"
    else label = "Older"

    const bucket = buckets.get(label) || []
    bucket.push(entry)
    buckets.set(label, bucket)
  }

  const order = ["Last hour", "Today", "Yesterday", "This week", "Older"]
  return order
    .filter((label) => buckets.has(label))
    .map((label) => ({ label, entries: buckets.get(label)! }))
}
