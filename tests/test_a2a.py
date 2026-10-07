"""A2A contract tests with the official SDK client, in-process over ASGI, crew in dry-run mode."""

import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
import pytest
from a2a.client import ClientConfig, ClientFactory
from a2a.types import DataPart, Message, Part, Role, Task, TaskQueryParams, TaskState, TextPart

from research_team.a2a_server import build_app
from research_team.jobs import JobManager

BASE = "http://testserver"


def message(*parts, **metadata) -> Message:
    return Message(role=Role.user, message_id=uuid4().hex, parts=[Part(root=p) for p in parts], metadata=metadata or None)


@pytest.fixture
def jobs(settings):
    jm = JobManager(settings)
    yield jm
    jm.shutdown()


def http_for(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE, timeout=60)


@asynccontextmanager
async def client_for(app, *, streaming=True, polling=False):
    async with http_for(app) as http:
        yield await ClientFactory.connect(BASE, client_config=ClientConfig(httpx_client=http, streaming=streaming,
                                                                           polling=polling))


async def final_task(client, msg) -> tuple[Task, list]:
    events, task = [], None
    async for item in client.send_message(msg):
        task, event = item if isinstance(item, tuple) else (None, item)
        events.append(event)
    return task, events


async def test_agent_card_is_discoverable(settings, jobs):
    async with http_for(build_app(settings, jobs, url=f"{BASE}/")) as http:
        card = (await http.get("/.well-known/agent-card.json")).json()
    assert card["name"] == "research-team"
    assert [s["id"] for s in card["skills"]] == ["research-plan", "research-report", "voiced-article"]
    assert card["capabilities"]["streaming"] is True and "securitySchemes" not in card


async def test_streamed_article_run(settings, jobs):
    async with client_for(build_app(settings, jobs, url=f"{BASE}/", poll_seconds=0.05)) as client:
        task, _ = await final_task(client, message(DataPart(data={"topic": "Agentic AI in aviation", "dry_run": True})))
    assert task.status.state == TaskState.completed
    names = [a.name for a in task.artifacts]
    assert names[:3] == ["article", "report", "review"] and "sources" in names
    article = task.artifacts[0].parts[0].root
    assert article.text.startswith("---\ntitle:")
    progress = [m.parts[0].root.text for m in task.history or [] if m.role == Role.agent]
    assert any("revise_article done" in t for t in progress)


async def test_text_message_and_skill_select_the_stage(settings, jobs):
    async with client_for(build_app(settings, jobs, url=f"{BASE}/", poll_seconds=0.05)) as client:
        task, _ = await final_task(client, message(TextPart(text="EA and agentic AI adoption"),
                                                   DataPart(data={"skill": "research-plan", "dry_run": True})))
    assert task.status.state == TaskState.completed
    assert [a.name for a in task.artifacts] == ["plan"]
    assert "thesis_hypothesis" in task.artifacts[0].parts[0].root.data


async def test_non_blocking_send_then_poll(settings, jobs):
    app = build_app(settings, jobs, url=f"{BASE}/", poll_seconds=0.05)
    async with client_for(app, streaming=False, polling=True) as client:
        task, _ = await final_task(client, message(DataPart(data={"topic": "Agentic AI in aviation",
                                                                  "stage": "report", "dry_run": True})))
        assert task.status.state in (TaskState.submitted, TaskState.working)
        for _ in range(200):
            task = await client.get_task(TaskQueryParams(id=task.id))
            if task.status.state == TaskState.completed:
                break
            await asyncio.sleep(0.05)
    assert task.status.state == TaskState.completed
    assert [a.name for a in task.artifacts][0] == "report"

    # The task store is on disk: a fresh server process still knows the finished task.
    async with client_for(build_app(settings, jobs, url=f"{BASE}/"), streaming=False) as client2:
        again = await client2.get_task(TaskQueryParams(id=task.id))
    assert again.status.state == TaskState.completed


async def test_bad_request_is_rejected(settings, jobs):
    async with client_for(build_app(settings, jobs, url=f"{BASE}/", poll_seconds=0.05)) as client:
        task, _ = await final_task(client, message(DataPart(data={"topic": "Agentic AI", "voice": "nobody"})))
    assert task.status.state == TaskState.rejected
    assert "nobody" in task.status.message.parts[0].root.text


async def test_bearer_token(settings, jobs):
    app = build_app(settings, jobs, url=f"{BASE}/", token="s3cret")
    async with http_for(app) as http:
        card = (await http.get("/.well-known/agent-card.json")).json()
        assert "bearer" in card["securitySchemes"]
        body = {"jsonrpc": "2.0", "id": 1, "method": "tasks/get", "params": {"id": "x"}}
        assert (await http.post("/", json=body)).status_code == 401
        ok = await http.post("/", json=body, headers={"Authorization": "Bearer s3cret"})
        assert ok.status_code == 200 and "error" in ok.json()  # authorised; the task simply does not exist
