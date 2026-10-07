"""The research team as an A2A agent (protocol 0.3, ``a2a-sdk`` ~= 0.3, the line CrewAI's own A2A extra uses).

A2A is built for exactly this shape of work: a client sends a message, gets a *task* back, and
follows the task through ``submitted -> working -> completed`` while the agent emits progress and
finally artifacts. The mapping onto this project is direct:

    A2A task            one research run (``runs/<run_id>/``), started through ``JobManager``
    status updates      one per crew task completed ("verify_main_claims done, 6/12")
    artifacts           article and report as markdown, plan / ledgers / sources as JSON data
    skills              the three stages: research-plan, research-report, voiced-article

The executor does not run the crew itself. It starts a job and follows its ``status.json``, so the
A2A server, the MCP server and the CLI all share one engine and one on-disk record of every run.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.apps import A2AStarletteApplication
from a2a.server.context import ServerCallContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import TaskStore, TaskUpdater
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
    DataPart,
    HTTPAuthSecurityScheme,
    Part,
    SecurityScheme,
    Task,
    TaskState,
    TextPart,
    UnsupportedOperationError,
)
from a2a.utils import new_task
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH, PREV_AGENT_CARD_WELL_KNOWN_PATH
from a2a.utils.errors import ServerError
from pydantic import ValidationError

from . import __version__
from .crew import stage_tasks
from .jobs import JobManager
from .platforms import get_platform
from .reuse import reused_tasks
from .runner import ARTIFACTS, ResearchRequest, atomic_write_text, read_text
from .settings import Settings, get_settings
from .voice import load_voice

SKILL_STAGES: dict[str, str] = {"research-plan": "plan", "research-report": "report", "voiced-article": "article"}

# What each stage hands back: (artifact name, kind). Text artifacts are markdown, data artifacts JSON.
STAGE_ARTIFACTS: dict[str, list[tuple[str, str]]] = {
    "plan": [("plan", "data")],
    "report": [("report", "text"), ("plan", "data"), ("ledger_main", "data"), ("ledger_secondary", "data"),
               ("sources", "data")],
    "article": [("article", "text"), ("report", "text"), ("review", "text"), ("ledger_main", "data"),
                ("ledger_secondary", "data"), ("sources", "data"), ("lint", "data")],
}


# --- agent card --------------------------------------------------------------

def agent_card(url: str, *, auth: bool = False) -> AgentCard:
    """The card clients fetch from ``/.well-known/agent-card.json`` to discover what this agent does."""
    text_and_data = ["text/plain", "application/json"]
    skills = [
        AgentSkill(
            id="research-plan", name="Research plan",
            description="A falsifiable working thesis, its strongest counter-thesis, main and secondary research "
                        "topics with search queries, and counter-evidence targets. Cheap: one LLM call.",
            tags=["research", "planning"],
            examples=["Plan research on agentic AI in the aviation industry"],
            input_modes=text_and_data, output_modes=["application/json"],
        ),
        AgentSkill(
            id="research-report", name="Cited research dossier",
            description="Parallel main, secondary and counter-evidence research, a source audit, verified claim "
                        "ledgers, a synthesis and a cited report with charts. Every citation was returned by a tool.",
            tags=["research", "fact-checking", "report"],
            examples=["Research Enterprise Architecture and agentic AI adoption: the why, the how and the risks"],
            input_modes=text_and_data, output_modes=["text/markdown", "application/json"],
        ),
        AgentSkill(
            id="voiced-article", name="Publishable article in a voice",
            description="The full dossier, then a draft, an editorial-board review and a revision, checked against a "
                        "voice profile and a platform's rules (linkedin-article, linkedin-post, blog-essay, substack).",
            tags=["writing", "linkedin", "essay", "voice"],
            examples=['{"topic": "Agentic AI in the aviation industry", "platform": "linkedin-article"}'],
            input_modes=text_and_data, output_modes=["text/markdown", "application/json"],
        ),
    ]
    extra: dict[str, Any] = {}
    if auth:
        extra["security_schemes"] = {"bearer": SecurityScheme(root=HTTPAuthSecurityScheme(scheme="bearer"))}
        extra["security"] = [{"bearer": []}]
    return AgentCard(
        name="research-team",
        description="A CrewAI research team: plans, researches in parallel, verifies claims against recorded "
                    "sources and writes a publishable piece in a declared voice. Runs take 10 to 40 minutes: "
                    "send with blocking=false or stream, then follow the task.",
        url=url, version=__version__,
        capabilities=AgentCapabilities(streaming=True, push_notifications=False, state_transition_history=False),
        default_input_modes=["text/plain", "application/json"],
        default_output_modes=["text/markdown", "application/json"],
        skills=skills,
        **extra,
    )


# --- request parsing -----------------------------------------------------------

def parse_request(context: RequestContext) -> ResearchRequest:
    """Build a ``ResearchRequest`` from an A2A message.

    A ``DataPart`` carries the fields as JSON (topic, angle, audience, notes, platform, voice, stage,
    dry_run, skill, reuse, reuse_from). Plain text is accepted as the topic, so a bare "Agentic AI in aviation" works.
    The skill id (in the data or the message metadata) selects the stage unless ``stage`` is given.
    """
    data: dict[str, Any] = {}
    texts: list[str] = []
    for part in (context.message.parts if context.message else []):
        if isinstance(part.root, DataPart):
            data.update(part.root.data)
        elif isinstance(part.root, TextPart):
            texts.append(part.root.text)
    if "topic" not in data and texts:
        data["topic"] = "\n".join(texts).strip()
    skill = data.pop("skill", None) or (context.metadata or {}).get("skill")
    if skill and "stage" not in data:
        if skill not in SKILL_STAGES:
            raise ValueError(f"unknown skill '{skill}' (known: {', '.join(SKILL_STAGES)})")
        data["stage"] = SKILL_STAGES[skill]
    return ResearchRequest(**data)


# --- executor --------------------------------------------------------------------

class ResearchExecutor(AgentExecutor):
    """Starts a run and translates its ``status.json`` into A2A status and artifact events."""

    def __init__(self, jobs: JobManager, poll_seconds: float = 2.0) -> None:
        self.jobs = jobs
        self.poll_seconds = poll_seconds

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task or new_task(context.message)
        if not context.current_task:
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)

        def say(text: str, **meta: Any):
            return updater.new_agent_message([Part(root=TextPart(text=text))], metadata=meta or None)

        try:
            request = parse_request(context)
            get_platform(request.platform)
            load_voice(request.voice, self.jobs.settings.path(self.jobs.settings.voices_dir))
        except (ValidationError, ValueError, FileNotFoundError) as exc:
            await updater.reject(say(f"Invalid research request: {exc}"))
            return

        started = self.jobs.start(request)
        run_id = started["run_id"]
        reuse = request.reuse or self.jobs.settings.reuse
        total = len(stage_tasks(request.stage)) - len(reused_tasks(reuse))
        await updater.start_work(say(f"Run {run_id} started: stage '{request.stage}', {total} tasks.",
                                     run_id=run_id, stage=request.stage))

        reported: set[str] = set()
        while True:
            await asyncio.sleep(self.poll_seconds)
            status = self.jobs.status(run_id)
            done = status.get("completed_tasks", [])
            for name in (n for n in done if n not in reported):
                reported.add(name)
                await updater.update_status(
                    TaskState.working,
                    say(f"{name} done ({len(reported)}/{total})", run_id=run_id, completed=done,
                        pending=status.get("pending_tasks", [])),
                )
            if status.get("status") in ("done", "failed"):
                break

        if status["status"] == "failed":
            await updater.failed(say(f"Run {run_id} failed: {status.get('error')}", run_id=run_id))
            return

        for name, kind in STAGE_ARTIFACTS[request.stage]:
            try:
                content = self.jobs.artifact(run_id, name)
            except FileNotFoundError:
                continue
            if kind == "text":
                part = Part(root=TextPart(text=content, metadata={"mime_type": "text/markdown"}))
            else:
                parsed = json.loads(content)
                part = Part(root=DataPart(data=parsed if isinstance(parsed, dict) else {name: parsed}))
            await updater.add_artifact([part], name=name, metadata={"run_id": run_id, "file": ARTIFACTS[name]})

        summary = status.get("summary") or {}
        flags = status.get("guardrail_overrides") or []
        note = f" {len(flags)} guardrail(s) waved through: review before publishing." if flags else ""
        title = summary.get("title") or request.topic
        await updater.complete(say(f"Run {run_id} done: '{title}'.{note}", run_id=run_id, summary=summary,
                                   usage=status.get("usage"), guardrail_overrides=flags))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        # CrewAI cannot stop a kickoff midway; say so instead of pretending.
        raise ServerError(error=UnsupportedOperationError(message="A running crew cannot be cancelled."))


# --- task store ----------------------------------------------------------------

class FileTaskStore(TaskStore):
    """A2A task records as JSON files, so ``tasks/get`` still answers after a server restart."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=True)

    def _path(self, task_id: str) -> Path:
        safe = "".join(c for c in task_id if c.isalnum() or c in "-_")
        return self.folder / f"{safe}.json"

    async def save(self, task: Task, context: ServerCallContext | None = None) -> None:
        atomic_write_text(self._path(task.id), task.model_dump_json(exclude_none=True))

    async def get(self, task_id: str, context: ServerCallContext | None = None) -> Task | None:
        path = self._path(task_id)
        return Task.model_validate_json(read_text(path)) if path.is_file() else None

    async def delete(self, task_id: str, context: ServerCallContext | None = None) -> None:
        self._path(task_id).unlink(missing_ok=True)


# --- app ---------------------------------------------------------------------------

PUBLIC_PATHS = {AGENT_CARD_WELL_KNOWN_PATH, PREV_AGENT_CARD_WELL_KNOWN_PATH}


def build_app(settings: Settings | None = None, jobs: JobManager | None = None, *, url: str = "http://127.0.0.1:8766/",
              token: str | None = None, poll_seconds: float = 2.0):
    """The Starlette app: agent card, JSON-RPC endpoint, optional bearer-token check."""
    settings = settings or get_settings()
    jobs = jobs or JobManager(settings)
    token = token if token is not None else os.getenv("RESEARCH_A2A_TOKEN") or None
    store = FileTaskStore(settings.path(settings.runs_dir) / ".a2a-tasks")
    handler = DefaultRequestHandler(agent_executor=ResearchExecutor(jobs, poll_seconds), task_store=store)
    app = A2AStarletteApplication(agent_card=agent_card(url, auth=bool(token)), http_handler=handler).build()

    if token:
        from starlette.middleware.base import BaseHTTPMiddleware
        from starlette.responses import JSONResponse

        async def require_bearer(request, call_next):
            if request.url.path in PUBLIC_PATHS or request.headers.get("authorization") == f"Bearer {token}":
                return await call_next(request)
            return JSONResponse({"error": "missing or invalid bearer token"}, status_code=401)

        app.add_middleware(BaseHTTPMiddleware, dispatch=require_bearer)
    return app


def serve(host: str = "127.0.0.1", port: int = 8766, public_url: str | None = None) -> None:
    import uvicorn

    url = public_url or os.getenv("RESEARCH_A2A_PUBLIC_URL") or f"http://{host}:{port}/"
    uvicorn.run(build_app(url=url), host=host, port=port)
