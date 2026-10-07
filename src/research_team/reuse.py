"""Reuse an earlier run's work instead of paying for it again.

``plan``     reuses task 1 (the research plan): the new run starts at the parallel research.
``research`` reuses tasks 1 to 4 (plan and the three research tracks): the new run starts at the
             source audit. The way back from a run that stopped mid-verification (credit, rate limit).
``report`` reuses tasks 1 to 9 (plan, research, audit, ledgers, synthesis, report): the new run only
           drafts, reviews and revises. Its sources are loaded too, so the citation guardrails still
           compare the article against URLs a tool actually returned in the original research.

The source run is either an explicit run id or ``latest``: the newest earlier run of the same topic
(compared as a slug) that has every output the reuse needs. Reused files are copied into the new run,
so each run directory stays complete on its own.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from .crew import TASK_ORDER
from .models import ClaimLedger, ResearchPlan
from .runner import STATUS_FILE, read_text, slugify
from .sources import SourceRegistry

STRUCTURED = {"plan_research": ResearchPlan, "verify_main_claims": ClaimLedger, "verify_secondary_claims": ClaimLedger}

# Last reused task per level, and which stages may reuse it (something must come after it).
REUSE_END = {"plan": "plan_research", "research": "research_counter_evidence", "report": "write_report"}
ALLOWED = {"plan": ("report", "article"), "research": ("report", "article"), "report": ("article",)}


def task_file(name: str) -> str:
    ext = "json" if name in STRUCTURED else "md"
    return f"{TASK_ORDER.index(name) + 1:02d}-{name}.{ext}"


def reused_tasks(reuse: str) -> tuple[str, ...]:
    return () if reuse == "none" else TASK_ORDER[: TASK_ORDER.index(REUSE_END[reuse]) + 1]


def _missing(run_dir: Path, reuse: str) -> list[str]:
    return [task_file(n) for n in reused_tasks(reuse) if not (run_dir / task_file(n)).is_file()]


def _topic(run_dir: Path) -> str:
    try:
        return (json.loads(read_text(run_dir / STATUS_FILE)).get("request") or {}).get("topic", "")
    except (OSError, ValueError):
        return ""


def find_source(runs_dir: Path, topic: str, reuse: str, reuse_from: str, current: Path) -> Path:
    """The run to reuse from, or ``ValueError`` saying why there is none."""
    if reuse_from != "latest":
        src = runs_dir / reuse_from
        if not src.is_dir():
            raise ValueError(f"reuse_from: no run '{reuse_from}' in {runs_dir}")
        if missing := _missing(src, reuse):
            raise ValueError(f"run '{reuse_from}' cannot provide a '{reuse}' reuse; missing: {', '.join(missing)}")
        return src
    slug = slugify(topic)
    candidates = sorted((d for d in runs_dir.iterdir() if d.is_dir() and not d.name.startswith(".")
                         and d.resolve() != current.resolve()), reverse=True)
    for d in candidates:
        if slugify(_topic(d)) == slug and not _missing(d, reuse):
            return d
    raise ValueError(f"no earlier run of topic '{topic}' has a complete '{reuse}' to reuse "
                     f"(set reuse_from to a run id, or run without reuse)")


def load(src: Path, reuse: str, dest: Path, registry: SourceRegistry) -> dict[str, tuple[str, Any]]:
    """Copy the reused outputs into ``dest``, load their sources, and return them for the crew."""
    prefilled: dict[str, tuple[str, Any]] = {}
    for name in reused_tasks(reuse):
        f = task_file(name)
        raw = read_text(src / f)
        shutil.copy2(src / f, dest / f)
        model = STRUCTURED.get(name)
        prefilled[name] = (raw, model.model_validate_json(raw) if model else None)
    if reuse == "report":
        for extra in ("report.md",):
            if (src / extra).is_file():
                shutil.copy2(src / extra, dest / extra)
        if (src / "charts").is_dir():
            shutil.copytree(src / "charts", dest / "charts", dirs_exist_ok=True)
    sources = src / "sources.json"
    if sources.is_file():
        for s in json.loads(read_text(sources)):
            registry.observe(s["url"], origin=f"reused:{src.name}")
    return prefilled
