"""Thin, testable wrappers around ffmpeg / ffprobe."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path


class MediaError(RuntimeError):
    pass


def ffmpeg_bin() -> str:
    return os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg") or "ffmpeg"


def ffprobe_bin() -> str:
    return os.environ.get("FFPROBE_BIN") or shutil.which("ffprobe") or "ffprobe"


def run(args: list[str], cwd: str | Path | None = None, timeout: float | None = None) -> subprocess.CompletedProcess:
    proc = subprocess.run(args, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-15:])
        raise MediaError(f"{Path(args[0]).name} falhou ({proc.returncode}):\n{tail}")
    return proc


def ffmpeg(*args: str, cwd: str | Path | None = None, timeout: float | None = None) -> subprocess.CompletedProcess:
    return run([ffmpeg_bin(), "-hide_banner", "-nostdin", "-y", *args], cwd=cwd, timeout=timeout)


@dataclass
class ProbeInfo:
    duration: float
    width: int
    height: int
    fps: Fraction
    rotation: int
    has_audio: bool
    vcodec: str
    acodec: str | None
    audio_duration: float | None
    video_duration: float | None
    size_bytes: int

    @property
    def display_size(self) -> tuple[int, int]:
        if abs(self.rotation) in (90, 270):
            return self.height, self.width
        return self.width, self.height


def probe(path: str | Path) -> ProbeInfo:
    out = run([
        ffprobe_bin(), "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]).stdout
    data = json.loads(out)
    v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    a = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    if v is None:
        raise MediaError("arquivo sem trilha de vídeo")
    fmt = data.get("format", {})
    rate = v.get("avg_frame_rate") or v.get("r_frame_rate") or "30/1"
    try:
        fps = Fraction(rate)
        if fps <= 0 or fps > 240:
            raise ValueError
    except (ValueError, ZeroDivisionError):
        fps = Fraction(30, 1)
    rotation = 0
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            rotation = int(sd["rotation"])
    if not rotation and "rotate" in (v.get("tags") or {}):
        rotation = int(v["tags"]["rotate"])

    def _dur(s: dict | None) -> float | None:
        if s and s.get("duration") not in (None, "N/A"):
            return float(s["duration"])
        return None

    duration = float(fmt.get("duration") or _dur(v) or 0.0)
    return ProbeInfo(
        duration=duration,
        width=int(v["width"]),
        height=int(v["height"]),
        fps=fps,
        rotation=rotation,
        has_audio=a is not None,
        vcodec=v.get("codec_name", ""),
        acodec=a.get("codec_name") if a else None,
        audio_duration=_dur(a),
        video_duration=_dur(v),
        size_bytes=int(fmt.get("size") or os.path.getsize(path)),
    )


def extract_audio(src: str | Path, dst: str | Path, sample_rate: int = 16000) -> Path:
    """Mono audio for ASR (flac keeps it small enough to ship to the GPU worker)."""
    dst = Path(dst)
    ffmpeg("-i", str(src), "-vn", "-ac", "1", "-ar", str(sample_rate), "-c:a", "flac", str(dst))
    return dst


def has_ffmpeg() -> bool:
    return shutil.which(ffmpeg_bin()) is not None and shutil.which(ffprobe_bin()) is not None
