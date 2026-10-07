"""Publishing targets (``config/platforms.yaml``): length, structure and citation style per platform."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel

PLATFORMS_FILE = Path(__file__).parent / "config" / "platforms.yaml"


class PlatformSpec(BaseModel):
    name: str
    label: str
    words: tuple[int, int]
    min_sources: int = 0
    format: str

    def brief(self) -> str:
        lo, hi = self.words
        return f"{self.label}, {lo} to {hi} words, at least {self.min_sources} distinct sources.\n{self.format.strip()}"

    def word_range(self, tolerance: float = 0.1) -> tuple[int, int]:
        lo, hi = self.words
        return int(lo * (1 - tolerance)), int(hi * (1 + tolerance))


@lru_cache
def platforms() -> dict[str, PlatformSpec]:
    data = yaml.safe_load(PLATFORMS_FILE.read_text(encoding="utf-8"))
    return {k: PlatformSpec(name=k, **v) for k, v in data.items()}


def get_platform(name: str) -> PlatformSpec:
    try:
        return platforms()[name]
    except KeyError:
        raise ValueError(f"unknown platform '{name}' (known: {', '.join(platforms())})") from None
