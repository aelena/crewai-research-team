import json

import pytest

from research_team.crew import TASK_ORDER, ResearchCrew, RunContext, stage_tasks
from research_team.platforms import get_platform, platforms
from research_team.runner import ResearchRequest, RunState, execute
from research_team.voice import load_voice


def ctx(settings, tmp_path, stage="article"):
    return RunContext(settings=settings, voice=load_voice(None, settings.path(settings.voices_dir)),
                      platform=get_platform("linkedin-article"), run_dir=tmp_path / "run", stage=stage)


def test_platforms_load():
    assert {"linkedin-article", "linkedin-post", "blog-essay", "substack"} <= set(platforms())
    with pytest.raises(ValueError, match="known"):
        get_platform("tiktok")


@pytest.mark.parametrize("stage,last", [("plan", "plan_research"), ("report", "write_report"), ("article", "revise_article")])
def test_stages_build_valid_crews(settings, tmp_path, stage, last):
    crew = ResearchCrew(ctx(settings, tmp_path, stage)).crew()  # CrewAI validates async/context rules here
    names = [t.name for t in crew.tasks]
    assert names == list(stage_tasks(stage)) and names[-1] == last


def test_parallel_tasks_get_their_own_agent_instances(settings, tmp_path):
    tasks = {t.name: t for t in ResearchCrew(ctx(settings, tmp_path)).crew().tasks}
    assert tasks["research_main_topics"].agent is not tasks["research_secondary_topics"].agent
    assert tasks["verify_main_claims"].agent is not tasks["verify_secondary_claims"].agent
    assert tasks["research_secondary_topics"].agent.role == "Field Researcher (secondary track)"


def test_per_agent_model_override(settings, monkeypatch):
    monkeypatch.setenv("RESEARCH_LLM_COLUMNIST", "openai/gpt-test")
    assert settings.llm_for("columnist", "strong") == "openai/gpt-test"
    assert settings.llm_for("fact_checker", "fast") == settings.llm_fast


def test_dry_run_end_to_end(settings, tmp_path):
    s = settings.model_copy(update={"dry_run": True})
    run_dir = tmp_path / "run"
    result = execute(ResearchRequest(topic="Agentic AI in aviation"), s, run_dir, RunState(run_dir))

    assert result["status"] == "done", result.get("error")
    assert sorted(result["completed_tasks"]) == sorted(TASK_ORDER)  # parallel tasks finish in any order
    assert result["guardrail_overrides"] == []
    for f in ("01-plan_research.json", "06-verify_main_claims.json", "report.md", "article.md", "sources.json", "lint.json"):
        assert (run_dir / f).is_file(), f
    assert json.loads((run_dir / "06-verify_main_claims.json").read_text())["track"] == "main"
    article = (run_dir / "article.md").read_text(encoding="utf-8")
    assert article.startswith("---\ntitle: ") and 'status: "draft"' in article
    assert "—" not in article


def test_dry_run_draft_is_sent_back_once(settings, tmp_path):
    """The scripted first draft breaks the voice rules; the guardrail must reject it and accept the retry."""
    from research_team.dryrun import DryRunLLM

    s = settings.model_copy(update={"dry_run": True})
    c = ctx(s, tmp_path)
    crew_base = ResearchCrew(c)
    crew_base.crew().kickoff(inputs={k: "x" for k in ("topic", "angle", "audience", "notes", "today",
                                                       "platform_brief", "voice_brief")})
    llm = crew_base._llms["dry-run"]
    assert isinstance(llm, DryRunLLM)
    assert llm._calls["draft_article"] == 2 and llm._calls["revise_article"] == 1
    assert c.overrides == []
