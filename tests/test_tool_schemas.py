"""Anthropic rejects requests whose tool schemas carry more than 16 union-typed parameters.

BraveSearchTool alone has 17, which killed the first real run right after the plan. These tests keep
every provider's tools under the limit and check the slim schema still reaches the real tool intact.
"""

import pytest
from crewai.tools import BaseTool
from pydantic import BaseModel, Field, create_model

from research_team.sources import SourceRegistry
from research_team.tools.search import recorded, research_tools, slim_args_schema, union_params

ANTHROPIC_UNION_LIMIT = 16


@pytest.mark.parametrize("provider", ["brave", "exa", "serper"])
def test_research_tools_stay_under_the_anthropic_union_limit(provider, monkeypatch):
    monkeypatch.setenv("BRAVE_API_KEY", "x")
    monkeypatch.setenv("EXA_API_KEY", "x")
    monkeypatch.setenv("SERPER_API_KEY", "x")
    tools = research_tools(provider, SourceRegistry())
    total = sum(len(union_params(t.args_schema)) for t in tools)  # an agent sends all its tools at once
    assert total <= ANTHROPIC_UNION_LIMIT, [(t.name, union_params(t.args_schema)) for t in tools]


Wide = create_model("Wide", query=(str, Field(description="q")), **{f"opt{i}": (int | None, None) for i in range(20)})


def test_slim_schema_keeps_required_and_a_few_optionals_without_null():
    slim = slim_args_schema(Wide)
    assert list(slim.model_fields) == ["query", "opt0", "opt1", "opt2", "opt3"]
    assert union_params(slim) == []
    assert slim.model_fields["query"].is_required()
    assert list(slim_args_schema(Wide, keep=("opt7",)).model_fields) == ["query", "opt7"]


class _Echo(BaseTool):
    name: str = "echo"
    description: str = "echo"
    args_schema: type[BaseModel] = Wide
    seen: dict = {}

    def _run(self, **kwargs):
        self.seen.clear()
        self.seen.update(kwargs)
        return "see https://a.test/x"


def test_wrapper_passes_only_given_arguments_to_the_real_tool():
    inner, registry = _Echo(), SourceRegistry()
    tool = recorded(inner, registry)
    tool.run(query="agentic ai", opt1=5)
    assert {k: v for k, v in inner.seen.items() if v is not None} == {"query": "agentic ai", "opt1": 5}
    assert "https://a.test/x" in registry
