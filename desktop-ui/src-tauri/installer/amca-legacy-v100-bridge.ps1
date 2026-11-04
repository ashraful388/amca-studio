# AMCA legacy v1.0.0 -> v1.0.1 upgrade bridge (H2B.2 Part A).
#
# Milestone: ZCODE-AMCA-V1.0.1-H2B.2-UPGRADE-BRIDGE-AND-FINAL-EVIDENCE-INTEGRITY-REPAIR-02
#
# The shipped, immutable AMCA v1.0.0 installer registered itself as
#   DisplayName "AMCA", DisplayVersion "1.0.0"
# under  Software\Microsoft\Windows\CurrentVersion\Uninstall\AMCA  (HKLM/HKCU),
# while the v1.0.1 identity is "AMCA Studio". The stock template therefore
# cannot see the legacy install and would install side-by-side.
#
# This helper activates ONLY for the fully corroborated shipped v1.0.0
# identity (fail-closed): display name, display version, existing install
# location, and the main binary's embedded ProductName/FileVersion must all
# match. Anything else is reported and left untouched.
#
# Removal set is closed and app-owned ONLY: the registry-recorded legacy
# install location, the legacy uninstall key (both hives), and the exact
# legacy "AMCA.lnk" shortcuts. User data (ai.amca.desktop / AppData
# app-data) is NEVER touched. Decisions are emitted as a final
# BRIDGE_DECISION:{json} line; -AuditPath appends a lifecycle record.
#
# Default mode is decision-only. Side effects require -Execute (set by the
# NSIS hook after the decision succeeded).

param(
    [string]$LegacyKey = "Software\Microsoft\Windows\CurrentVersion\Uninstall\AMCA",
    [string]$ExpectedDisplayName = "AMCA",
    [string]$ExpectedVersion = "1.0.0",
    [string]$ExpectedMainBinary = "amca-desktop.exe",
    [string]$ExpectedBinaryProductName = "AMCA",
    [string]$UserDataIdentity = "ai.amca.desktop",
    [string]$StopHelperPath,
    [string]$AuditPath,
    # Full paths of the exact legacy shortcut files to remove. Defaults are
    # the shipped v1.0.0 "AMCA.lnk" locations only; tests inject sandbox
    # paths. "AMCA Studio.lnk" is never in this list.
    [string[]]$ShortcutTargets = @(
        (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\AMCA.lnk"),
        (Join-Path $env:ProgramData "Microsoft\Windows\Start Menu\Programs\AMCA.lnk"),
        (Join-Path $env:USERPROFILE "Desktop\AMCA.lnk"),
        (Join-Path $env:PUBLIC "Desktop\AMCA.lnk")
    ),
    [int]$GraceSeconds = 3,
    [int]$ForceWaitSeconds = 3,
    [switch]$Execute
)

$ErrorActionPreference = "Stop"

# -File invocations pass arrays as one comma-joined string; split defensively.
$ShortcutTargets = @($ShortcutTargets | ForEach-Object { $_ -split "," } | Where-Object { $_ })

function Write-Audit {
    param([hashtable]$Record, [string]$Path)
    if (-not $Path) { return }
    try {
        $Record["event"] = "legacy_v100_upgrade_bridge"
        $Record["timestamp_utc"] = (Get-Date).ToUniversalTime().ToString("o")
        Add-Content -Path $Path -Value ($Record | ConvertTo-Json -Compress) -Encoding UTF8
    } catch {
        # auditing must never break the bridge decision itself
    }
}

function Get-RegistryPath {
    # Registry path values may carry wrapping quotes / trailing separators
    # (the shipped v1.0.0 NSIS wrote InstallLocation quoted). Normalize
    # before any existence check so corroboration judges the real path.
    param([string]$Raw)
    if (-not $Raw) { return $null }
    $v = $Raw.Trim().Trim('"').Trim("'")
    $v = $v -replace '[\\/]+$', ''
    if ($v) { return $v }
    return $null
}

function Get-LegacyValues {
    param([string]$Key)
    foreach ($root in @("HKLM:\", "HKCU:\")) {
        $path = $root + $Key
        if (Test-Path $path) {
            $values = Get-ItemProperty -Path $path
            return @{
                hive            = if ($root -like "HKLM*") { "HKLM" } else { "HKCU" }
                reg_path        = $path
                display_name    = $values.DisplayName
                display_version = $values.DisplayVersion
                publisher       = $values.Publisher
                install_location = Get-RegistryPath -Raw $values.InstallLocation
                uninstall_string = Get-RegistryPath -Raw $values.UninstallString
            }
        }
    }
    return $null
}

function Get-BinaryIdentity {
    param([string]$ExePath)
    if (-not (Test-Path $ExePath)) { return $null }
    $item = Get-Item $ExePath
    return @{
        present       = $true
        product_name  = $item.VersionInfo.ProductName
        file_version  = $item.VersionInfo.FileVersion
    }
}

function Test-VersionPrefix {
    param([string]$Actual, [string]$Expected)
    if (-not $Actual) { return $false }
    $parts = ($Actual.Trim() -split "\.")
    $expectedParts = $Expected -split "\."
    if ($parts.Count -lt $expectedParts.Count) { return $false }
    for ($i = 0; $i -lt $expectedParts.Count; $i++) {
        if ($parts[$i] -ne $expectedParts[$i]) { return $false }
    }
    return $true
}

function Remove-LegacyShortcuts {
    # Exact legacy names only ("AMCA.lnk"); "AMCA Studio.lnk" must survive.
    foreach ($t in $ShortcutTargets) {
        if (Test-Path $t) { Remove-Item -Path $t -Force }
    }
}

# ---------------------------------------------------------------------------
# Decision (fail-closed corroboration; mirrors src/amca/upgrade_bridge.py)
# ---------------------------------------------------------------------------
$reasons = New-Object System.Collections.Generic.List[string]
$removalTargets = New-Object System.Collections.Generic.List[string]
$activated = $false
$detected = $false
$installLocation = $null
$uninstallerPath = $null

$legacy = Get-LegacyValues -Key $LegacyKey
if ($null -eq $legacy) {
    $reasons.Add("legacy AMCA v1.0.0 install absent - no migration side effects") | Out-Null
} else {
    $detected = $true
    $installLocation = $legacy["install_location"]

    if ($legacy["display_name"] -ne $ExpectedDisplayName) {
        $reasons.Add("DisplayName mismatch: '$($legacy['display_name'])' != '$ExpectedDisplayName'") | Out-Null
    }
    if (-not (Test-VersionPrefix -Actual $legacy["display_version"] -Expected $ExpectedVersion)) {
        $reasons.Add("DisplayVersion mismatch: '$($legacy['display_version'])' != '$ExpectedVersion'") | Out-Null
    }
    if (-not $installLocation -or -not (Test-Path $installLocation)) {
        $reasons.Add("InstallLocation missing or does not exist: '$installLocation'") | Out-Null
    }

    $binary = $null
    if ($installLocation -and (Test-Path $installLocation)) {
        $exePath = Join-Path $installLocation $ExpectedMainBinary
        $binary = Get-BinaryIdentity -ExePath $exePath
        if ($null -eq $binary) {
            $reasons.Add("expected main binary missing: $exePath") | Out-Null
        } else {
            if ($binary["product_name"] -ne $ExpectedBinaryProductName) {
                $reasons.Add("binary ProductName mismatch: '$($binary['product_name'])' != '$ExpectedBinaryProductName'") | Out-Null
            }
            if (-not (Test-VersionPrefix -Actual $binary["file_version"] -Expected $ExpectedVersion)) {
                $reasons.Add("binary FileVersion mismatch: '$($binary['file_version'])' != '$ExpectedVersion'") | Out-Null
            }
        }
        if ($legacy["uninstall_string"]) {
            $uninstallerPath = $legacy["uninstall_string"].Trim('"')
        }
    }

    if ($reasons.Count -eq 0) {
        $activated = $true
        $reasons.Add("genuine shipped AMCA v1.0.0 installation corroborated - activate upgrade bridge") | Out-Null
        $removalTargets.Add("install-location:$installLocation") | Out-Null
        $removalTargets.Add("registry-key:$LegacyKey") | Out-Null
        $removalTargets.Add("shortcut:StartMenu\Programs\AMCA.lnk") | Out-Null
        $removalTargets.Add("shortcut:Desktop\AMCA.lnk") | Out-Null
    } else {
        $reasons.Insert(0, "registry entry does not corroborate the known shipped AMCA v1.0.0 identity - bridge not activated, nothing touched") | Out-Null
    }
}

$decision = [ordered]@{
    schema                  = "amca-legacy-upgrade-bridge-decision/1"
    legacy_key              = $LegacyKey
    hive                    = if ($legacy) { $legacy["hive"] } else { $null }
    legacy_detected         = $detected
    activated               = $activated
    reasons                 = $reasons
    removal_targets         = $removalTargets
    legacy_install_location = $installLocation
    user_data_identity      = $UserDataIdentity
    user_data_touched       = $false
    executed                = ([bool]$Execute -and $activated)
}

if ($Execute -and $activated) {
    # 1. Stop legacy processes: path-verified under the LEGACY install
    #    location only (same contract as the P1 shutdown helper), graceful
    #    first, then a bounded force pass.
    if ($StopHelperPath -and (Test-Path $StopHelperPath)) {
        & $StopHelperPath -InstallDir $installLocation `
            -GraceSeconds $GraceSeconds -ForceWaitSeconds $ForceWaitSeconds | Out-Null
        $decision["legacy_processes_stopped"] = $true
    }

    # 2. Run the legacy uninstaller silently. _?= pins it to its own
    #    directory; residual cleanup happens below either way.
    if ($uninstallerPath -and (Test-Path $uninstallerPath)) {
        try {
            $proc = Start-Process -FilePath $uninstallerPath `
                -ArgumentList "/S", "_?=$installLocation" `
                -Wait -PassThru -ErrorAction Stop
            $decision["legacy_uninstaller_exit"] = $proc.ExitCode
        } catch {
            $decision["legacy_uninstaller_error"] = $_.Exception.Message
        }
    }

    # 3. Remove residuals - confined to the app-owned removal set. The
    #    install directory is removed only if it is exactly the
    #    registry-recorded legacy location and never a user-data path.
    if ($installLocation -and
        ($installLocation -notlike "*$UserDataIdentity*") -and
        (Test-Path $installLocation)) {
        Remove-Item -Path $installLocation -Recurse -Force
    }
    foreach ($root in @("HKLM:\", "HKCU:\")) {
        $keyPath = $root + $LegacyKey
        if (Test-Path $keyPath) { Remove-Item -Path $keyPath -Recurse -Force }
    }
    Remove-LegacyShortcuts
}

Write-Output ("BRIDGE_DECISION:" + ($decision | ConvertTo-Json -Compress -Depth 4))
Write-Audit -Record @{ decision = $decision } -Path $AuditPath
exit 0
