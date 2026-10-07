"""Web research tools, wrapped so every URL they return lands in the run's ``SourceRegistry``."""

from __future__ import annotations

import types
from typing import Any, Union, get_args, get_origin

from crewai.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field, create_model

from ..sources import SourceRegistry, extract_urls


class RecordingTool(BaseTool):
    """Delegates to ``inner``, caps the result size, and records the URLs in its arguments and result."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    inner: BaseTool
    registry: Any  # SourceRegistry; Any keeps pydantic from trying to validate it
    max_chars: int = 0  # 0 = no cap

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        # The slim schema fills omitted optionals with None; the inner tool should see only real arguments.
        kwargs = {k: v for k, v in kwargs.items() if v is not None}
        result = self.inner.run(*args, **kwargs)
        text = str(result)
        if self.max_chars and len(text) > self.max_chars:
            # Every later step of the agent resends this text, so a whole scraped page costs many times over.
            text = f"{text[: self.max_chars]}\n\n[truncated: first {self.max_chars} of {len(text)} characters]"
            result = text
        # Record only what the agent actually saw: a URL cut off by the cap was never read.
        self.registry.observe(text, origin=self.inner.name)
        # A URL the agent passed in (scrape) only counts if fetching it produced something.
        if text.strip() and not text.lower().startswith(("error", "failed")):
            self.registry.observe(" ".join(map(str, kwargs.values())), origin=self.inner.name)
        return result


# Anthropic rejects a request whose tool schemas hold more than 16 union-typed parameters
# ("X | None" becomes anyOf). BraveSearchTool alone has 17. The LLM gets a slim schema: required
# fields plus a few optional ones that matter for research, with the null branch removed.
KEEP_OPTIONAL: dict[str, tuple[str, ...]] = {
    "BraveSearchTool": ("count", "freshness", "country", "search_lang"),
}
MAX_OPTIONAL = 4


def _without_none(annotation: Any) -> Any | None:
    """``T | None`` -> ``T``; ``None`` if what remains is still a union (it would count against the limit)."""
    if get_origin(annotation) in (Union, types.UnionType):
        rest = [a for a in get_args(annotation) if a is not type(None)]
        return rest[0] if len(rest) == 1 else None
    return annotation


def slim_args_schema(schema: type[BaseModel], keep: tuple[str, ...] | None = None,
                     max_optional: int = MAX_OPTIONAL) -> type[BaseModel]:
    fields: dict[str, Any] = {}
    kept = 0
    for name, info in schema.model_fields.items():
        if info.is_required():
            fields[name] = (info.annotation, info)
            continue
        wanted = name in keep if keep is not None else kept < max_optional
        annotation = _without_none(info.annotation)
        if wanted and annotation is not None:
            fields[name] = (annotation, Field(default=None, description=info.description))
            kept += 1
    return create_model(f"{schema.__name__}Slim", __doc__=schema.__doc__, **fields)


def union_params(schema: type[BaseModel]) -> list[str]:
    """Parameters Anthropic counts as union-typed (anyOf or a list of types)."""
    props = schema.model_json_schema().get("properties", {})
    return [k for k, v in props.items() if "anyOf" in v or isinstance(v.get("type"), list)]


def recorded(tool: BaseTool, registry: SourceRegistry, max_chars: int = 0) -> RecordingTool:
    schema = tool.args_schema
    if schema is not None and union_params(schema):
        schema = slim_args_schema(schema, KEEP_OPTIONAL.get(type(tool).__name__))
    return RecordingTool(name=tool.name, description=tool.description, args_schema=schema, inner=tool,
                         registry=registry, max_chars=max_chars)


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


def research_tools(provider: str, registry: SourceRegistry, *, scrape: bool = True,
                   max_chars: int = 0) -> list[BaseTool]:
    """Search (if a provider is configured) and scrape, both recording into ``registry``."""
    from crewai_tools import ScrapeWebsiteTool

    tools = [t for t in (_search_tool(provider),) if t is not None]
    if scrape:
        tools.append(ScrapeWebsiteTool())
    return [recorded(t, registry, max_chars) for t in tools]


__all__ = ["RecordingTool", "extract_urls", "recorded", "research_tools"]
