# A2A exposure: design note

Status: not built. This note fixes the mapping so the adapter is a thin layer when it is.

## Why it is cheap

`jobs.JobManager` already has the shape A2A wants: a long-running task started by a message,
polled or streamed for status, finished with artifacts. Run state lives on disk (`status.json`),
so the A2A server can be a separate process from the MCP server and still see every run.

| A2A concept | Here |
|---|---|
| Task | one run (`run_id`) |
| `message/send` | `JobManager.start(ResearchRequest)`; request fields from a `DataPart`, or the topic from a `TextPart` |
| Task state `submitted / working / completed / failed` | `status.json` `queued / running / done / failed` |
| `input-required` | not used in v1; candidate: pause after `plan` for the author to approve the plan |
| Status update events (streaming) | task callbacks already fire per completed task; publish them as `TaskStatusUpdateEvent` |
| Artifacts | `runner.ARTIFACTS`: `article` and `report` as markdown parts, ledgers and `sources` as JSON data parts |
| `tasks/cancel` | not supported by CrewAI mid-kickoff; return `TaskNotCancelableError` |

## Agent card (draft)

Served at `/.well-known/agent-card.json`. Skills mirror the three stages.

```json
{
  "name": "research-team",
  "description": "Plans, researches in parallel, verifies claims against recorded sources and writes a publishable piece in a declared voice.",
  "version": "0.1.0",
  "url": "http://localhost:8766/",
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

## Build plan

1. `a2a-sdk` (1.x) server: an `AgentExecutor` whose `execute()` calls `JobManager.start`, then
   tails `status.json` (or a queue fed by the task callbacks) into status events.
2. `research-team a2a --port 8766` CLI command next to `research-team mcp`.
3. Contract test with the SDK client: send, stream to completion, fetch the article artifact, in
   dry-run mode (`"dry_run": true` in the data part) so it runs in CI for free.
4. Auth: none locally; bearer token from env when bound beyond localhost.
