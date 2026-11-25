$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$GuiScript = Join-Path $ScriptDir "source_intake_gui.py"

$pyLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($pyLauncher) {
    & $pyLauncher.Source -3 $GuiScript
    exit $LASTEXITCODE
}

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Error "Python 3.10+ was not found. Please ensure 'py -3' or 'python' is available."
    exit 1
}
& $python.Source $GuiScript
exit $LASTEXITCODE
