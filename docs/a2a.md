# A2A exposure: design note

Status: built (2026-10-07) in `src/research_team/a2a_server.py`, served by `research-team a2a`.
This note keeps the reasoning; the README has the usage. Deviations from the original draft are marked.

## Why it is cheap

`jobs.JobManager` already has the shape A2A wants: a long-running task started by a message,
polled or streamed for status, finished with artifacts. Run state lives on disk (`status.json`),
so the A2A server can be a separate process from the MCP server and still see every run.

| A2A concept | Here |
|---|---|
| Task | one run (`run_id`) |
| `message/send` | `JobManager.start(ResearchRequest)`; request fields from a `DataPart`, or the topic from a `TextPart` |
| Task state `submitted / working / completed / failed` | `status.json` `queued / running / done / failed` |
| `rejected` | invalid request (unknown voice, platform or skill, missing topic), with the reason |
| `input-required` | not used in v1; candidate: pause after `plan` for the author to approve the plan |
| Status update events (streaming) | one `working` update per completed crew task. Built by polling `status.json` rather than from callbacks, so the A2A server needs no hook into the crew and can follow runs it did not start |
| Artifacts | `runner.ARTIFACTS`: `article` and `report` as markdown parts, ledgers and `sources` as JSON data parts |
| `tasks/cancel` | not supported by CrewAI mid-kickoff; answers `UnsupportedOperationError` |

## Agent card (draft)

Served at `/.well-known/agent-card.json`. Skills mirror the three stages.

```json
{
  "name": "research-team",
  "description": "Plans, researches in parallel, verifies claims against recorded sources and writes a publishable piece in a declared voice.",
  "version": "0.1.0",
  "url": "http://127.0.0.1:8766/",
  "capabilities": { "streaming": true, "pushNotifications": false },
  "defaultInputModes": ["text/plain", "application/json"],
  "defaultOutputModes": ["text/markdown", "application/json"],
  "skills": [
    { "id": "research-plan", "name": "Research plan",
      "description": "Falsifiable thesis, counter-thesis, main and secondary topics, counter-evidence targets.",
      "tags": ["research", "planning"], "examples": ["Plan research on agentic AI in the aviation industry"] },
    { "id": "research-report", "name": "Cited research dossier",
      "description": "Parallel research, source audit, verified claim ledgers, synthesis and a cited report with charts.",
      "tags": ["research", "fact-checking"] },
    { "id": "voiced-article", "name": "Publishable article in a voice",
      "description": "The dossier plus a draft, editorial review and revision, checked against a voice profile and platform rules.",
      "tags": ["writing", "linkedin", "essay"] }
  ]
}
```

## What was built

1. `ResearchExecutor` (an `a2a-sdk` `AgentExecutor`): parses the message, rejects bad input, starts a job on
   `JobManager`, turns each completed crew task into a status update, then emits artifacts and completes.
2. `FileTaskStore`: task records in `runs/.a2a-tasks/`, so `tasks/get` survives restarts.
3. `research-team a2a --host --port --public-url`, uvicorn underneath.
4. Contract tests with the SDK client in dry-run mode (`tests/test_a2a.py`): card, streamed article run,
   text input plus skill selection, non-blocking send then poll (and read back after a "restart"),
   rejection, bearer auth.
5. Auth: optional bearer token (`RESEARCH_A2A_TOKEN`); the card is always public.

Deviation: `a2a-sdk` 0.3.x, not 1.x. CrewAI's `crewai[a2a]` extra pins `a2a-sdk~=0.3.10`, and this
project should stay installable next to it. Revisit when CrewAI moves.

Not built: `tasks/cancel` (CrewAI cannot interrupt a kickoff), push notifications, `input-required`
plan approval, the authenticated extended card.
