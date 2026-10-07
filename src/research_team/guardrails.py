"""Task guardrails: deterministic checks that send an output back to its agent with the reasons.

CrewAI raises once ``guardrail_max_retries`` is exhausted, which would throw away an expensive run at
its last step. A ``Guard`` therefore counts its own attempts: on the final one it lets the output
through and reports the open problems (``on_override``), so the run finishes flagged instead of dead.
``strict=True`` restores fail-hard behaviour.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from .models import ClaimLedger
from .platforms import PlatformSpec
from .sources import SourceRegistry, extract_urls
from .voice import VoiceProfile, lint, word_count

Check = Callable[[str, Any], list[str]]  # (raw text, TaskOutput) -> problems


def raw_text(output: Any) -> str:
    return output if isinstance(output, str) else getattr(output, "raw", "") or ""


class Guard:
    def __init__(
        self,
        name: str,
        checks: list[Check],
        retries: int = 2,
        strict: bool = False,
        on_override: Callable[[str, list[str]], None] | None = None,
    ) -> None:
        self.name, self.checks, self.retries, self.strict = name, checks, retries, strict
        self.on_override = on_override
        self.attempts = 0

    def __call__(self, output: Any) -> tuple[bool, Any]:
        self.attempts += 1
        raw = raw_text(output)
        problems = [p for check in self.checks for p in check(raw, output)]
        if not problems:
            return True, output
        if not self.strict and self.attempts > self.retries:
            if self.on_override:
                self.on_override(self.name, problems)
            return True, output
        return False, "Fix every problem below and return the complete corrected output, not a diff:\n- " + "\n- ".join(problems)

    def as_function(self) -> Callable[[Any], tuple[bool, Any]]:
        """CrewAI inspects guardrails as plain functions (source, name), not callable objects."""
        def guardrail(output: Any) -> tuple[bool, Any]:
            return self(output)

        guardrail.__name__ = guardrail.__qualname__ = f"guard_{self.name}"
        return guardrail


# --- checks ------------------------------------------------------------------

def sections(*required: tuple[str, str]) -> Check:
    """Each ``(label, regex)`` must match a markdown heading."""
    def check(raw: str, _: Any) -> list[str]:
        headings = "\n".join(line.lower() for line in raw.splitlines() if line.lstrip().startswith("#"))
        return [f"Missing a '{label}' section (a markdown heading such as '## {label}')."
                for label, rx in required if not re.search(rx, headings)]
    return check


def min_citations(n: int) -> Check:
    def check(raw: str, _: Any) -> list[str]:
        found = len(extract_urls(raw))
        return [] if found >= n else [f"Only {found} distinct source URL(s) cited; at least {n} are required."]
    return check


def known_citations(registry: SourceRegistry) -> Check:
    """Every cited URL must have been returned by a research tool during this run."""
    def check(raw: str, _: Any) -> list[str]:
        if not len(registry):  # no search tools ran: nothing to compare against
            return []
        unknown = registry.unknown(extract_urls(raw))
        if not unknown:
            return []
        listed = "\n  ".join(unknown[:10])
        return [f"These cited URLs never appeared in any research tool result, so they may be invented. "
                f"Replace them with sources from the research, or remove the claim:\n  {listed}"]
    return check


# "[source]" alone is a placeholder; "[source](https://...)" is a real link, hence the lookahead.
_PLACEHOLDERS = re.compile(
    r"\[(citation needed|source|link|todo|tbd)\](?!\()|\bexample\.(com|org|net)\b|lorem ipsum|\bTODO\b", re.IGNORECASE
)


def no_placeholders(raw: str, _: Any) -> list[str]:
    hits = sorted({m.group(0) for m in _PLACEHOLDERS.finditer(raw)})
    return [f"Remove placeholders: {', '.join(hits)}."] if hits else []


def voice_rules(voice: VoiceProfile) -> Check:
    def check(raw: str, _: Any) -> list[str]:
        return [f"Voice rule '{v.rule}' broken: {v.detail}." for v in lint(raw, voice) if v.severity == "error"]
    return check


def length(platform: PlatformSpec) -> Check:
    lo, hi = platform.word_range()
    def check(raw: str, _: Any) -> list[str]:
        n = word_count(raw)
        if lo <= n <= hi:
            return []
        return [f"Body is {n} words; {platform.label} needs {platform.words[0]} to {platform.words[1]}."]
    return check


def ledger_integrity(raw: str, output: Any) -> list[str]:
    ledger = getattr(output, "pydantic", None)
    if not isinstance(ledger, ClaimLedger):
        return []  # schema conversion is CrewAI's job; nothing to check structurally
    problems = [f"Claim marked '{c.status}' has no source URL: '{c.statement[:90]}'"
                for c in ledger.claims if c.status in ("verified", "contested", "refuted") and not c.sources]
    if not any(c.status == "verified" for c in ledger.claims):
        problems.append("No claim is verified. Verify the core claims against sources before finishing.")
    return problems


# --- presets used by the crew ------------------------------------------------

REPORT_SECTIONS = (
    ("Summary", r"#+.*summary"),
    ("Insights", r"#+.*(insights|recommendations)"),
    ("Citations", r"#+.*(citations|references|sources)"),
)


def report_checks(registry: SourceRegistry) -> list[Check]:
    return [sections(*REPORT_SECTIONS), min_citations(5), known_citations(registry), no_placeholders]


def article_checks(voice: VoiceProfile, platform: PlatformSpec, registry: SourceRegistry) -> list[Check]:
    return [
        sections(("Sources", r"#+\s*sources")),
        min_citations(platform.min_sources),
        known_citations(registry),
        no_placeholders,
        voice_rules(voice),
        length(platform),
    ]
