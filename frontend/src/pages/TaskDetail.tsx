import { useState, useRef } from "react"
import { useParams, useNavigate, Link } from "react-router-dom"
import toast from "react-hot-toast"
import {
  ArrowLeft,
  ExternalLink,
  Play,
  Trash2,
  Circle,
  Loader,
  CheckCircle2,
  XCircle,
  Eye,
  Send,
  GitPullRequest,
  Activity,
  Clock,
  Bot,
  ChevronDown,
  ListTree,
  Lock,
  Copy,
  Check,
  Cpu,
  RotateCcw,
  Rocket,
  Zap,
  AlertTriangle,
  User,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Textarea } from "@/components/ui/textarea"
import Markdown from "@/components/Markdown"
import type { TaskStatus, TokenUsage } from "@/lib/types"
import {
  updateTaskStatus,
  runTask,
  rerunTask,
  addComment,
  deleteTask,
  replanTask,
} from "@/lib/api"
import { timeAgo } from "@/lib/time"
import { useTask } from "@/hooks/useTask"
import { useClickOutside } from "@/hooks/useClickOutside"
import { useClipboard } from "@/hooks/useClipboard"
import { useRoles } from "@/hooks/useRoles"

const STATUS_BADGE: Record<TaskStatus, string> = {
  pending: "bg-zinc-500/15 text-zinc-400 border-zinc-500/20",
  in_progress: "bg-yellow-500/15 text-yellow-400 border-yellow-500/20",
  in_review: "bg-violet-500/15 text-violet-400 border-violet-500/20",
  completed: "bg-emerald-500/15 text-emerald-400 border-emerald-500/20",
  failed: "bg-amber-500/15 text-amber-400 border-amber-500/20",
  cancelled: "bg-red-500/15 text-red-400 border-red-500/20",
}

const PRIORITY_BADGE: Record<string, string> = {
  low: "bg-zinc-500/15 text-zinc-400 border-zinc-500/20",
  medium: "bg-yellow-500/15 text-yellow-400 border-yellow-500/20",
  high: "bg-orange-500/15 text-orange-400 border-orange-500/20",
  urgent: "bg-red-500/15 text-red-400 border-red-500/20",
}

const STATUS_LABELS: Record<TaskStatus, string> = {
  pending: "Pending",
  in_progress: "In Progress",
  in_review: "In Review",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
}

function StatusIcon({ status, className = "size-4" }: { status: TaskStatus; className?: string }) {
  const map = {
    pending: <Circle className={`${className} text-zinc-500`} />,
    in_progress: <Loader className={`${className} text-yellow-500 animate-spin`} />,
    in_review: <Eye className={`${className} text-violet-400`} />,
    completed: <CheckCircle2 className={`${className} text-emerald-500`} />,
    failed: <AlertTriangle className={`${className} text-amber-500`} />,
    cancelled: <XCircle className={`${className} text-red-500`} />,
  }
  return map[status]
}

function extractPrNumber(url: string): string {
  const match = url.match(/\/pull\/(\d+)/)
  return match ? `#${match[1]}` : url
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`
  return String(n)
}

const COST_PER_1M: Record<string, number> = {
  inputTokens: 3.0,
  outputTokens: 15.0,
  cacheReadTokens: 0.3,
  cacheWriteTokens: 3.75,
}

function estimateCost(tokens: TokenUsage): number {
  let total = 0
  for (const [key, rate] of Object.entries(COST_PER_1M)) {
    total += ((tokens as unknown as Record<string, number>)[key] ?? 0) * rate / 1_000_000
  }
  return total
}

function TokenBadge({ tokens }: { tokens: TokenUsage }) {
  const total = tokens.inputTokens + tokens.outputTokens
  const cached = tokens.cacheReadTokens + tokens.cacheWriteTokens
  const cost = estimateCost(tokens)
  const title = [
    `Input: ${tokens.inputTokens.toLocaleString()}`,
    `Output: ${tokens.outputTokens.toLocaleString()}`,
    tokens.cacheReadTokens ? `Cache read: ${tokens.cacheReadTokens.toLocaleString()}` : null,
    tokens.cacheWriteTokens ? `Cache write: ${tokens.cacheWriteTokens.toLocaleString()}` : null,
    `Estimated cost: $${cost.toFixed(4)}`,
  ].filter(Boolean).join(" · ")
  return (
    <div className="flex items-center gap-1.5 text-[13px] text-zinc-500" title={title}>
      <Cpu className="size-3.5" />
      <span>{formatTokens(total)}</span>
      {cached > 0 && (
        <span className="text-[11px] text-zinc-600">({formatTokens(cached)} cached)</span>
      )}
      <span className="text-[11px] text-emerald-600">~${cost < 0.01 ? cost.toFixed(4) : cost.toFixed(2)}</span>
    </div>
  )
}

function CopyableTaskId({ taskId }: { taskId: string }) {
  const { copied, copy } = useClipboard()
  return (
    <button
      onClick={() => copy(window.location.href)}
      title="Copy link"
      className="inline-flex items-center gap-1 text-xs text-zinc-600 font-mono hover:text-zinc-400 transition-colors group"
    >
      {taskId}
      {copied
        ? <Check className="size-3 text-emerald-500" />
        : <Copy className="size-3 opacity-0 group-hover:opacity-100 transition-opacity" />
      }
    </button>
  )
}

export default function TaskDetail() {
  const { taskId: id } = useParams<{ taskId: string }>()
  const navigate = useNavigate()
  const { task, loading, error, reload } = useTask(id)
  const { roleLabel } = useRoles()
  const [commentBody, setCommentBody] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [runPending, setRunPending] = useState(false)
  const [rerunPending, setRerunPending] = useState(false)
  const [replanPending, setReplanPending] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [statusOpen, setStatusOpen] = useState(false)
  const statusRef = useRef<HTMLDivElement>(null)

  useClickOutside(statusRef, () => setStatusOpen(false), statusOpen)

  async function handleStatusChange(status: TaskStatus) {
    if (!id) return
    setStatusOpen(false)
    try {
      await updateTaskStatus(id, status)
      await reload()
      toast.success(`Status updated to ${STATUS_LABELS[status]}`)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to update status")
    }
  }

  async function handleRun() {
    if (!id) return
    setRunPending(true)
    try {
      await runTask(id)
      await reload()
      toast.success("Task queued — runner started")
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to run task")
    } finally {
      setRunPending(false)
    }
  }

  async function handleRerun() {
    if (!id) return
    setRerunPending(true)
    try {
      await rerunTask(id)
      await reload()
      toast.success("Task reset to pending — runner started")
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to rerun task")
    } finally {
      setRerunPending(false)
    }
  }

  async function handleComment(e: React.FormEvent) {
    e.preventDefault()
    if (!id || !commentBody.trim()) return
    setSubmitting(true)
    try {
      await addComment(id, commentBody.trim())
      setCommentBody("")
      await reload()
      toast.success("Comment posted")
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to post comment")
    } finally {
      setSubmitting(false)
    }
  }

  async function handleReplan() {
    if (!id) return
    setReplanPending(true)
    try {
      await replanTask(id)
      await reload()
      toast.success("Breaking into subtasks…")
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to replan task")
    } finally {
      setReplanPending(false)
    }
  }

  async function handleDelete() {
    if (!id) return
    if (!confirmDelete) { setConfirmDelete(true); return }
    try {
      await deleteTask(id)
      toast.success("Task deleted")
      navigate("/tasks")
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to delete task")
      setConfirmDelete(false)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[50vh]">
        <Loader className="size-5 text-zinc-600 animate-spin" />
      </div>
    )
  }

  if (error || !task) {
    return (
      <div className="max-w-3xl mx-auto px-4 py-8">
        <button onClick={() => navigate("/tasks")} className="flex items-center gap-1.5 text-sm text-zinc-500 hover:text-zinc-300 transition-colors mb-6">
          <ArrowLeft className="size-4" />Back to tasks
        </button>
        <p className="text-red-400">{error || "Task not found"}</p>
      </div>
    )
  }

  return (
    <div className="max-w-3xl mx-auto px-4 sm:px-6 py-6 space-y-5">
      {/* Back */}
      {task.parent ? (
        <Link to={`/tasks/${task.parent.id}`} className="inline-flex items-center gap-1.5 text-[13px] text-zinc-500 hover:text-zinc-300 transition-colors">
          <ArrowLeft className="size-3.5" />Back to: {task.parent.title}
        </Link>
      ) : task.spawned_by_task ? (
        <Link to={`/tasks/${task.spawned_by_task.id}`} className="inline-flex items-center gap-1.5 text-[13px] text-zinc-500 hover:text-zinc-300 transition-colors">
          <ArrowLeft className="size-3.5" />Back to: {task.spawned_by_task.title}
        </Link>
      ) : (
        <button onClick={() => navigate("/tasks")} className="inline-flex items-center gap-1.5 text-[13px] text-zinc-500 hover:text-zinc-300 transition-colors">
          <ArrowLeft className="size-3.5" />Back to tasks
        </button>
      )}

      {/* Header */}
      <div className="space-y-3">
        <div className="flex items-start justify-between gap-3">
          <h1 className="text-xl font-semibold text-zinc-100 leading-tight">{task.title}</h1>
          <div className="flex items-center gap-1 shrink-0">
            {task.status === "pending" && task.assignee !== "human" && (
              <Button
                onClick={handleRun}
                disabled={!task.deps_ready || runPending}
                size="sm"
                className="bg-indigo-600 hover:bg-indigo-500 text-white gap-1.5 h-7 text-xs px-2.5 disabled:opacity-40"
                title={!task.deps_ready ? "Blocked — dependencies not yet complete" : undefined}
              >
                {runPending ? <Loader className="size-3 animate-spin" /> : (!task.deps_ready ? <Lock className="size-3" /> : <Play className="size-3" />)}
                {runPending ? "Starting…" : (!task.deps_ready ? "Blocked" : "Run")}
              </Button>
            )}
            {(task.status === "cancelled" || task.status === "failed") && task.assignee !== "human" && (
              <Button
                onClick={handleReplan}
                disabled={replanPending}
                size="sm"
                className="bg-amber-600/80 hover:bg-amber-500/80 text-white gap-1.5 h-7 text-xs px-2.5"
                title="Ask the AI to decompose this task into independent subtasks, then run each one separately"
              >
                <ListTree className="size-3" />
                {replanPending ? "Breaking down…" : "Break into Subtasks"}
              </Button>
            )}
            {(task.status === "completed" || task.status === "in_review" || task.status === "cancelled" || task.status === "failed") && task.assignee !== "human" && (
              <Button
                onClick={handleRerun}
                disabled={rerunPending}
                size="sm"
                className="bg-zinc-700 hover:bg-zinc-600 text-zinc-200 gap-1.5 h-7 text-xs px-2.5"
                title="Reset to pending and run again from scratch"
              >
                {rerunPending ? <Loader className="size-3 animate-spin" /> : <RotateCcw className="size-3" />}
                {rerunPending ? "Restarting…" : "Rerun"}
              </Button>
            )}
            <Button
              variant="ghost"
              size="sm"
              onClick={handleDelete}
              onBlur={() => setConfirmDelete(false)}
              className={`h-7 px-2 gap-1 text-xs transition-colors ${confirmDelete ? "text-red-300 bg-red-500/15 hover:bg-red-500/20" : "text-zinc-500 hover:text-red-400 hover:bg-red-500/10"}`}
              title="Delete task"
            >
              <Trash2 className="size-3.5" />
              {confirmDelete && <span>Confirm?</span>}
            </Button>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {/* Clickable status badge with dropdown */}
          <div className="relative" ref={statusRef}>
            <button
              onClick={() => setStatusOpen((o) => !o)}
              className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full border text-xs font-medium transition-colors ${STATUS_BADGE[task.status]} hover:opacity-80`}
            >
              <StatusIcon status={task.status} className="size-3" />
              {STATUS_LABELS[task.status]}
              <ChevronDown className="size-3 opacity-60" />
            </button>
            {statusOpen && (
              <div className="absolute left-0 top-full mt-1 z-50 min-w-[140px] rounded-lg border border-zinc-700/60 bg-zinc-900 shadow-xl py-1">
                {(["pending", "in_progress", "in_review", "completed", "failed", "cancelled"] as const).map((s) => (
                  <button
                    key={s}
                    onClick={() => handleStatusChange(s)}
                    disabled={task.status === s}
                    className={`w-full flex items-center gap-2 px-3 py-1.5 text-xs text-left transition-colors ${
                      task.status === s
                        ? "text-zinc-400 bg-zinc-800/60 cursor-default"
                        : "text-zinc-300 hover:bg-zinc-800/80 hover:text-zinc-100"
                    }`}
                  >
                    <StatusIcon status={s} className="size-3" />
                    {STATUS_LABELS[s]}
                  </button>
                ))}
              </div>
            )}
          </div>
          <Badge className={`${PRIORITY_BADGE[task.priority] ?? PRIORITY_BADGE.low} border text-xs capitalize`}>{task.priority}</Badge>
          {task.assignee === "human" && (
            <Badge className="bg-orange-500/15 text-orange-400 border-orange-500/20 border text-xs gap-1">
              <User className="size-3" />Assigned to you
            </Badge>
          )}
          <CopyableTaskId taskId={task.id} />
          <span className="text-xs text-zinc-600">{timeAgo(task.created_at)}</span>
          {task.created_by && <span className="text-xs text-zinc-600">by {task.created_by}</span>}
        </div>
      </div>

      {/* Properties */}
      <div className="flex flex-wrap gap-x-6 gap-y-2 text-[13px]">
        {task.target_repo && (
          <div className="flex items-center gap-1.5 text-zinc-500">
            <span className="text-zinc-600">Repo</span>
            <span className="font-mono text-zinc-400" title={task.target_repo}>{task.target_repo.split("/").pop() || task.target_repo}</span>
          </div>
        )}
        {task.pr_url && (
          <a href={task.pr_url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1.5 text-indigo-400 hover:text-indigo-300 transition-colors">
            <GitPullRequest className="size-3.5" />
            {extractPrNumber(task.pr_url)}
            <ExternalLink className="size-3" />
          </a>
        )}
        {task.deployed_at ? (
          <div className="inline-flex items-center gap-1.5 text-emerald-400" title={`Deployed at ${task.deployed_at}`}>
            <Rocket className="size-3.5" />
            <span>Deployed {timeAgo(task.deployed_at)}</span>
          </div>
        ) : task.merged_at ? (
          <div className="inline-flex items-center gap-1.5 text-amber-400" title={`Merged at ${task.merged_at}`}>
            <Loader className="size-3.5 animate-spin" />
            <span>Merged — deploying…</span>
          </div>
        ) : null}
        {task.model && (
          <div className="flex items-center gap-1.5 text-zinc-500">
            <Cpu className="size-3.5" />
            <span className="text-zinc-400 capitalize">{task.model}</span>
          </div>
        )}
        {task.role && (
          <div className="flex items-center gap-1.5 text-zinc-500">
            <span className="text-zinc-600">Role</span>
            <span className="text-zinc-400">{roleLabel(task.role)}</span>
          </div>
        )}
        {task.runtime != null && task.runtime > 0 && (
          <div className="flex items-center gap-1.5 text-[13px] text-zinc-500">
            <Clock className="size-3.5" />
            <span>{task.runtime < 60 ? `${task.runtime}s` : `${Math.floor(task.runtime / 60)}m ${Math.round(task.runtime % 60)}s`}</span>
          </div>
        )}
        {task.tokens && <TokenBadge tokens={task.tokens} />}
        <Link to={`/activity?task_id=${task.id}`} className="inline-flex items-center gap-1.5 text-[13px] text-zinc-500 hover:text-zinc-300 transition-colors">
          <Activity className="size-3.5" />
          <span>Activity log</span>
        </Link>
        {task.spawned_by_task && (
          <Link to={`/tasks/${task.spawned_by_task.id}`} className="inline-flex items-center gap-1.5 text-[13px] text-zinc-500 hover:text-zinc-300 transition-colors" title="This task was created by another task's agent">
            <Zap className="size-3.5 text-amber-500/70" />
            <span>Spawned by: {task.spawned_by_task.title}</span>
          </Link>
        )}
        {task.tags.length > 0 && task.tags.map(tag => (
          <Badge key={tag} variant="secondary" className="text-[11px] h-5 bg-zinc-800/60 text-zinc-400 border-zinc-700/40">{tag}</Badge>
        ))}
      </div>

      <div className="border-t border-zinc-800/60" />

      {/* Blocked by */}
      {task.dep_tasks && task.dep_tasks.length > 0 && (
        <>
          <div className={`rounded-lg border px-3.5 py-3 space-y-2 ${
            !task.deps_ready ? "border-amber-500/30 bg-amber-500/5" : "border-zinc-800/60 bg-zinc-900/20"
          }`}>
            <div className="flex items-center gap-2">
              <Lock className={`size-3.5 ${!task.deps_ready ? "text-amber-500" : "text-zinc-600"}`} />
              <span className={`text-[12px] font-medium ${!task.deps_ready ? "text-amber-400" : "text-zinc-500"}`}>
                {task.deps_ready ? "Dependencies met" : "Blocked — waiting on"}
              </span>
            </div>
            <div className="space-y-1">
              {task.dep_tasks.map((dep) => (
                <Link key={dep.id} to={`/tasks/${dep.id}`} className="flex items-center gap-2 text-[13px] hover:text-zinc-100 transition-colors group">
                  <StatusIcon status={dep.status as TaskStatus} className="size-3.5" />
                  <span className={dep.status === "completed" || dep.status === "in_review" ? "text-zinc-500 line-through" : "text-zinc-300"}>
                    {dep.title}
                  </span>
                </Link>
              ))}
            </div>
          </div>
          <div className="border-t border-zinc-800/60" />
        </>
      )}

      {/* Subtasks */}
      {task.subtasks.length > 0 && (
        <>
          <div className="space-y-2">
            <h2 className="text-xs font-medium uppercase tracking-wider text-zinc-500">Subtasks</h2>
            <div className="space-y-px rounded-lg overflow-hidden border border-zinc-800/60">
              {task.subtasks.map((sub) => (
                <Link key={sub.id} to={`/tasks/${sub.id}`} className="flex items-center gap-3 px-3 py-2.5 bg-zinc-900/40 hover:bg-zinc-800/50 transition-colors group">
                  <StatusIcon status={sub.status} className="size-3.5" />
                  <span className="flex-1 text-[13px] text-zinc-300 truncate group-hover:text-zinc-100">{sub.title}</span>
                  {sub.role && <span className="text-[11px] text-zinc-600 bg-zinc-800/60 rounded px-1.5 py-0.5">{roleLabel(sub.role)}</span>}
                  {sub.model && <span className="text-[11px] text-zinc-600 bg-zinc-800/60 rounded px-1.5 py-0.5">{sub.model}</span>}
                  <span className="text-[11px] text-zinc-600 tabular-nums">{timeAgo(sub.updated_at)}</span>
                </Link>
              ))}
            </div>
          </div>
          <div className="border-t border-zinc-800/60" />
        </>
      )}

      {/* Spawned Tasks */}
      {task.spawned_tasks && task.spawned_tasks.length > 0 && (
        <>
          <div className="space-y-2">
            <h2 className="text-xs font-medium uppercase tracking-wider text-zinc-500 flex items-center gap-1.5">
              <Zap className="size-3 text-amber-500/70" />
              Spawned Tasks
            </h2>
            <div className="space-y-px rounded-lg overflow-hidden border border-amber-500/20">
              {task.spawned_tasks.map((t) => (
                <Link key={t.id} to={`/tasks/${t.id}`} className="flex items-center gap-3 px-3 py-2.5 bg-amber-500/5 hover:bg-amber-500/10 transition-colors group">
                  <StatusIcon status={t.status} className="size-3.5" />
                  <span className="flex-1 text-[13px] text-zinc-300 truncate group-hover:text-zinc-100">{t.title}</span>
                  {t.role && <span className="text-[11px] text-zinc-600 bg-zinc-800/60 rounded px-1.5 py-0.5">{roleLabel(t.role)}</span>}
                  {t.model && <span className="text-[11px] text-zinc-600 bg-zinc-800/60 rounded px-1.5 py-0.5">{t.model}</span>}
                  <span className="text-[11px] text-zinc-600 tabular-nums">{timeAgo(t.updated_at)}</span>
                </Link>
              ))}
            </div>
          </div>
          <div className="border-t border-zinc-800/60" />
        </>
      )}

      {/* Description */}
      {task.description && (
        <div className="space-y-2">
          <h2 className="text-xs font-medium uppercase tracking-wider text-zinc-500">Description</h2>
          <div className="text-sm text-zinc-300">
            <Markdown>{task.description}</Markdown>
          </div>
        </div>
      )}

      {/* Agent Output */}
      {task.agent_output && (
        <div className="space-y-2">
          <h2 className="text-xs font-medium uppercase tracking-wider text-zinc-500">Agent Output</h2>
          <div className="rounded-lg bg-zinc-900/50 border border-zinc-800/60 p-4 overflow-x-auto">
            <Markdown>{task.agent_output}</Markdown>
          </div>
        </div>
      )}

      {/* Comments */}
      <div className="space-y-3">
        <h2 className="text-xs font-medium uppercase tracking-wider text-zinc-500">
          Comments {task.comments.length > 0 && `(${task.comments.length})`}
        </h2>
        {task.comments.length === 0 && (
          <p className="text-[13px] text-zinc-600">No comments yet.</p>
        )}
        {task.comments.map((c, i) => {
          const isAgent = c.author === "agent"
          return (
            <div key={i} className="flex gap-3">
              <div className={`shrink-0 mt-0.5 size-7 rounded-full flex items-center justify-center ${isAgent ? "bg-indigo-500/20 text-indigo-400" : "bg-zinc-700/50 text-zinc-400"}`}>
                {isAgent ? <Bot className="size-3.5" /> : <span className="text-[11px] font-semibold uppercase">{c.author.slice(0, 1)}</span>}
              </div>
              <div className="flex-1 min-w-0 rounded-lg bg-zinc-900/30 border border-zinc-800/40 px-3 py-2.5 space-y-1">
                <div className="flex items-center gap-2 text-[11px] text-zinc-500">
                  <span className={`font-medium ${isAgent ? "text-indigo-400" : "text-zinc-400"}`}>
                    {isAgent ? "Agent" : c.author}
                  </span>
                  <span>·</span>
                  <span>{timeAgo(c.created_at)}</span>
                </div>
                <div className="text-[13px] text-zinc-300">
                  <Markdown>{c.body}</Markdown>
                </div>
              </div>
            </div>
          )
        })}
        {task.reply_pending && (
          <div className="rounded-lg bg-indigo-500/5 border border-indigo-500/20 px-3 py-2.5 flex items-center gap-2">
            <Loader className="size-3.5 text-indigo-400 animate-spin shrink-0" />
            <span className="text-[13px] text-indigo-400">Agent is composing a reply…</span>
          </div>
        )}
        <form onSubmit={handleComment} className="flex gap-2">
          <Textarea
            placeholder="Write a comment…"
            value={commentBody}
            onChange={(e) => setCommentBody(e.target.value)}
            className="min-h-10 h-10 py-2 bg-zinc-900/40 border-zinc-700/60 resize-none focus:h-24 transition-all"
          />
          <Button type="submit" size="sm" disabled={submitting || !commentBody.trim()} className="shrink-0 h-10 px-3">
            <Send className="size-3.5" />
          </Button>
        </form>
      </div>

    </div>
  )
}
