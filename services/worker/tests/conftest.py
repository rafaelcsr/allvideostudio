import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from studio.models import Transcript, Word  # noqa: E402
from studio.pipeline.transcribe import split_sentences  # noqa: E402

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg não encontrado")


def make_transcript(spec: list[tuple[str, float, float]], duration: float | None = None) -> Transcript:
    words = [Word(t, s, e) for t, s, e in spec]
    dur = duration if duration is not None else (words[-1].end + 1.0 if words else 1.0)
    return Transcript(language="pt", duration=dur, words=words, sentences=split_sentences(words, 0, len(words)))


def speak(text: str, start: float = 0.0, wdur: float = 0.3, gap: float = 0.1, pauses: dict[int, float] | None = None):
    """Build evenly timed words; pauses={word_index: extra_gap_before}."""
    out, t = [], start
    for i, w in enumerate(text.split()):
        t += (pauses or {}).get(i, 0.0)
        out.append((w, round(t, 3), round(t + wdur, 3)))
        t += wdur + gap
    return out


@pytest.fixture(scope="session")
def synthetic_video(tmp_path_factory) -> Path:
    """12 s 1280x720 30fps video: moving test pattern + 'speech' tone bursts with silences."""
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg não encontrado")
    out = tmp_path_factory.mktemp("media") / "src.mp4"
    # tone on during [0,3) [5,8) [9,12)
    expr = "0.4*sin(2*PI*220*t)*(lt(t,3)+between(t,5,8)+gte(t,9))"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-y", "-v", "error",
        "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=12",
        "-f", "lavfi", "-i", f"aevalsrc='{expr}':s=48000:d=12",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out),
    ], check=True)
    return out
