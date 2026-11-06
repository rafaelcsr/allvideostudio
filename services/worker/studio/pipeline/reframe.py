"""Face tracking for smart reframing (9:16 from 16:9 and focused punch-ins).

Samples frames at a low rate, detects the dominant face with OpenCV (YuNet when its
ONNX model is available, Haar cascade otherwise) and gives every clip a stable
subject centre. Per-clip static framing avoids the 'floaty' look of naive tracking."""
from __future__ import annotations

import os
import statistics
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from studio.media import ffmpeg_bin, probe
from studio.models import Clip


@dataclass
class FaceSample:
    t: float
    cx: float  # normalised 0..1
    cy: float
    size: float  # face height / frame height


def _detector(width: int, height: int):
    import cv2

    model = os.environ.get("YUNET_MODEL")
    if model and Path(model).exists():
        det = cv2.FaceDetectorYN.create(model, "", (width, height), 0.7, 0.3, 5000)

        def run(gray: np.ndarray):
            bgr = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
            _, faces = det.detect(bgr)
            if faces is None:
                return []
            return [(float(f[0]), float(f[1]), float(f[2]), float(f[3])) for f in faces]

        return run

    cascade = cv2.CascadeClassifier(os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml"))

    def run(gray: np.ndarray):
        eq = cv2.equalizeHist(gray)
        faces = cascade.detectMultiScale(eq, scaleFactor=1.15, minNeighbors=6, minSize=(int(height * 0.08),) * 2)
        return [tuple(map(float, f)) for f in faces]

    return run


def track_faces(src: str | Path, sample_fps: float = 2.0, width: int = 480, max_frames: int = 1500) -> list[FaceSample]:
    info = probe(src)
    dw, dh = info.display_size
    if info.duration * sample_fps > max_frames:
        sample_fps = max_frames / max(info.duration, 1)
    height = int(round(width * dh / dw / 2) * 2)
    cmd = [
        ffmpeg_bin(), "-hide_banner", "-nostdin", "-v", "error", "-i", str(src),
        "-vf", f"fps={sample_fps:.4f},scale={width}:{height}", "-an",
        "-f", "rawvideo", "-pix_fmt", "gray", "-",
    ]
    detect = _detector(width, height)
    frame_bytes = width * height
    samples: list[FaceSample] = []
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        idx = 0
        assert proc.stdout is not None
        while True:
            buf = proc.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            gray = np.frombuffer(buf, dtype=np.uint8).reshape(height, width)
            faces = detect(gray)
            if faces:
                x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
                samples.append(FaceSample(
                    t=idx / sample_fps,
                    cx=(x + w / 2) / width,
                    cy=(y + h / 2) / height,
                    size=h / height,
                ))
            idx += 1
    finally:
        proc.kill() if proc.poll() is None else None
        proc.wait()
    return samples


def assign_focus(clips: list[Clip], samples: list[FaceSample]) -> None:
    """Give each clip the median face position inside it (global median as fallback)."""
    if not samples:
        return
    gx = statistics.median(s.cx for s in samples)
    gy = statistics.median(s.cy for s in samples)
    for c in clips:
        inside = [s for s in samples if c.src_start - 0.5 <= s.t <= c.src_end + 0.5]
        if len(inside) >= 1:
            c.focus = (round(statistics.median(s.cx for s in inside), 4), round(statistics.median(s.cy for s in inside), 4))
        else:
            c.focus = (round(gx, 4), round(gy, 4))


def crop_box(
    src_w: int, src_h: int, out_w: int, out_h: int, zoom: float, focus: tuple[float, float]
) -> tuple[int, int, int, int]:
    """Crop (w, h, x, y) in source pixels for the target aspect, zoom and subject focus.
    The face is placed slightly above centre (rule of thirds headroom)."""
    target = out_w / out_h
    if src_w / src_h > target:
        ch, cw = src_h, src_h * target
    else:
        cw, ch = src_w, src_w / target
    cw, ch = cw / zoom, ch / zoom
    cw = max(2, int(cw) // 2 * 2)
    ch = max(2, int(ch) // 2 * 2)
    fx, fy = focus
    x = fx * src_w - cw / 2
    y = fy * src_h - ch * 0.42
    x = int(min(max(0, x), src_w - cw)) // 2 * 2
    y = int(min(max(0, y), src_h - ch)) // 2 * 2
    return cw, ch, x, y
