import { useState, useEffect, useRef } from "react"
import { useSearchParams, useNavigate } from "react-router-dom"
import {
  Circle,
  Loader,
  CheckCircle2,
  XCircle,
  Eye,
  Plus,
  GitBranch,
  Lock,
  AlertTriangle,
  User,
} from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { fetchTasks } from "@/lib/api"
import { timeAgo } from "@/lib/time"
import type { Task, TaskStatus, TaskPriority } from "@/lib/types"

const STATUS_CONFIG: Record<TaskStatus, { icon: typeof Circle; color: string; spin?: boolean }> = {
  pending: { icon: Circle, color: "text-zinc-500" },
  in_progress: { icon: Loader, color: "text-yellow-500", spin: true },
  in_review: { icon: Eye, color: "text-violet-400" },
  completed: { icon: CheckCircle2, color: "text-emerald-500" },
  failed: { icon: AlertTriangle, color: "text-amber-500" },
  cancelled: { icon: XCircle, color: "text-red-500" },
}

const PRIORITY_COLORS: Record<TaskPriority, string> = {
  urgent: "bg-red-500",
  high: "bg-orange-500",
  medium: "bg-yellow-500",
  low: "bg-zinc-600",
}

const PRIORITY_LEVELS: Record<TaskPriority, number> = {
  urgent: 4, high: 3, medium: 2, low: 1,
}

function PriorityBars({ priority }: { priority: TaskPriority }) {
  const filled = PRIORITY_LEVELS[priority]
  const color = PRIORITY_COLORS[priority]
  return (
    <div className="flex items-end gap-[2px]" title={priority}>
      {[1, 2, 3, 4].map((level) => (
        <div
          key={level}
          className={`w-[3px] rounded-sm ${level <= filled ? color : "bg-zinc-800"}`}
          style={{ height: `${6 + level * 2}px` }}
        />
      ))}
    </div>
  )
}

function StatusIcon({ status }: { status: TaskStatus }) {
  const config = STATUS_CONFIG[status]
  const Icon = config.icon
  return <Icon className={`size-[15px] shrink-0 ${config.color} ${config.spin ? "animate-spin" : ""}`} />
}

function TaskRow({ task, navigate }: { task: Task; navigate: (path: string) => void }) {
  return (
    <button
      key={task.id}
      onClick={() => navigate(`/tasks/${task.id}`)}
      className="flex w-full items-center gap-3 px-2 py-2.5 text-left transition-colors hover:bg-zinc-800/30 rounded-md -mx-2 group"
    >
      {task.status === "pending" && !task.deps_ready
        ? <span title="Blocked — waiting on dependencies"><Lock className="size-[15px] shrink-0 text-zinc-600" /></span>
        : <StatusIcon status={task.status} />
      }
      <PriorityBars priority={task.priority} />

      <div className="flex-1 min-w-0">
        <span className="text-[13px] font-medium text-zinc-300 truncate block group-hover:text-zinc-100">
          {task.title}
        </span>
        {(task.tags.length > 0 || task.target_repo || task.assignee === "human") && (
          <div className="flex items-center gap-1.5 mt-0.5 flex-wrap">
            {task.assignee === "human" && (
              <Badge variant="secondary" className="text-[10px] h-[18px] px-1.5 bg-orange-500/15 text-orange-400 border-orange-500/20">
                <User className="size-2.5 mr-0.5" />you
              </Badge>
            )}
            {task.tags.map((tag) => (
              <Badge key={tag} variant="secondary" className="text-[10px] h-[18px] px-1.5 bg-zinc-800/50 text-zinc-500 border-zinc-700/30">
                {tag}
              </Badge>
            ))}
            {task.target_repo && (
              <span className="inline-flex items-center gap-1 text-[10px] text-zinc-600" title={task.target_repo}>
                <GitBranch className="size-2.5" />{task.target_repo.split("/").pop() || task.target_repo}
              </span>
            )}
          </div>
        )}
      </div>

      <span className="text-[11px] text-zinc-700 shrink-0 tabular-nums">
        {timeAgo(task.created_at)}
      </span>
    </button>
  )
}

const PAGE_SIZE = 25

const STATUS_GROUP_ORDER: TaskStatus[] = ["in_review", "in_progress", "pending", "failed", "completed", "cancelled"]

const STATUS_GROUP_LABELS: Record<TaskStatus, string> = {
  in_review: "In Review",
  in_progress: "In Progress",
  pending: "Pending",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
}

function groupTasksByStatus(tasks: Task[]): { status: TaskStatus; tasks: Task[] }[] {
  const grouped = new Map<TaskStatus, Task[]>()
  for (const task of tasks) {
    if (!grouped.has(task.status)) grouped.set(task.status, [])
    grouped.get(task.status)!.push(task)
  }
  return STATUS_GROUP_ORDER
    .filter((s) => grouped.has(s))
    .map((s) => ({ status: s, tasks: grouped.get(s)! }))
}

export default function TaskList() {
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const status = searchParams.get("status") || "all"

  const [tasks, setTasks] = useState<Task[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)

  function load() {
    return fetchTasks(status, PAGE_SIZE, 0)
      .then(({ tasks: t, total: tot }) => {
        setLoading(false)
        setTasks(t)
        setTotal(tot)
      })
      .catch(() => { setLoading(false); setTasks([]) })
  }

  function loadMore() {
    setLoadingMore(true)
    fetchTasks(status, PAGE_SIZE, tasks.length)
      .then(({ tasks: t, total: tot }) => {
        setTasks((prev) => [...prev, ...t])
        setTotal(tot)
      })
      .finally(() => setLoadingMore(false))
  }

  const loadRef = useRef(load)
  useEffect(() => { loadRef.current = load })

  useEffect(() => { loadRef.current() }, [status])

  useEffect(() => {
    function onRefresh() { loadRef.current() }
    window.addEventListener("ptr:refresh", onRefresh)
    return () => window.removeEventListener("ptr:refresh", onRefresh)
  }, [])

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader className="size-5 animate-spin text-zinc-600" />
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-3xl px-4 sm:px-6 py-4">
      {tasks.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-24 text-center">
          <div className="rounded-full bg-zinc-800/40 p-4 mb-4">
            <CheckCircle2 className="size-7 text-zinc-700" />
          </div>
          <p className="text-sm text-zinc-400 mb-1">No tasks found</p>
          <p className="text-xs text-zinc-600 mb-5">
            {status === "all" ? "Create your first task to get started." : "Nothing here right now."}
          </p>
          <Button variant="outline" size="sm" onClick={() => navigate("/tasks/new")} className="gap-1.5">
            <Plus className="size-3.5" />Create Task
          </Button>
        </div>
      ) : (
        <>
          {status === "all" ? (
            <div className="space-y-6">
              {groupTasksByStatus(tasks).map(({ status: groupStatus, tasks: groupTasks }) => (
                <div key={groupStatus}>
                  <div className="flex items-center gap-2 mb-1 px-2">
                    <StatusIcon status={groupStatus} />
                    <span className="text-[11px] font-semibold uppercase tracking-wider text-zinc-500">
                      {STATUS_GROUP_LABELS[groupStatus]}
                    </span>
                    <span className="text-[11px] text-zinc-700">{groupTasks.length}</span>
                  </div>
                  <div className="divide-y divide-zinc-800/50">
                    {groupTasks.map((task) => (
                      <TaskRow key={task.id} task={task} navigate={navigate} />
                    ))}
                  </div>
                </div>
              ))}
            </div>
          ) : (
          <div className="divide-y divide-zinc-800/50">
            {tasks.map((task) => (
              <TaskRow key={task.id} task={task} navigate={navigate} />
            ))}
          </div>
          )}
          {tasks.length < total && (
            <div className="flex justify-center pt-4 pb-2">
              <Button
                variant="ghost"
                size="sm"
                onClick={loadMore}
                disabled={loadingMore}
                className="text-zinc-500 hover:text-zinc-300 gap-2"
              >
                {loadingMore
                  ? <><Loader className="size-3.5 animate-spin" />Loading…</>
                  : `Load more (${total - tasks.length} remaining)`
                }
              </Button>
            </div>
          )}
        </>
      )}
    </div>
  )
}
