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

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    $python = Get-Command py -ErrorAction SilentlyContinue
}
if (-not $python) {
    throw "Python non trovato nel PATH. Installa Python oppure avvia start_dashboard.py manualmente."
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

& $python.Source @args
exit $LASTEXITCODE
