"""The real thing: spawn ``research-team mcp`` over stdio and run a dry-run research job through it.

This is the test that proves CrewAI's console output does not corrupt the JSON-RPC stream.
"""

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


async def test_stdio_server_runs_a_dry_run_job(home):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    exe = shutil.which("research-team", path=str(Path(sys.executable).parent))
    assert exe, "research-team console script not installed"
    env = {**os.environ, "RESEARCH_HOME": str(home), "RESEARCH_VERBOSE": "true", "RESEARCH_SEARCH_PROVIDER": "none"}
    params = StdioServerParameters(command=exe, args=["mcp"], env=env, cwd=str(home))

    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        started = json.loads((await session.call_tool(
            "start_research", {"topic": "Agentic AI in aviation", "dry_run": True})).content[0].text)
        run_id = started["run_id"]
        for _ in range(120):
            status = json.loads((await session.call_tool("research_status", {"run_id": run_id})).content[0].text)
            if status["status"] in ("done", "failed"):
                break
            await asyncio.sleep(0.5)
        assert status["status"] == "done", status.get("error")
        assert status["dry_run"] is True
        article = (await session.call_tool("get_artifact", {"run_id": run_id, "name": "article"})).content[0].text
        assert "title:" in article
    assert (home / "runs" / run_id / "report.md").is_file()
