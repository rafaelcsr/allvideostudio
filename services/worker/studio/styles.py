"""Premium editing identities. Every visual/rhythm decision is driven from here,
so new styles (or per-user brand kits) are just new StyleSpec instances."""
from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class CaptionSpec:
    font: str
    size_ratio: float  # font size as fraction of output height
    bold: bool
    uppercase: bool
    max_words: int
    max_chars: int
    primary: str  # RRGGBB
    highlight: str  # RRGGBB, active word
    accent: str  # RRGGBB, impact words
    outline: str
    outline_ratio: float
    shadow: float
    box: bool  # opaque background box (corporate)
    word_highlight: bool  # karaoke-style active word
    pop: bool  # scale-pop on active word
    margin_v_ratio_h: float  # bottom margin for 16:9
    margin_v_ratio_v: float  # bottom margin for 9:16


@dataclass(frozen=True)
class StyleSpec:
    key: str
    label: str
    # rhythm
    max_pause: float  # longest silence kept inside the edit (s)
    pad_before: float
    pad_after: float
    min_clip: float
    # camera
    punch_in: float  # zoom used on alternate cuts (1.0 = off)
    impact_zoom: float  # zoom on impact phrases
    topic_fades: bool
    letterbox: bool
    # picture & sound
    grade: str  # ffmpeg video filter chain
    denoise: float  # afftdn noise reduction dB
    loudness: float  # LUFS target
    captions: CaptionSpec = None  # type: ignore[assignment]

    def with_overrides(self, **kw) -> "StyleSpec":
        return replace(self, **kw)


CORPORATIVO = StyleSpec(
    key="corporativo",
    label="Premium Corporativo",
    max_pause=0.55,
    pad_before=0.10,
    pad_after=0.22,
    min_clip=0.35,
    punch_in=1.0,
    impact_zoom=1.06,
    topic_fades=False,
    letterbox=False,
    grade="eq=contrast=1.03:saturation=1.04:gamma=1.0",
    denoise=10,
    loudness=-14,
    captions=CaptionSpec(
        font="Inter", size_ratio=0.046, bold=True, uppercase=False,
        max_words=7, max_chars=42, primary="FFFFFF", highlight="FFFFFF", accent="7FD3FF",
        outline="000000", outline_ratio=0.0, shadow=0, box=True, word_highlight=False, pop=False,
        margin_v_ratio_h=0.07, margin_v_ratio_v=0.22,
    ),
)

DINAMICO = StyleSpec(
    key="dinamico",
    label="Premium Dinâmico",
    max_pause=0.28,
    pad_before=0.06,
    pad_after=0.12,
    min_clip=0.25,
    punch_in=1.12,
    impact_zoom=1.22,
    topic_fades=False,
    letterbox=False,
    grade="eq=contrast=1.08:saturation=1.15,unsharp=5:5:0.35",
    denoise=12,
    loudness=-14,
    captions=CaptionSpec(
        font="Montserrat", size_ratio=0.062, bold=True, uppercase=True,
        max_words=3, max_chars=18, primary="FFFFFF", highlight="FFE14D", accent="4DFFB8",
        outline="000000", outline_ratio=0.0065, shadow=2, box=False, word_highlight=True, pop=True,
        margin_v_ratio_h=0.12, margin_v_ratio_v=0.30,
    ),
)

CINEMATOGRAFICO = StyleSpec(
    key="cinematografico",
    label="Premium Cinematográfico",
    max_pause=0.70,
    pad_before=0.14,
    pad_after=0.30,
    min_clip=0.45,
    punch_in=1.0,
    impact_zoom=1.10,
    topic_fades=True,
    letterbox=True,
    grade=(
        "eq=contrast=1.10:saturation=0.90:gamma=0.97,"
        "colorbalance=rs=0.04:gs=0.0:bs=-0.03:rh=-0.03:gh=0.0:bh=0.04,"
        "vignette=angle=PI/5"
    ),
    denoise=10,
    loudness=-16,
    captions=CaptionSpec(
        font="Lato", size_ratio=0.042, bold=False, uppercase=False,
        max_words=8, max_chars=44, primary="F2EEE6", highlight="F2EEE6", accent="E8C27A",
        outline="000000", outline_ratio=0.002, shadow=1.5, box=False, word_highlight=False, pop=False,
        margin_v_ratio_h=0.15, margin_v_ratio_v=0.24,
    ),
)

STYLES: dict[str, StyleSpec] = {s.key: s for s in (CORPORATIVO, DINAMICO, CINEMATOGRAFICO)}
AUTO = "auto"


def get_style(key: str) -> StyleSpec:
    if key not in STYLES:
        raise ValueError(f"estilo desconhecido: {key!r} (use {', '.join(STYLES)} ou auto)")
    return STYLES[key]


def auto_style(words_per_minute: float, duration: float) -> StyleSpec:
    """Pick a style when the user chooses 'automático'."""
    if duration <= 120 or words_per_minute >= 165:
        return DINAMICO
    if words_per_minute < 120:
        return CINEMATOGRAFICO
    return CORPORATIVO
