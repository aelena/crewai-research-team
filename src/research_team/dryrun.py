"""Dry run: a scripted LLM that walks the whole crew offline, with no keys and no cost.

Every task gets a canned, schema-valid answer, so the pipeline, callbacks, structured outputs, files
and guardrails all run for real. The first article draft deliberately breaks the voice rules, so a
dry run also shows a guardrail sending a draft back and the retry passing. Content is placeholder.
"""

from __future__ import annotations

import json
import threading
from collections import Counter
from typing import Any

from crewai.llms.base_llm import BaseLLM

# .test is a reserved TLD (RFC 2606): obviously fake, and not caught by the placeholder guardrail.
URLS = [
    "https://statistics.dryrun.test/reports/2026/adoption-survey",
    "https://regulator.dryrun.test/guidance/ai-systems-2026",
    "https://standards.dryrun.test/iso-42001-overview",
    "https://journal.dryrun.test/articles/agentic-systems-field-study",
    "https://analyst.dryrun.test/research/enterprise-agents-2026",
    "https://news.dryrun.test/2026/05/programme-paused-after-audit",
    "https://filings.dryrun.test/10-k/2025/annual-report",
]


def _plan() -> str:
    topic = {"title": "Placeholder topic", "why_it_matters": "Dry run.",
             "key_questions": ["What does the evidence say?"], "search_queries": ["placeholder query 2026"]}
    return json.dumps({
        "thesis_hypothesis": "Dry-run thesis that the evidence could disprove.",
        "counter_thesis": "Dry-run counter-thesis.",
        "main_topics": [topic, {**topic, "title": "Second main topic"}],
        "secondary_topics": [{**topic, "title": "Context topic"}],
        "counter_evidence_targets": ["Paused programmes", "Regulatory limits", "Survey methodology"],
        "success_criteria": ["Every load-bearing claim verified"], "out_of_scope": ["Vendor comparisons"],
    })


def _ledger(track: str) -> str:
    return json.dumps({
        "track": track,
        "claims": [
            {"statement": "Placeholder adoption figure for 2026.", "status": "verified", "confidence": "medium",
             "sources": URLS[:2], "source_quality": "primary", "as_of": "2026", "note": "dry run"},
            {"statement": "Placeholder contested claim.", "status": "contested", "confidence": "low",
             "sources": [URLS[5]], "source_quality": "reputable-secondary"},
        ],
        "chartable": [{"title": "Placeholder adoption by sector, 2026", "unit": "%",
                       "labels": ["Finance", "Energy", "Aviation"], "values": [41, 28, 17], "source": "Dry run, 2026"}],
        "gaps": ["No primary data on outcomes"], "needs_human_review": [],
    })


def _md(title: str, body: str) -> str:
    return f"# {title}\n\n{body}\n\n## All sources\n\n" + "\n".join(f"- {u}" for u in URLS)


PARAGRAPH = (
    "The evidence in this dry run is placeholder text, written to the length and rules of the target "
    "platform so every check in the pipeline runs for real. It cites a source [like this]({url}) on the claim "
    "itself. Short sentences carry the argument. A longer one carries the qualification, which is where the "
    "honest part of any claim usually lives. The trade-off is stated plainly and the reader is left with a "
    "decision rather than a slogan."
)


def _article(clean: bool) -> str:
    paras = [PARAGRAPH.format(url=URLS[i % len(URLS)]) for i in range(24)]
    if not clean:
        paras[0] = "Let us delve into this game-changing landscape — it's a journey!"
    sections = ["## The claim", "## The evidence", "## The strongest objection", "## What to do on Monday"]
    body = ["# Dry run: placeholder article", "*A pipeline check, not content.*"]
    for i, para in enumerate(paras):
        if i % 6 == 0:
            body.append(sections[i // 6])
        body.append(para)
    body.append("## Sources")
    body += [f"- [Dry-run source {i + 1}, 2026]({u})" for i, u in enumerate(URLS)]
    return "\n\n".join(body)


REPORT = (
    "# Dry-run research dossier\n\n## Executive summary\n\nPlaceholder [source](" + URLS[0] + ").\n\n"
    "## Key findings\n\n- Finding [source](" + URLS[1] + ")\n\n## Analysis\n\nPlaceholder.\n\n"
    "## Counter-evidence and risks\n\nPlaceholder [source](" + URLS[5] + ").\n\n"
    "## Insights and recommendations\n\nPlaceholder.\n\n## Open questions\n\nPlaceholder.\n\n"
    "## Methodology and source quality\n\nPlaceholder.\n\n## References\n\n" + "\n".join(f"- {u}" for u in URLS)
)


class DryRunLLM(BaseLLM):
    """Answers by task name; counts calls so the first draft can fail its guardrail."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(model="dry-run", **kwargs)
        self._calls: Counter[str] = Counter()
        self._lock = threading.Lock()

    def answer(self, task: str) -> str:
        with self._lock:
            self._calls[task] += 1
            n = self._calls[task]
        match task:
            case "plan_research":
                return _plan()
            case "verify_main_claims":
                return _ledger("main")
            case "verify_secondary_claims":
                return _ledger("secondary")
            case "write_report":
                return REPORT
            case "draft_article":
                return _article(clean=n > 1)
            case "revise_article":
                return _article(clean=True).replace("# Dry run: placeholder article", "# Dry run: revised article")
            case "review_article":
                return "## Verdict\n\nPublishable after changes.\n\n## Scores\n\nhook 6\n\n## Critical\n\n- none\n\n## Headline options\n\n1. A\n2. B\n3. C"
            case _:
                return _md(task.replace("_", " ").title(), "Placeholder findings [source](" + URLS[3] + ").")

    def call(self, messages: Any, tools: Any = None, callbacks: Any = None, available_functions: Any = None,
             from_task: Any = None, from_agent: Any = None, response_model: Any = None) -> Any:
        task = getattr(from_task, "name", None) or "unknown"
        content = self.answer(task)
        if response_model is not None:
            return response_model.model_validate_json(content)
        return f"Thought: I now know the final answer\nFinal Answer: {content}"

    def supports_function_calling(self) -> bool:
        return False

    def get_context_window_size(self) -> int:
        return 200_000
