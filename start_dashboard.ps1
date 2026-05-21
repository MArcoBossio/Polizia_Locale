param(
    [string]$BackendHost = "127.0.0.1",
    [int]$BackendPort = 8000,
    [string]$FrontendHost = "127.0.0.1",
    [int]$FrontendPort = 5173,
    [int]$ReadyTimeout = 60,
    [switch]$NoOpenBrowser
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$launcher = Join-Path $root "start_dashboard.py"

# Prefer a local .venv Python if present, otherwise fall back to system python/py
$venvPy = Join-Path $root ".venv\Scripts\python.exe"
if (Test-Path $venvPy) {
    $pythonExe = $venvPy
} else {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $cmd) { $cmd = Get-Command py -ErrorAction SilentlyContinue }
    if (-not $cmd) {
        throw "Python non trovato nel PATH. Installa Python oppure avvia start_dashboard.py manualmente."
    }
    $pythonExe = $cmd.Source
}

$args = @(
    $launcher,
    "--backend-host", $BackendHost,
    "--backend-port", $BackendPort,
    "--frontend-host", $FrontendHost,
    "--frontend-port", $FrontendPort,
    "--ready-timeout", $ReadyTimeout
)

if ($NoOpenBrowser) {
    $args += "--no-open-browser"
}

& $pythonExe @args
exit $LASTEXITCODE
