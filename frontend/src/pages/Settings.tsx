import { useState, useEffect } from "react"
import { Settings as SettingsIcon, Save, Loader, ArrowLeft } from "lucide-react"
import { Link } from "react-router-dom"
import { Button } from "@/components/ui/button"
import { fetchSettings, patchSettings } from "@/lib/api"
import type { Settings } from "@/lib/api"
import toast from "react-hot-toast"

const FIELDS: {
  key: keyof Settings
  label: string
  description: string
  min: number
  max: number
  step: number
  unit: string
}[] = [
  {
    key: "max_concurrent_runners",
    label: "Max concurrent agents",
    description: "Maximum number of task agents running simultaneously",
    min: 1,
    max: 4,
    step: 1,
    unit: "",
  },
  {
    key: "min_spawn_interval",
    label: "Cooldown between tasks",
    description: "Minimum seconds between spawning new task executions",
    min: 0,
    max: 3600,
    step: 30,
    unit: "s",
  },
  {
    key: "task_timeout",
    label: "Agent timeout",
    description: "Maximum seconds an agent can run before being terminated",
    min: 60,
    max: 3600,
    step: 60,
    unit: "s",
  },
  {
    key: "budget_daily_usd",
    label: "Daily budget cap",
    description: "Maximum estimated spend per day (0 = unlimited)",
    min: 0,
    max: 1000,
    step: 1,
    unit: "USD",
  },
]

function formatValue(key: keyof Settings, val: number): string {
  if (key === "min_spawn_interval" || key === "task_timeout") {
    if (val >= 60) {
      const m = Math.floor(val / 60)
      const s = val % 60
      return s > 0 ? `${m}m ${s}s` : `${m}m`
    }
    return `${val}s`
  }
  if (key === "budget_daily_usd") {
    return val === 0 ? "Unlimited" : `$${val.toFixed(2)}`
  }
  return String(val)
}

export default function SettingsPage() {
  const [settings, setSettings] = useState<Settings | null>(null)
  const [draft, setDraft] = useState<Partial<Settings>>({})
  const [saving, setSaving] = useState(false)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetchSettings()
      .then((s) => {
        setSettings(s)
        setDraft(s)
      })
      .catch(() => toast.error("Failed to load settings"))
      .finally(() => setLoading(false))
  }, [])

  const hasChanges =
    settings != null &&
    FIELDS.some((f) => draft[f.key] !== settings[f.key])

  async function save() {
    if (!settings) return
    const patch: Partial<Settings> = {}
    for (const f of FIELDS) {
      if (draft[f.key] !== settings[f.key]) {
        patch[f.key] = draft[f.key] as number
      }
    }
    if (Object.keys(patch).length === 0) return
    setSaving(true)
    try {
      const updated = await patchSettings(patch)
      setSettings(updated)
      setDraft(updated)
      toast.success("Settings saved")
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Failed to save settings")
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader className="size-5 animate-spin text-zinc-500" />
      </div>
    )
  }

  return (
    <div className="max-w-xl mx-auto">
      <div className="flex items-center gap-3 mb-6">
        <Link to="/tasks" className="text-zinc-500 hover:text-zinc-300 transition-colors">
          <ArrowLeft className="size-4" />
        </Link>
        <SettingsIcon className="size-5 text-zinc-400" />
        <h1 className="text-lg font-medium text-zinc-200">Settings</h1>
      </div>

      <div className="space-y-5">
        {FIELDS.map((field) => {
          const val = draft[field.key] ?? 0
          return (
            <div key={field.key} className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-4">
              <div className="flex items-baseline justify-between mb-1">
                <label className="text-[13px] font-medium text-zinc-300">
                  {field.label}
                </label>
                <span className="text-[12px] tabular-nums text-indigo-400">
                  {formatValue(field.key, val)}
                </span>
              </div>
              <p className="text-[11px] text-zinc-600 mb-3">{field.description}</p>
              <div className="flex items-center gap-3">
                <input
                  type="range"
                  min={field.min}
                  max={field.max}
                  step={field.step}
                  value={val}
                  onChange={(e) =>
                    setDraft((d) => ({
                      ...d,
                      [field.key]: Number(e.target.value),
                    }))
                  }
                  className="flex-1 h-1.5 rounded-full appearance-none bg-zinc-800 accent-indigo-500 cursor-pointer
                    [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:h-3.5 [&::-webkit-slider-thumb]:w-3.5
                    [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-indigo-500 [&::-webkit-slider-thumb]:cursor-pointer"
                />
                <input
                  type="number"
                  min={field.min}
                  max={field.max}
                  step={field.step}
                  value={val}
                  onChange={(e) =>
                    setDraft((d) => ({
                      ...d,
                      [field.key]: Number(e.target.value),
                    }))
                  }
                  className="w-20 rounded-md border border-zinc-700 bg-zinc-800 px-2 py-1 text-[12px] tabular-nums text-zinc-300
                    focus:outline-none focus:border-indigo-500 transition-colors"
                />
                {field.unit && (
                  <span className="text-[11px] text-zinc-600">{field.unit}</span>
                )}
              </div>
            </div>
          )
        })}
      </div>

      <div className="mt-6 flex justify-end">
        <Button
          onClick={save}
          disabled={!hasChanges || saving}
          className="gap-2 bg-indigo-600 hover:bg-indigo-500 text-white disabled:opacity-40"
        >
          {saving ? (
            <Loader className="size-3.5 animate-spin" />
          ) : (
            <Save className="size-3.5" />
          )}
          Save changes
        </Button>
      </div>
    </div>
  )
}
