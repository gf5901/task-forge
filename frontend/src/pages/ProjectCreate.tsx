import { useState, useEffect } from "react"
import { useNavigate, Link } from "react-router-dom"
import toast from "react-hot-toast"
import { ArrowLeft, Loader, Sparkles } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { createProject, fetchRepos, generateProjectSpec } from "@/lib/api"
import type { TaskPriority } from "@/lib/types"

const PRIORITIES: TaskPriority[] = ["low", "medium", "high", "urgent"]

export default function ProjectCreate() {
  const navigate = useNavigate()
  const [title, setTitle] = useState("")
  const [spec, setSpec] = useState("")
  const [priority, setPriority] = useState<TaskPriority>("medium")
  const [targetRepo, setTargetRepo] = useState("")
  const [repos, setRepos] = useState<string[]>([])
  const [submitting, setSubmitting] = useState(false)
  const [genOpen, setGenOpen] = useState(false)
  const [genPrompt, setGenPrompt] = useState("")
  const [generating, setGenerating] = useState(false)
  const [autopilot, setAutopilot] = useState(false)

  useEffect(() => {
    fetchRepos()
      .then((r) => setRepos(r.repos))
      .catch(() => {})
  }, [])

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!title.trim()) {
      toast.error("Title is required")
      return
    }
    setSubmitting(true)
    try {
      const p = await createProject({
        title: title.trim(),
        spec: spec.trim(),
        priority,
        target_repo: targetRepo.trim(),
        autopilot,
      })
      toast.success("Project created")
      navigate(`/projects/${p.id}`)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to create")
    } finally {
      setSubmitting(false)
    }
  }

  async function handleGenerateSpec() {
    if (!genPrompt.trim()) {
      toast.error("Describe what you want in the spec")
      return
    }
    setGenerating(true)
    try {
      const { spec: next } = await generateProjectSpec(
        genPrompt.trim(),
        spec.trim() || undefined,
      )
      setSpec(next)
      setGenOpen(false)
      setGenPrompt("")
      toast.success("Spec generated — review before creating")
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Generation failed")
    } finally {
      setGenerating(false)
    }
  }

  return (
    <div className="mx-auto max-w-2xl px-4 py-6 lg:py-10">
      <Link
        to="/projects"
        className="mb-6 inline-flex items-center gap-1.5 text-[13px] text-zinc-500 hover:text-zinc-300"
      >
        <ArrowLeft className="size-4" />
        Projects
      </Link>

      <h1 className="mb-6 text-lg font-semibold tracking-tight">New project</h1>

      <form onSubmit={handleSubmit} className="space-y-5">
        <div>
          <label className="mb-1.5 block text-[13px] text-zinc-400">Title</label>
          <Input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Project name"
            className="bg-zinc-900/50 border-zinc-800"
          />
        </div>

        <div>
          <div className="mb-1.5 flex flex-wrap items-center justify-between gap-2">
            <label className="block text-[13px] text-zinc-400">Spec (markdown)</label>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-7 gap-1 border-zinc-700 text-[12px] text-zinc-300"
              onClick={() => setGenOpen((v) => !v)}
            >
              <Sparkles className="size-3.5" />
              {genOpen ? "Hide" : "Generate from prompt"}
            </Button>
          </div>
          {genOpen && (
            <div className="mb-3 space-y-2 rounded-md border border-zinc-800 bg-zinc-900/40 p-3">
              <p className="text-[12px] text-zinc-500">
                {spec.trim()
                  ? "Uses your current spec as context and refines it."
                  : "Describe the product or system; a full markdown spec will be drafted."}
              </p>
              <Textarea
                value={genPrompt}
                onChange={(e) => setGenPrompt(e.target.value)}
                placeholder="e.g. A task bot with Discord + web UI, DynamoDB, Lambda API…"
                rows={3}
                className="resize-y bg-zinc-950/50 border-zinc-800 text-[13px]"
              />
              <Button
                type="button"
                size="sm"
                className="gap-1.5"
                disabled={generating}
                onClick={() => void handleGenerateSpec()}
              >
                {generating && <Loader className="size-3.5 animate-spin" />}
                Generate
              </Button>
            </div>
          )}
          <Textarea
            value={spec}
            onChange={(e) => setSpec(e.target.value)}
            placeholder="Evolving product / technical spec for agents…"
            rows={12}
            className="resize-y bg-zinc-900/50 border-zinc-800 font-mono text-[13px]"
          />
        </div>

        <div>
          <label className="mb-1.5 block text-[13px] text-zinc-400">Priority</label>
          <select
            value={priority}
            onChange={(e) => setPriority(e.target.value as TaskPriority)}
            className="w-full rounded-md border border-zinc-800 bg-zinc-900/50 px-3 py-2 text-[13px] text-zinc-100"
          >
            {PRIORITIES.map((pr) => (
              <option key={pr} value={pr}>
                {pr}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className="flex cursor-pointer items-center gap-2 text-[13px] text-zinc-400">
            <input
              type="checkbox"
              checked={autopilot}
              onChange={(e) => setAutopilot(e.target.checked)}
              className="rounded border-zinc-600 bg-zinc-900"
            />
            Autopilot — daily proposed plan (7 AM UTC), you approve once, then tasks run via poller
          </label>
        </div>

        <div>
          <label className="mb-1.5 block text-[13px] text-zinc-400">Target repo</label>
          <input
            list="repo-options"
            value={targetRepo}
            onChange={(e) => setTargetRepo(e.target.value)}
            placeholder="Select or type a new repo name"
            className="w-full rounded-md border border-zinc-800 bg-zinc-900/50 px-3 py-2 text-[13px] text-zinc-100"
          />
          <datalist id="repo-options">
            {repos.map((r) => (
              <option key={r} value={r} />
            ))}
          </datalist>
        </div>

        <div className="flex gap-2 pt-2">
          <Button type="submit" disabled={submitting} className="gap-2">
            {submitting && <Loader className="size-4 animate-spin" />}
            Create
          </Button>
          <Button type="button" variant="ghost" onClick={() => navigate("/projects")}>
            Cancel
          </Button>
        </div>
      </form>
    </div>
  )
}
