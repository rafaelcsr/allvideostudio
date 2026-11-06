"""Audiovisual engine integration tests — real ffmpeg renders + QC."""
import subprocess

from conftest import make_transcript, needs_ffmpeg, speak

from studio.media import probe
from studio.models import Clip, EditDecisionList
from studio.pipeline.captions import CapWord, build_ass
from studio.pipeline.cuts import map_words_to_output, plan_cuts
from studio.pipeline.process import JobOptions, process
from studio.pipeline.qc import check_video
from studio.pipeline.render import RenderSpec, output_fps, output_size, quantize_edl, render
from studio.styles import CINEMATOGRAFICO, DINAMICO


@needs_ffmpeg
def test_render_horizontal_with_cuts_and_captions(synthetic_video, tmp_path):
    info = probe(synthetic_video)
    edl = EditDecisionList(clips=[Clip(0.2, 2.9), Clip(5.1, 7.9, zoom=1.12), Clip(9.0, 11.8, fade_in=True)])
    fps = output_fps(info)
    quantize_edl(edl, fps)
    w, h = output_size(info, "16:9")
    ass, _ = build_ass([CapWord("olá", 0.1, 0.6), CapWord("mundo", 0.7, 1.2, True)], DINAMICO.captions, w, h)
    out = render(synthetic_video, info, edl, tmp_path / "o.mp4", RenderSpec(w, h, fps, CINEMATOGRAFICO), tmp_path / "w", ass_text=ass)
    rep = check_video(out, edl.duration, (w, h), CINEMATOGRAFICO.loudness)
    assert rep.ok, rep.to_dict()
    assert (w, h) == (1280, 720)


@needs_ffmpeg
def test_render_vertical_reframe(synthetic_video, tmp_path):
    info = probe(synthetic_video)
    edl = EditDecisionList(clips=[Clip(0.0, 3.0, focus=(0.3, 0.4)), Clip(5.0, 8.0, zoom=1.2, focus=(0.7, 0.4))])
    fps = output_fps(info)
    quantize_edl(edl, fps)
    out = render(synthetic_video, info, edl, tmp_path / "v.mp4", RenderSpec(1080, 1920, fps, DINAMICO), tmp_path / "w")
    rep = check_video(out, edl.duration, (1080, 1920), DINAMICO.loudness)
    assert rep.ok, rep.to_dict()


@needs_ffmpeg
def test_many_cuts_keep_av_sync(synthetic_video, tmp_path):
    info = probe(synthetic_video)
    clips = [Clip(i * 0.5, i * 0.5 + 0.37) for i in range(22)]  # 22 jump cuts
    edl = EditDecisionList(clips=clips)
    fps = output_fps(info)
    quantize_edl(edl, fps)
    out = render(synthetic_video, info, edl, tmp_path / "s.mp4", RenderSpec(640, 360, fps, DINAMICO, preset="ultrafast"), tmp_path / "w")
    p = probe(out)
    assert abs(p.audio_duration - p.video_duration) < 0.06
    assert abs(p.duration - edl.duration) < 0.15


@needs_ffmpeg
def test_qc_detects_corrupted_file(synthetic_video, tmp_path):
    bad = tmp_path / "bad.mp4"
    data = synthetic_video.read_bytes()
    bad.write_bytes(data[: len(data) // 3])
    rep = check_video(bad, 12.0, (1280, 720), -14)
    assert not rep.ok


@needs_ffmpeg
def test_full_process_with_fake_transcriber(synthetic_video, tmp_path):
    # words aligned with the tone bursts of the synthetic video
    spec = speak("bem-vindos ao estúdio ahn hoje.", start=0.2) + speak("vamos editar vídeos com inteligência!", start=5.1) \
        + speak("obrigado por assistir até o final.", start=9.1)
    t = make_transcript(spec, duration=12.0)

    def fake_transcribe(_audio):
        return t, 0.0

    res = process(synthetic_video, tmp_path / "out", JobOptions(style="dinamico", shorts=1, use_llm=False),
                  transcribe_fn=fake_transcribe)
    kinds = [o.kind for o in res.outputs]
    assert "full" in kinds, res.warnings
    full = next(o for o in res.outputs if o.kind == "full")
    assert full.qc["ok"] and full.srt_path and full.srt_path.exists()
    assert full.duration < 12.0  # silences + filler removed
    srt = full.srt_path.read_text(encoding="utf-8")
    assert "ahn" not in srt.lower()
