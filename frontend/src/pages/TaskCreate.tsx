import { useState, useEffect } from "react"
import { useNavigate } from "react-router-dom"
import { ArrowLeft } from "lucide-react"
import toast from "react-hot-toast"
import { createTask, fetchRepos } from "@/lib/api"
import type { TaskPriority } from "@/lib/types"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { useRoles } from "@/hooks/useRoles"

const priorities: { value: TaskPriority; label: string; color: string; active: string }[] = [
  { value: "low",    label: "Low",    color: "text-zinc-500 border-zinc-800 hover:border-zinc-600", active: "bg-zinc-700/50 text-zinc-200 border-zinc-600" },
  { value: "medium", label: "Medium", color: "text-yellow-500/70 border-zinc-800 hover:border-yellow-600/40", active: "bg-yellow-600/15 text-yellow-400 border-yellow-600/30" },
  { value: "high",   label: "High",   color: "text-orange-500/70 border-zinc-800 hover:border-orange-600/40", active: "bg-orange-600/15 text-orange-400 border-orange-600/30" },
  { value: "urgent", label: "Urgent", color: "text-red-500/70 border-zinc-800 hover:border-red-600/40", active: "bg-red-600/15 text-red-400 border-red-600/30" },
]

const DEFAULT_REPO = "task-forge"

export default function TaskCreate() {
  const navigate = useNavigate()
  const { roles } = useRoles()
  const [title, setTitle] = useState("")
  const [description, setDescription] = useState("")
  const [priority, setPriority] = useState<TaskPriority>("medium")
  const [tags, setTags] = useState("")
  const [role, setRole] = useState("")
  const [model, setModel] = useState("")
  const [repo, setRepo] = useState(DEFAULT_REPO)
  const [knownRepos, setKnownRepos] = useState<string[]>([DEFAULT_REPO])
  const [planOnly, setPlanOnly] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState("")

  useEffect(() => {
    fetchRepos().then(({ repos: fetched }) => {
      const merged = Array.from(new Set([DEFAULT_REPO, ...fetched]))
      setKnownRepos(merged)
    }).catch(() => {})
  }, [])

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!title.trim()) return
    setSubmitting(true)
    setError("")
    try {
      await createTask({
        title: title.trim(),
        description: description.trim() || undefined,
        priority,
        tags: tags.trim() || undefined,
        target_repo: repo.trim() || undefined,
        plan_only: planOnly || undefined,
        role: role || undefined,
        model: model || undefined,
      })
      toast.success("Task created — runner queued")
      navigate("/tasks")
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Failed to create task"
      setError(msg)
      toast.error(msg)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="mx-auto max-w-xl lg:max-w-2xl px-4 sm:px-6 py-6 pb-28 lg:pb-6 space-y-5">
      <button
        onClick={() => navigate("/tasks")}
        className="inline-flex items-center gap-1.5 text-[13px] text-zinc-500 hover:text-zinc-300 transition-colors"
      >
        <ArrowLeft className="size-3.5" />Back to tasks
      </button>

      <h1 className="text-lg font-semibold text-zinc-100">New Task</h1>

      <form onSubmit={handleSubmit} className="space-y-4">
        {error && (
          <div className="rounded-md bg-red-500/10 border border-red-500/20 px-3 py-2 text-[13px] text-red-400">
            {error}
          </div>
        )}

        <div className="space-y-1.5">
          <label className="text-[13px] font-medium text-zinc-400">Title</label>
          <Input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="What needs to be done?"
            required
            className="h-9 bg-zinc-900/50 border-zinc-700/60 text-zinc-200 placeholder:text-zinc-600"
          />
        </div>

        <div className="space-y-1.5">
          <label className="text-[13px] font-medium text-zinc-400">Description</label>
          <Textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Describe the task in detail…"
            rows={4}
            className="bg-zinc-900/50 border-zinc-700/60 text-zinc-200 placeholder:text-zinc-600"
          />
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <div className="space-y-1.5">
            <label className="text-[13px] font-medium text-zinc-400">Priority</label>
            <div className="flex gap-1.5">
              {priorities.map((p) => (
                <button
                  key={p.value}
                  type="button"
                  onClick={() => setPriority(p.value)}
                  className={`flex-1 rounded-md border px-2 py-1.5 text-[13px] font-medium transition-all ${
                    priority === p.value ? p.active : p.color
                  }`}
                >
                  {p.label}
                </button>
              ))}
            </div>
          </div>

          <div className="space-y-1.5">
            <label className="text-[13px] font-medium text-zinc-400">Tags</label>
            <Input
              value={tags}
              onChange={(e) => setTags(e.target.value)}
              placeholder="bug, frontend, urgent (comma-separated)"
              className="h-9 bg-zinc-900/50 border-zinc-700/60 text-zinc-200 placeholder:text-zinc-600"
            />
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {roles.length > 0 && (
            <div className="space-y-1.5">
              <label className="text-[13px] font-medium text-zinc-400">Role</label>
              <select
                value={role}
                onChange={(e) => setRole(e.target.value)}
                className="h-9 w-full rounded-md border border-zinc-700/60 bg-zinc-900/50 px-3 text-[13px] text-zinc-200 transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-500/40 focus:border-indigo-500/60 placeholder:text-zinc-600"
              >
                <option value="" className="bg-zinc-900 text-zinc-400">No role</option>
                {roles.map((r) => (
                  <option key={r.id} value={r.id} className="bg-zinc-900 text-zinc-200">{r.label}</option>
                ))}
              </select>
            </div>
          )}

          <div className="space-y-1.5">
            <label className="text-[13px] font-medium text-zinc-400">Target Repo</label>
            <input
              list="repo-options"
              value={repo}
              onChange={(e) => setRepo(e.target.value)}
              placeholder="Select or type a new repo name"
              className="h-9 w-full rounded-md border border-zinc-700/60 bg-zinc-900/50 px-3 text-[13px] text-zinc-200 transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-500/40 focus:border-indigo-500/60"
            />
            <datalist id="repo-options">
              {knownRepos.map((r) => (
                <option key={r} value={r} />
              ))}
            </datalist>
          </div>

          <div className="space-y-1.5">
            <label className="text-[13px] font-medium text-zinc-400">Model tier</label>
            <select
              value={model}
              onChange={(e) => setModel(e.target.value)}
              className="h-9 w-full rounded-md border border-zinc-700/60 bg-zinc-900/50 px-3 text-[13px] text-zinc-200 transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-500/40 focus:border-indigo-500/60"
            >
              <option value="" className="bg-zinc-900 text-zinc-400">Default</option>
              <option value="fast" className="bg-zinc-900 text-zinc-200">Fast</option>
              <option value="full" className="bg-zinc-900 text-zinc-200">Full</option>
            </select>
          </div>
        </div>

        <button
          type="button"
          onClick={() => setPlanOnly((v) => !v)}
          className={`w-full flex items-start gap-3 rounded-lg border px-3.5 py-3 text-left transition-all ${
            planOnly
              ? "border-indigo-500/40 bg-indigo-500/10"
              : "border-zinc-800/60 bg-zinc-900/30 hover:border-zinc-700"
          }`}
        >
          <div className={`mt-0.5 flex size-4 shrink-0 items-center justify-center rounded border transition-colors ${
            planOnly ? "border-indigo-500 bg-indigo-500" : "border-zinc-600"
          }`}>
            {planOnly && (
              <svg className="size-2.5 text-white" viewBox="0 0 10 10" fill="none">
                <path d="M2 5l2.5 2.5L8 3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            )}
          </div>
          <div>
            <div className={`text-[13px] font-medium ${planOnly ? "text-indigo-300" : "text-zinc-300"}`}>
              Plan only — break into subtasks
            </div>
            <div className="mt-0.5 text-[12px] text-zinc-500 leading-relaxed">
              Instead of executing, the agent decomposes this into independent tasks that run separately. Use for large or complex work that would time out in a single session.
            </div>
          </div>
        </button>

        {/* Desktop: inline buttons */}
        <div className="hidden lg:flex items-center gap-2.5 pt-2">
          <Button
            type="submit"
            disabled={submitting || !title.trim()}
            className="h-9 px-6 text-sm font-medium bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-white rounded-md"
          >
            {submitting ? "Creating…" : planOnly ? "Create & Plan" : "Create Task"}
          </Button>
          <Button
            type="button"
            variant="outline"
            className="h-9 px-6 text-sm font-medium border-zinc-700/70 text-zinc-300 hover:bg-zinc-800 hover:text-zinc-100 bg-transparent rounded-md"
            onClick={() => navigate("/tasks")}
          >
            Cancel
          </Button>
        </div>

        {/* Mobile: fixed bottom action bar */}
        <div className="lg:hidden fixed bottom-0 left-0 right-0 z-50 flex items-center gap-2.5 px-4 py-3.5 bg-zinc-950/95 backdrop-blur border-t border-zinc-800/60 safe-area-pb">
          <Button
            type="submit"
            disabled={submitting || !title.trim()}
            className="flex-1 h-9 text-sm font-medium bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-white rounded-md"
          >
            {submitting ? "Creating…" : planOnly ? "Create & Plan" : "Create Task"}
          </Button>
          <Button
            type="button"
            variant="outline"
            className="flex-1 h-9 text-sm font-medium border-zinc-700/70 text-zinc-300 hover:bg-zinc-800 hover:text-zinc-100 bg-transparent rounded-md"
            onClick={() => navigate("/tasks")}
          >
            Cancel
          </Button>
        </div>
      </form>
    </div>
  )
}
