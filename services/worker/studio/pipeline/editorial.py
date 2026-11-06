"""Editorial agent: understands the whole talk and decides what to cut, what to
highlight and which moments become shorts. Claude does the semantic work; a
heuristic fallback keeps the pipeline alive when the AI budget is exhausted."""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from studio.models import Analysis, Transcript

log = logging.getLogger(__name__)

PROMPT_VERSION = "editorial-v1"

# USD per million tokens (input, output). Verify against current pricing.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}
CACHE_READ_FACTOR = 0.1
CACHE_WRITE_FACTOR = 1.25

SYSTEM = """Você é o editor-chefe de um estúdio audiovisual premium brasileiro. Recebe a transcrição \
integral (pt-BR, com marcações de tempo) de um vídeo bruto gravado por um criador e toma decisões editoriais.

Princípios:
- Entenda a narrativa inteira antes de decidir. Preserve o sentido, a naturalidade e a continuidade da fala.
- Remova apenas o que prejudica: retakes (quando a pessoa recomeça a frase — remova a tentativa ruim e mantenha a melhor), \
falsos começos, repetições involuntárias, frases abandonadas, trechos fora de contexto ou erros evidentes. \
Hesitações isoladas ("ahn", "hum") e silêncios já são tratados automaticamente — não os liste.
- Nunca corte de modo que a frase seguinte perca o sujeito, o verbo ou a referência.
- Cada remoção cita o trecho EXATO como aparece na transcrição (copie as palavras), dentro da frase indicada.
- Destaques (shorts/reels): trechos autossuficientes de 20 a 75 segundos, com começo que prende, \
desenvolvimento e conclusão clara. Ordene por potencial de retenção (score 0-100).
- Ganchos: frases fortes que funcionam como abertura do vídeo completo.
- Frases de impacto: afirmações memoráveis; liste 1-3 palavras-chave de cada uma para destaque visual.
- Tópicos: divisão da narrativa em blocos de assunto, cobrindo o vídeo inteiro em ordem.
Escreva títulos e textos em português brasileiro, sem clickbait enganoso."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "summary", "hashtags", "topics", "removals", "highlights", "hooks", "impact_phrases", "style_recommendation"],
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "style_recommendation": {"type": "string", "enum": ["corporativo", "dinamico", "cinematografico"]},
        "topics": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["title", "start_sentence", "end_sentence"],
            "properties": {"title": {"type": "string"}, "start_sentence": {"type": "integer"}, "end_sentence": {"type": "integer"}},
        }},
        "removals": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["sentence_id", "quote", "reason"],
            "properties": {
                "sentence_id": {"type": "integer"},
                "quote": {"type": "string"},
                "reason": {"type": "string", "enum": ["retake", "falso começo", "repetição", "fora de contexto", "erro", "frase abandonada"]},
            },
        }},
        "highlights": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["start_sentence", "end_sentence", "title", "hook", "score", "reason"],
            "properties": {
                "start_sentence": {"type": "integer"}, "end_sentence": {"type": "integer"},
                "title": {"type": "string"}, "hook": {"type": "string"},
                "score": {"type": "integer"}, "reason": {"type": "string"},
            },
        }},
        "hooks": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["sentence_id", "quote", "why"],
            "properties": {"sentence_id": {"type": "integer"}, "quote": {"type": "string"}, "why": {"type": "string"}},
        }},
        "impact_phrases": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["sentence_id", "quote", "keywords"],
            "properties": {
                "sentence_id": {"type": "integer"}, "quote": {"type": "string"},
                "keywords": {"type": "array", "items": {"type": "string"}},
            },
        }},
    },
}


def _fmt(t: float) -> str:
    m, s = divmod(t, 60)
    return f"{int(m):02d}:{s:04.1f}"


def transcript_prompt(t: Transcript) -> str:
    lines = [f"Duração total: {_fmt(t.duration)} — {len(t.sentences)} frases.", ""]
    prev_end = 0.0
    for s in t.sentences:
        pause = s.start - prev_end
        mark = f" (pausa {pause:.1f}s)" if pause > 1.5 else ""
        lines.append(f"[{s.id}] {_fmt(s.start)}–{_fmt(s.end)}{mark} | {s.text}")
        prev_end = s.end
    return "\n".join(lines)


def estimate_tokens(t: Transcript) -> tuple[int, int]:
    """(input, output) token estimate — output includes adaptive thinking headroom."""
    chars = len(transcript_prompt(t)) + len(SYSTEM) + len(json.dumps(SCHEMA))
    inp = int(chars / 3.0) + 200
    out = min(16000, 2500 + len(t.sentences) * 25)
    return inp, out


def cost_usd(model: str, usage: dict[str, int]) -> float:
    pin, pout = PRICES.get(model, PRICES["claude-opus-5-5"])
    inp = usage.get("input_tokens", 0)
    cr = usage.get("cache_read_input_tokens", 0) or 0
    cw = usage.get("cache_creation_input_tokens", 0) or 0
    out = usage.get("output_tokens", 0)
    return (inp * pin + cr * pin * CACHE_READ_FACTOR + cw * pin * CACHE_WRITE_FACTOR + out * pout) / 1e6


def estimate_cost(t: Transcript, model: str) -> float:
    i, o = estimate_tokens(t)
    return cost_usd(model, {"input_tokens": i, "output_tokens": o})


def analyze_with_claude(
    t: Transcript,
    model: str | None = None,
    effort: str | None = None,
    revision: str | None = None,
    previous: Analysis | None = None,
    client=None,
) -> Analysis:
    import anthropic

    model = model or os.environ.get("EDITORIAL_MODEL", "claude-opus-5-5")
    effort = effort or os.environ.get("EDITORIAL_EFFORT", "medium")
    client = client or anthropic.Anthropic(max_retries=3, timeout=600)

    messages: list[dict[str, Any]] = [{
        "role": "user",
        "content": [
            {"type": "text", "text": "Transcrição do vídeo bruto:\n\n" + transcript_prompt(t), "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": "Produza a análise editorial completa no formato solicitado."},
        ],
    }]
    if revision and previous is not None:
        prev = {k: v for k, v in previous.to_dict().items() if k not in ("source", "usage")}
        messages += [
            {"role": "assistant", "content": json.dumps(prev, ensure_ascii=False)},
            {"role": "user", "content": (
                "O criador revisou o resultado e pediu esta correção:\n\n"
                f"\"{revision}\"\n\n"
                "Refaça a análise completa aplicando o pedido; mantenha o que não foi questionado."
            )},
        ]

    kwargs: dict[str, Any] = dict(
        model=model,
        max_tokens=16000,
        system=SYSTEM,
        messages=messages,
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": SCHEMA}},
    )
    use_fallback = os.environ.get("EDITORIAL_FALLBACKS", "1") == "1" and model in ("claude-opus-5-5", "claude-sonnet-5-5")
    if use_fallback:
        resp = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
    else:
        resp = client.messages.create(**kwargs)

    if resp.stop_reason == "refusal":
        raise RuntimeError("o modelo recusou a análise")
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("análise truncada (max_tokens)")
    text = next(b.text for b in resp.content if b.type == "text")
    data = json.loads(text)
    u = resp.usage
    usage = {
        "model": resp.model,
        "input_tokens": u.input_tokens,
        "output_tokens": u.output_tokens,
        "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
        "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
    }
    usage["cost_usd"] = round(cost_usd(resp.model, usage), 5)
    return _sanitize(Analysis.from_dict({**data, "source": "llm", "usage": usage}), t)


def _sanitize(a: Analysis, t: Transcript) -> Analysis:
    """Drop references to non-existent sentences and clamp highlight sizes."""
    ids = {s.id for s in t.sentences}
    by_id = {s.id: s for s in t.sentences}
    a.removals = [r for r in a.removals if r.get("sentence_id") in ids and r.get("quote", "").strip()]
    a.hooks = [h for h in a.hooks if h.get("sentence_id") in ids]
    a.impact_phrases = [p for p in a.impact_phrases if p.get("sentence_id") in ids]
    a.topics = [x for x in a.topics if x.get("start_sentence") in ids and x.get("end_sentence") in ids]
    hl = []
    for h in a.highlights:
        s, e = h.get("start_sentence"), h.get("end_sentence")
        if s in ids and e in ids and e >= s:
            dur = by_id[e].end - by_id[s].start
            if 8 <= dur <= 120:
                hl.append(h)
    a.highlights = sorted(hl, key=lambda h: -int(h.get("score", 0)))
    return a


def heuristic_analysis(t: Transcript) -> Analysis:
    """Zero-cost fallback: density/intonation based highlights and hooks."""
    a = Analysis(source="heuristic")
    if not t.sentences:
        return a
    a.title = t.sentences[0].text[:80]
    a.summary = " ".join(s.text for s in t.sentences[:3])[:400]
    a.topics = [{"title": "Conteúdo", "start_sentence": t.sentences[0].id, "end_sentence": t.sentences[-1].id}]

    def strength(i: int) -> float:
        s = t.sentences[i]
        dur = max(0.5, s.end - s.start)
        wps = (s.word_end - s.word_start) / dur
        bonus = 0.6 if s.text.endswith(("!", "?")) else 0.0
        return wps + bonus

    hooks = sorted(range(len(t.sentences)), key=strength, reverse=True)[:3]
    a.hooks = [{"sentence_id": t.sentences[i].id, "quote": t.sentences[i].text, "why": "frase enérgica"} for i in hooks]

    cands = []
    for i in range(len(t.sentences)):
        for j in range(i, len(t.sentences)):
            dur = t.sentences[j].end - t.sentences[i].start
            if dur > 60:
                break
            if dur >= 20:
                score = sum(strength(k) for k in range(i, j + 1)) / (j - i + 1)
                cands.append((score, i, j))
    cands.sort(reverse=True)
    used: set[int] = set()
    for score, i, j in cands:
        if any(k in used for k in range(i, j + 1)):
            continue
        used.update(range(i, j + 1))
        a.highlights.append({
            "start_sentence": t.sentences[i].id, "end_sentence": t.sentences[j].id,
            "title": t.sentences[i].text[:60], "hook": t.sentences[i].text[:80],
            "score": int(min(100, score * 20)), "reason": "densidade de fala",
        })
        if len(a.highlights) >= 5:
            break
    return a
