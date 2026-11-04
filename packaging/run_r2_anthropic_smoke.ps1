param(
    [string]$Output = ".amca\r2-anthropic-smoke.json"
)

$ErrorActionPreference = "Stop"
$secureKey = Read-Host "Enter the fresh official Anthropic API key (hidden)" -AsSecureString
$keyPointer = [IntPtr]::Zero
$plainKey = $null
try {
    $keyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
    $plainKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPointer)
    if ([string]::IsNullOrWhiteSpace($plainKey)) {
        throw "No credential was entered."
    }
    $env:ANTHROPIC_API_KEY = $plainKey
    $env:PYTHONPATH = "src"
    & .\.venv\Scripts\python.exe -m amca.provider_smoke --output $Output
    if ($LASTEXITCODE -ne 0) {
        throw "The single Anthropic smoke request failed."
    }
}
finally {
    Remove-Item Env:ANTHROPIC_API_KEY -ErrorAction SilentlyContinue
    Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    $plainKey = $null
    if ($keyPointer -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPointer)
    }
    $secureKey.Dispose()
}
