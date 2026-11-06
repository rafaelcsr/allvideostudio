# Custos e proteções financeiras

Teto: **R$ 100/mês** (`MONTHLY_BUDGET_BRL`), convertido por `USD_BRL` (padrão 5,5 → ~US$ 18). Ajuste `USD_BRL` ao câmbio do dia.

## Custos fixos: R$ 0
| Serviço | Plano | Limite gratuito relevante |
|---|---|---|
| Cloudflare Pages | Free | sites estáticos ilimitados |
| Cloudflare R2 | Free tier | 10 GB armazenados, egress grátis, 1M escritas/10M leituras por mês |
| Supabase | Free | 500 MB Postgres, 50k usuários; **projeto pausa após 7 dias sem uso** (reativa no painel) |
| Modal | Starter | cobra por segundo de uso; verifique no painel o crédito mensal incluído no seu plano |
| GitHub | Free | repositório privado + Actions |

## Custo variável por vídeo (estimativa do próprio sistema, `python -m studio.cli estimate`)
Versão completa 16:9 + 3 shorts 9:16, análise com Claude Opus 5.5 (effort medium), preços de tabela sem créditos:

| Duração | Transcrição (GPU L4) | Análise (Claude) | Render (CPU) | Armazenamento | **Total** |
|---|---|---|---|---|---|
| 5 min | US$ 0,011 | US$ 0,075 | US$ 0,067 | US$ 0,007 | **≈ R$ 0,88** |
| 10 min | US$ 0,016 | US$ 0,087 | US$ 0,104 | US$ 0,011 | **≈ R$ 1,20** |
| 30 min | US$ 0,038 | US$ 0,132 | US$ 0,251 | US$ 0,029 | **≈ R$ 2,47** |

≈ 80 vídeos de 10 min por mês dentro do teto. Correções reaproveitam transcrição (custo ≈ análise + render).

## Proteções implementadas
- **Estimativa antes do job** (exibida na tela) e **bloqueio** (HTTP 402) se gasto do mês + estimativa > teto.
- **Ledger por serviço** (`usage_ledger`): GPU, CPU, Anthropic (tokens reais da resposta), R2.
- **Alertas** a partir de 80% do teto (`budget_alerts`), também verificados no cron diário.
- **Limites**: duração máx. (30 min), upload máx. (4 GB), 12 jobs/dia, 2 jobs simultâneos, 5 shorts/job, teto de US$ 0,75 de IA por job (acima disso usa heurística), armazenamento máx. 9 GB (abaixo do grátis do R2).
- **Infra**: `max_containers` (GPU=1, render=2), timeouts, escala a zero, sem VMs ligadas.
- **Reuso**: transcrição em cache por ETag; análise reaproveitada entre jobs; modelo econômico configurável (`EDITORIAL_MODEL=claude-haiku-5-5`).
- **Acesso**: só e-mails em `ALLOWED_EMAILS` podem gastar.

## Recomendações no painel de cada fornecedor (manuais, você decide)
- Anthropic Console → Limits: defina um *spend limit* mensal (ex.: US$ 10).
- Modal → Settings → Usage: defina *workspace spend limit*.
- Cloudflare: mantenha apenas o plano Free; R2 exige cartão cadastrado mas não cobra dentro do free tier.
