"""One research run, end to end, with everything persisted under ``runs/<run_id>/`` as it happens.

Each task's output is written the moment the task finishes, so a failure at the review step still
leaves the plan, research, ledgers and report on disk. ``status.json`` is the run's live state and is
what the MCP server (and later the A2A adapter) reads.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from .platforms import get_platform
from .settings import Settings
from .usage import report as usage_report
from .voice import lint, load_voice, word_count

Stage = Literal["plan", "report", "article"]
STATUS_FILE = "status.json"

# Public artifact name -> file in the run directory.
ARTIFACTS: dict[str, str] = {
    "plan": "01-plan_research.json",
    "research_main": "02-research_main_topics.md",
    "research_secondary": "03-research_secondary_topics.md",
    "counter_evidence": "04-research_counter_evidence.md",
    "source_audit": "05-audit_sources.md",
    "ledger_main": "06-verify_main_claims.json",
    "ledger_secondary": "07-verify_secondary_claims.json",
    "synthesis": "08-synthesize_findings.md",
    "report": "report.md",
    "draft": "10-draft_article.md",
    "review": "11-review_article.md",
    "article": "article.md",
    "sources": "sources.json",
    "lint": "lint.json",
    "status": STATUS_FILE,
}


class ResearchRequest(BaseModel):
    topic: str = Field(min_length=3)
    angle: str = ""
    audience: str | None = Field(None, description="Defaults to the voice profile's audience")
    notes: str = ""
    platform: str = "linkedin-article"
    voice: str | None = Field(None, description="Voice profile name; default is voices/.active")
    stage: Stage = "article"
    dry_run: bool = Field(False, description="Scripted offline LLM: checks the pipeline, costs nothing")
    reuse: Literal["none", "plan", "research", "report"] | None = Field(
        None, description="Reuse an earlier run's plan, research tracks, or whole report. Default: RESEARCH_REUSE")
    reuse_from: str | None = Field(None, description="'latest' or a run id. Default: RESEARCH_REUSE_FROM")


def slugify(text: str, limit: int = 50) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:limit].rstrip("-") or "run"


def new_run_id(topic: str) -> str:
    return f"{datetime.now():%Y%m%d-%H%M%S}-{slugify(topic)}"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _retry_on_lock(fn, attempts: int = 50, delay: float = 0.02):
    """Windows refuses to replace (or open) a file another thread has open at that instant, which is
    routine here: the crew writes status.json while MCP and A2A clients poll it. Retry briefly."""
    for i in range(attempts):
        try:
            return fn()
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(delay)


def atomic_write_text(path: Path, text: str) -> None:
    """Write via a temp file and rename, so readers never see half a file."""
    tmp = path.with_suffix(path.suffix + f".{threading.get_ident()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    _retry_on_lock(lambda: tmp.replace(path))


def read_text(path: Path) -> str:
    return _retry_on_lock(lambda: path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: Any) -> None:
    atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False, default=str))


class RunState:
    """``status.json`` for one run; thread-safe because parallel task callbacks update it."""

    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self.run_dir / STATUS_FILE

    def read(self) -> dict[str, Any]:
        return json.loads(read_text(self.path)) if self.path.is_file() else {}

    def update(self, **fields: Any) -> dict[str, Any]:
        with self._lock:
            data = {**self.read(), **fields, "updated": _now()}
            _write_json(self.path, data)
            return data

    def task_done(self, name: str) -> None:
        with self._lock:
            data = self.read()
            done = [*data.get("completed_tasks", []), name]
            data.update(completed_tasks=done, pending_tasks=[t for t in data.get("pending_tasks", []) if t != name],
                        updated=_now())
            _write_json(self.path, data)


def save_task_output(run_dir: Path, name: str, output: Any) -> Path:
    from .crew import TASK_ORDER

    index = TASK_ORDER.index(name) + 1
    pyd = getattr(output, "pydantic", None)
    if pyd is not None:
        path = run_dir / f"{index:02d}-{name}.json"
        path.write_text(pyd.model_dump_json(indent=2), encoding="utf-8")
    else:
        path = run_dir / f"{index:02d}-{name}.md"
        path.write_text(getattr(output, "raw", str(output)), encoding="utf-8")
    return path


def _front_matter(fields: dict[str, Any]) -> str:
    lines = [f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in fields.items()]
    return "---\n" + "\n".join(lines) + "\n---\n\n"


def _title(markdown: str) -> str:
    m = re.search(r"^#\s+(.+)$", markdown, re.MULTILINE)
    return m.group(1).strip() if m else ""


def execute(request: ResearchRequest, settings: Settings, run_dir: Path, state: RunState | None = None) -> dict[str, Any]:
    """Run the crew for ``request`` into ``run_dir``. Returns the final status dict."""
    from .crew import AGENT_TIERS, ResearchCrew, RunContext, stage_tasks

    run_dir.mkdir(parents=True, exist_ok=True)
    state = state or RunState(run_dir)
    if request.dry_run:
        settings = settings.model_copy(update={"dry_run": True})
    voice = load_voice(request.voice, settings.path(settings.voices_dir))
    platform = get_platform(request.platform)
    audience = request.audience or voice.audience.strip()
    reuse = request.reuse or settings.reuse
    reuse_from = request.reuse_from or settings.reuse_from

    def on_done(name: str, output: Any) -> None:
        save_task_output(run_dir, name, output)
        if name == "write_report":
            (run_dir / "report.md").write_text(output.raw, encoding="utf-8")
        state.task_done(name)

    ctx = RunContext(settings=settings, voice=voice, platform=platform, run_dir=run_dir,
                     stage=request.stage, on_task_done=on_done)
    reused: dict[str, Any] = {}
    if reuse != "none":
        from . import reuse as reuse_mod

        try:
            if request.stage not in reuse_mod.ALLOWED[reuse]:
                raise ValueError(f"reuse '{reuse}' needs stage {' or '.join(reuse_mod.ALLOWED[reuse])}, "
                                 f"not '{request.stage}'")
            src = reuse_mod.find_source(settings.path(settings.runs_dir), request.topic, reuse, reuse_from, run_dir)
            ctx.prefilled = reuse_mod.load(src, reuse, run_dir, ctx.registry)
        except ValueError as exc:
            return state.update(status="failed", error=f"reuse: {exc}", finished=_now(), stage=request.stage,
                                request=request.model_dump())
        reused = {"reuse": reuse, "reused_from": src.name, "reused_tasks": list(ctx.prefilled)}
    tasks = [t for t in stage_tasks(request.stage) if t not in ctx.prefilled]
    inputs = {
        "topic": request.topic,
        "angle": request.angle or "(none: choose the strongest angle the evidence supports)",
        "audience": audience,
        "notes": request.notes or "(none)",
        "today": date.today().isoformat(),
        "platform_brief": platform.brief(),
        "voice_brief": voice.prompt(settings.home),
    }
    models = {k: settings.llm_for(k, tier) for k, tier in AGENT_TIERS.items()}
    state.update(status="running", started=_now(), pid=os.getpid(), stage=request.stage,
                 request=request.model_dump(), dry_run=settings.dry_run, voice=voice.slug, platform=platform.name,
                 search=settings.resolved_search(), models=models, completed_tasks=[], pending_tasks=tasks,
                 tool_max_chars=settings.tool_max_chars, agent_max_iter=settings.agent_max_iter, **reused)
    t0 = time.monotonic()
    crew_base = ResearchCrew(ctx)
    try:
        result = crew_base.crew().kickoff(inputs=inputs)
    except Exception as exc:
        # A failed run has still spent money: record what, so a credit or rate-limit stop is measurable.
        _write_json(run_dir / "sources.json", [s.model_dump() for s in ctx.registry.sources()])
        return state.update(status="failed", error=f"{type(exc).__name__}: {exc}", finished=_now(),
                            elapsed_s=round(time.monotonic() - t0), guardrail_overrides=ctx.overrides,
                            usage=usage_report(crew_base._llms))

    sources = [s.model_dump() for s in ctx.registry.sources()]
    _write_json(run_dir / "sources.json", sources)
    summary: dict[str, Any] = {"sources_seen": len(sources)}

    if request.stage == "article":
        body = result.raw
        violations = [v.model_dump() for v in lint(body, voice)]
        _write_json(run_dir / "lint.json", violations)
        needs_attention = bool(ctx.overrides) or any(v["severity"] == "error" for v in violations)
        meta = {
            "title": _title(body), "topic": request.topic, "platform": platform.name, "voice": voice.slug,
            "date": date.today().isoformat(), "status": "needs-attention" if needs_attention else "draft",
            "words": word_count(body), "sourced": settings.resolved_search() != "none",
        }
        (run_dir / "article.md").write_text(_front_matter(meta) + body.strip() + "\n", encoding="utf-8")
        summary.update(title=meta["title"], words=meta["words"], lint=violations, article_status=meta["status"])

    return state.update(
        status="done", finished=_now(), elapsed_s=round(time.monotonic() - t0),
        usage=usage_report(crew_base._llms),
        guardrail_overrides=ctx.overrides, summary=summary,
        artifacts=sorted(k for k, f in ARTIFACTS.items() if (run_dir / f).is_file()),
    )
