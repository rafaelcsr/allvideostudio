# AI Video Studio

Estúdio audiovisual premium e cloud-first: envie um vídeo bruto pela web e receba a edição feita por um agente editorial — cortes semânticos, remoção de silêncios/gaguejos/retakes, legendas dinâmicas, enquadramento facial, tratamento de áudio, versão completa e cortes curtos (Reels/TikTok/Shorts).

| Camada | Tecnologia |
|---|---|
| Web | Next.js (export estático) → Cloudflare Pages |
| Auth + banco | Supabase (RLS, Realtime) |
| Mídia | Cloudflare R2 (upload direto pré-assinado) |
| API + processamento | Modal (FastAPI, CPU e GPU L4 sob demanda, escala a zero) |
| Motor | FFmpeg, faster-whisper, OpenCV, libass |
| Inteligência editorial | Claude (API Anthropic) com JSON Schema |

```
apps/web/            frontend Next.js
services/worker/     motor editorial + API (Python)  — studio/pipeline/*, modal_app.py, tests/
supabase/migrations/ schema + RLS
infra/               CORS do R2
scripts/deploy.ps1   implantação reprodutível
docs/                ARCHITECTURE · COSTS · SETUP · DECISIONS
```

- Estilos: **Corporativo**, **Dinâmico**, **Cinematográfico** ou **Automático** (`studio/styles.py`).
- Orçamento: teto mensal com bloqueio prévio, ledger por serviço e alertas — ver [docs/COSTS.md](docs/COSTS.md).
- Implantação: [docs/SETUP.md](docs/SETUP.md).

Testes: `cd services/worker && .venv\Scripts\python.exe -m pytest -q` (renderizações reais com FFmpeg).
