# Implantação e recuperação

Tudo é reconstruível a partir deste repositório + as credenciais abaixo. Nenhum caminho local é usado em produção.

## 1. Contas e credenciais (uma vez)
| # | Serviço | O que fazer | Vai para |
|---|---|---|---|
| 1 | **GitHub** | `gh auth login` | push do repositório |
| 2 | **Supabase** | Criar projeto (região São Paulo). *Authentication → Sign In / Providers*: desative "Allow new users to sign up"; *Users → Add user* com seu e-mail e senha. *Settings → API*: copie URL, `anon` e `service_role` | `.env` |
| 3 | **Cloudflare** | `npx wrangler login`. No painel, ative o R2 (exige cartão; free tier 10 GB). *R2 → Manage API tokens*: token **Object Read & Write** restrito ao bucket `ai-video-studio`. Copie Account ID, Access Key ID, Secret | `.env` |
| 4 | **Modal** | Criar conta, `services\worker\.venv\Scripts\modal.exe token new`. Defina um spend limit em Settings | login local |
| 5 | **Anthropic** | console.anthropic.com → API key; defina spend limit | `.env` |

Copie `.env.example` → `.env` e preencha. **Nunca** versione o `.env`.

## 2. Implantar
```powershell
$env:SUPABASE_PROJECT_REF = "<ref do projeto>"
powershell -ExecutionPolicy Bypass -File scripts\deploy.ps1 -Step secrets
powershell -ExecutionPolicy Bypass -File scripts\deploy.ps1 -Step worker   # anote a URL do endpoint "api"
# adicione API_URL=<essa url> ao .env
powershell -ExecutionPolicy Bypass -File scripts\deploy.ps1 -Step db
powershell -ExecutionPolicy Bypass -File scripts\deploy.ps1 -Step r2
powershell -ExecutionPolicy Bypass -File scripts\deploy.ps1 -Step web     # anote a URL *.pages.dev
```
Depois: coloque a URL do Pages em `CORS_ORIGINS` (.env) e em `infra/r2-cors.json`, rode `-Step secrets`, `-Step worker` e `-Step r2` de novo. No Supabase, *Authentication → URL Configuration*: Site URL = URL do Pages.

## 3. Verificar (checkpoint)
1. `GET <API_URL>/api/health` → `{"ok":true,"missing_config":[]}`
2. Entrar no site, enviar um vídeo curto, iniciar edição, acompanhar progresso, assistir e baixar.
3. Página **Custos** mostra lançamentos de GPU/CPU/IA.

## Desenvolvimento local
```powershell
cd services\worker; uv venv --python 3.12 .venv; uv pip install --python .venv\Scripts\python.exe -e ".[dev,transcribe]"
.venv\Scripts\python.exe -m pytest -q                       # motor + API (requer ffmpeg no PATH)
.venv\Scripts\python.exe -m studio.cli edit video.mp4 --out saida --no-llm   # pipeline completo offline
cd ..\..\apps\web; npm ci; npm run dev
```
Redes com inspeção TLS: `UV_NATIVE_TLS=1` e `NODE_OPTIONS=--use-system-ca`.

## Backup e recuperação
- **Código**: GitHub. **Banco**: Supabase faz backup diário no plano pago; no Free, exporte periodicamente `npx supabase db dump -f backup.sql` (dados) — o schema está em `supabase/migrations`.
- **Mídia**: originais e renders no R2. Para cópia externa: `npx wrangler r2 object get`/rclone.
- **Reconstrução**: clonar repo → `.env` → `scripts\deploy.ps1`.
