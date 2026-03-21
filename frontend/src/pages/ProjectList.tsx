import { useState, useEffect, useRef } from "react"
import { useNavigate } from "react-router-dom"
import { FolderKanban, Plus, Loader } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { fetchProjects } from "@/lib/api"
import { timeAgo } from "@/lib/time"
import type { ProjectListItem, ProjectStatus } from "@/lib/types"

const STATUS_LABEL: Record<ProjectStatus, string> = {
  active: "Active",
  paused: "Paused",
  completed: "Completed",
}

const STATUS_BADGE: Record<ProjectStatus, string> = {
  active: "bg-emerald-500/15 text-emerald-400 border-emerald-500/20",
  paused: "bg-yellow-500/15 text-yellow-400 border-yellow-500/20",
  completed: "bg-zinc-500/15 text-zinc-400 border-zinc-500/20",
}

export default function ProjectList() {
  const navigate = useNavigate()
  const [projects, setProjects] = useState<ProjectListItem[]>([])
  const [loading, setLoading] = useState(true)

  function load() {
    return fetchProjects()
      .then(({ projects: p }) => {
        setProjects(p)
        setLoading(false)
      })
      .catch(() => {
        setLoading(false)
        setProjects([])
      })
  }

  const loadRef = useRef(load)
  useEffect(() => {
    loadRef.current = load
  })

  useEffect(() => {
    load()
  }, [])

  useEffect(() => {
    function onRefresh() {
      loadRef.current()
    }
    window.addEventListener("ptr:refresh", onRefresh)
    return () => window.removeEventListener("ptr:refresh", onRefresh)
  }, [])

  if (loading) {
    return (
      <div className="flex min-h-[40vh] items-center justify-center text-zinc-500">
        <Loader className="size-5 animate-spin" />
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-3xl px-4 py-6 lg:py-10">
      <div className="mb-6 flex items-center justify-between gap-4">
        <div className="flex items-center gap-2">
          <FolderKanban className="size-5 text-indigo-400" />
          <h1 className="text-lg font-semibold tracking-tight">Projects</h1>
        </div>
        <Button size="sm" className="gap-1.5" onClick={() => navigate("/projects/new")}>
          <Plus className="size-4" />
          New project
        </Button>
      </div>

      {projects.length === 0 ? (
        <p className="text-sm text-zinc-500">No projects yet. Create one to add specs and daily directives.</p>
      ) : (
        <ul className="space-y-2">
          {projects.map((p) => (
            <li key={p.id}>
              <button
                type="button"
                onClick={() => navigate(`/projects/${p.id}`)}
                className="flex w-full flex-col gap-2 rounded-lg border border-zinc-800/60 bg-zinc-900/30 px-4 py-3 text-left transition-colors hover:bg-zinc-800/40"
              >
                <div className="flex items-start justify-between gap-2">
                  <span className="font-medium text-zinc-100">{p.title}</span>
                  <Badge variant="outline" className={`shrink-0 text-[11px] ${STATUS_BADGE[p.status]}`}>
                    {STATUS_LABEL[p.status]}
                  </Badge>
                </div>
                <div className="flex flex-wrap items-center gap-3 text-[12px] text-zinc-500">
                  <span>
                    Tasks: {p.task_done ?? 0}/{p.task_total ?? 0} done
                  </span>
                  {p.last_directive_at && (
                    <span>Last directive {timeAgo(p.last_directive_at)}</span>
                  )}
                  {p.target_repo && (
                    <span className="font-mono text-zinc-600">{p.target_repo}</span>
                  )}
                </div>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
