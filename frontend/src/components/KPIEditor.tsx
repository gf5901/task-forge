import { useState } from "react"
import { Plus, Trash2, Loader, Save } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import type { KPI, KPIDirection } from "@/lib/types"

function slugify(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "").slice(0, 40) || "kpi"
}

function blankKPI(): KPI {
  return { id: "", label: "", target: 0, current: 0, source: "", direction: "up", unit: "" }
}

const DIRECTIONS: { value: KPIDirection; label: string }[] = [
  { value: "up", label: "↑ Up" },
  { value: "down", label: "↓ Down" },
  { value: "maintain", label: "↔ Maintain" },
]

const SOURCES = ["pagespeed", "github", "ga4", "gsc", "manual"]

export default function KPIEditor({
  kpis,
  onSave,
}: {
  kpis: KPI[]
  onSave: (kpis: KPI[]) => Promise<void>
}) {
  const [rows, setRows] = useState<KPI[]>(kpis.length > 0 ? kpis : [])
  const [saving, setSaving] = useState(false)

  function update(idx: number, patch: Partial<KPI>) {
    setRows((prev) => prev.map((r, i) => (i === idx ? { ...r, ...patch } : r)))
  }

  function remove(idx: number) {
    setRows((prev) => prev.filter((_, i) => i !== idx))
  }

  function add() {
    setRows((prev) => [...prev, blankKPI()])
  }

  async function handleSave() {
    const cleaned = rows
      .filter((r) => r.label.trim())
      .map((r) => ({
        ...r,
        id: r.id || slugify(r.label),
        label: r.label.trim(),
        source: r.source.trim() || "manual",
        unit: r.unit.trim(),
      }))
    setSaving(true)
    try {
      await onSave(cleaned)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-3">
      {rows.length === 0 && (
        <p className="text-[13px] text-zinc-500">No KPIs defined yet. Add one to get started.</p>
      )}
      {rows.map((kpi, idx) => (
        <div
          key={idx}
          className="rounded-lg border border-zinc-800/60 bg-zinc-900/30 px-3 py-3 space-y-2"
        >
          <div className="flex items-center gap-2">
            <Input
              value={kpi.label}
              onChange={(e) => update(idx, { label: e.target.value })}
              placeholder="Label (e.g. Monthly visitors)"
              className="flex-1 bg-zinc-900/50 border-zinc-800 text-[13px] h-8"
            />
            <button
              onClick={() => remove(idx)}
              className="shrink-0 p-1.5 text-zinc-600 hover:text-red-400 transition-colors"
              title="Remove KPI"
            >
              <Trash2 className="size-3.5" />
            </button>
          </div>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <div>
              <label className="mb-0.5 block text-[10px] text-zinc-600">Target</label>
              <Input
                type="number"
                value={kpi.target || ""}
                onChange={(e) => update(idx, { target: Number(e.target.value) || 0 })}
                placeholder="0"
                className="bg-zinc-900/50 border-zinc-800 text-[13px] h-8"
              />
            </div>
            <div>
              <label className="mb-0.5 block text-[10px] text-zinc-600">Current</label>
              <Input
                type="number"
                value={kpi.current || ""}
                onChange={(e) => update(idx, { current: Number(e.target.value) || 0 })}
                placeholder="0"
                className="bg-zinc-900/50 border-zinc-800 text-[13px] h-8"
              />
            </div>
            <div>
              <label className="mb-0.5 block text-[10px] text-zinc-600">Unit</label>
              <Input
                value={kpi.unit}
                onChange={(e) => update(idx, { unit: e.target.value })}
                placeholder="visits, ms, %"
                className="bg-zinc-900/50 border-zinc-800 text-[13px] h-8"
              />
            </div>
            <div>
              <label className="mb-0.5 block text-[10px] text-zinc-600">Direction</label>
              <select
                value={kpi.direction}
                onChange={(e) => update(idx, { direction: e.target.value as KPIDirection })}
                className="w-full h-8 rounded-md border border-zinc-800 bg-zinc-900/50 px-2 text-[13px] text-zinc-100"
              >
                {DIRECTIONS.map((d) => (
                  <option key={d.value} value={d.value}>{d.label}</option>
                ))}
              </select>
            </div>
          </div>
          <div>
            <label className="mb-0.5 block text-[10px] text-zinc-600">Source</label>
            <select
              value={SOURCES.includes(kpi.source) ? kpi.source : "manual"}
              onChange={(e) => update(idx, { source: e.target.value })}
              className="w-full h-8 rounded-md border border-zinc-800 bg-zinc-900/50 px-2 text-[13px] text-zinc-100"
            >
              {SOURCES.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          </div>
        </div>
      ))}
      <div className="flex items-center gap-2 pt-1">
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="gap-1.5 text-[12px] h-8"
          onClick={add}
        >
          <Plus className="size-3.5" />
          Add KPI
        </Button>
        <Button
          type="button"
          size="sm"
          className="gap-1.5 text-[12px] h-8 bg-indigo-600 hover:bg-indigo-500"
          disabled={saving}
          onClick={() => void handleSave()}
        >
          {saving ? <Loader className="size-3.5 animate-spin" /> : <Save className="size-3.5" />}
          Save KPIs
        </Button>
      </div>
    </div>
  )
}
