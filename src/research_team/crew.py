"""The research team as a CrewAI ``@CrewBase`` crew.

Extends the deep-research lab crew (plan, parallel main/secondary research, fact-check, report) with
a contrarian track, a source audit, structured claim ledgers, a synthesis step, a recorded source
registry behind the citation guardrails, and a voice-aware writing room (draft, review, revise).
"""

from __future__ import annotations

import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from crewai import LLM, Agent, Crew, Process, Task
from crewai.project import CrewBase, agent, crew, task
from crewai.tasks.output_format import OutputFormat
from crewai.tasks.task_output import TaskOutput

from .guardrails import Guard, article_checks, known_citations, ledger_integrity, report_checks
from .models import ClaimLedger, ResearchPlan
from .platforms import PlatformSpec
from .settings import Settings
from .sources import SourceRegistry
from .tools import ChartGeneratorTool, research_tools
from .voice import VoiceProfile

Stage = Literal["plan", "report", "article"]

# Task callbacks are closures over the run context; CrewAI warns they cannot be checkpointed. Known, fine.
warnings.filterwarnings("ignore", message="function callbacks cannot be serialized")

# Task names in execution order; a stage runs a prefix of this list.
TASK_ORDER: tuple[str, ...] = (
    "plan_research",
    "research_main_topics",
    "research_secondary_topics",
    "research_counter_evidence",
    "audit_sources",
    "verify_main_claims",
    "verify_secondary_claims",
    "synthesize_findings",
    "write_report",
    "draft_article",
    "review_article",
    "revise_article",
)
STAGE_END: dict[str, str] = {"plan": "plan_research", "report": "write_report", "article": "revise_article"}

# fast = tool-heavy work, strong = judgement and prose
AGENT_TIERS: dict[str, str] = {
    "research_planner": "strong",
    "topic_researcher": "fast",
    "contrarian_researcher": "fast",
    "fact_checker": "fast",
    "research_analyst": "strong",
    "report_writer": "strong",
    "columnist": "strong",
    "editorial_board": "strong",
}


def stage_tasks(stage: str) -> tuple[str, ...]:
    return TASK_ORDER[: TASK_ORDER.index(STAGE_END[stage]) + 1]


@dataclass
class RunContext:
    """Everything one run needs that is not a prompt input."""

    settings: Settings
    voice: VoiceProfile
    platform: PlatformSpec
    run_dir: Path
    stage: Stage = "article"
    registry: SourceRegistry = field(default_factory=SourceRegistry)
    on_task_done: Callable[[str, Any], None] | None = None
    overrides: list[dict[str, Any]] = field(default_factory=list)  # guardrails waved through on the last attempt
    # Reused work: task name -> (raw text, structured output or None), loaded from an earlier run.
    # These tasks are not executed; their saved output is handed to the tasks that read them.
    prefilled: dict[str, tuple[str, Any]] = field(default_factory=dict)

    def record_override(self, guard: str, problems: list[str]) -> None:
        self.overrides.append({"guard": guard, "problems": problems})


@CrewBase
class ResearchCrew:
    """Plan, research in parallel, verify, synthesise, report, then draft, review and revise in a voice."""

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    def __init__(self, ctx: RunContext) -> None:
        self.ctx = ctx
        self._llms: dict[str, LLM] = {}

    # --- helpers -------------------------------------------------------------

    def _llm(self, agent_key: str) -> LLM:
        if self.ctx.settings.dry_run:
            from .dryrun import DryRunLLM

            return self._llms.setdefault("dry-run", DryRunLLM())
        model = self.ctx.settings.llm_for(agent_key, AGENT_TIERS[agent_key])
        if model not in self._llms:
            s = self.ctx.settings
            self._llms[model] = LLM(model=model, temperature=s.llm_temperature, timeout=s.llm_timeout)
        return self._llms[model]

    def _agent(self, key: str, tools: list | None = None, max_iter: int = 15, track: str = "") -> Agent:
        s = self.ctx.settings
        config = self.agents_config[key]
        if track:
            config = {**config, "role": f"{config['role']} ({track} track)"}
        return Agent(
            config=config,
            llm=self._llm(key),
            tools=tools or [],
            verbose=s.verbose,
            max_rpm=s.max_rpm,
            max_iter=max_iter,
            allow_delegation=False,
        )

    def _track_agent(self, key: str, track: str) -> Agent:
        """A second instance of a YAML agent for a parallel track.

        CrewAI 1.15 refuses to run one agent instance on two async tasks at once ("Executor is
        already running"), so the secondary track gets its own instance of the same definition.
        """
        return self._agent(key, self._research_tools(), max_iter=self.ctx.settings.agent_max_iter, track=track)

    def _research_tools(self, scrape: bool = True) -> list:
        if self.ctx.settings.dry_run:
            return []
        s = self.ctx.settings
        return research_tools(s.resolved_search(), self.ctx.registry, scrape=scrape, max_chars=s.tool_max_chars)

    def _guard(self, name: str, checks: list) -> Callable[[Any], tuple[bool, Any]]:
        s = self.ctx.settings
        return Guard(name, checks, retries=s.guardrail_retries, strict=s.strict_guardrails,
                     on_override=self.ctx.record_override).as_function()

    def _task(self, name: str, **kwargs: Any) -> Task:
        def done(output: Any, _name: str = name) -> None:
            if self.ctx.on_task_done:
                self.ctx.on_task_done(_name, output)

        if "guardrails" in kwargs:
            kwargs.setdefault("guardrail_max_retries", self.ctx.settings.guardrail_retries + 1)
        return Task(config=self.tasks_config[name], name=name, callback=done, **kwargs)

    # --- agents --------------------------------------------------------------

    @agent
    def research_planner(self) -> Agent:
        return self._agent("research_planner")

    @agent
    def topic_researcher(self) -> Agent:
        return self._agent("topic_researcher", self._research_tools(), max_iter=self.ctx.settings.agent_max_iter)

    @agent
    def contrarian_researcher(self) -> Agent:
        return self._agent("contrarian_researcher", self._research_tools(), max_iter=self.ctx.settings.agent_max_iter)

    @agent
    def fact_checker(self) -> Agent:
        return self._agent("fact_checker", self._research_tools(), max_iter=self.ctx.settings.agent_max_iter)

    @agent
    def research_analyst(self) -> Agent:
        return self._agent("research_analyst")

    @agent
    def report_writer(self) -> Agent:
        return self._agent("report_writer", [ChartGeneratorTool(out_dir=self.ctx.run_dir / "charts")])

    @agent
    def columnist(self) -> Agent:
        return self._agent("columnist")

    @agent
    def editorial_board(self) -> Agent:
        return self._agent("editorial_board", self._research_tools(scrape=False), max_iter=12)

    # --- tasks ---------------------------------------------------------------

    @task
    def plan_research(self) -> Task:
        return self._task("plan_research", output_pydantic=ResearchPlan)

    @task
    def research_main_topics(self) -> Task:
        return self._task("research_main_topics", async_execution=True)

    @task
    def research_secondary_topics(self) -> Task:
        return self._task("research_secondary_topics", async_execution=True,
                          agent=self._track_agent("topic_researcher", "secondary"))

    @task
    def research_counter_evidence(self) -> Task:
        return self._task("research_counter_evidence", async_execution=True)

    @task
    def audit_sources(self) -> Task:
        return self._task("audit_sources")

    @task
    def verify_main_claims(self) -> Task:
        return self._task(
            "verify_main_claims", async_execution=True, output_pydantic=ClaimLedger,
            guardrails=[self._guard("ledger_main", [ledger_integrity, known_citations(self.ctx.registry)])],
        )

    @task
    def verify_secondary_claims(self) -> Task:
        return self._task(
            "verify_secondary_claims", async_execution=True, output_pydantic=ClaimLedger,
            agent=self._track_agent("fact_checker", "secondary"),
            guardrails=[self._guard("ledger_secondary", [ledger_integrity, known_citations(self.ctx.registry)])],
        )

    @task
    def synthesize_findings(self) -> Task:
        return self._task("synthesize_findings")

    @task
    def write_report(self) -> Task:
        return self._task("write_report", markdown=True,
                          guardrails=[self._guard("report", report_checks(self.ctx.registry))])

    @task
    def draft_article(self) -> Task:
        ctx = self.ctx
        return self._task("draft_article",
                          guardrails=[self._guard("draft", article_checks(ctx.voice, ctx.platform, ctx.registry))])

    @task
    def review_article(self) -> Task:
        return self._task("review_article")

    @task
    def revise_article(self) -> Task:
        ctx = self.ctx
        return self._task("revise_article",
                          guardrails=[self._guard("article", article_checks(ctx.voice, ctx.platform, ctx.registry))])

    # --- crew ----------------------------------------------------------------

    @crew
    def crew(self) -> Crew:
        s = self.ctx.settings
        wanted = set(stage_tasks(self.ctx.stage)) - set(self.ctx.prefilled)
        for t in self.tasks:
            if t.name in self.ctx.prefilled:
                t.output = _saved_output(t, *self.ctx.prefilled[t.name])
        tasks = [t for t in self.tasks if t.name in wanted]
        agents = list({id(t.agent): t.agent for t in tasks if t.agent}.values())
        kwargs: dict[str, Any] = {}
        if s.knowledge:
            kwargs["knowledge_sources"] = _knowledge_sources(s.path(s.knowledge_dir))
        return Crew(
            agents=agents,
            tasks=tasks,
            process=Process.sequential,
            memory=s.memory,
            verbose=s.verbose,
            tracing=False,
            **kwargs,
        )


def _saved_output(task: Task, raw: str, structured: Any) -> TaskOutput:
    """A reused task's output, in the shape CrewAI reads when it builds the next tasks' context."""
    return TaskOutput(
        description=task.description, name=task.name, expected_output=task.expected_output, raw=raw,
        pydantic=structured, agent=task.agent.role if task.agent else "reused",
        output_format=OutputFormat.PYDANTIC if structured is not None else OutputFormat.RAW,
    )


def _knowledge_sources(folder: Path) -> list:
    """Every .md/.txt under knowledge/ (the author's positions, prior pieces) as crew knowledge."""
    from crewai.knowledge.source.text_file_knowledge_source import TextFileKnowledgeSource

    files = [p for p in folder.glob("**/*") if p.suffix in (".md", ".txt") and p.name != "README.md"]
    return [TextFileKnowledgeSource(file_paths=[str(p.relative_to(folder)) for p in files])] if files else []
