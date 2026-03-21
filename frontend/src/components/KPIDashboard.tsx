import { useMemo } from "react"
import type { KPI } from "@/lib/types"
import type { Snapshot } from "@/lib/api"
import { ArrowUp, ArrowDown, Minus, TrendingUp, TrendingDown } from "lucide-react"

function directionIcon(dir: string) {
  if (dir === "up") return <ArrowUp className="size-3.5 text-emerald-400" />
  if (dir === "down") return <ArrowDown className="size-3.5 text-emerald-400" />
  return <Minus className="size-3.5 text-zinc-500" />
}

function statusColor(kpi: KPI): string {
  if (kpi.direction === "maintain") {
    const diff = Math.abs(kpi.current - kpi.target)
    const pct = kpi.target > 0 ? diff / kpi.target : 0
    if (pct <= 0.05) return "text-emerald-400"
    if (pct <= 0.15) return "text-amber-400"
    return "text-red-400"
  }
  const ratio = kpi.target > 0 ? kpi.current / kpi.target : 0
  if (ratio >= 0.9) return "text-emerald-400"
  if (ratio >= 0.5) return "text-amber-400"
  return "text-red-400"
}

function progressPct(kpi: KPI): number {
  if (kpi.target <= 0) return 0
  return Math.min(100, Math.round((kpi.current / kpi.target) * 100))
}

function Sparkline({ data, className }: { data: (number | null)[]; className?: string }) {
  const filtered = data.filter((v): v is number => v !== null)
  if (filtered.length < 2) return null

  const min = Math.min(...filtered)
  const max = Math.max(...filtered)
  const range = max - min || 1
  const w = 80
  const h = 24
  const step = w / (filtered.length - 1)

  const points = filtered.map((v, i) => {
    const x = i * step
    const y = h - ((v - min) / range) * (h - 4) - 2
    return `${x},${y}`
  })

  const trending = filtered[filtered.length - 1] >= filtered[0]

  return (
    <svg width={w} height={h} className={className} viewBox={`0 0 ${w} ${h}`}>
      <polyline
        points={points.join(" ")}
        fill="none"
        stroke={trending ? "#34d399" : "#f87171"}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

function trendIndicator(data: (number | null)[]) {
  const filtered = data.filter((v): v is number => v !== null)
  if (filtered.length < 2) return null
  const last = filtered[filtered.length - 1]
  const prev = filtered[filtered.length - 2]
  const diff = last - prev
  if (Math.abs(diff) < 0.01) return null
  if (diff > 0) return <TrendingUp className="size-3.5 text-emerald-400" />
  return <TrendingDown className="size-3.5 text-red-400" />
}

export default function KPIDashboard({
  kpis,
  snapshots,
}: {
  kpis: KPI[]
  snapshots: Snapshot[]
}) {
  const sparklineData = useMemo(() => {
    const map: Record<string, (number | null)[]> = {}
    const sorted = [...snapshots].sort((a, b) => a.date.localeCompare(b.date))
    for (const kpi of kpis) {
      map[kpi.id] = sorted.map((s) => s.kpi_readings[kpi.id] ?? null)
    }
    return map
  }, [kpis, snapshots])

  if (kpis.length === 0) {
    return <p className="text-[13px] text-zinc-500">No KPIs defined yet. Click Edit to add some.</p>
  }

  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {kpis.map((kpi) => {
        const pct = progressPct(kpi)
        const color = statusColor(kpi)
        return (
          <div
            key={kpi.id}
            className="rounded-lg border border-zinc-800/60 bg-zinc-900/30 px-4 py-3"
          >
            <div className="mb-1 flex items-center justify-between">
              <span className="text-[12px] text-zinc-500">{kpi.label}</span>
              {directionIcon(kpi.direction)}
            </div>
            <div className="flex items-baseline gap-2">
              <span className={`text-xl font-semibold tabular-nums ${color}`}>
                {kpi.current.toLocaleString()}
              </span>
              <span className="text-[12px] text-zinc-600">
                / {kpi.target.toLocaleString()} {kpi.unit}
              </span>
              {trendIndicator(sparklineData[kpi.id] ?? [])}
            </div>
            <div className="mt-2 flex items-center gap-3">
              <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-zinc-800">
                <div
                  className={`h-full rounded-full transition-all ${
                    pct >= 90
                      ? "bg-emerald-500"
                      : pct >= 50
                        ? "bg-amber-500"
                        : "bg-red-500"
                  }`}
                  style={{ width: `${pct}%` }}
                />
              </div>
              <Sparkline data={sparklineData[kpi.id] ?? []} className="shrink-0 opacity-60" />
            </div>
          </div>
        )
      })}
    </div>
  )
}
