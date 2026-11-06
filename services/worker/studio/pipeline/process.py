"""End-to-end editing of one source file. Storage/DB agnostic: takes a local path,
returns local output files + metadata. The cloud runner wraps this."""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from studio.media import MediaError, extract_audio, probe
from studio.models import Analysis, Transcript
from studio.pipeline import editorial
from studio.pipeline.captions import CapWord, build_ass, build_srt, font_px
from studio.pipeline.cuts import map_words_to_output, plan_cuts
from studio.pipeline.qc import check_captions, check_cuts, check_video
from studio.pipeline.reframe import assign_focus, track_faces
from studio.pipeline.render import RenderSpec, output_fps, output_size, quantize_edl, render
from studio.pipeline.textnorm import fold, locate_quote
from studio.pipeline.transcribe import words_per_minute
from studio.styles import AUTO, DINAMICO, STYLES, StyleSpec, auto_style, get_style

log = logging.getLogger(__name__)

Progress = Callable[[float, str], None]
TranscribeFn = Callable[[Path], tuple[Transcript, float]]  # -> (transcript, gpu_seconds)


@dataclass
class JobOptions:
    style: str = AUTO
    full: bool = True
    full_aspect: str = "16:9"
    shorts: int = 3
    shorts_aspect: str = "9:16"
    shorts_style: str = "dinamico"
    hook_open: bool = False
    captions: bool = True
    remove_disfluencies: bool = True
    use_llm: bool = True
    revision: str | None = None
    max_llm_usd: float = 0.75

    @classmethod
    def from_dict(cls, d: dict) -> "JobOptions":
        return cls(**{k: v for k, v in (d or {}).items() if k in cls.__dataclass_fields__})


@dataclass
class Output:
    kind: str  # full | short
    path: Path
    srt_path: Path | None
    title: str
    aspect: str
    duration: float
    width: int
    height: int
    qc: dict
    variant: str = ""


@dataclass
class ProcessResult:
    transcript: Transcript
    analysis: Analysis
    style: str
    outputs: list[Output] = field(default_factory=list)
    gpu_seconds: float = 0.0
    llm_usage: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _noop(p: float, s: str) -> None:
    pass


def get_analysis(t: Transcript, opts: JobOptions, previous: Analysis | None, warnings: list[str]) -> Analysis:
    if opts.revision and previous is not None and opts.use_llm:
        try:
            return editorial.analyze_with_claude(t, revision=opts.revision, previous=previous)
        except Exception as e:  # noqa: BLE001 — never fail the job because of the LLM
            warnings.append(f"revisão por IA indisponível ({type(e).__name__}); mantendo análise anterior")
            return previous
    if previous is not None:
        return previous
    if opts.use_llm and t.sentences:
        est = editorial.estimate_cost(t, "claude-opus-5-5")
        if est <= opts.max_llm_usd:
            try:
                return editorial.analyze_with_claude(t)
            except Exception as e:  # noqa: BLE001
                log.exception("editorial LLM failed")
                warnings.append(f"análise por IA falhou ({type(e).__name__}: {str(e)[:120]}); usando heurística")
        else:
            warnings.append(f"análise por IA estimada em US$ {est:.2f} excede o limite por job; usando heurística")
    return editorial.heuristic_analysis(t)


def _emphasis_indices(t: Transcript, a: Analysis) -> set[int]:
    out: set[int] = set()
    for p in a.impact_phrases:
        loc = locate_quote(t.words, t.sentences, int(p.get("sentence_id", -1)), p.get("quote", ""))
        if not loc:
            continue
        keys = {fold(k) for kw in p.get("keywords", []) for k in kw.split()}
        for i in range(*loc):
            if fold(t.words[i].text) in keys:
                out.add(i)
    return out


def _render_one(
    src: Path, info, t: Transcript, a: Analysis, style: StyleSpec, edl, aspect: str, out_dir: Path,
    name: str, title_overlay: str | None, captions: bool, progress: Progress, faces,
) -> Output:
    w, h = output_size(info, aspect)
    fps = output_fps(info)
    assign_focus(edl.clips, faces)
    quantize_edl(edl, fps)
    emph = _emphasis_indices(t, a)
    mapped = map_words_to_output(edl, t.words)
    cap_words = [CapWord(t.words[i].text, s, e, i in emph) for i, s, e in mapped]
    ass_text, chunks = (None, [])
    if captions:
        ass_text, chunks = build_ass(cap_words, style.captions, w, h, title=title_overlay)
    spec = RenderSpec(width=w, height=h, fps=fps, style=style)
    work = out_dir / f"work_{name}"
    out_path = out_dir / f"{name}.mp4"
    render(src, info, edl, out_path, spec, work, ass_text=ass_text, progress=progress)
    rep = check_video(out_path, edl.duration, (w, h), style.loudness)
    if captions:
        vertical = h > w
        max_chars = style.captions.max_chars if not vertical else max(14, int(style.captions.max_chars * 0.62))
        check_captions(rep, chunks, font_px(style.captions, w, h), h, w, max_chars)
    check_cuts(rep, edl, t.words)
    srt_path = None
    if chunks:
        srt_path = out_dir / f"{name}.srt"
        srt_path.write_text(build_srt(chunks), encoding="utf-8")
    (out_dir / f"{name}.edl.json").write_text(json.dumps(edl.to_dict(), ensure_ascii=False), encoding="utf-8")
    return Output(
        kind="full", path=out_path, srt_path=srt_path, title=a.title, aspect=aspect,
        duration=round(edl.duration, 3), width=w, height=h, qc=rep.to_dict(),
    )


def process(
    src: str | Path,
    out_dir: str | Path,
    opts: JobOptions,
    transcribe_fn: TranscribeFn,
    transcript: Transcript | None = None,
    previous_analysis: Analysis | None = None,
    progress: Progress = _noop,
    max_minutes: float = 30,
) -> ProcessResult:
    src, out_dir = Path(src), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []

    progress(0.02, "analisando arquivo")
    info = probe(src)
    if info.duration <= 0.5:
        raise MediaError("vídeo vazio ou corrompido")
    if info.duration > max_minutes * 60:
        raise MediaError(f"vídeo com {info.duration / 60:.1f} min excede o limite de {max_minutes:.0f} min")

    gpu_s = 0.0
    if transcript is None:
        progress(0.05, "extraindo áudio")
        if not info.has_audio:
            transcript = Transcript(language="pt", duration=info.duration, words=[], sentences=[])
            warnings.append("vídeo sem áudio — sem transcrição/legendas")
        else:
            audio = extract_audio(src, out_dir / "audio.flac")
            progress(0.08, "transcrevendo (pt-BR)")
            transcript, gpu_s = transcribe_fn(audio)
            audio.unlink(missing_ok=True)
    progress(0.30, "análise editorial")
    analysis = get_analysis(transcript, opts, previous_analysis, warnings)

    if opts.style == AUTO:
        rec = (analysis.to_dict().get("style_recommendation") if analysis.source == "llm" else None)
        style = STYLES.get(rec) if rec else auto_style(words_per_minute(transcript), info.duration)
    else:
        style = get_style(opts.style)

    progress(0.36, "rastreando rosto para enquadramento")
    try:
        faces = track_faces(src)
    except Exception as e:  # noqa: BLE001
        warnings.append(f"rastreamento facial indisponível: {e}")
        faces = []

    result = ProcessResult(transcript=transcript, analysis=analysis, style=style.key, gpu_seconds=gpu_s,
                           llm_usage=analysis.usage if analysis.source == "llm" else {}, warnings=warnings)

    renders: list[tuple[str, Callable[[Progress], Output]]] = []
    if opts.full:
        def full_job(p: Progress, hook_open: bool = opts.hook_open) -> Output:
            hook = None
            if hook_open and analysis.hooks:
                h = analysis.hooks[0]
                hook = locate_quote(transcript.words, transcript.sentences, int(h["sentence_id"]), h["quote"])
            edl = plan_cuts(transcript, style, analysis, hook=hook, remove_disfluencies=opts.remove_disfluencies)
            if not edl.clips:  # no speech: keep the whole video
                from studio.models import Clip
                from studio.models import EditDecisionList
                edl = EditDecisionList(clips=[Clip(0.0, info.duration)])
            o = _render_one(src, info, transcript, analysis, style, edl, opts.full_aspect, out_dir,
                            "completo", None, opts.captions, p, faces)
            o.variant = "gancho" if hook else "original"
            return o
        renders.append(("versão completa", full_job))

    short_style = STYLES.get(opts.shorts_style, DINAMICO)
    for k, hl in enumerate(analysis.highlights[: max(0, opts.shorts)]):
        def short_job(p: Progress, hl=hl, k=k) -> Output:
            edl = plan_cuts(transcript, short_style, analysis, sentence_range=(int(hl["start_sentence"]), int(hl["end_sentence"])),
                            remove_disfluencies=opts.remove_disfluencies)
            o = _render_one(src, info, transcript, analysis, short_style, edl, opts.shorts_aspect, out_dir,
                            f"short_{k + 1:02d}", hl.get("hook") or hl.get("title"), opts.captions, p, faces)
            o.kind, o.title, o.variant = "short", hl.get("title") or f"Short {k + 1}", f"score {hl.get('score', 0)}"
            return o
        renders.append((f"short {k + 1}", short_job))

    span = 0.58 / max(1, len(renders))
    for n, (label, fn) in enumerate(renders):
        base = 0.40 + n * span

        def sub(p: float, s: str, base=base, label=label) -> None:
            progress(base + p * span, f"{label}: {s}")

        t0 = time.time()
        try:
            out = fn(sub)
        except Exception as e:  # noqa: BLE001
            log.exception("render failed: %s", label)
            if label == "versão completa":
                raise
            warnings.append(f"{label} falhou: {str(e)[:160]}")
            continue
        log.info("%s rendered in %.1fs (qc ok=%s)", label, time.time() - t0, out.qc["ok"])
        if not out.qc["ok"]:
            failed = [c["name"] for c in out.qc["checks"] if c["blocking"] and not c["ok"]]
            if label == "versão completa":
                raise MediaError(f"controle de qualidade reprovou a versão completa: {', '.join(failed)}")
            warnings.append(f"{label} reprovado no QC ({', '.join(failed)}) — descartado")
            continue
        result.outputs.append(out)
    progress(1.0, "concluído")
    return result
