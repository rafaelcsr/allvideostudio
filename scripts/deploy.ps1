<#
  Implantação completa a partir do repositório (qualquer computador Windows com Node, Python/uv e Git).
  Pré-requisito: .env preenchido na raiz e logins feitos (docs/SETUP.md):
    modal token new            (Modal)
    npx wrangler login         (Cloudflare)
    npx supabase login         (Supabase CLI) + SUPABASE_PROJECT_REF no ambiente
  Uso:  powershell -ExecutionPolicy Bypass -File scripts\deploy.ps1 [-Step all|secrets|worker|db|r2|web]
#>
param([ValidateSet("all", "secrets", "worker", "db", "r2", "web")][string]$Step = "all")
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$env:NODE_OPTIONS = "--use-system-ca"   # redes com inspeção TLS
$env:UV_NATIVE_TLS = "1"

function Read-DotEnv($path) {
  $h = @{}
  foreach ($line in Get-Content $path) {
    if ($line -match '^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$') { $h[$Matches[1]] = $Matches[2] }
  }
  return $h
}
if (-not (Test-Path "$root\.env")) { throw ".env não encontrado na raiz. Copie .env.example e preencha." }
$cfg = Read-DotEnv "$root\.env"
$py = "$root\services\worker\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
  Push-Location "$root\services\worker"; uv venv --python 3.12 .venv; uv pip install --python .venv\Scripts\python.exe -e ".[dev]"; Pop-Location
}
$modal = "$root\services\worker\.venv\Scripts\modal.exe"

if ($Step -in "all", "secrets") {
  Write-Host "==> Secret do Modal (a partir do .env)" -ForegroundColor Cyan
  & $modal secret create ai-video-studio --from-dotenv "$root\.env" --force
}

if ($Step -in "all", "worker") {
  Write-Host "==> Deploy do worker/API no Modal" -ForegroundColor Cyan
  Push-Location "$root\services\worker"
  & $modal deploy modal_app.py
  Pop-Location
}

if ($Step -in "all", "db") {
  Write-Host "==> Migrações Supabase" -ForegroundColor Cyan
  if (-not $env:SUPABASE_PROJECT_REF) { throw "Defina `$env:SUPABASE_PROJECT_REF (Project Settings > General > Reference ID)" }
  Push-Location $root
  npx supabase link --project-ref $env:SUPABASE_PROJECT_REF
  npx supabase db push
  Pop-Location
}

if ($Step -in "all", "r2") {
  Write-Host "==> Bucket R2 + CORS" -ForegroundColor Cyan
  $bucket = $cfg["R2_BUCKET"]
  npx wrangler r2 bucket create $bucket 2>$null
  npx wrangler r2 bucket cors set $bucket --file "$root\infra\r2-cors.json" --force
}

if ($Step -in "all", "web") {
  Write-Host "==> Build e deploy do frontend (Cloudflare Pages)" -ForegroundColor Cyan
  if (-not $cfg["API_URL"]) { throw "Adicione API_URL=<url do endpoint 'api' mostrada pelo modal deploy> ao .env" }
  Push-Location "$root\apps\web"
  $env:NEXT_PUBLIC_SUPABASE_URL = $cfg["SUPABASE_URL"]
  $env:NEXT_PUBLIC_SUPABASE_ANON_KEY = $cfg["SUPABASE_ANON_KEY"]
  $env:NEXT_PUBLIC_API_URL = $cfg["API_URL"]
  npm ci
  npm run build
  npx wrangler pages deploy out --project-name ai-video-studio --branch main
  Pop-Location
}
Write-Host "Concluído." -ForegroundColor Green
