param(
    [string]$AppDataRoot = "",
    [switch]$SkipTesseract
)

$ErrorActionPreference = "Stop"
$whisperRelease = "b4938"
$whisperArchiveSha256 = "c2a4b60edb11f7e11a9191ffb50929535527d4d91c9903dbe3e554583bbbc63d"
$whisperModelSha256 = "921e4cf8686fdd993dcd081a5da5b6c365bfde1162e72b08d75ac75289920b1f"

if (-not $AppDataRoot) {
    $AppDataRoot = Join-Path $env:APPDATA "ai.amca.desktop"
}
$appData = [System.IO.Path]::GetFullPath($AppDataRoot)
$target = Join-Path $appData "media-tools\whisper"

if (-not $SkipTesseract) {
    $knownTesseract = Join-Path $env:ProgramFiles "Tesseract-OCR\tesseract.exe"
    if (-not (Test-Path -LiteralPath $knownTesseract -PathType Leaf)) {
        & winget install `
            --id UB-Mannheim.TesseractOCR `
            --exact `
            --silent `
            --accept-package-agreements `
            --accept-source-agreements `
            --disable-interactivity
        if ($LASTEXITCODE -ne 0) {
            throw "Tesseract installation failed with exit code $LASTEXITCODE"
        }
    }
}

$existingRuntime = Join-Path $target "whisper-cli.exe"
$existingModel = Join-Path $target "ggml-tiny.en.bin"
if (
    (Test-Path -LiteralPath $existingRuntime -PathType Leaf) -and
    (Test-Path -LiteralPath $existingModel -PathType Leaf) -and
    (Get-FileHash -LiteralPath $existingModel -Algorithm SHA256).Hash.ToLowerInvariant() -eq $whisperModelSha256
) {
    Write-Output "AMCA local media tools are already installed at $target"
    exit 0
}
if (Test-Path -LiteralPath $target) {
    throw "Refusing to overwrite an incomplete or different runtime: $target"
}

$stage = Join-Path (
    [System.IO.Path]::GetTempPath()
) ("amca-whisper-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $stage | Out-Null
try {
    $archive = Join-Path $stage "whisper-bin-x64.zip"
    Invoke-WebRequest `
        -Uri "https://github.com/ggml-org/whisper.cpp/releases/download/$whisperRelease/whisper-bin-x64.zip" `
        -OutFile $archive
    $archiveHash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($archiveHash -ne $whisperArchiveSha256) {
        throw "whisper.cpp archive SHA-256 mismatch"
    }

    $expanded = Join-Path $stage "expanded"
    Expand-Archive -LiteralPath $archive -DestinationPath $expanded
    $release = Join-Path $expanded "Release"
    if (-not (Test-Path -LiteralPath (Join-Path $release "whisper-cli.exe") -PathType Leaf)) {
        throw "whisper.cpp archive does not contain whisper-cli.exe"
    }

    $model = Join-Path $release "ggml-tiny.en.bin"
    Invoke-WebRequest `
        -Uri "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny.en.bin" `
        -OutFile $model
    $modelHash = (Get-FileHash -LiteralPath $model -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($modelHash -ne $whisperModelSha256) {
        throw "whisper.cpp model SHA-256 mismatch"
    }

    New-Item -ItemType Directory -Path $target | Out-Null
    Get-ChildItem -LiteralPath $release -File | Copy-Item -Destination $target
}
finally {
    if (Test-Path -LiteralPath $stage) {
        $resolvedStage = (Resolve-Path -LiteralPath $stage).Path
        $resolvedTemp = (Resolve-Path -LiteralPath ([System.IO.Path]::GetTempPath())).Path
        if ($resolvedStage.StartsWith($resolvedTemp, [System.StringComparison]::OrdinalIgnoreCase)) {
            Remove-Item -LiteralPath $resolvedStage -Recurse -Force
        }
    }
}

Write-Output "Installed AMCA local media tools at $target"
