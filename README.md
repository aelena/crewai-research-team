# research-team

A CrewAI research team that plans, researches in parallel, verifies every load-bearing claim against
sources a tool actually returned, and writes a publishable piece in a declared voice. It runs from a
CLI and is exposed as an MCP server, with an A2A adapter designed and next in line.

It started as the deep-research lab crew (planner, researcher, fact checker, report writer, with
main and secondary topics researched in parallel). This version keeps that skeleton and adds what a
piece needs before it carries your name: a contrarian research track, a source audit, structured
claim ledgers, a synthesis step, citation checks against a recorded source registry, and a writing
room (draft, editorial board, revision) that is held to a voice profile and a platform's rules.

## The pipeline

```
plan ─┬─ research_main ────────┐                ┌─ verify_main ──────┐
      ├─ research_secondary ───┼─ audit_sources ┤                    ├─ synthesize ─ write_report
      └─ research_counter ─────┘   (barrier)    └─ verify_secondary ─┘        │
                                                                              draft ─ review ─ revise
```

| # | Task | Agent | Output | Checked by |
|---|---|---|---|---|
| 1 | plan_research | Research Director | `ResearchPlan`: falsifiable thesis, counter-thesis, main/secondary topics, counter-evidence targets | schema |
| 2-4 | research main / secondary / counter-evidence (parallel) | Field Researcher ×2, Contrarian Analyst | cited notes | |
| 5 | audit_sources (sync barrier) | Fact Checker | graded source register, laundered statistics, sources to drop | |
| 6-7 | verify main / secondary (parallel) | Fact Checker ×2 | `ClaimLedger`: claim, status, confidence, URLs, chartable series, gaps | every verdict has a URL; every URL was seen by a tool |
| 8 | synthesize_findings | Principal Analyst | thesis verdict, findings, mechanisms, objection, unknowns, angles | |
| 9 | write_report | Research Report Writer (+ chart tool) | `report.md`, the cited dossier | sections, ≥5 citations, no unknown URLs, no placeholders |
| 10 | draft_article | Columnist | the piece | voice rules, platform word range, min sources, no unknown URLs |
| 11 | review_article | Editorial Board (editor, SME, hostile expert) | severity-ranked review, scores, headlines | |
| 12 | revise_article | Columnist | `article.md` | same as the draft |

Stages run a prefix of this: `plan` (1), `report` (1-9), `article` (1-12).

### What makes it trustworthy enough to publish from

- **Recorded sources.** The search and scrape tools are wrapped; every URL they return goes into a
  per-run `SourceRegistry`. The ledgers, report and article are rejected if they cite a URL no tool
  ever returned, which is the cheapest reliable defence against invented references.
- **A contrarian track by design.** Counter-evidence is researched in parallel, not left to a
  reviewer's goodwill, and it feeds verification and synthesis.
- **Claims have states.** Verified, contested, unverified, refuted. Only verified claims may carry
  the argument; the rest appear as what they are.
- **Guardrails that do not kill the run.** CrewAI raises when a guardrail runs out of retries, which
  would discard twenty minutes of research at the final step. Guards here let the last attempt
  through and flag it (`guardrail_overrides` in `status.json`, `status: needs-attention` in the
  article front matter). `RESEARCH_STRICT_GUARDRAILS=true` restores fail-hard.
- **Everything on disk as it happens.** Each task's output is written the moment it completes.

## Voice profiles

`voices/*.yaml` uses the schema of the `/voice` Claude Code command (name, tone, perspective,
audience, vocabulary, sentence style, hooks, paragraph style, examples) plus `structure` and `rules`.
The profile is rendered into the Columnist's and the Editorial Board's prompts, its `examples` are
read in for rhythm and register, and its `rules` are enforced by a linter that the writing
guardrails call:

```yaml
rules:
  no_em_dashes: true
  contractions: avoid
  max_sentence_words: 32   # warning only
  max_exclamations: 0
```

`voices/antonio-elena.yaml` is seeded from a published LinkedIn post, and that post passes its own
profile clean (`research-team lint references/flowtrack-li-post.md`). Quotes, blockquotes, URLs and
the sources section are excluded from linting: a quoted CEO may say "game-changing", you may not.

In Claude Code, `/voice` (in `.claude/commands/`) creates, analyses and switches profiles.

## Platforms

`src/research_team/config/platforms.yaml`: `linkedin-article` (1200-2000 words), `linkedin-post`
(200-450), `blog-essay` (1800-3200), `substack` (1200-2200). Each sets structure, citation style and
a minimum number of distinct sources.

## Quick start

```bash
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"   # bin/ on macOS/Linux
cp .env.example .env                                             # add an LLM key and a search key

research-team doctor                    # models per agent, which keys are present, search provider
research-team run "Agentic AI in the aviation industry" --dry-run   # whole pipeline offline, free
research-team plan "Enterprise Architecture and Agentic AI adoption"   # cheap: just the plan
research-team run "Enterprise Architecture and Agentic AI adoption" \
  --angle "Why EA is the control plane agentic AI is missing" --platform linkedin-article
research-team runs
research-team lint runs/<run_id>/article.md --platform linkedin-article
```

A run writes `runs/<run_id>/`: `01-plan_research.json` ... `12-revise_article.md`, `report.md`,
`article.md` (with front matter), `charts/`, `sources.json`, `lint.json` and `status.json` (state,
models, timings, token usage, guardrail overrides).

### Models

Each agent is `fast` (tool-heavy: researchers, fact checkers) or `strong` (planner, analyst,
writers, board). Defaults: `RESEARCH_LLM_FAST=anthropic/claude-sonnet-5-5`,
`RESEARCH_LLM_STRONG=anthropic/claude-opus-5-5`. Any CrewAI/LiteLLM model string works, and any
single agent can be overridden: `RESEARCH_LLM_COLUMNIST=...`.

### Search

`RESEARCH_SEARCH_PROVIDER=auto` picks the first of Exa, Serper, Tavily, Brave with a key present.
With none, the crew runs from model knowledge, citations cannot be checked, and the article is
marked `sourced: false`. Do not publish from that.

## MCP server

```bash
research-team mcp                                    # stdio (Claude Code / Desktop)
research-team mcp --transport streamable-http --port 8765
```

`.mcp.json` registers it for Claude Code in this folder. Tools: `start_research` (returns a run id
immediately; runs take many minutes), `research_status`, `get_artifact`, `list_runs`, `list_voices`,
`get_voice`, `list_platforms`, `lint_text` (no LLM: check any text against a voice). Resources:
`research://runs/{run_id}/{name}`, `voice://{name}`. `start_research` takes `dry_run: true` for
testing a client for free.

CrewAI prints to stdout, which would corrupt a stdio JSON-RPC stream. The server writes protocol
messages to the real stdout and sends everything else to stderr; `tests/test_mcp_stdio.py` spawns
the server with verbose logging on and runs a job through it to prove it.

## A2A

Designed, not built: [docs/a2a.md](docs/a2a.md) maps runs onto A2A tasks and drafts the agent card.
`jobs.JobManager` is the transport-neutral service both adapters sit on.

## Development

```bash
.venv/Scripts/python -m pytest -q     # 33 tests, no keys, no network
.venv/Scripts/ruff check src tests
```

The dry-run LLM (`dryrun.py`) answers every task with schema-valid placeholder content, and its
first draft deliberately breaks the voice rules, so the tests see a guardrail reject a draft and
accept the retry.

Notes for CrewAI 1.15: one agent instance cannot run two async tasks at once ("Executor is already
running"), so the parallel secondary tracks get their own instance of the same YAML agent; an
async task cannot read another async task's output without a sync task in between, hence the
audit barrier; guardrails must be plain functions, not callable objects.

## License

MIT
