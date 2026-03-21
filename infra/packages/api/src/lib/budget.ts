import * as db from "./dynamo.js";

const BUDGET_DAILY_USD = Number(process.env.BUDGET_DAILY_USD ?? "0");

const COST_PER_1M: Record<string, number> = {
  inputTokens: 3.0,
  outputTokens: 15.0,
  cacheReadTokens: 0.3,
  cacheWriteTokens: 3.75,
};

export function estimateCost(usage: Record<string, number>): number {
  let total = 0;
  for (const [key, rate] of Object.entries(COST_PER_1M)) {
    total += (usage[key] ?? 0) * rate / 1_000_000;
  }
  return total;
}

export async function dailySpend(targetDate?: string): Promise<number> {
  const todayStr = targetDate ?? new Date().toISOString().slice(0, 10);
  const entries = await db.readLogs({ limit: 10_000, offset: 0, forStats: true });
  let total = 0;
  for (const e of entries) {
    if (!e.ts.startsWith(todayStr)) continue;
    const usage: Record<string, number> = {};
    for (const key of Object.keys(COST_PER_1M)) {
      const val = e.extra?.[key];
      if (val !== undefined) usage[key] = Number(val);
    }
    if (Object.keys(usage).length > 0) {
      total += estimateCost(usage);
    }
  }
  return total;
}

export async function withinBudget(): Promise<boolean> {
  if (BUDGET_DAILY_USD <= 0) return true;
  return (await dailySpend()) < BUDGET_DAILY_USD;
}

export async function budgetStatus(): Promise<{
  daily_cap_usd: number;
  spent_today_usd: number;
  remaining_usd: number;
  budget_enabled: boolean;
}> {
  const spent = await dailySpend();
  const cap = BUDGET_DAILY_USD;
  return {
    daily_cap_usd: cap,
    spent_today_usd: Math.round(spent * 10000) / 10000,
    remaining_usd: cap > 0 ? Math.round(Math.max(cap - spent, 0) * 10000) / 10000 : -1,
    budget_enabled: cap > 0,
  };
}
