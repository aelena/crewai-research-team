import json

from crewai.tools import BaseTool

from research_team.crew import stage_tasks
from research_team.runner import ResearchRequest, RunState, execute, new_run_id
from research_team.sources import SourceRegistry
from research_team.tools.search import recorded

TOPIC = "Agentic AI in aviation"


def run(settings, **request):
    s = settings.model_copy(update={"dry_run": True})
    run_dir = s.path(s.runs_dir) / new_run_id(request.get("topic", TOPIC))
    while run_dir.exists():  # two runs inside the same second
        run_dir = run_dir.with_name(run_dir.name + "-x")
    run_dir.mkdir(parents=True)
    return run_dir, execute(ResearchRequest(**{"topic": TOPIC, **request}), s, run_dir, RunState(run_dir))


def test_reuse_report_only_writes(settings):
    src_dir, src = run(settings, stage="report")
    assert src["status"] == "done"
    # A source the original research "saw": the reused run must still accept citations to it.
    (src_dir / "sources.json").write_text(json.dumps([{"url": "https://seen.test/a", "origins": ["x"]}]))

    new_dir, new = run(settings, stage="article", reuse="report")
    assert new["status"] == "done", new.get("error")
    assert new["reused_from"] == src_dir.name
    assert new["reused_tasks"] == list(stage_tasks("report"))
    assert sorted(new["completed_tasks"]) == ["draft_article", "review_article", "revise_article"]
    for f in ("01-plan_research.json", "06-verify_main_claims.json", "09-write_report.md", "report.md", "article.md"):
        assert (new_dir / f).is_file(), f
    assert "https://seen.test/a" in json.loads((new_dir / "sources.json").read_text())[0]["url"]


def test_reuse_plan_skips_planning(settings):
    run(settings, stage="plan")
    _, new = run(settings, stage="report", reuse="plan")
    assert new["status"] == "done", new.get("error")
    assert new["reused_tasks"] == ["plan_research"]
    assert "plan_research" not in new["completed_tasks"] and "write_report" in new["completed_tasks"]


def test_reuse_from_explicit_run_and_topic_matching(settings):
    other_dir, _ = run(settings, topic="A different topic entirely", stage="plan")
    _, latest = run(settings, stage="report", reuse="plan")  # 'latest' never picks another topic
    assert latest["status"] == "failed" and "no earlier run of topic" in latest["error"]
    _, explicit = run(settings, stage="report", reuse="plan", reuse_from=other_dir.name)
    assert explicit["status"] == "done" and explicit["reused_from"] == other_dir.name


def test_reuse_errors_are_clear(settings):
    plan_dir, _ = run(settings, stage="plan")
    _, wrong_stage = run(settings, stage="report", reuse="report")
    assert wrong_stage["status"] == "failed" and "needs stage article" in wrong_stage["error"]
    _, incomplete = run(settings, stage="article", reuse="report", reuse_from=plan_dir.name)
    assert incomplete["status"] == "failed" and "missing" in incomplete["error"]


def test_reuse_from_env_default(settings):
    run(settings, stage="report")
    _, new = run(settings.model_copy(update={"reuse": "report"}), stage="article")
    assert new["status"] == "done" and new["reuse"] == "report"


class _Long(BaseTool):
    name: str = "long"
    description: str = "returns a long page"

    def _run(self, **kwargs):
        return "x" * 50 + " https://early.test/a " + "y" * 200 + " https://late.test/b"


def test_tool_results_are_capped_and_only_seen_urls_recorded():
    registry = SourceRegistry()
    out = recorded(_Long(), registry, max_chars=100).run()
    assert out.startswith("x" * 50) and "[truncated: first 100 of" in out
    assert "https://early.test/a" in registry and "https://late.test/b" not in registry

    uncapped = SourceRegistry()
    recorded(_Long(), uncapped, max_chars=0).run()
    assert "https://late.test/b" in uncapped


def test_reuse_research_resumes_at_the_audit(settings):
    src_dir, _ = run(settings, stage="report")
    for f in src_dir.glob("0[5-9]-*"):  # simulate a run that stopped after the research tracks
        f.unlink()
    _, new = run(settings, stage="report", reuse="research")
    assert new["status"] == "done", new.get("error")
    assert new["reused_tasks"] == list(stage_tasks("report")[:4])
    assert sorted(new["completed_tasks"])[0] == "audit_sources"


class _FakeLLM:
    def __init__(self, **counts):
        self.counts = counts

    def get_token_usage_summary(self):
        from types import SimpleNamespace

        return SimpleNamespace(**self.counts)


def test_usage_is_counted_once_per_model_and_priced():
    from research_team.usage import report

    out = report({
        "anthropic/claude-sonnet-5-5": _FakeLLM(prompt_tokens=1_000_000, cached_prompt_tokens=0,
                                                completion_tokens=100_000, total_tokens=1_100_000,
                                                successful_requests=10),
        "openai/unpriced-model": _FakeLLM(prompt_tokens=10, completion_tokens=1, total_tokens=11,
                                          successful_requests=1),
    })
    sonnet = out["by_model"]["anthropic/claude-sonnet-5-5"]
    assert sonnet["estimated_cost_usd"] == 3.0  # 1M x $2 + 0.1M x $10
    assert out["total"]["successful_requests"] == 11
    assert out["estimated_cost_usd"] is None  # one model has no price: no misleading total


def test_dry_run_records_usage_even_when_zero(settings):
    _, result = run(settings, stage="plan")
    assert result["usage"]["by_model"]["dry-run"]["successful_requests"] == 0
