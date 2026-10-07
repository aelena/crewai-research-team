"""Runtime settings, read from the environment (prefix ``RESEARCH_``) and ``.env``."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

SearchProvider = Literal["auto", "exa", "serper", "tavily", "brave", "none"]

# Env var holding the key for each search provider, in auto-detection order.
SEARCH_KEYS: dict[str, str] = {
    "exa": "EXA_API_KEY",
    "serper": "SERPER_API_KEY",
    "tavily": "TAVILY_API_KEY",
    "brave": "BRAVE_API_KEY",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RESEARCH_", env_file=".env", extra="ignore")

    home: Path = Field(default_factory=Path.cwd, description="Base for every relative path below.")
    runs_dir: Path = Path("runs")
    voices_dir: Path = Path("voices")
    knowledge_dir: Path = Path("knowledge")

    llm_fast: str = "anthropic/claude-sonnet-5-5"
    llm_strong: str = "anthropic/claude-opus-5-5"
    llm_temperature: float | None = None
    llm_timeout: float = 600

    search_provider: SearchProvider = "auto"
    verbose: bool = True
    max_rpm: int = 60
    memory: bool = False
    knowledge: bool = False
    guardrail_retries: int = 2
    strict_guardrails: bool = False
    dry_run: bool = Field(False, description="Scripted offline LLM and no web tools: exercises the pipeline for free")

    # Cost controls. Tool-using agents resend their whole conversation, scraped pages included, on
    # every step, so input tokens grow with (result size x steps). These two caps bound that product.
    tool_max_chars: int = Field(12_000, description="Max characters of one search/scrape result the agent sees; 0 = no cap")
    agent_max_iter: int = Field(12, description="Max reasoning/tool steps for researchers and fact checkers")

    # Reuse an earlier run's work instead of paying for it again.
    reuse: Literal["none", "plan", "research", "report"] = "none"
    reuse_from: str = Field("latest", description="'latest' (newest earlier run of the same topic) or a run id")

    def path(self, p: Path) -> Path:
        return p if p.is_absolute() else self.home / p

    def llm_for(self, agent_key: str, tier: str) -> str:
        """Model string for an agent: ``RESEARCH_LLM_<AGENT_KEY>`` wins over the tier default."""
        return os.getenv(f"RESEARCH_LLM_{agent_key.upper()}") or (self.llm_strong if tier == "strong" else self.llm_fast)

    def resolved_search(self) -> str:
        if self.dry_run:
            return "none"
        if self.search_provider != "auto":
            return self.search_provider
        return next((p for p, key in SEARCH_KEYS.items() if os.getenv(key)), "none")


@lru_cache
def get_settings() -> Settings:
    from dotenv import load_dotenv

    load_dotenv(override=False)  # provider keys (ANTHROPIC_API_KEY, EXA_API_KEY...) must reach os.environ
    return Settings()
