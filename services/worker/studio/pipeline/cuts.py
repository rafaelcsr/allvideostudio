"""Semantic cut planner: turns a word-level transcript + editorial analysis into an
Edit Decision List. Cuts happen only at word boundaries and always keep breathing
room (padding) so speech stays natural."""
from __future__ import annotations

import re

from studio.models import Analysis, Clip, EditDecisionList, Transcript, Word
from studio.pipeline.textnorm import fold, locate_quote
from studio.styles import StyleSpec

FILLERS = {
    "ahn", "ãh", "ã", "hã", "hum", "humm", "hmm", "hm", "uhm", "uh", "eh", "éh", "ehh",
    "éé", "ééé", "ãã", "ããã", "mm", "mmm",
}
# Real pt-BR words that look like fillers — never cut them as fillers ("um" = one/a, "aham" = yes).
REAL_SHORT = {"a", "e", "o", "é", "ou", "eu", "ai", "aí", "ao", "há", "oi", "ah", "ia", "ui", "um", "aham"}
# Words commonly repeated on purpose ("muito muito bom").
EMPHATIC = {"muito", "não", "nao", "sim", "nunca", "bem", "já", "ja", "mais", "super", "tá", "ta", "isso", "vai"}
_ELONGATED = re.compile(r"^([aeiouéêãâ])\1+h?$")


def _is_filler(w: Word) -> bool:
    raw = w.text.strip().lower().strip(".,!?;:…\"'")
    t = raw.strip("-")
    if not t or t in REAL_SHORT:
        return False
    return t in FILLERS or bool(_ELONGATED.match(t))


def detect_disfluencies(words: list[Word]) -> dict[int, str]:
    """Rule-based detection of fillers, stutters, repeated words and retakes."""
    removed: dict[int, str] = {}
    f = [fold(w.text) for w in words]
    n = len(words)

    for i, w in enumerate(words):
        if _is_filler(w):
            removed[i] = "hesitação"

    for i in range(n - 1):
        gap = words[i + 1].start - words[i].end
        a, b = f[i], f[i + 1]
        if not a or not b or gap > 0.6:
            continue
        if a == b and a not in EMPHATIC:
            removed.setdefault(i, "palavra repetida")
        elif (
            len(a) < len(b)
            and b.startswith(a)
            and (words[i].text.rstrip().endswith(("-", "…", "...")) or (len(a) <= 2 and a not in REAL_SHORT))
        ):
            removed.setdefault(i, "gaguejo")

    # repeated n-grams: "eu acho que eu acho que" -> drop the first take
    for size in range(6, 1, -1):
        i = 0
        while i + 2 * size <= n:
            first, second = f[i : i + size], f[i + size : i + 2 * size]
            gap = words[i + size].start - words[i + size - 1].end
            if all(first) and first == second and gap < 1.2 and not set(first) <= EMPHATIC:
                for k in range(i, i + size):
                    removed.setdefault(k, "trecho repetido")
                i += size
            else:
                i += 1
    return removed


def llm_removals(transcript: Transcript, analysis: Analysis | None) -> dict[int, str]:
    out: dict[int, str] = {}
    if not analysis:
        return out
    for r in analysis.removals:
        loc = locate_quote(transcript.words, transcript.sentences, int(r.get("sentence_id", -1)), r.get("quote", ""))
        if loc is None:
            continue
        for k in range(*loc):
            out[k] = r.get("reason") or "corte editorial"
    return out


def _impact_word_ranges(transcript: Transcript, analysis: Analysis | None) -> list[tuple[int, int]]:
    ranges = []
    if analysis:
        for p in analysis.impact_phrases:
            loc = locate_quote(transcript.words, transcript.sentences, int(p.get("sentence_id", -1)), p.get("quote", ""))
            if loc:
                ranges.append(loc)
    return ranges


def plan_cuts(
    transcript: Transcript,
    style: StyleSpec,
    analysis: Analysis | None = None,
    sentence_range: tuple[int, int] | None = None,
    hook: tuple[int, int] | None = None,
    remove_disfluencies: bool = True,
    max_clip_len: float | None = None,
) -> EditDecisionList:
    """Build the EDL.

    sentence_range: (first_id, last_id) inclusive — used for short-form clips.
    hook: word index range (start, end_exclusive) played as a cold open before the edit.
    """
    words = transcript.words
    n = len(words)
    removed: dict[int, str] = {}
    if remove_disfluencies:
        removed.update(detect_disfluencies(words))
    removed.update(llm_removals(transcript, analysis))

    lo, hi = 0, n
    if sentence_range is not None:
        by_id = {s.id: s for s in transcript.sentences}
        lo = by_id[sentence_range[0]].word_start
        hi = by_id[sentence_range[1]].word_end

    kept = [i for i in range(lo, hi) if i not in removed]
    clips = _group(words, kept, style, transcript.duration)

    if max_clip_len is None and style.punch_in > 1.0:
        max_clip_len = 6.0
    if max_clip_len:
        clips = _split_long(clips, words, kept, max_clip_len)

    _assign_camera(clips, words, style, _impact_word_ranges(transcript, analysis))
    if style.topic_fades and analysis and analysis.topics:
        _assign_topic_fades(clips, transcript, analysis)

    if hook is not None:
        hook_idx = [i for i in range(*hook) if i not in removed]
        hook_clips = _group(words, hook_idx, style, transcript.duration)
        for c in hook_clips:
            c.zoom = max(style.impact_zoom, 1.0)
        if hook_clips:
            hook_clips[-1].fade_out = style.topic_fades
            clips = hook_clips + clips

    in_range_removed = sorted(i for i in removed if lo <= i < hi)
    return EditDecisionList(
        clips=clips,
        removed_words=in_range_removed,
        reasons={i: removed[i] for i in in_range_removed},
    )


def _group(words: list[Word], kept: list[int], style: StyleSpec, duration: float) -> list[Clip]:
    clips: list[Clip] = []
    if not kept:
        return clips
    run = [kept[0]]

    def flush(run: list[int]) -> None:
        first, last = words[run[0]], words[run[-1]]
        prev_end = words[run[0] - 1].end if run[0] > 0 else 0.0
        next_start = words[run[-1] + 1].start if run[-1] + 1 < len(words) else duration
        start = max(first.start - style.pad_before, prev_end, 0.0)
        end = min(last.end + style.pad_after, next_start, duration)
        if run[0] > 0 and (run[0] - 1) not in kept_set:
            # previous word was cut: never include its tail
            start = max(start, words[run[0] - 1].end + 0.01)
        if clips and start <= clips[-1].src_end + 0.04:
            clips[-1].src_end = max(clips[-1].src_end, end)
        else:
            clips.append(Clip(src_start=round(start, 3), src_end=round(end, 3)))

    kept_set = set(kept)
    for i in kept[1:]:
        prev = run[-1]
        gap = words[i].start - words[prev].end
        if i == prev + 1 and gap <= style.max_pause:
            run.append(i)
        else:
            flush(run)
            run = [i]
    flush(run)
    for c in clips:
        c.src_start = round(c.src_start, 3)
        c.src_end = round(c.src_end, 3)
    return [c for c in clips if c.duration > 0.05]


def _split_long(clips: list[Clip], words: list[Word], kept: list[int], max_len: float) -> list[Clip]:
    """Split long continuous clips at the widest word gap so punch-ins can vary."""
    out: list[Clip] = []
    for c in clips:
        pending = [c]
        while pending:
            cur = pending.pop(0)
            if cur.duration <= max_len * 1.5:
                out.append(cur)
                continue
            inner = [i for i in kept if cur.src_start <= words[i].start and words[i].end <= cur.src_end]
            best, best_gap = None, -1.0
            for a, b in zip(inner, inner[1:]):
                t = (words[a].end + words[b].start) / 2
                if cur.src_start + max_len * 0.5 < t < cur.src_end - max_len * 0.5:
                    g = words[b].start - words[a].end
                    ends_sentence = words[a].text.rstrip().endswith((".", "!", "?", ","))
                    g += 0.5 if ends_sentence else 0.0
                    if g > best_gap:
                        best, best_gap = t, g
            if best is None:
                out.append(cur)
                continue
            left = Clip(cur.src_start, round(best, 3), fade_in=cur.fade_in)
            right = Clip(round(best, 3), cur.src_end, fade_out=cur.fade_out)
            pending[:0] = [left, right]
    return out


def _assign_camera(clips: list[Clip], words: list[Word], style: StyleSpec, impact: list[tuple[int, int]]) -> None:
    impact_spans = [(words[a].start, words[b - 1].end) for a, b in impact]
    for idx, c in enumerate(clips):
        z = style.punch_in if (style.punch_in > 1.0 and idx % 2 == 1) else 1.0
        for s, e in impact_spans:
            overlap = min(c.src_end, e) - max(c.src_start, s)
            if overlap > 0.5 * min(c.duration, e - s):
                z = max(z, style.impact_zoom)
        c.zoom = z


def _assign_topic_fades(clips: list[Clip], transcript: Transcript, analysis: Analysis) -> None:
    by_id = {s.id: s for s in transcript.sentences}
    starts = []
    for t in analysis.topics[1:]:
        s = by_id.get(int(t.get("start_sentence", -1)))
        if s:
            starts.append(s.start)
    for t in starts:
        for i, c in enumerate(clips):
            if i > 0 and c.src_start <= t + 0.3 and c.src_end > t:
                c.fade_in = True
                clips[i - 1].fade_out = True
                break


def map_words_to_output(edl: EditDecisionList, words: list[Word], removed: set[int] | None = None) -> list[tuple[int, float, float]]:
    """Return (word_idx, out_start, out_end) for every word that survives the edit.
    Hook clips (cold open) are handled because the mapping walks the clip list."""
    removed = removed if removed is not None else set(edl.removed_words)
    out: list[tuple[int, float, float]] = []
    acc = 0.0
    for c in edl.clips:
        for i, w in enumerate(words):
            if i in removed:
                continue
            mid = (w.start + w.end) / 2
            if c.src_start <= mid <= c.src_end:
                s = acc + max(w.start, c.src_start) - c.src_start
                e = acc + min(w.end, c.src_end) - c.src_start
                out.append((i, round(s, 3), round(max(e, s + 0.05), 3)))
        acc += c.duration
    return out
