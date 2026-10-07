# research-team

CrewAI research crew (12 tasks, 8 agents) + voice profiles + MCP server + A2A agent. See README for the pipeline.

## Conventions
- Commits as `aelena <antonioelena@gmail.com>` (set in local git config). No em dashes in prose.
- Prompts live in `src/research_team/config/{agents,tasks}.yaml`; async flags, structured outputs,
  tools and guardrails are wired in `crew.py`. Keep `{placeholders}` limited to the inputs built in
  `runner.execute` (topic, angle, audience, notes, today, platform_brief, voice_brief); literal braces
  in YAML break CrewAI interpolation.
- `TASK_ORDER` in `crew.py` is the single source of truth for task order, stages and file prefixes.
- Untracked on purpose: `runs/`, `references/*`, `knowledge/*`, `_memoria/`, `.env`.

## Checks
- `.venv/Scripts/python -m pytest -q` (no keys, no network; includes a real stdio MCP round trip and SDK-client A2A contract tests)
- `.venv/Scripts/research-team run "<topic>" --dry-run` walks the whole crew offline.

## Gotchas (CrewAI 1.15)
- One agent instance cannot serve two concurrent async tasks: use `_track_agent`.
- Async task reading an async task's output needs a sync task between them (`audit_sources`).
- Guardrails must be plain functions: use `Guard(...).as_function()`.
- Never print to stdout in code reachable from the MCP stdio server.
- Files polled by MCP/A2A while the crew writes them: use `runner.atomic_write_text` / `runner.read_text` (Windows locks).
- A2A uses a2a-sdk 0.3.x on purpose (crewai[a2a] pins ~=0.3.10); do not bump to 1.x.
- Never use `Crew.calculate_usage_metrics` / `CrewOutput.token_usage`: LLM instances are shared per model, so CrewAI's per-agent sum overcounts. Use `usage.report(crew._llms)`.
- Anthropic rejects >16 union-typed tool params per request: research tools go through `slim_args_schema`.
