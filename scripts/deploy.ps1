<#
  Implantação completa a partir do repositório (qualquer computador Windows com Node, Python/uv e Git).
  Pré-requisito: .env preenchido na raiz e logins feitos (docs/SETUP.md):
    modal token new            (Modal)
    npx wrangler login         (Cloudflare)
    (Supabase: só o .env — migrations via SUPABASE_DB_URL, ver docs/SUPABASE.md)
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
  Write-Host "==> Secret do Modal (a partir do .env, sem a URL do banco)" -ForegroundColor Cyan
  # Menor privilégio: o worker não precisa da conexão Postgres nem de valores de frontend.
  $exclude = @("SUPABASE_DB_URL", "API_URL")
  $tmp = Join-Path $env:TEMP ("avs-secret-" + [guid]::NewGuid().ToString("N") + ".env")
  try {
    $lines = foreach ($k in $cfg.Keys) { if ($k -notin $exclude -and $k -notlike "NEXT_PUBLIC_*" -and $cfg[$k]) { "$k=$($cfg[$k])" } }
    [IO.File]::WriteAllLines($tmp, $lines)
    & $modal secret create ai-video-studio --from-dotenv $tmp --force
  } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
}

if ($Step -in "all", "worker") {
  Write-Host "==> Deploy do worker/API no Modal" -ForegroundColor Cyan
  Push-Location "$root\services\worker"
  & $modal deploy modal_app.py
  Pop-Location
}

if ($Step -in "all", "db") {
  Write-Host "==> Migrações Supabase (validação -> pendentes -> aplicar -> verificar)" -ForegroundColor Cyan
  Push-Location "$root\services\worker"
  & $py -m studio.supa check --live; if ($LASTEXITCODE -ne 0) { Pop-Location; throw "Configuração do Supabase inválida" }
  & $py -m studio.supa migrate; if ($LASTEXITCODE -ne 0) { Pop-Location; throw "Migration falhou (revertida)" }
  & $py -m studio.supa verify; if ($LASTEXITCODE -ne 0) { Pop-Location; throw "Verificação de segurança do banco falhou" }
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
  $env:NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY = $(if ($cfg["SUPABASE_PUBLISHABLE_KEY"]) { $cfg["SUPABASE_PUBLISHABLE_KEY"] } else { $cfg["SUPABASE_ANON_KEY"] })
  $env:NEXT_PUBLIC_API_URL = $cfg["API_URL"]
  npm ci
  npm run build
  npx wrangler pages deploy out --project-name ai-video-studio --branch main
  Pop-Location
}
Write-Host "Concluído." -ForegroundColor Green
