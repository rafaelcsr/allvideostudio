"""pt-BR transcription with word timestamps (faster-whisper / CTranslate2)."""
from __future__ import annotations

import os
from pathlib import Path

from studio.models import Sentence, Transcript, Word

_MODEL = None


def _model(name: str, device: str, compute_type: str, download_root: str | None):
    global _MODEL
    if _MODEL is None:
        from faster_whisper import WhisperModel

        _MODEL = WhisperModel(name, device=device, compute_type=compute_type, download_root=download_root)
    return _MODEL


def transcribe(
    audio_path: str | Path,
    model_name: str | None = None,
    device: str | None = None,
    language: str = "pt",
) -> Transcript:
    model_name = model_name or os.environ.get("WHISPER_MODEL", "large-v3-turbo")
    device = device or os.environ.get("WHISPER_DEVICE", "auto")
    compute = os.environ.get("WHISPER_COMPUTE", "float16" if device == "cuda" else "int8")
    m = _model(model_name, device, compute, os.environ.get("WHISPER_CACHE"))
    segments, info = m.transcribe(
        load_audio(audio_path),
        language=language,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 300},
        beam_size=5,
        condition_on_previous_text=False,
        # keep hesitations in the transcript so the editor can remove them deliberately
        initial_prompt="Ahn, é... então, hum, bom, vamos lá.",
    )
    words: list[Word] = []
    sentences: list[Sentence] = []
    for seg in segments:
        seg_words = [w for w in (seg.words or []) if w.word.strip()]
        if not seg_words:
            continue
        first = len(words)
        for w in seg_words:
            words.append(Word(text=w.word.strip(), start=round(w.start, 3), end=round(max(w.end, w.start + 0.02), 3), prob=round(w.probability, 3)))
        sentences.extend(split_sentences(words, first, len(words), start_id=len(sentences)))
    return Transcript(language=info.language, duration=float(info.duration), words=words, sentences=sentences)


def load_audio(path: str | Path, sr: int = 16000):
    """Decode with ffmpeg to 16 kHz mono float32 (avoids PyAV version coupling)."""
    import subprocess

    import numpy as np

    from studio.media import ffmpeg_bin

    raw = subprocess.run(
        [ffmpeg_bin(), "-nostdin", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.float32).copy()


def split_sentences(words: list[Word], lo: int, hi: int, start_id: int = 0, max_words: int = 40) -> list[Sentence]:
    """Break a whisper segment into sentences at terminal punctuation (or long pauses)."""
    out: list[Sentence] = []
    s = lo
    for i in range(lo, hi):
        end_punct = words[i].text.endswith((".", "!", "?", "…"))
        long_pause = i + 1 < hi and words[i + 1].start - words[i].end > 1.2
        if end_punct or long_pause or i - s + 1 >= max_words or i == hi - 1:
            out.append(Sentence(
                id=start_id + len(out), start=words[s].start, end=words[i].end,
                text=" ".join(w.text for w in words[s : i + 1]), word_start=s, word_end=i + 1,
            ))
            s = i + 1
    return out


def words_per_minute(t: Transcript) -> float:
    if not t.words:
        return 0.0
    span = max(1.0, t.words[-1].end - t.words[0].start)
    return len(t.words) / span * 60
