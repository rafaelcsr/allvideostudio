# Arquitetura

```
 Navegador (qualquer computador)
   │  Next.js estático — Cloudflare Pages (grátis)
   │
   ├─ login / leitura de projetos, jobs, resultados ──► Supabase (Auth + Postgres, RLS, Realtime)
   │
   ├─ PUT direto do vídeo (URL pré-assinada) ─────────► Cloudflare R2  users/{uid}/projects/{pid}/…
   │
   └─ chamadas que custam dinheiro ──────────────────► API FastAPI (Modal, escala a zero)
                                                         │ valida usuário, estima custo, aplica teto
                                                         │ spawn
                                                         ▼
                                            process_job (Modal, 4 CPU, sob demanda)
                                              ├─ FFmpeg: extrai áudio
                                              ├─ transcribe ──► Modal GPU L4 (faster-whisper large-v3-turbo)
                                              ├─ análise editorial ──► Claude (API Anthropic)
                                              ├─ rastreamento facial (OpenCV YuNet)
                                              ├─ plano de cortes → EDL → render FFmpeg (2 estágios)
                                              ├─ legendas ASS dinâmicas + SRT
                                              ├─ controle de qualidade (bloqueia entrega)
                                              └─ envia ao R2, grava renders + ledger de custos
```

## Princípios
- **Nada ligado sem uso.** API, render e GPU escalam a zero no Modal; frontend é estático.
- **Original intocado.** O arquivo enviado nunca é modificado; toda edição gera novos arquivos em `renders/{job}/`.
- **Escritas só pelo servidor.** O navegador só lê (RLS por `user_id`); criar jobs, URLs de upload/download e custos passam pela API com service role.
- **Fornecedores substituíveis.** `Storage` (R2 ↔ local/S3), `DB` (Supabase ↔ memória), `transcribe_fn` (GPU remota ↔ local) e o modelo editorial são injetados.

## Pipeline editorial (`services/worker/studio/pipeline`)
| Módulo | Função |
|---|---|
| `transcribe.py` | faster-whisper com timestamps por palavra, VAD, prompt que preserva hesitações para remoção controlada |
| `editorial.py` | Claude lê a transcrição inteira e devolve JSON validado por schema: tópicos, remoções (retakes, falsos começos), destaques para shorts, ganchos, frases de impacto, hashtags, estilo recomendado. Fallback heurístico gratuito |
| `cuts.py` | Remoção de hesitações/gaguejos/repetições por regras conservadoras para pt-BR + remoções da IA localizadas por citação (fuzzy). Cortes só em fronteiras de palavra, com respiro (padding) por estilo; silêncios longos comprimidos; zooms alternados para esconder jump cuts; zoom de ênfase em frases de impacto; fades entre tópicos (cinematográfico); cold open com gancho |
| `reframe.py` | Rosto dominante amostrado a 2 fps; enquadramento estável por corte (sem "flutuar"); headroom de terços |
| `captions.py` | ASS/libass: destaque palavra a palavra, cor de ênfase em palavras-chave, caixa (corporativo), maiúsculas (dinâmico), área segura para 9:16, título-gancho nos shorts; exporta SRT |
| `render.py` | Estágio 1: cada corte renderizado com seek exato + crop/zoom + micro-fades de áudio (PCM, sem drift de sincronia), em paralelo. Estágio 2: concat sem perda + grade de cor, legendas, tratamento de voz (passa-alta, redução de ruído, compressor, loudnorm -14/-16 LUFS) |
| `qc.py` | Integridade, decodificação completa, duração, resolução, sincronia A/V, loudness, tela preta, legibilidade e continuidade dos cortes |

## Dados
`projects` → `jobs` (edit/revise, opções, análise, progresso, custo) → `renders` (arquivo, legenda, QC). `usage_ledger` registra cada custo por serviço; `budget_alerts` registra alertas. Transcrição fica em cache no R2 (`derived/transcript.json`, invalidada pelo ETag do original); a análise é reaproveitada entre jobs do mesmo projeto.
