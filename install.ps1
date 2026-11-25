param(
    [string]$Python = '',
    [string]$Codex = '',
    [switch]$PrepareOnly
)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$pluginRoot = Join-Path $projectRoot 'plugin'
$node = (Get-Command node -ErrorAction Stop).Source
$npm = (Get-Command npm.cmd -ErrorAction Stop).Source
Push-Location $pluginRoot
try {
    & $npm ci --ignore-scripts
    if ($LASTEXITCODE -ne 0) { throw 'npm ci failed' }
    $prepareArgs = @('prepare-plugin.mjs')
    if ($Python) { $prepareArgs += @('--python', $Python) }
    $preparedText = & $node @prepareArgs
    if ($LASTEXITCODE -ne 0) { throw 'Plugin preparation failed' }
    $prepared = ($preparedText -join "`n") | ConvertFrom-Json
    if ($PrepareOnly) { $prepared; return }
    $codexRoot = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE '.codex' }
    if (-not $Codex) {
        $bundledCli = Join-Path $codexRoot 'plugins\.plugin-appserver\codex.exe'
        $Codex = if (Test-Path -LiteralPath $bundledCli) { $bundledCli } else { (Get-Command codex -ErrorAction Stop).Source }
    }
    $config = Join-Path $codexRoot 'config.toml'
    if (Test-Path -LiteralPath $config) {
        $backupDir = Join-Path $env:LOCALAPPDATA ('SourceIntake\plugin-install-backups\' + [Guid]::NewGuid().ToString())
        New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
        Copy-Item -LiteralPath $config -Destination (Join-Path $backupDir 'config.toml')
    }
    & $Codex plugin marketplace add $prepared.root --json
    if ($LASTEXITCODE -ne 0) { throw 'Marketplace registration failed' }
    & $Codex plugin add $prepared.selector --json
    if ($LASTEXITCODE -ne 0) { throw 'Plugin installation failed' }
    Write-Output 'Installed. Restart Codex and open plugin://source-intake@source-intake-local'
} finally { Pop-Location }
