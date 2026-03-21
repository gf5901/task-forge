import { useState, useEffect } from "react"
import { Link } from "react-router-dom"
import { ArrowLeft, DollarSign, Loader } from "lucide-react"
import { fetchBudget, fetchStats } from "@/lib/api"
import type { BudgetStatus, StatsData } from "@/lib/api"

function TokenRow({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex items-center justify-between gap-4 text-[13px] py-1.5 border-b border-zinc-800/40 last:border-0">
      <span className="text-zinc-500">{label}</span>
      <span className="tabular-nums text-zinc-200">{value.toLocaleString()}</span>
    </div>
  )
}

export default function Stats() {
  const [stats, setStats] = useState<StatsData | null>(null)
  const [budget, setBudget] = useState<BudgetStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    Promise.all([fetchStats(), fetchBudget()])
      .then(([s, b]) => {
        if (!cancelled) {
          setStats(s)
          setBudget(b)
          setError(null)
        }
      })
      .catch((e) => {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : "Failed to load stats")
          setStats(null)
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <div className="mx-auto max-w-2xl px-4 py-6 lg:py-10 pb-20 lg:pb-10">
      <Link
        to="/tasks"
        className="mb-6 inline-flex items-center gap-1.5 text-[13px] text-zinc-500 hover:text-zinc-300"
      >
        <ArrowLeft className="size-4" />
        Tasks
      </Link>

      <div className="mb-8 flex items-center gap-3">
        <div className="flex size-10 items-center justify-center rounded-lg bg-indigo-500/15 text-indigo-400">
          <DollarSign className="size-5" />
        </div>
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-zinc-100">Usage &amp; cost</h1>
          <p className="text-[12px] text-zinc-500">Estimated from pipeline token logs (Anthropic-style rates).</p>
        </div>
      </div>

      {loading && (
        <div className="flex min-h-[30vh] items-center justify-center text-zinc-500">
          <Loader className="size-6 animate-spin" />
        </div>
      )}

      {error && !loading && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-[13px] text-red-300">
          {error}
        </div>
      )}

      {stats && !loading && (
        <div className="space-y-8">
          {/* Today */}
          <section>
            <h2 className="mb-3 text-[13px] font-medium uppercase tracking-wide text-zinc-500">Today (UTC)</h2>
            <div className="rounded-lg border border-zinc-800/60 bg-zinc-900/30 px-4 py-3">
              <div className="flex flex-wrap items-baseline gap-2">
                <span className="text-2xl font-semibold tabular-nums text-zinc-100">
                  ~${stats.today.cost_usd.toFixed(2)}
                </span>
                <span className="text-[13px] text-zinc-500">estimated</span>
              </div>
              {budget?.budget_enabled && (
                <>
                  <div className="mt-3 flex justify-between text-[12px] text-zinc-500">
                    <span>Daily cap</span>
                    <span className="tabular-nums">${budget.daily_cap_usd.toFixed(2)}</span>
                  </div>
                  <div className="mt-1.5 h-2 rounded-full bg-zinc-800 overflow-hidden">
                    <div
                      className={`h-full rounded-full transition-all ${
                        budget.remaining_usd <= 0
                          ? "bg-red-500"
                          : budget.spent_today_usd / budget.daily_cap_usd > 0.8
                            ? "bg-yellow-500"
                            : "bg-indigo-500"
                      }`}
                      style={{
                        width: `${Math.min((budget.spent_today_usd / budget.daily_cap_usd) * 100, 100)}%`,
                      }}
                    />
                  </div>
                  <p className="mt-1.5 text-[11px] text-zinc-600">
                    Spent today ~${budget.spent_today_usd.toFixed(2)} · Remaining ~$
                    {budget.remaining_usd.toFixed(2)}
                  </p>
                </>
              )}
              <div className="mt-4 space-y-0">
                <TokenRow label="Input tokens" value={stats.today.inputTokens} />
                <TokenRow label="Output tokens" value={stats.today.outputTokens} />
                <TokenRow label="Cache read" value={stats.today.cacheReadTokens} />
                <TokenRow label="Cache write" value={stats.today.cacheWriteTokens} />
              </div>
            </div>
          </section>

          {/* All time */}
          <section>
            <h2 className="mb-3 text-[13px] font-medium uppercase tracking-wide text-zinc-500">All time</h2>
            <div className="rounded-lg border border-zinc-800/60 bg-zinc-900/30 px-4 py-3">
              <div className="text-xl font-semibold tabular-nums text-zinc-100">
                ~${stats.all_time.cost_usd.toFixed(2)}
              </div>
              <div className="mt-4 space-y-0">
                <TokenRow label="Input tokens" value={stats.all_time.inputTokens} />
                <TokenRow label="Output tokens" value={stats.all_time.outputTokens} />
                <TokenRow label="Cache read" value={stats.all_time.cacheReadTokens} />
                <TokenRow label="Cache write" value={stats.all_time.cacheWriteTokens} />
              </div>
            </div>
          </section>

          {/* 14-day chart */}
          {stats.daily.length > 0 && (() => {
            const maxCost = Math.max(...stats.daily.map((x) => x.cost_usd), 0.01)
            return (
            <section>
              <h2 className="mb-3 text-[13px] font-medium uppercase tracking-wide text-zinc-500">
                Last {stats.daily.length} days (UTC)
              </h2>
              <div className="rounded-lg border border-zinc-800/60 bg-zinc-900/30 px-3 py-4">
                <div className="flex h-28 items-end gap-px">
                  {stats.daily.map((d) => {
                    const pct = Math.max((d.cost_usd / maxCost) * 100, 4)
                    return (
                      <div
                        key={d.date}
                        className="flex flex-1 flex-col justify-end h-full min-w-0 group"
                        title={`${d.date}: $${d.cost_usd.toFixed(4)} · ${d.tokens.toLocaleString()} tokens`}
                      >
                        <div
                          className="w-full rounded-t-sm bg-indigo-500/50 group-hover:bg-indigo-500/80 transition-colors mx-px"
                          style={{ height: `${pct}%`, minHeight: "4px" }}
                        />
                      </div>
                    )
                  })}
                </div>
                <div className="mt-1 flex gap-px">
                  {stats.daily.map((d) => (
                    <div key={`${d.date}-lbl`} className="flex-1 min-w-0 text-center text-[9px] text-zinc-600 tabular-nums">
                      {d.date.slice(5)}
                    </div>
                  ))}
                </div>
              </div>
            </section>
            )
          })()}
        </div>
      )}
    </div>
  )
}
