import pytest

from studio.budget import BudgetExceeded, check_budget, estimate_job
from studio.pipeline.captions import CapWord, build_ass, build_srt, chunk_words
from studio.pipeline.editorial import heuristic_analysis
from studio.styles import CORPORATIVO, DINAMICO
from conftest import make_transcript, speak


def words(text, start=0.0):
    return [CapWord(w, s, e) for w, s, e in speak(text, start)]


def test_chunking_respects_limits_and_sentence_end():
    ch = chunk_words(words("um dois três. quatro cinco seis sete oito"), max_words=3, max_chars=40)
    assert [c.text for c in ch] == ["um dois três.", "quatro cinco seis", "sete oito"]


def test_dynamic_ass_has_one_event_per_word_and_highlight():
    ws = words("isso muda tudo agora")
    ws[2].emphasis = True
    ass, chunks = build_ass(ws, DINAMICO.captions, 1080, 1920, title="Gancho {forte}")
    dialogues = [ln for ln in ass.splitlines() if ln.startswith("Dialogue: 0")]
    assert len(dialogues) == 4
    assert "TUDO" in ass and "ISSO" in ass  # uppercase style
    assert "{forte}" not in ass  # braces escaped
    assert "PlayResY: 1920" in ass


def test_corporate_ass_single_event_per_chunk_with_box():
    ass, chunks = build_ass(words("uma legenda elegante e bem legível."), CORPORATIVO.captions, 1920, 1080)
    assert sum(ln.startswith("Dialogue") for ln in ass.splitlines()) == len(chunks) == 1
    style_line = next(ln for ln in ass.splitlines() if ln.startswith("Style: Cap"))
    assert ",3," in style_line  # BorderStyle 3 = opaque box


def test_srt_format():
    _, chunks = build_ass(words("olá mundo."), CORPORATIVO.captions, 1920, 1080)
    assert build_srt(chunks).startswith("1\n00:00:00,000 --> ")


def test_budget_estimate_and_guard():
    est = estimate_job(600, full=True, shorts=3)
    assert 0 < est.total_usd < 1.5, est
    assert check_budget(1.0, est.total_usd, 18.0) is None
    assert check_budget(15.0, est.total_usd, 18.0) is not None  # alert zone
    with pytest.raises(BudgetExceeded):
        check_budget(17.9, est.total_usd, 18.0)


def test_heuristic_analysis_finds_highlights():
    spec = []
    t0 = 0.0
    for i in range(12):
        spec += speak(f"frase número {i} com conteúdo interessante e relevante!", start=t0)
        t0 = spec[-1][2] + 0.5
    a = heuristic_analysis(make_transcript(spec))
    assert a.highlights and a.hooks and a.source == "heuristic"
