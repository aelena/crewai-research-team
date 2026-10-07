"""Voice profiles: YAML files in ``voices/`` that tell the writers how to sound, and a linter that checks they did.

The schema is the one used by the ``/voice`` Claude Code command (name, tone, perspective, audience,
vocabulary, sentence_style, hooks, paragraph_style, examples) plus two extensions: ``structure`` and
``rules``. Rules are what the linter enforces; everything else is guidance rendered into the prompt.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

ACTIVE_FILE = ".active"


class Vocabulary(BaseModel):
    prefer: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)


class VoiceRules(BaseModel):
    no_em_dashes: bool = False
    contractions: Literal["allow", "avoid"] = "allow"
    max_sentence_words: int | None = None
    max_exclamations: int | None = None


class VoiceProfile(BaseModel):
    slug: str = ""
    name: str
    tone: str = ""
    perspective: str = ""
    audience: str = ""
    vocabulary: Vocabulary = Field(default_factory=Vocabulary)
    sentence_style: str = ""
    hooks: str = ""
    paragraph_style: str = ""
    structure: str = ""
    rules: VoiceRules = Field(default_factory=VoiceRules)
    examples: list[str] = Field(default_factory=list)

    def prompt(self, home: Path, max_example_chars: int = 6000) -> str:
        """The profile as a prompt block, with the example texts appended for calibration."""
        rules = self.rules
        hard = [f"Never use these words or phrases: {', '.join(self.vocabulary.avoid)}."] if self.vocabulary.avoid else []
        if rules.no_em_dashes:
            hard.append("Never use em dashes (the long dash). Use a full stop, a comma, a colon or parentheses.")
        if rules.contractions == "avoid":
            hard.append('No contractions: write "it is", "does not", "I have", never "it\'s", "doesn\'t", "I\'ve".')
        if rules.max_exclamations == 0:
            hard.append("No exclamation marks.")
        if rules.max_sentence_words:
            hard.append(f"Keep sentences under {rules.max_sentence_words} words.")
        parts = [
            f"VOICE: {self.name}",
            f"Tone: {self.tone}",
            f"Perspective: {self.perspective}",
            f"Audience: {self.audience}",
            f"Sentence style: {self.sentence_style}",
            f"Paragraph style: {self.paragraph_style}",
            f"Hooks: {self.hooks}",
            f"Structure: {self.structure}" if self.structure else "",
            f"Words and phrases this voice uses naturally: {', '.join(self.vocabulary.prefer)}" if self.vocabulary.prefer else "",
            "HARD RULES (checked automatically, a violation sends the text back):\n- " + "\n- ".join(hard) if hard else "",
        ]
        examples = self._examples(home, max_example_chars)
        if examples:
            parts.append(
                "STYLE EXAMPLES (match the rhythm and register; do not reuse their content or claims):\n" + examples
            )
        return "\n".join(p for p in parts if p)

    def _examples(self, home: Path, budget: int) -> str:
        out: list[str] = []
        for rel in self.examples:
            path = home / rel
            if not path.is_file() or budget <= 0:
                continue
            text = path.read_text(encoding="utf-8")[:budget]
            budget -= len(text)
            out.append(f"--- {path.name} ---\n{text.strip()}")
        return "\n\n".join(out)


def list_voices(voices_dir: Path) -> list[VoiceProfile]:
    return [load_voice(p.stem, voices_dir) for p in sorted(voices_dir.glob("*.yaml"))]


def active_voice(voices_dir: Path) -> str | None:
    f = voices_dir / ACTIVE_FILE
    return Path(f.read_text(encoding="utf-8").strip()).stem if f.is_file() else None


def load_voice(name: str | None, voices_dir: Path) -> VoiceProfile:
    """Load ``voices/<name>.yaml``; ``None`` means the active profile, then ``default``."""
    stem = Path(name).stem if name else (active_voice(voices_dir) or "default")
    path = voices_dir / f"{stem}.yaml"
    if not path.is_file():
        known = ", ".join(p.stem for p in voices_dir.glob("*.yaml")) or "none"
        raise FileNotFoundError(f"voice '{stem}' not found in {voices_dir} (known: {known})")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return VoiceProfile(slug=stem, **data)


# --- linting -----------------------------------------------------------------

class Violation(BaseModel):
    rule: str
    severity: Literal["error", "warning"]
    detail: str


_CONTRACTION = re.compile(
    r"\b(?:\w+n['’]t|(?:it|that|there|what|here|who|let|he|she)['’]s|\w+['’](?:re|ve|ll|d|m))\b", re.IGNORECASE
)
_FENCE = re.compile(r"```.*?```", re.DOTALL)
_FRONT_MATTER = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)
_QUOTED = re.compile(r"\"[^\"\n]*\"|“[^”\n]*”")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_URL = re.compile(r"https?://\S+")
_TAIL_SECTION = re.compile(
    r"^#{1,6}\s*(sources|references|citations|further reading)\s*$.*", re.IGNORECASE | re.MULTILINE | re.DOTALL
)


def prose(text: str) -> str:
    """The author's own prose: no front matter, code, quotes, blockquotes, URLs or trailing sources section."""
    text = _FRONT_MATTER.sub("", text)
    text = _FENCE.sub("", text)
    text = _TAIL_SECTION.sub("", text)
    text = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith(">"))
    text = _LINK.sub(r"\1", text)
    text = _URL.sub("", text)
    return _QUOTED.sub("", text)


def word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'’-]+\b", prose(text)))


def _phrase_pattern(phrase: str) -> re.Pattern[str]:
    """``"not just X, but Y"`` style entries: a standalone X or Y is a wildcard."""
    def token(word: str) -> str:
        core = word.strip(",.;:")
        return r".{1,60}?" + re.escape(word[len(core):]) if core in ("X", "Y") else re.escape(word)

    return re.compile(r"(?<!\w)" + r"\s+".join(map(token, phrase.split())) + r"(?!\w)", re.IGNORECASE)


def _sentences(text: str) -> list[str]:
    flat = re.sub(r"^\s*(#+|[-*]|\d+\.)\s+", "", text, flags=re.MULTILINE)
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n{2,}", flat) if s.strip()]


def lint(text: str, voice: VoiceProfile) -> list[Violation]:
    """Check ``text`` against the profile. Errors are hard rules; warnings are style drift."""
    body = prose(text)
    out: list[Violation] = []
    for phrase in voice.vocabulary.avoid:
        hits = _phrase_pattern(phrase).findall(body)
        if hits:
            out.append(Violation(rule="avoid-vocabulary", severity="error", detail=f"'{phrase}' used {len(hits)}x"))
    rules = voice.rules
    if rules.no_em_dashes and (n := body.count("—") + len(re.findall(r"\s–\s", body))):
        out.append(Violation(rule="no-em-dashes", severity="error", detail=f"{n} em dash(es)"))
    if rules.contractions == "avoid" and (hits := sorted({m.lower() for m in _CONTRACTION.findall(body)})):
        out.append(Violation(rule="no-contractions", severity="error", detail=", ".join(hits[:12])))
    if rules.max_exclamations is not None and (n := body.count("!")) > rules.max_exclamations:
        out.append(Violation(rule="exclamations", severity="error", detail=f"{n} exclamation mark(s), max {rules.max_exclamations}"))
    if rules.max_sentence_words:
        long = [s for s in _sentences(body) if len(s.split()) > rules.max_sentence_words]
        if long:
            sample = long[0][:120] + ("..." if len(long[0]) > 120 else "")
            out.append(Violation(rule="sentence-length", severity="warning",
                                 detail=f"{len(long)} sentence(s) over {rules.max_sentence_words} words, e.g. '{sample}'"))
    return out
