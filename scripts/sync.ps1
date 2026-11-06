<#
  Rotina de versionamento e sincronização com o repositório oficial
  https://github.com/rafaelcsr/allvideostudio  (fonte oficial do código)

  Uso:
    powershell -ExecutionPolicy Bypass -File scripts\sync.ps1 -Message "feat: descrição"
    powershell -ExecutionPolicy Bypass -File scripts\sync.ps1 -Message "fix: ..." -SkipTests
    powershell -ExecutionPolicy Bypass -File scripts\sync.ps1 -PullOnly

  Etapas: ativa a trava anti-segredos -> busca o remoto -> testes -> commit -> rebase -> push.
  Nunca usa --force: se o remoto divergir com conflito, para e pede intervenção.
#>
param(
  [string]$Message = "",
  [switch]$SkipTests,
  [switch]$PullOnly
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:UV_NATIVE_TLS = "1"
$env:NODE_OPTIONS = "--use-system-ca"

function Step($t) { Write-Host "==> $t" -ForegroundColor Cyan }
function Invoke-Git { & git.exe @args; if ($LASTEXITCODE -ne 0) { throw "git $($args -join ' ') falhou ($LASTEXITCODE)" } }

Step "Trava anti-segredos (core.hooksPath)"
Invoke-Git config core.hooksPath scripts/hooks

$branch = (git rev-parse --abbrev-ref HEAD).Trim()
if ($branch -ne "main") { Write-Host "Atenção: branch atual é '$branch' (oficial: main)" -ForegroundColor Yellow }

Step "Buscando o remoto"
Invoke-Git fetch origin --prune
$remoteHasMain = (git ls-remote --heads origin main) -ne $null

if ($PullOnly) {
  if ($remoteHasMain) { Invoke-Git pull --rebase --autostash origin main }
  Write-Host "Atualizado com o remoto." -ForegroundColor Green
  exit 0
}

if (-not $SkipTests) {
  Step "Testes do worker"
  $py = "$root\services\worker\.venv\Scripts\python.exe"
  if (Test-Path $py) {
    Push-Location "$root\services\worker"; & $py -m pytest -q; $ok = $LASTEXITCODE; Pop-Location
    if ($ok -ne 0) { throw "Testes do worker falharam — nada foi enviado." }
  } else { Write-Host "venv do worker ausente; pulei (veja docs/SETUP.md)" -ForegroundColor Yellow }
  Step "Build do frontend"
  if (Test-Path "$root\apps\web\node_modules") {
    Push-Location "$root\apps\web"; npm run build --silent; $ok = $LASTEXITCODE; Pop-Location
    if ($ok -ne 0) { throw "Build do frontend falhou — nada foi enviado." }
  }
}

$dirty = git status --porcelain
if ($dirty) {
  if (-not $Message) { throw "Há alterações; informe -Message `"tipo: descrição`"" }
  Step "Commit"
  Invoke-Git add -A
  git status --short
  Invoke-Git commit -m $Message
}

if ($remoteHasMain) {
  Step "Rebase sobre origin/main (preserva o histórico remoto)"
  & git pull --rebase origin main
  if ($LASTEXITCODE -ne 0) { throw "Conflito no rebase. Resolva, 'git rebase --continue' e rode de novo." }
}

Step "Push"
Invoke-Git push -u origin main
$local = (git rev-parse HEAD).Trim(); $remote = (git rev-parse origin/main).Trim()
if ($local -eq $remote) { Write-Host "Sincronizado: main = origin/main = $($local.Substring(0,7))" -ForegroundColor Green }
else { throw "Divergência após push: local $local vs remoto $remote" }
