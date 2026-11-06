# Supabase — configuração, segurança e reprodução

Plano: **Free** (R$ 0). Nada aqui exige upgrade. Observação do plano Free: o projeto **pausa após 7 dias sem uso** — reative em *Dashboard → Restore project* (dados preservados).

## Modelo de segurança
| Credencial | Onde é usada | Pode vazar para o navegador? |
|---|---|---|
| `SUPABASE_URL` | navegador, API, worker | sim (pública) |
| `SUPABASE_PUBLISHABLE_KEY` (`sb_publishable_…` ou `anon` legada) | navegador + verificação de login na API | sim (pública; acesso controlado por RLS) |
| `SUPABASE_SECRET_KEY` (`sb_secret_…` ou `service_role` legada) | API e worker no Modal | **nunca** — ignora RLS |
| `SUPABASE_DB_URL` (senha do Postgres) | só no seu computador, para migrations | **nunca** — nem vai ao Modal |

Camadas: RLS em todas as tabelas (usuário lê só o que é dele) → `anon` sem privilégio algum e `authenticated` só com `SELECT` (escritas apenas via API com a chave secreta) → `next.config.mjs` aborta o build se uma chave secreta for colocada em `NEXT_PUBLIC_*` → trava de commit bloqueia `sb_secret_`, JWT, `SUPABASE_DB_URL=` e URLs Postgres com senha → `.env` no `.gitignore`.

## 1. Criar o projeto (painel — uma vez)
1. https://supabase.com/dashboard → **New project**: organização Free, nome `ai-video-studio`, região **South America (São Paulo)**, senha do banco forte (*Generate a password*) — guarde no gerenciador de senhas.
2. **Authentication → Sign In / Providers**: mantenha *Email* ativo e **desative "Allow new users to sign up"** (estúdio privado; impede que terceiros consumam seu orçamento).
3. **Authentication → Users → Add user → Create new user**: seu e-mail + senha, marque *Auto Confirm User*.
4. **Authentication → URL Configuration**: *Site URL* = `http://localhost:3000` por enquanto (depois, a URL do Cloudflare Pages); em *Redirect URLs* adicione `http://localhost:3000/**`.

## 2. Preencher o `.env` local (sem colar nada no chat)
Abra o `.env` da raiz (`notepad .env`) e preencha:
| Variável | Onde copiar |
|---|---|
| `SUPABASE_URL` | *Project Settings → Data API → Project URL* |
| `SUPABASE_PUBLISHABLE_KEY` | *Project Settings → API Keys → Publishable key* |
| `SUPABASE_SECRET_KEY` | *Project Settings → API Keys → Secret keys* (crie uma chamada `ai-video-studio`) |
| `SUPABASE_DB_URL` | botão **Connect** (topo) → *Connection string* → **Session pooler** (IPv4, porta 5432); troque `[YOUR-PASSWORD]` pela senha do banco |

Por que *Session pooler*: a conexão direta do plano Free é só IPv6, que muitas redes corporativas não têm.

## 3. Validar, migrar e verificar
```powershell
cd services\worker
.venv\Scripts\python.exe -m studio.supa check --live     # formato/tipo das chaves + Auth/REST/Postgres reais (sem exibir valores)
.venv\Scripts\python.exe -m studio.supa migrate --dry-run
.venv\Scripts\python.exe -m studio.supa migrate          # cada arquivo em transação; falha = rollback
.venv\Scripts\python.exe -m studio.supa verify           # RLS, policies, grants, realtime
.venv\Scripts\python.exe -m pytest -m live -v            # integração real: persistência, RLS, bloqueio de escrita, tokens
.venv\Scripts\python.exe -m studio.supa web-env          # apps/web/.env.local só com valores públicos
```
O histórico fica em `supabase_migrations.schema_migrations` (mesmo formato do Supabase CLI — `npx supabase db push` reconhece o que já foi aplicado). Os testes ao vivo criam 2 usuários temporários e os removem ao final.

## Nova migration
Crie `supabase/migrations/AAAAMMDDHHMMSS_descricao.sql` (nunca edite uma migration já aplicada) e rode `migrate` + `verify` + `pytest -m live`.

## Outro computador
Clone (docs/VERSIONING.md) → recrie o `.env` com os mesmos valores (gerenciador de senhas / painel) → `studio.supa check --live`. Migrations já aplicadas são reconhecidas e não rodam de novo.

## Problemas comuns
- `check` acusa chaves trocadas → a publishable foi colada no lugar da secret (ou vice-versa).
- Postgres `timeout`/`could not translate host` → use a URL do *Session pooler*, não a conexão direta.
- `password authentication failed` → senha do banco errada (*Project Settings → Database → Reset database password*).
- REST 404 em `projects` → migrations ainda não aplicadas.
- Login funciona mas a página não carrega dados → confira se o usuário logado é o mesmo do `ALLOWED_EMAILS`.
