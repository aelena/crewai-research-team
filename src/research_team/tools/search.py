"""Web research tools, wrapped so every URL they return lands in the run's ``SourceRegistry``."""

from __future__ import annotations

from typing import Any

from crewai.tools import BaseTool
from pydantic import ConfigDict

from ..sources import SourceRegistry, extract_urls


class RecordingTool(BaseTool):
    """Delegates to ``inner`` and records the URLs in its arguments and result."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    inner: BaseTool
    registry: Any  # SourceRegistry; Any keeps pydantic from trying to validate it

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        result = self.inner.run(*args, **kwargs)
        text = str(result)
        self.registry.observe(text, origin=self.inner.name)
        # A URL the agent passed in (scrape) only counts if fetching it produced something.
        if text.strip() and not text.lower().startswith(("error", "failed")):
            self.registry.observe(" ".join(map(str, kwargs.values())), origin=self.inner.name)
        return result


def recorded(tool: BaseTool, registry: SourceRegistry) -> RecordingTool:
    return RecordingTool(
        name=tool.name, description=tool.description, args_schema=tool.args_schema, inner=tool, registry=registry
    )


def _search_tool(provider: str) -> BaseTool | None:
    match provider:
        case "exa":
            from crewai_tools import ExaSearchTool
            return ExaSearchTool(highlights=True, summary=True)
        case "serper":
            from crewai_tools import SerperDevTool
            return SerperDevTool(n_results=10)
        case "tavily":
            from crewai_tools import TavilySearchTool  # needs: pip install tavily-python
            return TavilySearchTool(max_results=8)
        case "brave":
            from crewai_tools import BraveSearchTool
            return BraveSearchTool()
        case "none":
            return None
    raise ValueError(f"unknown search provider '{provider}'")


def research_tools(provider: str, registry: SourceRegistry, *, scrape: bool = True) -> list[BaseTool]:
    """Search (if a provider is configured) and scrape, both recording into ``registry``."""
    from crewai_tools import ScrapeWebsiteTool

    tools = [t for t in (_search_tool(provider),) if t is not None]
    if scrape:
        tools.append(ScrapeWebsiteTool())
    return [recorded(t, registry) for t in tools]


__all__ = ["RecordingTool", "extract_urls", "recorded", "research_tools"]
