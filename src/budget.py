"""
Daily budget tracking for agent execution costs.

Estimates cost from token usage (Anthropic pricing) and enforces a daily cap.
Spend is computed from pipeline.log entries for today's date.
"""

import os
from datetime import date, datetime, timezone
from typing import Dict, Optional

from .pipeline_log import read_logs

BUDGET_DAILY_USD = float(os.getenv("BUDGET_DAILY_USD", "0"))

COST_PER_1M = {
    "inputTokens": 3.00,
    "outputTokens": 15.00,
    "cacheReadTokens": 0.30,
    "cacheWriteTokens": 3.75,
}


def estimate_cost(usage: Dict[str, int]) -> float:
    """Estimate USD cost from a token usage dict."""
    total = 0.0
    for key, rate in COST_PER_1M.items():
        tokens = usage.get(key, 0)
        total += tokens * rate / 1_000_000
    return total


def daily_spend(target_date: Optional[date] = None) -> float:
    """Sum estimated cost from today's pipeline log entries."""
    today = target_date or datetime.now(timezone.utc).date()
    today_str = today.isoformat()
    entries = read_logs(limit=5000)
    total = 0.0
    for e in entries:
        ts = e.get("ts", "")
        if not ts.startswith(today_str):
            continue
        extra = e.get("extra", {})
        usage = {}  # type: Dict[str, int]
        for key in COST_PER_1M:
            if key in extra:
                usage[key] = int(extra[key])
        if usage:
            total += estimate_cost(usage)
    return total


def _get_budget_cap():
    # type: () -> float
    """Read budget_daily_usd from runtime config, falling back to env."""
    try:
        from .config import get_settings

        return float(get_settings().get("budget_daily_usd", BUDGET_DAILY_USD))
    except Exception:
        return BUDGET_DAILY_USD


def within_budget() -> bool:
    """Return True if today's spend is under the daily budget cap.

    If BUDGET_DAILY_USD is 0 or unset, the budget is unlimited.
    """
    cap = _get_budget_cap()
    if cap <= 0:
        return True
    return daily_spend() < cap


def budget_status() -> Dict[str, float]:
    """Return current budget status for API/UI consumption."""
    spent = daily_spend()
    cap = _get_budget_cap()
    return {
        "daily_cap_usd": cap,
        "spent_today_usd": round(spent, 4),
        "remaining_usd": round(max(cap - spent, 0), 4) if cap > 0 else -1,
        "budget_enabled": cap > 0,
    }
