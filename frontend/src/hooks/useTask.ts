import { useState, useEffect, useCallback, useRef } from "react"
import { fetchTask } from "@/lib/api"
import type { TaskDetail } from "@/lib/types"

interface Result {
  task: TaskDetail | null
  loading: boolean
  error: string | null
  reload: () => Promise<void>
}

export function useTask(id: string | undefined): Result {
  const [task, setTask] = useState<TaskDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const reload = useCallback(async () => {
    if (!id) return
    try {
      const data = await fetchTask(id)
      setTask(data)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load task")
    } finally {
      setLoading(false)
    }
  }, [id])

  // Keep a ref to the latest reload so event listeners always call the current version
  const reloadRef = useRef(reload)
  useEffect(() => { reloadRef.current = reload }, [reload])

  useEffect(() => { reload() }, [reload])

  // Poll every 3s while agent reply is in progress
  useEffect(() => {
    if (!task?.reply_pending) return
    const interval = setInterval(() => reloadRef.current(), 3000)
    return () => clearInterval(interval)
  }, [task?.reply_pending])

  // Reload on pull-to-refresh — uses ref so it never goes stale
  useEffect(() => {
    function onRefresh() { reloadRef.current() }
    window.addEventListener("ptr:refresh", onRefresh)
    return () => window.removeEventListener("ptr:refresh", onRefresh)
  }, [])

  return { task, loading, error, reload }
}
