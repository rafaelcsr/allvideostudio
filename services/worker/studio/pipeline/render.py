"""FFmpeg render engine.

Two stages, chosen for robustness on long videos with hundreds of cuts:
 1. every clip is rendered (frame-accurate seek, crop/zoom/reframe, micro audio fades)
    to a high-quality intermediate (H.264 CRF 15 + PCM in MKV) — parallelisable;
 2. the intermediates are concatenated losslessly and a single final pass applies
    colour grade, captions, voice treatment and loudness normalisation.
PCM intermediates avoid AAC priming gaps, so A/V sync cannot drift across cuts."""
from __future__ import annotations

import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Callable

from studio.media import ProbeInfo, ffmpeg
from studio.models import EditDecisionList
from studio.pipeline.reframe import crop_box
from studio.styles import StyleSpec

ProgressCb = Callable[[float, str], None]


@dataclass
class RenderSpec:
    width: int
    height: int
    fps: Fraction
    style: StyleSpec
    crf: int = 20
    preset: str = "medium"
    intermediate_crf: int = 15

    @property
    def vertical(self) -> bool:
        return self.height > self.width


def output_size(info: ProbeInfo, aspect: str) -> tuple[int, int]:
    sw, sh = info.display_size
    if aspect == "9:16":
        return 1080, 1920
    if aspect == "1:1":
        return 1080, 1080
    # 16:9 — never upscale beyond the source height, cap at 1080p for cost
    h = min(1080, sh if sh >= 480 else 720)
    h = h // 2 * 2
    return int(round(h * 16 / 9 / 2) * 2), h


def output_fps(info: ProbeInfo) -> Fraction:
    fps = info.fps
    if fps > 60:
        return Fraction(60)
    if fps < 20:
        return Fraction(30)
    return fps


def quantize_edl(edl: EditDecisionList, fps: Fraction) -> None:
    """Snap clip boundaries to the frame grid so the rendered timeline equals
    the EDL timeline exactly (captions are computed from the EDL)."""
    f = float(fps)
    for c in edl.clips:
        s = round(c.src_start * f) / f
        n = max(1, round((c.src_end - c.src_start) * f))
        c.src_start = s
        c.src_end = s + n / f


def _clip_cmd(
    src: Path, out: Path, start: float, dur: float, vf: str, af: str, has_audio: bool, threads: int
) -> list[str]:
    args = ["-ss", f"{start:.6f}", "-i", str(src)]
    if not has_audio:
        args += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    args += [
        "-map", "0:v:0", "-map", "0:a:0" if has_audio else "1:a:0",
        "-vf", vf, "-af", af, "-t", f"{dur:.6f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "15", "-pix_fmt", "yuv420p",
        "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2",
        "-threads", str(threads), str(out),
    ]
    return args


def render(
    src: str | Path,
    info: ProbeInfo,
    edl: EditDecisionList,
    out_path: str | Path,
    spec: RenderSpec,
    workdir: str | Path,
    ass_text: str | None = None,
    progress: ProgressCb | None = None,
    workers: int | None = None,
) -> Path:
    src, out_path, workdir = Path(src), Path(out_path), Path(workdir)
    clips_dir = workdir / "clips"
    shutil.rmtree(clips_dir, ignore_errors=True)
    clips_dir.mkdir(parents=True, exist_ok=True)
    sw, sh = info.display_size
    fps = spec.fps
    fade_d = 0.35
    cpu = os.cpu_count() or 2
    workers = workers or max(1, min(8, cpu // 2))
    threads = max(1, cpu // workers)

    jobs = []
    for i, c in enumerate(edl.clips):
        cw, ch, x, y = crop_box(sw, sh, spec.width, spec.height, c.zoom, c.focus)
        vf = [f"crop={cw}:{ch}:{x}:{y}", f"scale={spec.width}:{spec.height}:flags=lanczos", "setsar=1", f"fps={fps}"]
        if c.fade_in:
            vf.append(f"fade=t=in:st=0:d={fade_d}")
        if c.fade_out:
            vf.append(f"fade=t=out:st={max(0.0, c.duration - fade_d):.3f}:d={fade_d}")
        af = [
            "aresample=48000", "aformat=channel_layouts=stereo",
            "afade=t=in:st=0:d=0.012", f"afade=t=out:st={max(0.0, c.duration - 0.012):.4f}:d=0.012", "apad",
        ]
        if c.fade_in:
            af.append(f"afade=t=in:st=0:d={fade_d}")
        out = clips_dir / f"{i:05d}.mkv"
        jobs.append((out, _clip_cmd(src, out, c.src_start, c.duration, ",".join(vf), ",".join(af), info.has_audio, threads)))

    done = 0

    def work(job):
        ffmpeg(*job[1])
        return job[0]

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for _ in pool.map(work, jobs):
            done += 1
            if progress:
                progress(done / max(1, len(jobs)) * 0.75, f"renderizando cortes {done}/{len(jobs)}")

    concat = workdir / "concat.txt"
    concat.write_text("".join(f"file 'clips/{p.name}'\n" for p, _ in jobs), encoding="utf-8")

    st = spec.style
    vchain = [st.grade]
    if st.letterbox and not spec.vertical:
        bar = int(spec.height * 0.056)
        vchain.append(f"drawbox=x=0:y=0:w=iw:h={bar}:color=black:t=fill")
        vchain.append(f"drawbox=x=0:y=ih-{bar}:w=iw:h={bar}:color=black:t=fill")
    if ass_text:
        (workdir / "captions.ass").write_text(ass_text, encoding="utf-8")
        vchain.append("ass=captions.ass")
    vchain.append("format=yuv420p")
    achain = [
        "highpass=f=70",
        f"afftdn=nr={st.denoise}:nf=-42:tn=1",
        "acompressor=threshold=-21dB:ratio=2.5:attack=8:release=120:makeup=1.5",
        f"loudnorm=I={st.loudness}:TP=-1.5:LRA=11",
        "aresample=48000",
    ]
    if progress:
        progress(0.8, "acabamento: cor, legendas e áudio")
    tmp_out = workdir / "final.mp4"
    ffmpeg(
        "-f", "concat", "-safe", "0", "-i", "concat.txt",
        "-filter_complex", f"[0:v]{','.join(vchain)}[v];[0:a]{','.join(achain)}[a]",
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", spec.preset, "-crf", str(spec.crf), "-profile:v", "high",
        "-pix_fmt", "yuv420p", "-r", str(fps),
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart", "final.mp4",
        cwd=workdir,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(tmp_out), out_path)
    shutil.rmtree(clips_dir, ignore_errors=True)
    if progress:
        progress(1.0, "render concluído")
    return out_path
