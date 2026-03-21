import { useState, useEffect, useCallback, useMemo, useRef } from "react"
import { useParams, Link } from "react-router-dom"
import toast from "react-hot-toast"
import {
  ArrowLeft,
  Loader,
  Pencil,
  Send,
  Bot,
  User,
  Circle,
  CheckCircle2,
  XCircle,
  Eye,
  AlertTriangle,
  Sparkles,
  RotateCcw,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Textarea } from "@/components/ui/textarea"
import Markdown from "@/components/Markdown"
import KPIDashboard from "@/components/KPIDashboard"
import KPIEditor from "@/components/KPIEditor"
import ProposalQueue from "@/components/ProposalQueue"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog"
import {
  fetchProjectDetail,
  patchProject,
  postProjectDirective,
  fetchSnapshots,
  generateProjectSpec,
  generateProjectKPIs,
  fetchPlanDetail,
  approvePlan,
  regeneratePlan,
} from "@/lib/api"
import type { Snapshot } from "@/lib/api"
import type { Project, Directive, Task, TaskStatus, KPI, DailyPlan } from "@/lib/types"
import { timeAgo } from "@/lib/time"

const STATUS_BADGE: Record<string, string> = {
  pending: "bg-zinc-500/15 text-zinc-400 border-zinc-500/20",
  in_progress: "bg-yellow-500/15 text-yellow-400 border-yellow-500/20",
  in_review: "bg-violet-500/15 text-violet-400 border-violet-500/20",
  completed: "bg-emerald-500/15 text-emerald-400 border-emerald-500/20",
  cancelled: "bg-red-500/15 text-red-400 border-red-500/20",
}

const STATUS_ICONS: Record<
  TaskStatus,
  { icon: typeof Circle; color: string; spin?: boolean }
> = {
  pending: { icon: Circle, color: "text-zinc-500" },
  in_progress: { icon: Loader, color: "text-yellow-500", spin: true },
  in_review: { icon: Eye, color: "text-violet-400" },
  completed: { icon: CheckCircle2, color: "text-emerald-500" },
  failed: { icon: AlertTriangle, color: "text-amber-500" },
  cancelled: { icon: XCircle, color: "text-red-500" },
}

function TaskStatusIcon({ status }: { status: TaskStatus }) {
  const cfg = STATUS_ICONS[status]
  const Icon = cfg.icon
  return (
    <Icon className={`size-3.5 shrink-0 ${cfg.color} ${cfg.spin ? "animate-spin" : ""}`} />
  )
}

function formatDirectiveWhen(iso: string): string {
  try {
    const d = new Date(iso)
    if (Number.isNaN(d.getTime())) return iso
    return d.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      year: "numeric",
      hour: "numeric",
      minute: "2-digit",
    })
  } catch {
    return iso
  }
}

function EditableTitle({
  value,
  onSave,
}: {
  value: string
  onSave: (title: string) => Promise<void>
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(value)
  const [saving, setSaving] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    setDraft(value)
  }, [value])

  useEffect(() => {
    if (editing) inputRef.current?.select()
  }, [editing])

  const commit = async () => {
    const trimmed = draft.trim()
    if (!trimmed || trimmed === value) {
      setDraft(value)
      setEditing(false)
      return
    }
    setSaving(true)
    try {
      await onSave(trimmed)
      setEditing(false)
    } catch {
      toast.error("Failed to update title")
      setDraft(value)
    } finally {
      setSaving(false)
    }
  }

  if (editing) {
    return (
      <input
        ref={inputRef}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault()
            commit()
          }
          if (e.key === "Escape") {
            setDraft(value)
            setEditing(false)
          }
        }}
        disabled={saving}
        className="w-full bg-transparent text-xl font-semibold tracking-tight text-zinc-100 outline-none border-b border-indigo-500 pb-0.5"
      />
    )
  }

  return (
    <h1
      className="group inline-flex cursor-pointer items-center gap-2 text-xl font-semibold tracking-tight text-zinc-100"
      onClick={() => setEditing(true)}
      title="Click to edit"
    >
      {value}
      <Pencil className="size-3.5 text-zinc-600 opacity-0 transition-opacity group-hover:opacity-100" />
    </h1>
  )
}

export default function ProjectDetail() {
  const { projectId } = useParams<{ projectId: string }>()
  const [project, setProject] = useState<Project | null>(null)
  const [directives, setDirectives] = useState<Directive[]>([])
  const [tasks, setTasks] = useState<Task[]>([])
  const [progress, setProgress] = useState({ total: 0, done: 0 })
  const [loading, setLoading] = useState(true)
  const [editing, setEditing] = useState(false)
  const [draftSpec, setDraftSpec] = useState("")
  const [savingSpec, setSavingSpec] = useState(false)
  const [directiveText, setDirectiveText] = useState("")
  const [directiveOpen, setDirectiveOpen] = useState(false)
  const [sendingDir, setSendingDir] = useState(false)
  const [snapshots, setSnapshots] = useState<Snapshot[]>([])
  const [editingKpis, setEditingKpis] = useState(false)
  const [genSpecOpen, setGenSpecOpen] = useState(false)
  const [genSpecPrompt, setGenSpecPrompt] = useState("")
  const [generatingSpec, setGeneratingSpec] = useState(false)
  const [generatingKpis, setGeneratingKpis] = useState(false)
  const [todayPlan, setTodayPlan] = useState<DailyPlan | null>(null)
  const [planTasks, setPlanTasks] = useState<Task[]>([])
  const [planNotes, setPlanNotes] = useState("")
  const [planActionLoading, setPlanActionLoading] = useState(false)
  const [planDateUtc, setPlanDateUtc] = useState("")
  /** When true, polling must not overwrite the spec textarea (draft). */
  const specEditingRef = useRef(false)
  useEffect(() => {
    specEditingRef.current = editing
  }, [editing])

  const load = useCallback(() => {
    if (!projectId) return Promise.resolve()
    const today = new Date().toISOString().slice(0, 10)
    return Promise.all([
      fetchProjectDetail(projectId),
      fetchSnapshots(projectId),
    ])
      .then(async ([d, s]) => {
        setPlanDateUtc(today)
        setProject(d.project)
        setDirectives(d.directives)
        setTasks(d.tasks)
        setProgress(d.progress)
        if (!specEditingRef.current) {
          setDraftSpec(d.project.spec)
        }
        setSnapshots(s.snapshots)
        if (d.project.autopilot) {
          try {
            const pl = await fetchPlanDetail(projectId, today)
            setTodayPlan(pl.plan)
            setPlanTasks(pl.tasks)
          } catch {
            setTodayPlan(null)
            setPlanTasks([])
          }
        } else {
          setTodayPlan(null)
          setPlanTasks([])
        }
        setLoading(false)
      })
      .catch(() => {
        setLoading(false)
        setProject(null)
      })
  }, [projectId])

  useEffect(() => {
    setLoading(true)
    load()
  }, [load])

  const busy =
    tasks.some((t) => t.status === "pending" || t.status === "in_progress") ||
    (project && !project.awaiting_next_directive && project.active_directive_sk) ||
    (todayPlan?.status === "approved" &&
      planTasks.some((t) => t.status === "pending" || t.status === "in_progress"))

  useEffect(() => {
    if (!busy) return
    const id = window.setInterval(() => {
      load()
    }, 3000)
    return () => window.clearInterval(id)
  }, [busy, load])

  async function saveSpec() {
    if (!projectId || !project) return
    setSavingSpec(true)
    try {
      const p = await patchProject(projectId, { spec: draftSpec })
      setProject(p)
      setGenSpecOpen(false)
      setGenSpecPrompt("")
      setEditing(false)
      toast.success("Spec saved")
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Save failed")
    } finally {
      setSavingSpec(false)
    }
  }

  async function handleGenerateSpec() {
    if (!genSpecPrompt.trim()) {
      toast.error("Describe what you want in the spec")
      return
    }
    setGeneratingSpec(true)
    try {
      const { spec: next } = await generateProjectSpec(
        genSpecPrompt.trim(),
        draftSpec.trim() || undefined,
      )
      setDraftSpec(next)
      setGenSpecOpen(false)
      setGenSpecPrompt("")
      toast.success("Spec generated — review and save when ready")
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Generation failed")
    } finally {
      setGeneratingSpec(false)
    }
  }

  async function sendDirective() {
    if (!projectId || !directiveText.trim()) {
      toast.error("Enter a directive")
      return
    }
    setSendingDir(true)
    try {
      await postProjectDirective(projectId, directiveText.trim())
      setDirectiveText("")
      setDirectiveOpen(false)
      toast.success("Directive sent — decomposing into tasks")
      await load()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed")
    } finally {
      setSendingDir(false)
    }
  }

  const taskMap = useMemo(
    () => Object.fromEntries(tasks.map((t) => [t.id, t])) as Record<string, Task>,
    [tasks],
  )

  if (loading || !project) {
    return (
      <div className="flex min-h-[40vh] items-center justify-center text-zinc-500">
        <Loader className="size-5 animate-spin" />
      </div>
    )
  }

  const reviewCount = tasks.filter((t) => t.status === "in_review").length
  const pct = progress.total > 0 ? Math.round((progress.done / progress.total) * 100) : 0

  const activeTasks = tasks.filter(
    (t) => t.status === "pending" || t.status === "in_progress" || t.status === "in_review",
  )

  const humanTasks = tasks.filter(
    (t) => t.assignee === "human" && !["completed", "cancelled"].includes(t.status),
  )

  return (
    <div className="mx-auto max-w-4xl px-4 py-6 lg:py-10 pb-20 lg:pb-32">
      <Link
        to="/projects"
        className="mb-4 inline-flex items-center gap-1.5 text-[13px] text-zinc-500 hover:text-zinc-300"
      >
        <ArrowLeft className="size-4" />
        Projects
      </Link>

      <div className="mb-6 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <EditableTitle
            value={project.title}
            onSave={async (title) => {
              const p = await patchProject(projectId!, { title })
              setProject(p)
              toast.success("Title updated")
            }}
          />
          <p className="mt-1 text-[12px] text-zinc-500 font-mono">{project.id}</p>
        </div>
        <Badge variant="outline" className="text-[11px]">
          {project.status}
        </Badge>
      </div>

      {/* Progress */}
      <div className="mb-8 rounded-lg border border-zinc-800/60 bg-zinc-900/30 px-4 py-3">
        <div className="mb-2 flex justify-between text-[13px] text-zinc-400">
          <span>
            Progress: {progress.done}/{progress.total} tasks done
          </span>
          {reviewCount > 0 && (
            <span className="text-violet-400">{reviewCount} in review</span>
          )}
        </div>
        <div className="h-2 overflow-hidden rounded-full bg-zinc-800">
          <div
            className="h-full rounded-full bg-indigo-500 transition-all"
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>

      {/* KPI Dashboard */}
      <section className="mb-8">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-[13px] font-medium uppercase tracking-wide text-zinc-500">KPIs</h2>
          <div className="flex items-center gap-1.5">
            <Button
              variant="outline"
              size="sm"
              className="h-7 gap-1 text-[12px] border-zinc-700 text-zinc-300"
              disabled={generatingKpis}
              onClick={async () => {
                if (!projectId) return
                setGeneratingKpis(true)
                try {
                  const { kpis: suggested } = await generateProjectKPIs(projectId)
                  if (suggested.length === 0) {
                    toast("No new KPIs suggested")
                    return
                  }
                  const existing = project.kpis ?? []
                  const existingIds = new Set(existing.map((k) => k.id))
                  const merged = [...existing, ...suggested.filter((k) => !existingIds.has(k.id))]
                  const p = await patchProject(projectId, { kpis: merged })
                  setProject(p)
                  toast.success(`Added ${merged.length - existing.length} KPI(s)`)
                } catch (e) {
                  toast.error(e instanceof Error ? e.message : "Generation failed")
                } finally {
                  setGeneratingKpis(false)
                }
              }}
            >
              {generatingKpis ? <Loader className="size-3 animate-spin" /> : <Sparkles className="size-3" />}
              Generate
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="h-7 gap-1 text-[12px]"
              onClick={() => setEditingKpis((v) => !v)}
            >
              <Pencil className="size-3" />
              {editingKpis ? "Done" : "Edit"}
            </Button>
          </div>
        </div>
        {editingKpis ? (
          <KPIEditor
            kpis={project.kpis ?? []}
            onSave={async (kpis: KPI[]) => {
              if (!projectId) return
              const p = await patchProject(projectId, { kpis })
              setProject(p)
              setEditingKpis(false)
              toast.success("KPIs saved")
            }}
          />
        ) : (
          <KPIDashboard kpis={project.kpis ?? []} snapshots={snapshots} />
        )}
      </section>

      {/* Proposals & Human Requests */}
      {projectId && (project.kpis?.length ?? 0) > 0 && (
        <ProposalQueue projectId={projectId} />
      )}

      {/* Autopilot — daily plan */}
      <section className="mb-10">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-[13px] font-medium uppercase tracking-wide text-zinc-500">
            Autopilot
          </h2>
          <label className="flex cursor-pointer items-center gap-2 text-[12px] text-zinc-400">
            <input
              type="checkbox"
              checked={Boolean(project.autopilot)}
              onChange={async (e) => {
                if (!projectId) return
                try {
                  const p = await patchProject(projectId, { autopilot: e.target.checked })
                  setProject(p)
                  toast.success(e.target.checked ? "Autopilot enabled" : "Autopilot disabled")
                  await load()
                } catch (err) {
                  toast.error(err instanceof Error ? err.message : "Update failed")
                }
              }}
              className="rounded border-zinc-600 bg-zinc-900"
            />
            Daily plan (7 AM UTC) → approve → tasks run via poller
          </label>
        </div>
        {project.autopilot && (
          <div className="rounded-lg border border-zinc-800/60 bg-zinc-900/30 px-4 py-3 space-y-3">
            {!todayPlan ? (
              <p className="text-[13px] text-zinc-500">
                No plan for{" "}
                <span className="font-mono text-zinc-400">
                  {planDateUtc || new Date().toISOString().slice(0, 10)}
                </span>{" "}
                yet. The
                proposal job runs at 7:00 UTC (after metrics). You can also add a directive anytime.
              </p>
            ) : todayPlan.status === "completed" && todayPlan.items.length === 0 ? (
              <div className="space-y-2">
                <p className="text-[12px] font-medium text-zinc-400">Nothing scheduled today</p>
                <div className="prose-custom text-[13px] text-zinc-300">
                  <Markdown>{todayPlan.reflection || "_No reflection._"}</Markdown>
                </div>
              </div>
            ) : todayPlan.status === "proposed" ? (
              <div className="space-y-3">
                <div className="prose-custom text-[13px] text-zinc-400">
                  <Markdown>{todayPlan.reflection || "_Plan reflection._"}</Markdown>
                </div>
                <ul className="space-y-2">
                  {todayPlan.items.map((it, i) => (
                    <li
                      key={i}
                      className="rounded border border-zinc-800/80 bg-zinc-950/40 px-3 py-2 text-[13px]"
                    >
                      <span className="font-medium text-zinc-200">{it.title}</span>
                      <span className="ml-2 text-[11px] text-zinc-500">
                        {it.priority}
                        {it.role ? ` · ${it.role}` : ""}
                      </span>
                      {it.description ? (
                        <p className="mt-1 text-[12px] text-zinc-500 whitespace-pre-wrap">
                          {it.description}
                        </p>
                      ) : null}
                    </li>
                  ))}
                </ul>
                <Textarea
                  value={planNotes}
                  onChange={(e) => setPlanNotes(e.target.value)}
                  placeholder="Optional notes before approve…"
                  rows={2}
                  className="bg-zinc-950/50 border-zinc-800 text-[13px]"
                />
                <div className="flex flex-wrap gap-2">
                  <Button
                    size="sm"
                    disabled={planActionLoading || todayPlan.items.length === 0}
                    onClick={async () => {
                      if (!projectId) return
                      setPlanActionLoading(true)
                      try {
                        await approvePlan(projectId, planDateUtc, planNotes.trim())
                        toast.success("Plan approved — tasks created")
                        setPlanNotes("")
                        await load()
                      } catch (e) {
                        toast.error(e instanceof Error ? e.message : "Approve failed")
                      } finally {
                        setPlanActionLoading(false)
                      }
                    }}
                  >
                    Approve plan
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    className="gap-1 border-zinc-700"
                    disabled={planActionLoading}
                    onClick={async () => {
                      if (!projectId) return
                      setPlanActionLoading(true)
                      try {
                        await regeneratePlan(projectId, planDateUtc)
                        toast.success("Regeneration started on server — refresh in a minute")
                        await load()
                      } catch (e) {
                        toast.error(e instanceof Error ? e.message : "Regenerate failed")
                      } finally {
                        setPlanActionLoading(false)
                      }
                    }}
                  >
                    <RotateCcw className="size-3.5" />
                    Regenerate
                  </Button>
                </div>
              </div>
            ) : todayPlan.status === "approved" ? (
              <div className="space-y-2">
                <p className="text-[12px] text-zinc-500">
                  Plan approved — tasks run through the normal pipeline (poller).
                </p>
                <ul className="space-y-1.5">
                  {planTasks.map((t) => (
                    <li key={t.id}>
                      <Link
                        to={`/tasks/${t.id}`}
                        className="inline-flex items-center gap-2 text-[13px] text-indigo-400 hover:text-indigo-300"
                      >
                        <TaskStatusIcon status={t.status} />
                        {t.title}
                      </Link>
                    </li>
                  ))}
                </ul>
              </div>
            ) : (
              <div className="space-y-2">
                <p className="text-[12px] font-medium text-emerald-500/90">Day plan completed</p>
                {todayPlan.outcome_summary && (
                  <p className="font-mono text-[12px] text-zinc-500">
                    {Object.entries(todayPlan.outcome_summary)
                      .map(([k, v]) => `${k}: ${v}`)
                      .join(" · ")}
                  </p>
                )}
                <div className="prose-custom text-[13px] text-zinc-400">
                  <Markdown>{todayPlan.reflection}</Markdown>
                </div>
              </div>
            )}
          </div>
        )}
      </section>

      {/* Spec */}
      <section className="mb-10">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-[13px] font-medium uppercase tracking-wide text-zinc-500">Spec</h2>
          {!editing ? (
            <Button variant="ghost" size="sm" className="h-8 gap-1 text-[12px]" onClick={() => setEditing(true)}>
              <Pencil className="size-3.5" />
              Edit
            </Button>
          ) : (
            <div className="flex flex-wrap items-center justify-end gap-2">
              <Button
                variant="outline"
                size="sm"
                className="h-8 gap-1 border-zinc-700 text-[12px] text-zinc-300"
                onClick={() => setGenSpecOpen((v) => !v)}
              >
                <Sparkles className="size-3.5" />
                {genSpecOpen ? "Hide" : "Generate"}
              </Button>
              <Button
                size="sm"
                className="h-8 text-[12px]"
                disabled={savingSpec}
                onClick={() => void saveSpec()}
              >
                {savingSpec ? <Loader className="size-3.5 animate-spin" /> : "Save"}
              </Button>
              <Button
                variant="ghost"
                size="sm"
                className="h-8 text-[12px]"
                onClick={() => {
                  setDraftSpec(project.spec)
                  setGenSpecOpen(false)
                  setGenSpecPrompt("")
                  setEditing(false)
                }}
              >
                Cancel
              </Button>
            </div>
          )}
        </div>
        {editing ? (
          <div className="space-y-3">
            {genSpecOpen && (
              <div className="space-y-2 rounded-md border border-zinc-800 bg-zinc-900/40 p-3">
                <p className="text-[12px] text-zinc-500">
                  {draftSpec.trim()
                    ? "Refines the draft below from your instructions."
                    : "Describe the project; a markdown spec will be drafted into the editor."}
                </p>
                <Textarea
                  value={genSpecPrompt}
                  onChange={(e) => setGenSpecPrompt(e.target.value)}
                  placeholder="e.g. Add API design, auth model, and deployment constraints…"
                  rows={3}
                  className="resize-y bg-zinc-950/50 border-zinc-800 text-[13px]"
                />
                <Button
                  type="button"
                  size="sm"
                  className="gap-1.5"
                  disabled={generatingSpec}
                  onClick={() => void handleGenerateSpec()}
                >
                  {generatingSpec && <Loader className="size-3.5 animate-spin" />}
                  Generate
                </Button>
              </div>
            )}
            <Textarea
              value={draftSpec}
              onChange={(e) => setDraftSpec(e.target.value)}
              rows={14}
              className="resize-y bg-zinc-900/50 border-zinc-800 font-mono text-[13px]"
            />
            {busy && (
              <p className="text-[11px] text-zinc-500">
                This page refreshes while work is in progress — click <strong className="text-zinc-400">Save</strong>{" "}
                to persist spec changes.
              </p>
            )}
          </div>
        ) : (
          <div className="prose prose-invert prose-sm max-w-none rounded-lg border border-zinc-800/40 bg-zinc-950/50 p-4">
            {project.spec.trim() ? (
              <Markdown>{project.spec}</Markdown>
            ) : (
              <p className="text-sm text-zinc-500">No spec yet.</p>
            )}
          </div>
        )}
      </section>

      {/* Directive timeline */}
      <section className="mb-10">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-[13px] font-medium uppercase tracking-wide text-zinc-500">
            Directives
          </h2>
          <Button
            variant="outline"
            size="sm"
            className="h-7 gap-1.5 text-[12px] border-zinc-700 text-zinc-300"
            disabled={project.status !== "active"}
            onClick={() => setDirectiveOpen(true)}
          >
            <Send className="size-3.5" />
            New directive
          </Button>
        </div>
        {directives.length === 0 ? (
          <p className="text-sm text-zinc-500">No directives yet.</p>
        ) : (
          <ul className="relative ml-2 border-l border-zinc-800/80 pl-6 space-y-8">
            {directives.map((d) => {
              const isUser = d.author !== "agent"
              return (
                <li key={d.sk} className="relative">
                  <span
                    className={`absolute -left-[25px] top-1.5 size-2.5 rounded-full ring-4 ring-zinc-950 ${
                      isUser ? "bg-zinc-500" : "bg-indigo-500"
                    }`}
                    aria-hidden
                  />
                  <div
                    className={`w-full rounded-lg border px-4 py-3 text-[13px] ${
                      isUser
                        ? "border-zinc-800/60 bg-zinc-900/40"
                        : "border-indigo-500/30 bg-indigo-500/10"
                    }`}
                  >
                    <div className="mb-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-zinc-500">
                      {isUser ? (
                        <User className="size-3.5 shrink-0" />
                      ) : (
                        <Bot className="size-3.5 shrink-0 text-indigo-400" />
                      )}
                      <span className="text-zinc-400">{formatDirectiveWhen(d.created_at)}</span>
                      <span className="text-zinc-600">·</span>
                      <span>{timeAgo(d.created_at)}</span>
                    </div>
                    <div className="prose prose-invert prose-sm max-w-none">
                      <Markdown>{d.content}</Markdown>
                    </div>
                    {d.task_ids.length > 0 && (
                      <div className="mt-3 border-t border-zinc-800/50 pt-3">
                        <p className="mb-2 text-[11px] font-medium uppercase tracking-wide text-zinc-600">
                          Tasks ({d.task_ids.length})
                        </p>
                        <div className="space-y-2">
                                 {d.task_ids.map((tid) => {
                            const t = taskMap[tid]
                            const status: TaskStatus = t?.status ?? "pending"
                            const label = t?.title ?? tid
                            return (
                              <Link
                                key={tid}
                                to={`/tasks/${tid}`}
                                className="flex items-center gap-2 rounded-md border border-zinc-800/70 bg-zinc-950/40 px-2.5 py-1.5 text-[12px] text-zinc-200 transition-colors hover:border-indigo-500/40 hover:bg-zinc-900/50"
                              >
                                <TaskStatusIcon status={status} />
                                <span className="min-w-0 flex-1 truncate">{label}</span>
                                <Badge
                                  variant="outline"
                                  className={`shrink-0 text-[10px] ${STATUS_BADGE[status]}`}
                                >
                                  {status.replace("_", " ")}
                                </Badge>
                              </Link>
                            )
                          })}
                        </div>
                      </div>
                    )}
                  </div>
                </li>
              )
            })}
          </ul>
        )}
      </section>

      {/* Your tasks (human-assigned) */}
      {humanTasks.length > 0 && (
        <section className="mb-10">
          <h2 className="mb-3 flex items-center gap-2 text-[13px] font-medium uppercase tracking-wide text-orange-400">
            <User className="size-4" />
            Your tasks ({humanTasks.length})
          </h2>
          <ul className="space-y-2">
            {humanTasks.map((t) => (
              <li key={t.id}>
                <Link
                  to={`/tasks/${t.id}`}
                  className="flex items-center justify-between gap-3 rounded-md border border-orange-500/20 bg-orange-500/5 px-3 py-2 text-[13px] hover:bg-orange-500/10"
                >
                  <span className="truncate text-zinc-200">{t.title}</span>
                  <Badge variant="outline" className={`text-[10px] ${STATUS_BADGE[t.status]}`}>
                    {t.status.replace("_", " ")}
                  </Badge>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* Active tasks */}
      <section className="mb-10">
        <h2 className="mb-3 text-[13px] font-medium uppercase tracking-wide text-zinc-500">
          Active tasks
        </h2>
        {activeTasks.length === 0 ? (
          <p className="text-sm text-zinc-500">No active tasks for this project.</p>
        ) : (
          <ul className="space-y-2">
            {activeTasks.map((t) => (
              <li key={t.id}>
                <Link
                  to={`/tasks/${t.id}`}
                  className="flex items-center justify-between gap-3 rounded-md border border-zinc-800/50 bg-zinc-900/20 px-3 py-2 text-[13px] hover:bg-zinc-800/30"
                >
                  <span className="truncate text-zinc-200">{t.title}</span>
                  <span className="flex shrink-0 items-center gap-1.5">
                    <TaskStatusIcon status={t.status} />
                    <Badge variant="outline" className={`text-[10px] ${STATUS_BADGE[t.status]}`}>
                      {t.status.replace("_", " ")}
                    </Badge>
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* Directive modal */}
      <Dialog open={directiveOpen} onOpenChange={setDirectiveOpen}>
        <DialogContent className="sm:max-w-lg bg-zinc-950 border border-zinc-800">
          <DialogHeader>
            <DialogTitle>Send directive</DialogTitle>
            <DialogDescription>
              What should the agents focus on next? Supports markdown.
            </DialogDescription>
          </DialogHeader>
          <Textarea
            value={directiveText}
            onChange={(e) => setDirectiveText(e.target.value)}
            placeholder="e.g. Implement user registration with email verification"
            rows={5}
            className="min-h-[120px] bg-zinc-900/50 border-zinc-800 text-[13px]"
            autoFocus
          />
          {busy && (
            <p className="flex items-center gap-2 text-[12px] text-indigo-400">
              <Loader className="size-3.5 animate-spin" />
              Updating tasks…
            </p>
          )}
          <div className="flex justify-end gap-2">
            <Button
              variant="outline"
              size="sm"
              className="border-zinc-700 text-zinc-300"
              onClick={() => setDirectiveOpen(false)}
            >
              Cancel
            </Button>
            <Button
              size="sm"
              className="gap-2"
              disabled={sendingDir || !directiveText.trim()}
              onClick={() => void sendDirective()}
            >
              {sendingDir ? <Loader className="size-4 animate-spin" /> : <Send className="size-4" />}
              Send
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
