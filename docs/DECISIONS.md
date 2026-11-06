# Registro de decisões

**D1 — Frontend estático (Next.js `output: export`) no Cloudflare Pages.** Sem servidor Node para manter: custo zero e nada a escalar. Toda lógica com segredo fica na API Python. Rotas dinâmicas usam query string (`/project/?id=`).

**D2 — API no Modal (FastAPI) em vez de Supabase Edge Functions/Workers.** Uma única base de código Python para API e worker, testável com pytest; escala a zero; `spawn` direto para o job.

**D3 — Upload direto navegador → R2 por URL pré-assinada.** O vídeo nunca passa pela API (sem limites de corpo/tempo, sem custo de banda). Egress do R2 é gratuito, ideal para pré-visualização e download.

**D4 — GPU só para transcrição.** faster-whisper large-v3-turbo em L4 (~15× tempo real). Render é CPU (FFmpeg/x264) em função separada para não pagar GPU ociosa durante a renderização.

**D5 — Render em dois estágios com intermediários PCM.** Concat de centenas de cortes com AAC acumula atraso de *priming*; PCM em MKV elimina o drift. Seek exato por corte + paralelismo. Teste com 22 jump cuts verifica Δ A/V < 60 ms.

**D6 — Cortes só em fronteira de palavra, com padding por estilo.** Evita cortar sílabas e preserva naturalidade. Regras de hesitação são conservadoras em pt-BR ("um", "é", "aham" são palavras reais — o teste pegou "um" na lista de fillers e foi corrigido). Decisões semânticas (retakes, falsos começos) vêm da IA por citação literal, localizada por correspondência aproximada — a IA nunca precisa contar índices.

**D7 — Claude Opus 5.5 (effort medium) para a análise editorial, com saída por JSON Schema.** É a única etapa que exige compreensão da narrativa inteira; custa ~US$ 0,09 por vídeo de 10 min. Modelo configurável (`EDITORIAL_MODEL`) e teto por job; fallback heurístico gratuito se a IA falhar ou o orçamento não permitir. Fallback de recusa do lado do servidor (`fallbacks: "default"`) habilitado; desative com `EDITORIAL_FALLBACKS=0`.

**D8 — Legendas em ASS/libass em vez de Remotion (por ora).** libass entrega destaque palavra a palavra, cores de ênfase e caixas com custo zero dentro do FFmpeg. Remotion exige licença de empresa acima de certo porte e um Chromium para renderizar — avaliar para P4 (animações gráficas complexas).

**D9 — Enquadramento estável por corte.** Face tracking a 2 fps e crop fixo por corte (mediana) — visual profissional, sem "câmera flutuante" e barato.

**D10 — Registro financeiro conservador.** O ledger calcula custos por preço de tabela, sem descontar créditos gratuitos, para que o teto nunca seja ultrapassado por subestimação.
