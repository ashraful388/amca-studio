# AMCA P1 installer process shutdown helper.
#
# Stops ONLY processes that provably belong to the installed AMCA
# application: an executable whose image path is under -InstallDir and whose
# file name matches the AMCA binaries (amca*.exe). Identically named
# processes anywhere else - for example a portable copy running from another
# directory - are never touched. Nothing on disk is modified or deleted.
#
# Exit codes consumed by the NSIS installer hook:
#   0 = no qualifying processes remain
#   3 = qualifying processes survived the bounded shutdown; the installer
#       must still verify file access and fail closed
#   4 = process enumeration failed; the installer falls back to file
#       verification before touching anything

param(
    [Parameter(Mandatory = $true)]
    [string]$InstallDir,
    # Production defaults; overridden only by the automated targeted tests.
    [int]$GraceSeconds = 12,
    [int]$ForceWaitSeconds = 6,
    [string]$AuditPath = (Join-Path $env:APPDATA 'ai.amca.desktop\background-lifecycle.jsonl')
)

$ErrorActionPreference = 'SilentlyContinue'

$root = [System.IO.Path]::GetFullPath($InstallDir.TrimEnd('\') + '\')
$maxForcePasses = 3

function Test-UnderInstallDir([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) { return $false }
    $full = $null
    try {
        $full = [System.IO.Path]::GetFullPath($Path)
    } catch {
        return $false
    }
    return $full.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)
}

function Get-AmcaTargetProcesses {
    Get-CimInstance Win32_Process -Filter "Name LIKE 'amca%'" |
        Where-Object { Test-UnderInstallDir $_.ExecutablePath }
}

function Wait-TargetsGone([int]$Seconds) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if (-not (Get-AmcaTargetProcesses)) { return $true }
        Start-Sleep -Milliseconds 250
    }
    return -not (Get-AmcaTargetProcesses)
}

function Write-AmcaAudit([bool]$Success, [string]$Detail) {
    try {
        $entry = @{
            event       = 'installer_process_shutdown'
            at_epoch_ms = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
            success     = $Success
            detail      = $Detail
        } | ConvertTo-Json -Compress
        Add-Content -Path $AuditPath -Value $entry -Encoding UTF8
    } catch { }
}

# Enumeration failure must be visible to the installer (exit 4) rather than
# silently reported as "nothing to stop".
$enumErrors = @()
$targets = @(
    Get-CimInstance Win32_Process -Filter "Name LIKE 'amca%'" -ErrorVariable enumErrors |
        Where-Object { Test-UnderInstallDir $_.ExecutablePath }
)
if ($enumErrors.Count -gt 0 -and $targets.Count -eq 0) {
    Write-AmcaAudit $false 'process enumeration failed'
    Write-Output 'AMCA shutdown: process enumeration failed; file access will be verified instead.'
    exit 4
}

if ($targets.Count -eq 0) {
    Write-AmcaAudit $true 'no installed AMCA processes were running'
    Write-Output 'AMCA shutdown: no installed AMCA processes were running.'
    exit 0
}

$names = (@($targets | ForEach-Object { $_.Name } | Sort-Object -Unique) -join ', ')

# Graceful phase: if an installed desktop is running, hand the request to it
# via the single-instance --quit contract so it stops its own backend through
# the normal graceful path (HTTP shutdown, bounded wait, tree cleanup).
$desktop = $targets |
    Where-Object { $_.Name -ieq 'amca-desktop.exe' -or $_.Name -ieq 'amca.exe' } |
    Select-Object -First 1
if ($desktop) {
    $mainExe = Join-Path $InstallDir $desktop.Name
    if (Test-Path -LiteralPath $mainExe) {
        Start-Process -FilePath $mainExe -ArgumentList '--quit' -WindowStyle Hidden
    }
    if (Wait-TargetsGone $GraceSeconds) {
        Write-AmcaAudit $true "graceful shutdown of $names"
        Write-Output "AMCA shutdown: stopped $names gracefully."
        exit 0
    }
}

# Bounded force phase: terminate only the verified process trees, by PID.
# The PID list is re-derived from the executable path on every pass, so a
# same-named process outside the install directory is never signalled.
for ($pass = 1; $pass -le $maxForcePasses; $pass++) {
    $remaining = @(Get-AmcaTargetProcesses)
    if ($remaining.Count -eq 0) { break }
    foreach ($proc in $remaining) {
        & taskkill.exe /PID $proc.ProcessId /T /F | Out-Null
    }
    Wait-TargetsGone $ForceWaitSeconds | Out-Null
}

if (-not (Get-AmcaTargetProcesses)) {
    Write-AmcaAudit $true "forced shutdown of $names after the graceful timeout"
    Write-Output "AMCA shutdown: stopped $names (forced after the graceful timeout)."
    exit 0
}

$survivors = (@(Get-AmcaTargetProcesses) |
    ForEach-Object { "$($_.Name) (pid $($_.ProcessId))" }) -join ', '
Write-AmcaAudit $false "could not stop $survivors"
Write-Output "AMCA shutdown FAILED: could not stop $survivors."
exit 3
