"""Text helpers for pt-BR speech: normalisation and fuzzy quote location."""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from studio.models import Sentence, Word

_PUNCT = re.compile(r"[^\w\-]+", re.UNICODE)


def norm(token: str) -> str:
    t = unicodedata.normalize("NFC", token.strip().lower())
    return _PUNCT.sub("", t).strip("-")


def fold(token: str) -> str:
    """norm() without accents — used for tolerant matching."""
    t = unicodedata.normalize("NFKD", norm(token))
    return "".join(c for c in t if not unicodedata.combining(c))


def tokens(text: str) -> list[str]:
    return [t for t in (fold(x) for x in text.split()) if t]


def locate_quote(
    words: list[Word],
    sentences: list[Sentence],
    sentence_id: int,
    quote: str,
    min_ratio: float = 0.8,
) -> tuple[int, int] | None:
    """Find `quote` inside sentence `sentence_id` (or its neighbours).

    Returns (start_word_idx, end_word_idx_exclusive) or None when no confident match.
    """
    q = tokens(quote)
    if not q:
        return None
    by_id = {s.id: s for s in sentences}
    best: tuple[float, int, int] | None = None
    for sid in (sentence_id, sentence_id - 1, sentence_id + 1):
        s = by_id.get(sid)
        if s is None:
            continue
        lo = max(0, s.word_start - (len(q) if sid != sentence_id else 0))
        hi = min(len(words), s.word_end + (len(q) if sid != sentence_id else 0))
        cand = [fold(w.text) for w in words[lo:hi]]
        n = len(q)
        for size in {n, max(1, n - 1), n + 1}:
            for i in range(0, max(0, len(cand) - size) + 1):
                window = cand[i : i + size]
                ratio = SequenceMatcher(None, window, q, autojunk=False).ratio()
                penalty = 0.0 if sid == sentence_id else 0.03
                score = ratio - penalty
                if best is None or score > best[0]:
                    best = (score, lo + i, lo + i + size)
        if best and best[0] >= 0.999:
            break
    if best is None or best[0] < min_ratio:
        return None
    return best[1], best[2]
