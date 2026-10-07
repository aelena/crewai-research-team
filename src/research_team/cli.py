"""``research-team``: run the crew, manage voices, lint text, serve MCP."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from .platforms import platforms
from .settings import SEARCH_KEYS, get_settings

app = typer.Typer(help="A CrewAI research team that writes publishable pieces in a declared voice.", no_args_is_help=True)
voice_app = typer.Typer(help="Voice profiles in voices/.", no_args_is_help=True)
app.add_typer(voice_app, name="voice")
console = Console()


def _voices_dir() -> Path:
    s = get_settings()
    return s.path(s.voices_dir)


@app.command()
def run(
    topic: Annotated[str, typer.Argument(help="What to research and write about")],
    angle: Annotated[str, typer.Option(help="The angle you want argued, if any")] = "",
    audience: Annotated[str, typer.Option(help="Defaults to the voice profile's audience")] = "",
    notes: Annotated[str, typer.Option(help="Your own notes, positions or constraints")] = "",
    platform: Annotated[str, typer.Option(help="See `research-team platforms`")] = "linkedin-article",
    voice: Annotated[str, typer.Option(help="Voice profile name (default: voices/.active)")] = "",
    stage: Annotated[str, typer.Option(help="plan | report | article")] = "article",
    dry_run: Annotated[bool, typer.Option(help="Scripted offline LLM, no keys, no cost: checks the pipeline")] = False,
) -> None:
    """Run the crew in the foreground. Everything lands in runs/<run_id>/."""
    from .runner import ResearchRequest, RunState, execute, new_run_id

    s = get_settings()
    req = ResearchRequest(topic=topic, angle=angle, audience=audience or None, notes=notes, platform=platform,
                          voice=voice or None, stage=stage, dry_run=dry_run)  # type: ignore[arg-type]
    run_id = new_run_id(topic)
    run_dir = s.path(s.runs_dir) / run_id
    run_dir.mkdir(parents=True)
    state = RunState(run_dir)
    state.update(run_id=run_id, status="queued")
    console.print(f"[bold]run[/] {run_id}  stage={stage}  dry_run={dry_run}  platform={platform}  search={'none' if dry_run else s.resolved_search()}")
    result = execute(req, s, run_dir, state)
    console.print(f"\n[bold]{result['status']}[/] in {result.get('elapsed_s', '?')}s -> {run_dir}")
    if result.get("error"):
        console.print(f"[red]{escape(result['error'])}[/]")
        raise typer.Exit(1)
    for o in result.get("guardrail_overrides") or []:
        console.print(f"[yellow]guardrail '{o['guard']}' waved through on its last attempt:[/] " + escape("; ".join(o["problems"])))
    for v in (result.get("summary") or {}).get("lint", []):
        console.print(f"[{'red' if v['severity'] == 'error' else 'yellow'}]{v['rule']}[/]: {escape(v['detail'])}")


@app.command()
def plan(topic: str, angle: str = "", notes: str = "", dry_run: bool = False) -> None:
    """Only the research plan: a cheap check of scope and thesis before a full run."""
    run(topic=topic, angle=angle, audience="", notes=notes, platform="linkedin-article", voice="", stage="plan",
        dry_run=dry_run)


@app.command()
def runs(limit: int = 20) -> None:
    """Recent runs."""
    from .jobs import JobManager

    t = Table("run_id", "status", "stage", "title / topic")
    for r in JobManager(get_settings()).list_runs(limit):
        t.add_row(r["run_id"], r["status"], r["stage"] or "", r["title"] or r["topic"] or "")
    console.print(t)


@app.command("platforms")
def platforms_cmd() -> None:
    """Publishing targets."""
    t = Table("name", "label", "words", "min sources")
    for p in platforms().values():
        t.add_row(p.name, p.label, f"{p.words[0]}-{p.words[1]}", str(p.min_sources))
    console.print(t)


@app.command()
def lint(
    file: Path,
    voice: Annotated[str, typer.Option(help="Voice profile (default: active)")] = "",
    platform: Annotated[str, typer.Option(help="Also check this platform's word range")] = "",
) -> None:
    """Check a markdown file against a voice profile's rules. Exit code 1 on errors."""
    from .platforms import get_platform
    from .voice import lint as lint_text
    from .voice import load_voice, word_count

    text = file.read_text(encoding="utf-8")
    profile = load_voice(voice or None, _voices_dir())
    issues = lint_text(text, profile)
    words = word_count(text)
    console.print(f"{file.name}: {words} words, voice '{profile.slug}'")
    if platform:
        lo, hi = get_platform(platform).words
        console.print(f"  platform {platform}: {lo}-{hi} words -> {'ok' if lo <= words <= hi else 'OUT OF RANGE'}")
    for v in issues:
        console.print(f"  [{'red' if v.severity == 'error' else 'yellow'}]{v.severity}[/] {v.rule}: {escape(v.detail)}")
    if not issues:
        console.print("  [green]clean[/]")
    raise typer.Exit(1 if any(v.severity == "error" for v in issues) else 0)


@app.command()
def doctor() -> None:
    """Show resolved models, search provider and which keys are present (never their values)."""
    from .crew import AGENT_TIERS

    s = get_settings()
    t = Table("agent", "tier", "model")
    for k, tier in AGENT_TIERS.items():
        t.add_row(k, tier, s.llm_for(k, tier))
    console.print(t)
    keys = ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", *SEARCH_KEYS.values()]
    console.print("keys: " + "  ".join(f"{k}={'set' if os.getenv(k) else '-'}" for k in keys))
    console.print(f"search: {s.resolved_search()}   home: {s.home}   memory: {s.memory}   knowledge: {s.knowledge}")
    if s.resolved_search() == "none":
        console.print("[yellow]No search provider: agents will work from model knowledge only and citations "
                      "cannot be checked against tool results.[/]")


@app.command()
def mcp(
    transport: Annotated[str, typer.Option(help="stdio | streamable-http | sse")] = "stdio",
    host: str = "127.0.0.1",
    port: int = 8765,
) -> None:
    """Serve the crew over MCP."""
    from .mcp_server import serve

    serve(transport=transport, host=host, port=port)  # type: ignore[arg-type]


@app.command()
def a2a(
    host: str = "127.0.0.1",
    port: int = 8766,
    public_url: Annotated[str, typer.Option(help="URL advertised in the agent card (default http://host:port/)")] = "",
) -> None:
    """Serve the crew as an A2A agent. Card at /.well-known/agent-card.json, JSON-RPC at /.

    Set RESEARCH_A2A_TOKEN to require a bearer token on everything except the card.
    """
    from .a2a_server import serve

    serve(host=host, port=port, public_url=public_url or None)


@voice_app.command("list")
def voice_list() -> None:
    from .voice import active_voice, list_voices

    active = active_voice(_voices_dir())
    t = Table("", "name", "label", "tone")
    for v in list_voices(_voices_dir()):
        t.add_row("*" if v.slug == active else "", v.slug, v.name, v.tone)
    console.print(t)


@voice_app.command("show")
def voice_show(name: str = "", prompt: Annotated[bool, typer.Option(help="Show the rendered prompt block")] = False) -> None:
    from .voice import load_voice

    v = load_voice(name or None, _voices_dir())
    console.print(v.prompt(get_settings().home) if prompt else json.dumps(v.model_dump(), indent=2, ensure_ascii=False))


@voice_app.command("switch")
def voice_switch(name: str) -> None:
    from .voice import ACTIVE_FILE, load_voice

    v = load_voice(name, _voices_dir())
    (_voices_dir() / ACTIVE_FILE).write_text(f"{v.slug}.yaml\n", encoding="utf-8")
    console.print(f"active voice: {v.slug} ({v.name})")


if __name__ == "__main__":
    app()
