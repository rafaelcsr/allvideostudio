import pytest
from conftest import make_transcript, speak

from studio.models import Analysis
from studio.pipeline.cuts import detect_disfluencies, map_words_to_output, plan_cuts
from studio.styles import CINEMATOGRAFICO, CORPORATIVO, DINAMICO


def texts(t, idx):
    return [t.words[i].text for i in idx]


def test_fillers_and_repetitions_detected():
    t = make_transcript(speak("ahn eu eu acho que pro- produto é muito muito bom hum."))
    rem = detect_disfluencies(t.words)
    assert sorted(texts(t, rem)) == sorted(["ahn", "eu", "pro-", "hum."])
    # emphatic repetition and real short words are preserved
    kept = [w.text for i, w in enumerate(t.words) if i not in rem]
    assert kept.count("muito") == 2 and "é" in kept


def test_retake_ngram_removed_keeps_second_take():
    t = make_transcript(speak("eu acho que eu acho que isso funciona."))
    rem = detect_disfluencies(t.words)
    assert sorted(rem) == [0, 1, 2]


def test_long_silence_is_compressed_with_padding():
    spec = speak("primeira frase aqui.") + speak("segunda frase aqui.", start=6.0)
    t = make_transcript(spec, duration=8.0)
    edl = plan_cuts(t, CORPORATIVO)
    assert len(edl.clips) == 2
    a, b = edl.clips
    assert a.src_end - t.words[2].end == pytest.approx(CORPORATIVO.pad_after)
    assert t.words[3].start - b.src_start == pytest.approx(CORPORATIVO.pad_before)
    assert edl.duration < 4.0  # ~4.6s of dead air removed


def test_short_pauses_kept_inside_clip():
    t = make_transcript(speak("uma fala contínua com respiros curtos."))
    edl = plan_cuts(t, CINEMATOGRAFICO)
    assert len(edl.clips) == 1


def test_cut_never_includes_removed_word():
    t = make_transcript(speak("então ahn vamos começar."))
    edl = plan_cuts(t, DINAMICO)
    filler = t.words[1]
    for c in edl.clips:
        assert not (c.src_start < (filler.start + filler.end) / 2 < c.src_end)


def test_llm_removal_by_quote():
    t = make_transcript(speak("hoje vou falar de vendas. não, espera, hoje vou falar de marketing digital."))
    a = Analysis(removals=[{"sentence_id": 0, "quote": "hoje vou falar de vendas.", "reason": "retake"},
                           {"sentence_id": 1, "quote": "não, espera,", "reason": "falso começo"}])
    edl = plan_cuts(t, CORPORATIVO, a)
    kept = [t.words[i].text for i, _, _ in map_words_to_output(edl, t.words)]
    assert " ".join(kept) == "hoje vou falar de marketing digital."


def test_output_mapping_is_monotonic_and_within_duration():
    spec = speak("um dois três quatro.") + speak("cinco seis sete.", start=5.0) + speak("oito nove.", start=9.0)
    t = make_transcript(spec, duration=11)
    edl = plan_cuts(t, DINAMICO)
    m = map_words_to_output(edl, t.words)
    starts = [s for _, s, _ in m]
    assert starts == sorted(starts)
    assert m[-1][2] <= edl.duration + 1e-6
    assert len(m) == len(t.words)


def test_short_range_and_hook():
    spec = speak("intro sem graça.") + speak("a frase de impacto que prende!", start=3) + speak("conclusão final.", start=7)
    t = make_transcript(spec, duration=9)
    edl = plan_cuts(t, DINAMICO, sentence_range=(1, 1))
    assert edl.clips[0].src_start >= t.sentences[1].start - DINAMICO.pad_before - 1e-6
    assert edl.clips[-1].src_end <= t.sentences[1].end + DINAMICO.pad_after + 1e-6
    hooked = plan_cuts(t, DINAMICO, hook=(t.sentences[1].word_start, t.sentences[1].word_end))
    assert hooked.clips[0].src_start > 2.5  # cold open starts with the hook


def test_dynamic_punch_in_alternates():
    spec = speak("a b c d e f g h i j k l m n o p q r s t u v w x y z a1 b1 c1 d1", wdur=0.35, gap=0.05)
    t = make_transcript(spec)
    edl = plan_cuts(t, DINAMICO, remove_disfluencies=False)
    zooms = [c.zoom for c in edl.clips]
    assert len(edl.clips) >= 2 and 1.0 in zooms and DINAMICO.punch_in in zooms
