param(
    [string]$Python = "",
    [string]$BuildSha = ""
)

$ErrorActionPreference = "Stop"
$softwareRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
if (-not $Python) {
    $Python = Join-Path $softwareRoot ".venv\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Packaging Python was not found: $Python"
}

if (-not $BuildSha) {
    $BuildSha = $env:AMCA_BUILD_SHA
}
if (-not $BuildSha) {
    # 28B: bind the packaged backend to the exact source revision. The value is
    # injected into src/amca/_build_identity.py, then the file is restored so
    # the source tree stays clean after the build.
    $BuildSha = (& git -C $softwareRoot rev-parse HEAD 2>$null | Select-Object -First 1)
    if ($LASTEXITCODE -ne 0 -or -not $BuildSha) {
        $BuildSha = ""
    }
    $BuildSha = $BuildSha.Trim()
}
$identityPath = Join-Path $softwareRoot "src\amca\_build_identity.py"
if (-not (Test-Path -LiteralPath $identityPath -PathType Leaf)) {
    throw "Build identity module was not found: $identityPath"
}
$identityOriginal = [System.IO.File]::ReadAllText($identityPath)
$identityInjected = [System.Text.RegularExpressions.Regex]::Replace(
    $identityOriginal,
    'BUILD_SHA = "[^"]*"',
    ('BUILD_SHA = "' + $BuildSha + '"')
)
if ($identityInjected -eq $identityOriginal -and $BuildSha -ne "") {
    throw "Failed to inject BUILD_SHA into $identityPath"
}
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($identityPath, $identityInjected, $utf8NoBom)
Write-Output "Packaging backend with BUILD_SHA=$BuildSha"

$distribution = Join-Path $softwareRoot "backend-dist"
$work = Join-Path $softwareRoot ".build\pyinstaller"
try {
    & $Python -m PyInstaller `
        --noconfirm `
        --clean `
        --distpath $distribution `
        --workpath $work `
        (Join-Path $softwareRoot "packaging\amca-backend.spec")
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }
}
finally {
    [System.IO.File]::WriteAllText($identityPath, $identityOriginal, $utf8NoBom)
}

$backend = Join-Path $distribution "amca-backend.exe"
if (-not (Test-Path -LiteralPath $backend -PathType Leaf)) {
    throw "Expected backend executable was not produced: $backend"
}

# CR-02 Code Signing Hook: Backend (after PyInstaller, before packaging embedding)
$signScript = Join-Path $PSScriptRoot "sign_artifact.ps1"
if (Test-Path -LiteralPath $signScript -PathType Leaf) {
    & $signScript -FilePath $backend -Stage "backend" -Description "AMCA Studio Backend Runtime"
    if ($LASTEXITCODE -ne 0) {
        throw "Backend code signing hook failed with exit code $LASTEXITCODE"
    }
}

Write-Output $backend
