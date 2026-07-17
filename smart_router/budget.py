"""
Monthly per-provider budget tracking (USD).

Stores spend in `budget.json` next to `config.yaml`. Format:
    {"2026-07": {"zai": 0.0, "openrouter": 0.0123, ...}}

A cap of 0 (the default) means "unlimited".
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
BUDGET_PATH = HERE.parent / "budget.json"


def _path() -> Path:
    return BUDGET_PATH


def load_budget() -> dict:
    p = _path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def save_budget(b: dict) -> None:
    _path().write_text(json.dumps(b, indent=2))


def spend_for_current_month() -> dict[str, float]:
    b = load_budget()
    return b.get(time.strftime("%Y-%m"), {})


def spend_for_months(n: int = 1) -> dict[str, dict[str, float]]:
    """Return spend data for the last N months.
    Returns {month_key: {provider: cost, ...}, ...}
    Most recent month first.
    """
    b = load_budget()
    months = sorted(b.keys(), reverse=True)[:n]
    return {m: b[m] for m in months}


def check(provider: str, est_cost: float, cap_usd: float) -> bool:
    """Return True if spending `est_cost` more on `provider` stays under cap."""
    if not cap_usd:
        return True
    return spend_for_current_month().get(provider, 0.0) + est_cost <= cap_usd


def record(provider: str, cost: float) -> None:
    b = load_budget()
    key = time.strftime("%Y-%m")
    b.setdefault(key, {})
    b[key][provider] = b[key].get(provider, 0.0) + cost
    save_budget(b)
