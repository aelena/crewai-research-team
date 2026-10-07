"""Token usage and estimated cost of a run, per model.

CrewAI's ``Crew.calculate_usage_metrics`` adds up each agent's LLM counters, but those counters live on
the LLM instance and this crew shares one instance per model across agents, so the crew-level sum
counts a shared model once per agent using it. Usage here is read once per unique LLM instance.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

PRICES_FILE = Path(__file__).parent / "config" / "prices.yaml"
FIELDS = ("prompt_tokens", "cached_prompt_tokens", "completion_tokens", "total_tokens", "successful_requests")


@lru_cache
def prices() -> dict[str, dict[str, float]]:
    return yaml.safe_load(PRICES_FILE.read_text(encoding="utf-8")) or {}


def estimate_usd(model: str, usage: dict[str, int]) -> float | None:
    """List-price estimate; ``None`` for models without a price entry."""
    p = prices().get(model)
    if not p:
        return None
    cached = usage.get("cached_prompt_tokens", 0)
    fresh = max(usage.get("prompt_tokens", 0) - cached, 0)
    cost = fresh * p["input"] + cached * p.get("cached_input", p["input"]) + usage.get("completion_tokens", 0) * p["output"]
    return round(cost / 1_000_000, 4)


def report(llms: dict[str, Any]) -> dict[str, Any]:
    """``{"by_model": {...}, "total": {...}, "estimated_cost_usd": x}`` from unique LLM instances."""
    by_model: dict[str, dict[str, Any]] = {}
    for model, llm in llms.items():
        summary = llm.get_token_usage_summary()
        row = {f: int(getattr(summary, f, 0) or 0) for f in FIELDS}
        row["estimated_cost_usd"] = estimate_usd(model, row)
        by_model[model] = row
    total = {f: sum(r[f] for r in by_model.values()) for f in FIELDS}
    costs = [r["estimated_cost_usd"] for r in by_model.values()]
    estimate = round(sum(costs), 4) if costs and all(c is not None for c in costs) else None
    return {"by_model": by_model, "total": total, "estimated_cost_usd": estimate,
            "note": "list-price estimate; your invoice is authoritative" if estimate is not None else None}
