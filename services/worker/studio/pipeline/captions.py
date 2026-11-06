"""Dynamic captions as ASS (libass) — word highlight, impact accents, safe areas.
Also exports SRT for platform upload."""
from __future__ import annotations

from dataclasses import dataclass

from studio.styles import CaptionSpec

SENTENCE_END = (".", "!", "?", "…")


@dataclass
class CapWord:
    text: str
    start: float
    end: float
    emphasis: bool = False


@dataclass
class Chunk:
    words: list[CapWord]

    @property
    def start(self) -> float:
        return self.words[0].start

    @property
    def end(self) -> float:
        return self.words[-1].end

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


def chunk_words(words: list[CapWord], max_words: int, max_chars: int, max_gap: float = 0.6) -> list[Chunk]:
    chunks: list[Chunk] = []
    cur: list[CapWord] = []
    for w in words:
        if cur:
            chars = len(" ".join(x.text for x in cur)) + 1 + len(w.text)
            gap = w.start - cur[-1].end
            if (
                len(cur) >= max_words
                or chars > max_chars
                or gap > max_gap
                or cur[-1].text.endswith(SENTENCE_END)
            ):
                chunks.append(Chunk(cur))
                cur = []
        cur.append(w)
    if cur:
        chunks.append(Chunk(cur))
    return chunks


def _ts(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _srt_ts(t: float) -> str:
    ms = int(round(max(0.0, t) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _col(rrggbb: str, alpha: int = 0) -> str:
    rr, gg, bb = rrggbb[0:2], rrggbb[2:4], rrggbb[4:6]
    return f"&H{alpha:02X}{bb}{gg}{rr}".upper()


def _esc(text: str) -> str:
    return text.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")


def font_px(spec: CaptionSpec, width: int, height: int) -> int:
    vertical = height > width
    base = width * 1.45 if vertical else height
    return max(18, int(round(spec.size_ratio * base)))


def build_ass(
    words: list[CapWord],
    spec: CaptionSpec,
    width: int,
    height: int,
    title: str | None = None,
    title_duration: float = 3.0,
    top_safe_ratio: float = 0.0,
) -> tuple[str, list[Chunk]]:
    vertical = height > width
    size = font_px(spec, width, height)
    max_chars = spec.max_chars if not vertical else max(14, int(spec.max_chars * 0.62))
    chunks = chunk_words(words, spec.max_words, max_chars)
    margin_v = int(height * (spec.margin_v_ratio_v if vertical else spec.margin_v_ratio_h))
    margin_lr = int(width * (0.08 if vertical else 0.10))
    outline = max(0.0, round(spec.outline_ratio * height, 1))
    border_style = 3 if spec.box else 1
    back = _col("000000", 0x60) if spec.box else _col("000000", 0x80)
    box_pad = max(6, int(size * 0.28)) if spec.box else outline
    bold = -1 if spec.bold else 0
    title_size = int(size * 0.95)
    title_margin = int(height * (0.10 + top_safe_ratio))

    head = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Cap,{spec.font},{size},{_col(spec.primary)},{_col(spec.highlight)},{_col(spec.outline)},"
        f"{back},{bold},0,0,0,100,100,0,0,{border_style},{box_pad},{spec.shadow},2,{margin_lr},{margin_lr},{margin_v},1",
        f"Style: Title,{spec.font},{title_size},{_col('FFFFFF')},{_col(spec.accent)},{_col('000000')},"
        f"{_col('000000', 0x30)},-1,0,0,0,100,100,0,0,3,{max(8, int(title_size * 0.35))},0,8,{margin_lr},{margin_lr},{title_margin},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    ev: list[str] = []

    def word_txt(w: CapWord) -> str:
        t = w.text.upper() if spec.uppercase else w.text
        return _esc(t)

    for ci, ch in enumerate(chunks):
        nxt = chunks[ci + 1].start if ci + 1 < len(chunks) else ch.end + 0.6
        chunk_end = min(ch.end + 0.25, nxt)
        if spec.word_highlight:
            for k, w in enumerate(ch.words):
                s = ch.start if k == 0 else w.start
                e = ch.words[k + 1].start if k + 1 < len(ch.words) else chunk_end
                if e <= s:
                    continue
                parts = []
                for j, x in enumerate(ch.words):
                    if j == k:
                        pop = r"\fscx112\fscy112" if spec.pop else ""
                        parts.append(f"{{\\c{_col(spec.highlight)}{pop}}}{word_txt(x)}{{\\r}}")
                    elif x.emphasis:
                        parts.append(f"{{\\c{_col(spec.accent)}}}{word_txt(x)}{{\\r}}")
                    else:
                        parts.append(word_txt(x))
                lead = r"{\fad(60,0)}" if k == 0 else ""
                ev.append(f"Dialogue: 0,{_ts(s)},{_ts(e)},Cap,,0,0,0,,{lead}{' '.join(parts)}")
        else:
            parts = [
                f"{{\\c{_col(spec.accent)}}}{word_txt(x)}{{\\r}}" if x.emphasis else word_txt(x)
                for x in ch.words
            ]
            ev.append(f"Dialogue: 0,{_ts(ch.start)},{_ts(chunk_end)},Cap,,0,0,0,,{{\\fad(90,90)}}{' '.join(parts)}")

    if title:
        ev.append(f"Dialogue: 1,{_ts(0)},{_ts(title_duration)},Title,,0,0,0,,{{\\fad(150,250)}}{_esc(title)}")

    return "\n".join(head + ev) + "\n", chunks


def build_srt(chunks: list[Chunk]) -> str:
    out = []
    for i, ch in enumerate(chunks, 1):
        end = ch.end + 0.2
        if i < len(chunks):
            end = min(end, chunks[i].start)
        out.append(f"{i}\n{_srt_ts(ch.start)} --> {_srt_ts(max(end, ch.start + 0.1))}\n{ch.text}\n")
    return "\n".join(out)
