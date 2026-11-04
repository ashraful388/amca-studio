# Source-only native development session. Never installs or stops another app.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
$ui = Join-Path $root 'desktop-ui'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'Source .venv\Scripts\python.exe is required; no system Python fallback.'
}
if (-not (Test-Path -LiteralPath (Join-Path $ui 'node_modules\.bin\tauri.cmd'))) {
    throw 'Source desktop-ui dependencies must already be installed.'
}
# Inspect local listener metadata only; never probe HTTP or kill an occupant.
$occupied = @(Get-NetTCPConnection -State Listen -ErrorAction Stop |
    Where-Object { $_.LocalPort -in @(8877, 1420) })
if ($occupied.Count -gt 0) {
    throw 'Source port 8877 or frontend port 1420 is occupied. No process was stopped.'
}
$names = @('AMCA_PYTHON', 'PYTHONPATH', 'AMCA_DESKTOP_AUTOMATIC_CLOUD',
    'VITE_AMCA_API_URL', 'AMCA_SESSION_TOKEN', 'CARGO_NET_OFFLINE')
$saved = @{}
foreach ($name in $names) {
    $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
Push-Location $ui
try {
    $env:AMCA_PYTHON = $python
    $env:PYTHONPATH = Join-Path $root 'src'
    # Gate-2 fix order decision 1: the shell no longer grants automatic cloud
    # approval. Governed Transmit-Excerpt requests stay PENDING for a human
    # decision; do not re-add AMCA_DESKTOP_AUTOMATIC_CLOUD here.
    $env:VITE_AMCA_API_URL = 'http://127.0.0.1:8877'
    # Let the native shell generate a fresh CSPRNG token, not reuse a prior session.
    [Environment]::SetEnvironmentVariable('AMCA_SESSION_TOKEN', $null, 'Process')
    $env:CARGO_NET_OFFLINE = 'true'
    & npm.cmd run dev:tauri
    if ($LASTEXITCODE -ne 0) { throw "Source native dev exited with code $LASTEXITCODE." }
}
finally {
    Pop-Location
    foreach ($name in $names) {
        [Environment]::SetEnvironmentVariable($name, $saved[$name], 'Process')
    }
}
