# Versionamento, sincronização e recuperação

**Fonte oficial e permanente do código:** https://github.com/rafaelcsr/allvideostudio (branch `main`).
O computador local é descartável: tudo que importa está no GitHub (código), no R2 (mídia) e no Supabase (dados).

## O que NUNCA vai para o Git
| Categoria | Onde fica |
|---|---|
| `.env`, chaves, tokens, `.modal.toml` | `.env` local + Secret do Modal + gerenciador de senhas |
| Vídeos originais e renderizados | Cloudflare R2 |
| Modelos (whisper, YuNet) | baixados na nuvem / cache local |
| `node_modules`, `.venv`, builds, temporários | recriados por `npm ci` / `uv pip install` |

Três camadas de proteção: `.gitignore` → trava `scripts/hooks/pre-commit` (bloqueia `.env`, chaves, padrões de credenciais, mídia e arquivos > 5 MB) → CI no GitHub.

## Rotina diária
```powershell
# começar a trabalhar (outro computador ou depois de um tempo)
powershell -ExecutionPolicy Bypass -File scripts\sync.ps1 -PullOnly

# ao concluir uma etapa: testa, commita, rebase sobre o remoto e envia
powershell -ExecutionPolicy Bypass -File scripts\sync.ps1 -Message "feat: legendas com emoji"
```
Convenção de mensagens: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`.
O script nunca usa `--force`; se houver conflito, ele para sem enviar.
Marcos (checkpoints P0–P4) recebem tag: `git tag -a v0.2.0 -m "P2: render cloud" ; git push origin v0.2.0`.

## Reconstruir em outro computador
1. Instalar ferramentas (Windows):
   ```powershell
   winget install -e --id Git.Git; winget install -e --id OpenJS.NodeJS.LTS
   winget install -e --id Gyan.FFmpeg; winget install -e --id astral-sh.uv; winget install -e --id GitHub.cli
   ```
2. Login e clone:
   ```powershell
   gh auth login; gh auth setup-git
   git clone https://github.com/rafaelcsr/allvideostudio.git AI-VIDEO-STUDIO
   cd AI-VIDEO-STUDIO; git config core.hooksPath scripts/hooks
   ```
3. Dependências e testes:
   ```powershell
   cd services\worker; uv venv --python 3.12 .venv; uv pip install --python .venv\Scripts\python.exe -e ".[dev]"
   .venv\Scripts\python.exe -m pytest -q
   cd ..\..\apps\web; npm ci; npm run build
   ```
   Rede com inspeção TLS: antes, `$env:UV_NATIVE_TLS="1"; $env:NODE_OPTIONS="--use-system-ca"`.
4. Credenciais: recrie o `.env` a partir de `.env.example` (valores no seu gerenciador de senhas ou nos painéis Supabase/Cloudflare/Anthropic) e faça `modal token new` / `npx wrangler login`.
5. Implantação (se necessário): `scripts\deploy.ps1` — ver [SETUP.md](SETUP.md).

Para apenas **usar** o estúdio de outro computador nada disso é necessário: basta abrir o site e entrar.
