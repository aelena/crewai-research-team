import os
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Constructing native-provider LLMs needs a key to be present; nothing in the tests calls a model.
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test-not-used")
os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")
for key in ("EXA_API_KEY", "SERPER_API_KEY", "TAVILY_API_KEY", "BRAVE_API_KEY"):
    os.environ.pop(key, None)


@pytest.fixture
def home(tmp_path: Path) -> Path:
    """A throwaway project home with the real voices and references."""
    shutil.copytree(ROOT / "voices", tmp_path / "voices")
    shutil.copytree(ROOT / "references", tmp_path / "references")
    return tmp_path


@pytest.fixture
def settings(home: Path):
    from research_team.settings import Settings

    return Settings(home=home, verbose=False, search_provider="none")
