"""Transport-neutral job service: start runs in the background, read their state and artifacts.

A full run takes many minutes, so no protocol adapter should block on it. The MCP server is one
adapter over this class; an A2A server will be another (a run maps onto an A2A task, ``status.json``
onto task status, artifacts onto task artifacts). State lives on disk, so any process can read any run.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .runner import ARTIFACTS, ResearchRequest, RunState, execute, new_run_id, read_text
from .settings import Settings

Runner = Callable[[ResearchRequest, Settings, Path, RunState], dict[str, Any]]


class JobManager:
    def __init__(self, settings: Settings, workers: int = 1, runner: Runner = execute) -> None:
        self.settings = settings
        self.runner = runner
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="research")
        self._futures: dict[str, Future] = {}

    @property
    def runs_dir(self) -> Path:
        return self.settings.path(self.settings.runs_dir)

    def _dir(self, run_id: str) -> Path:
        path = (self.runs_dir / run_id).resolve()
        if path.parent != self.runs_dir.resolve() or not path.is_dir():
            raise KeyError(f"unknown run '{run_id}'")
        return path

    def start(self, request: ResearchRequest) -> dict[str, Any]:
        run_id = new_run_id(request.topic)
        run_dir = self.runs_dir / run_id
        run_dir.mkdir(parents=True)
        state = RunState(run_dir)
        state.update(run_id=run_id, status="queued", stage=request.stage, request=request.model_dump())

        def work() -> dict[str, Any]:
            try:
                return self.runner(request, self.settings, run_dir, state)
            except Exception as exc:  # config errors (unknown voice, platform) land here
                return state.update(status="failed", error=f"{type(exc).__name__}: {exc}")

        self._futures[run_id] = self._pool.submit(work)
        return {"run_id": run_id, "status": "queued", "run_dir": str(run_dir)}

    def status(self, run_id: str) -> dict[str, Any]:
        data = RunState(self._dir(run_id)).read()
        future = self._futures.get(run_id)
        if data.get("status") == "running" and future is None and data.get("pid") is not None:
            data["note"] = "started by another process; if that process has exited the run was interrupted"
        return data

    def artifact(self, run_id: str, name: str) -> str:
        if name not in ARTIFACTS:
            raise KeyError(f"unknown artifact '{name}' (known: {', '.join(ARTIFACTS)})")
        path = self._dir(run_id) / ARTIFACTS[name]
        if not path.is_file():
            raise FileNotFoundError(f"run '{run_id}' has no '{name}' yet")
        return read_text(path)

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        if not self.runs_dir.is_dir():
            return []
        out = []
        for d in sorted((p for p in self.runs_dir.iterdir() if p.is_dir() and not p.name.startswith(".")), reverse=True)[:limit]:
            data = RunState(d).read()
            out.append({
                "run_id": d.name, "status": data.get("status", "unknown"), "stage": data.get("stage"),
                "topic": (data.get("request") or {}).get("topic"), "updated": data.get("updated"),
                "title": (data.get("summary") or {}).get("title"),
            })
        return out

    def wait(self, run_id: str, timeout: float | None = None) -> dict[str, Any]:
        """Block until a run started by this manager finishes (tests, CLI)."""
        self._futures[run_id].result(timeout=timeout)
        return self.status(run_id)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


def dumps(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False, default=str)
