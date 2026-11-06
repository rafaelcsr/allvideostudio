"""Quality control gate — nothing is delivered without passing the blocking checks."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from studio.media import MediaError, ffmpeg, probe
from studio.models import EditDecisionList, Word
from studio.pipeline.captions import Chunk


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    blocking: bool = True


@dataclass
class QCReport:
    checks: list[Check] = field(default_factory=list)
    loudness: float | None = None

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks if c.blocking)

    def add(self, name: str, ok: bool, detail: str, blocking: bool = True) -> None:
        self.checks.append(Check(name, bool(ok), detail, blocking))

    def to_dict(self) -> dict:
        return {"ok": self.ok, "loudness": self.loudness, "checks": [asdict(c) for c in self.checks]}


_ERR = re.compile(r"(error while decoding|corrupt|invalid data|non-existing pps|missing picture)", re.I)
_LUFS = re.compile(r"I:\s+(-?\d+(?:\.\d+)?)\s+LUFS")
_BLACK = re.compile(r"black_duration:(\d+(?:\.\d+)?)")


def check_video(
    path: str | Path,
    expected_duration: float,
    expected_size: tuple[int, int],
    loudness_target: float,
) -> QCReport:
    rep = QCReport()
    path = Path(path)
    try:
        info = probe(path)
    except MediaError as e:
        rep.add("integridade", False, f"ffprobe falhou: {e}")
        return rep
    rep.add("integridade_container", info.size_bytes > 1024, f"{info.size_bytes} bytes")

    tol = 0.25 + 0.002 * expected_duration
    rep.add("duracao", abs(info.duration - expected_duration) <= tol,
            f"{info.duration:.2f}s (esperado {expected_duration:.2f}s ±{tol:.2f})")
    rep.add("resolucao", (info.width, info.height) == tuple(expected_size),
            f"{info.width}x{info.height} (esperado {expected_size[0]}x{expected_size[1]})")
    rep.add("audio_presente", info.has_audio, info.acodec or "sem áudio")
    if info.audio_duration is not None and info.video_duration is not None:
        diff = abs(info.audio_duration - info.video_duration)
        rep.add("sincronizacao", diff <= 0.1, f"Δ áudio/vídeo = {diff * 1000:.0f} ms")

    # one full decode: decoding errors + loudness + black frames
    try:
        proc = ffmpeg(
            "-v", "info", "-i", str(path),
            "-vf", "blackdetect=d=0.8:pix_th=0.08", "-af", "ebur128=framelog=quiet",
            "-f", "null", "-",
        )
        log = proc.stderr
    except MediaError as e:
        rep.add("decodificacao", False, str(e))
        return rep
    errors = [ln for ln in log.splitlines() if _ERR.search(ln)]
    rep.add("decodificacao", not errors, "sem erros" if not errors else errors[0][:200])
    m = _LUFS.findall(log)
    if m:
        lufs = float(m[-1])
        rep.loudness = lufs
        rep.add("loudness", abs(lufs - loudness_target) <= 3.0,
                f"{lufs:.1f} LUFS (alvo {loudness_target})", blocking=False)
        rep.add("audio_nao_mudo", lufs > -50, f"{lufs:.1f} LUFS")
    black = sum(float(x) for x in _BLACK.findall(log))
    rep.add("quadros_pretos", black <= max(1.5, 0.1 * expected_duration), f"{black:.1f}s de tela preta", blocking=False)
    return rep


def check_captions(rep: QCReport, chunks: list[Chunk], font_px: int, height: int, width: int, max_chars: int) -> None:
    vertical = height > width
    min_px = (0.035 * width * 1.45) if vertical else 0.033 * height
    rep.add("legenda_tamanho", font_px >= min_px, f"{font_px}px (mín {min_px:.0f}px)")
    if not chunks:
        rep.add("legenda_presente", False, "nenhuma legenda gerada", blocking=False)
        return
    short = [c for c in chunks if (c.end - c.start) < 0.18 and len(c.words) > 1]
    rep.add("legenda_tempo_leitura", len(short) <= max(1, len(chunks) // 20),
            f"{len(short)} blocos rápidos demais de {len(chunks)}", blocking=False)
    long_ = [c for c in chunks if len(c.text) > max_chars + 8]
    rep.add("legenda_comprimento", not long_, f"{len(long_)} blocos longos demais", blocking=False)


def check_cuts(rep: QCReport, edl: EditDecisionList, words: list[Word]) -> None:
    removed = set(edl.removed_words)
    bad = 0
    for c in edl.clips:
        for b in (c.src_start, c.src_end):
            for i, w in enumerate(words):
                if i in removed:
                    continue
                if w.start + 0.06 < b < w.end - 0.06:
                    bad += 1
                    break
    rep.add("continuidade_cortes", bad <= max(1, len(edl.clips) // 25),
            f"{bad} cortes no meio de palavra em {len(edl.clips)} cortes", blocking=False)
    tiny = [c for c in edl.clips if c.duration < 0.12]
    rep.add("cortes_minimos", not tiny, f"{len(tiny)} cortes < 120ms", blocking=False)
