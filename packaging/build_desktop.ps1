param(
    [ValidateSet("nsis", "msi", "both")]
    [string]$Bundle = "both",
    [switch]$SkipBackendSmoke,
    [switch]$Offline,
    [switch]$Sign
)

$ErrorActionPreference = "Stop"
$softwareRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$desktopRoot = Join-Path $softwareRoot "desktop-ui"
$cargoHome = Join-Path $softwareRoot ".cargo-home"
New-Item -ItemType Directory -Path $cargoHome -Force | Out-Null

$pathValue = $env:Path
Remove-Item Env:Path -ErrorAction SilentlyContinue
$env:Path = $pathValue
$env:CARGO_HOME = $cargoHome
if ($Offline) {
    $env:CARGO_NET_OFFLINE = "true"
}

$cargo = Get-Command cargo.exe -ErrorAction SilentlyContinue
if ($null -eq $cargo) {
    $fallbackCargo = Join-Path $env:USERPROFILE ".cargo\bin\cargo.exe"
    if (-not (Test-Path -LiteralPath $fallbackCargo -PathType Leaf)) {
        throw "cargo.exe was not found. Install the Rust MSVC toolchain first."
    }
    $cargoPath = $fallbackCargo
    $env:Path = "$(Split-Path -Parent $fallbackCargo);$env:Path"
}
else {
    $cargoPath = $cargo.Source
}
if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
    throw "npm.cmd was not found. Install Node.js first."
}

$buildSha = $env:AMCA_BUILD_SHA
if (-not $buildSha) {
    $buildSha = (& git -C $softwareRoot rev-parse HEAD 2>$null | Select-Object -First 1)
    if ($LASTEXITCODE -ne 0 -or -not $buildSha) {
        $buildSha = ""
    }
    $buildSha = $buildSha.Trim()
}
# 28B: the desktop binary embeds the same revision the backend is packaged
# with, so the installed shell can verify it launched a matching backend.
$env:AMCA_BUILD_SHA = $buildSha

& (Join-Path $PSScriptRoot "build_backend.ps1") -BuildSha $buildSha
if ($LASTEXITCODE -ne 0) {
    throw "Backend packaging failed with exit code $LASTEXITCODE."
}
if (-not $SkipBackendSmoke) {
    & (Join-Path $PSScriptRoot "verify_backend.ps1")
    if ($LASTEXITCODE -ne 0) {
        throw "Backend smoke verification failed with exit code $LASTEXITCODE."
    }
}

& $cargoPath check `
    --manifest-path (Join-Path $desktopRoot "src-tauri\Cargo.toml") `
    --locked
if ($LASTEXITCODE -ne 0) {
    throw "Rust validation failed with exit code $LASTEXITCODE."
}

$bundleArgument = if ($Bundle -eq "both") { "nsis,msi" } else { $Bundle }
$tauriArguments = @(
    "run", "tauri", "--", "build",
    "--bundles", $bundleArgument,
    "--ci"
)
if (-not $Sign) {
    $tauriArguments += "--no-sign"
}

Push-Location $desktopRoot
try {
    & npm.cmd @tauriArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Tauri installer build failed with exit code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}

if ($Bundle -eq "nsis" -or $Bundle -eq "both") {
    # Safety assertion for the P1 installer shutdown fix: the vendored NSIS
    # template (src-tauri/nsis/installer.nsi) removes the Tauri name-only
    # process check, which would force-kill ANY current-user process named
    # amca-desktop.exe — including an unrelated portable copy — instead of the
    # path-verified shutdown in hooks.nsh. If a Tauri CLI upgrade changes the
    # template pipeline so this property no longer holds, fail the production
    # build loudly instead of shipping a regressed installer.
    $nsisWorkDirs = Get-ChildItem `
        -LiteralPath (Join-Path $desktopRoot "src-tauri\target\release\nsis") `
        -Directory -ErrorAction SilentlyContinue
    foreach ($workDir in $nsisWorkDirs) {
        $generatedNsi = Join-Path $workDir.FullName "installer.nsi"
        if (-not (Test-Path -LiteralPath $generatedNsi)) {
            throw "NSIS work directory without generated installer.nsi: $generatedNsi"
        }
        $generated = Get-Content -LiteralPath $generatedNsi -Raw
        if ($generated -match '!insertmacro\s+CheckIfAppIsRunning') {
            throw "SAFETY REGRESSION: the generated NSIS installer at $generatedNsi contains the name-only CheckIfAppIsRunning check. The vendored template (src-tauri/nsis/installer.nsi) is out of sync with the Tauri CLI; re-vendor it before packaging."
        }
        if ($generated -notmatch 'path-verified shutdown step') {
            throw "SAFETY REGRESSION: the generated NSIS installer at $generatedNsi lost the path-verified shutdown replacement. The vendored template (src-tauri/nsis/installer.nsi) is not being applied; re-check bundle > windows > nsis > template."
        }
    }
}

# CR-02 Code Signing Hook: Desktop Executable (after compile, before/with bundle)
$desktopExe = Join-Path $desktopRoot "src-tauri\target\release\amca-desktop.exe"
$signScript = Join-Path $PSScriptRoot "sign_artifact.ps1"
if ((Test-Path -LiteralPath $desktopExe -PathType Leaf) -and (Test-Path -LiteralPath $signScript -PathType Leaf)) {
    & $signScript -FilePath $desktopExe -Stage "desktop" -Description "AMCA Studio Desktop UI"
    if ($LASTEXITCODE -ne 0) {
        throw "Desktop UI code signing hook failed with exit code $LASTEXITCODE"
    }
}

# CR-02 Code Signing Hook: Generated Installers (post-bundle)
if (Test-Path -LiteralPath $signScript -PathType Leaf) {
    $bundleDir = Join-Path $desktopRoot "src-tauri\target\release\bundle"
    if (Test-Path -LiteralPath $bundleDir) {
        $bundleExes = Get-ChildItem -LiteralPath $bundleDir -Recurse -File -Include "*.exe" -ErrorAction SilentlyContinue
        foreach ($exe in $bundleExes) {
            & $signScript -FilePath $exe.FullName -Stage "installer" -Description "AMCA Studio Setup"
            if ($LASTEXITCODE -ne 0) {
                throw "Installer code signing hook failed with exit code $LASTEXITCODE"
            }
        }
    }
}

Get-ChildItem `
    -LiteralPath (Join-Path $desktopRoot "src-tauri\target\release\bundle") `
    -Recurse `
    -File `
    -Include "*.exe", "*.msi" |
    Select-Object FullName, Length, LastWriteTime
