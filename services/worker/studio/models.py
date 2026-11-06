"""Core data structures shared by every pipeline stage (JSON-serialisable)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Word:
    text: str
    start: float
    end: float
    prob: float = 1.0


@dataclass
class Sentence:
    id: int
    start: float
    end: float
    text: str
    word_start: int  # index of first word (inclusive)
    word_end: int  # index of last word (exclusive)


@dataclass
class Transcript:
    language: str
    duration: float
    words: list[Word]
    sentences: list[Sentence]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Transcript":
        return cls(
            language=d["language"],
            duration=float(d["duration"]),
            words=[Word(**w) for w in d["words"]],
            sentences=[Sentence(**s) for s in d["sentences"]],
        )


@dataclass
class Clip:
    """A kept interval of the source, in source seconds."""

    src_start: float
    src_end: float
    zoom: float = 1.0
    fade_in: bool = False
    fade_out: bool = False
    focus: tuple[float, float] = (0.5, 0.42)  # normalised subject centre (x, y)

    @property
    def duration(self) -> float:
        return self.src_end - self.src_start


@dataclass
class EditDecisionList:
    clips: list[Clip]
    removed_words: list[int] = field(default_factory=list)  # word indices cut out
    reasons: dict[int, str] = field(default_factory=dict)  # word index -> reason

    @property
    def duration(self) -> float:
        return sum(c.duration for c in self.clips)

    def src_to_out(self, t: float) -> float | None:
        """Map a source timestamp to the output timeline (None if cut away)."""
        acc = 0.0
        for c in self.clips:
            if c.src_start <= t <= c.src_end:
                return acc + (t - c.src_start)
            acc += c.duration
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "clips": [asdict(c) for c in self.clips],
            "removed_words": self.removed_words,
            "reasons": {str(k): v for k, v in self.reasons.items()},
            "duration": self.duration,
        }


@dataclass
class Analysis:
    """Editorial understanding of the video (LLM or heuristic)."""

    title: str = ""
    summary: str = ""
    topics: list[dict[str, Any]] = field(default_factory=list)  # {title, start_sentence, end_sentence}
    removals: list[dict[str, Any]] = field(default_factory=list)  # {sentence_id, quote, reason}
    highlights: list[dict[str, Any]] = field(default_factory=list)  # {start_sentence, end_sentence, title, hook, score, reason}
    hooks: list[dict[str, Any]] = field(default_factory=list)  # {sentence_id, quote, why}
    impact_phrases: list[dict[str, Any]] = field(default_factory=list)  # {sentence_id, quote, keywords}
    hashtags: list[str] = field(default_factory=list)
    source: str = "heuristic"  # "llm" | "heuristic"
    usage: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Analysis":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)
