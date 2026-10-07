import json

import pytest

from research_team.jobs import JobManager
from research_team.runner import ResearchRequest


def fake_runner(request, settings, run_dir, state):
    (run_dir / "article.md").write_text(f"# {request.topic}\n", encoding="utf-8")
    return state.update(status="done", summary={"title": request.topic})


@pytest.fixture
def jobs(settings):
    jm = JobManager(settings, runner=fake_runner)
    yield jm
    jm.shutdown()


def test_job_lifecycle(jobs):
    started = jobs.start(ResearchRequest(topic="EA and agentic AI", stage="plan"))
    final = jobs.wait(started["run_id"], timeout=10)
    assert final["status"] == "done"
    assert jobs.artifact(started["run_id"], "article") == "# EA and agentic AI\n"
    [listed] = jobs.list_runs()
    assert listed["run_id"] == started["run_id"] and listed["title"] == "EA and agentic AI"


def test_runner_exceptions_become_failed_runs(settings):
    def boom(*_):
        raise RuntimeError("no key")

    jm = JobManager(settings, runner=boom)
    run_id = jm.start(ResearchRequest(topic="x" * 5))["run_id"]
    assert jm.wait(run_id, timeout=10)["status"] == "failed"
    jm.shutdown()


def test_run_ids_cannot_escape_runs_dir(jobs):
    with pytest.raises(KeyError):
        jobs.status("../voices")
    with pytest.raises(KeyError):
        jobs.artifact("nope", "article")


async def test_mcp_tools(settings, jobs):
    from mcp.shared.memory import create_connected_server_and_client_session

    from research_team.mcp_server import build_server

    server = build_server(settings, jobs)
    async with create_connected_server_and_client_session(server) as client:
        names = {t.name for t in (await client.list_tools()).tools}
        assert {"start_research", "research_status", "get_artifact", "list_runs", "list_voices",
                "get_voice", "list_platforms", "lint_text"} <= names

        lint = await client.call_tool("lint_text", {"text": "It's game-changing — truly."})
        rules = {v["rule"] for v in json.loads(lint.content[0].text)["violations"]}
        assert rules == {"no-contractions", "avoid-vocabulary", "no-em-dashes"}

        voices = json.loads((await client.call_tool("list_voices", {})).content[0].text)
        assert any(v["active"] and v["name"] == "antonio-elena" for v in voices)

        bad = await client.call_tool("start_research", {"topic": "Agentic AI", "voice": "nobody"})
        assert bad.isError

        started = json.loads((await client.call_tool("start_research", {"topic": "Agentic AI in aviation"})).content[0].text)
        jobs.wait(started["run_id"], timeout=10)
        status = json.loads((await client.call_tool("research_status", {"run_id": started["run_id"]})).content[0].text)
        assert status["status"] == "done"
        article = await client.call_tool("get_artifact", {"run_id": started["run_id"], "name": "article"})
        assert article.content[0].text.startswith("# Agentic AI in aviation")
