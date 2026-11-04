param(
    [Parameter(Mandatory=$true)]
    [string]$FilePath,
    [ValidateSet("backend", "desktop", "installer", "uninstaller", "generic")]
    [string]$Stage = "generic",
    [string]$Description = "AMCA Studio",
    [switch]$Strict
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $FilePath -PathType Leaf)) {
    throw "Target file for signing not found: $FilePath"
}
$resolvedPath = (Resolve-Path -LiteralPath $FilePath).Path

$signEnabled = ($env:AMCA_SIGN_ENABLED -eq "1") -or 
               ($null -ne $env:SIGN_PROVIDER -and $env:SIGN_PROVIDER -ne "") -or 
               ($null -ne $env:CERT_THUMBPRINT -and $env:CERT_THUMBPRINT -ne "") -or 
               $Strict

if (-not $signEnabled) {
    Write-Host "[SIGNING HOOK - $Stage] Signing bypassed (development mode). File: $resolvedPath"
    exit 0
}

Write-Host "[SIGNING HOOK - $Stage] Executing code signing for: $resolvedPath"

$python = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
if (-not $python) {
    $python = Join-Path $PSScriptRoot "..\.venv\Scripts\python.exe"
}

$providerScript = Join-Path $PSScriptRoot "signing_provider.py"
if (-not (Test-Path -LiteralPath $providerScript)) {
    throw "Signing provider script missing: $providerScript"
}

$strictFlag = if ($Strict) { "--strict" } else { "" }

$custodyJson = & $python -c "
import sys
import json
from pathlib import Path
sys.path.insert(0, r'$PSScriptRoot')
from signing_provider import sign_and_custody_hash

res = sign_and_custody_hash(r'$resolvedPath', description=r'$Description')
print(json.dumps(res))
"

if ($LASTEXITCODE -ne 0) {
    throw "[SIGNING HOOK - $Stage] Signing failed for: $resolvedPath"
}

Write-Host "[SIGNING HOOK - $Stage] Signature & Hash Custody verified:"
Write-Host $custodyJson
