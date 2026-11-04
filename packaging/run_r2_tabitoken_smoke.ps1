param(
    [string]$Output = ".amca\r2-tabitoken-smoke.json",
    [Parameter(Mandatory = $true)]
    [string]$Model
)

$ErrorActionPreference = "Stop"
$secureKey = Read-Host "Enter the fresh Tabitoken API key (hidden)" -AsSecureString
$keyPointer = [IntPtr]::Zero
$plainKey = $null
try {
    $keyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
    $plainKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPointer)
    if ([string]::IsNullOrWhiteSpace($plainKey)) {
        throw "No credential was entered."
    }
    $env:TABITOKEN_API_KEY = $plainKey
    $env:PYTHONPATH = "src"
    & .\.venv\Scripts\python.exe -m amca.provider_smoke `
        --provider tabitoken --model $Model --output $Output
    if ($LASTEXITCODE -ne 0) {
        throw "The single Tabitoken smoke request failed."
    }
}
finally {
    Remove-Item Env:TABITOKEN_API_KEY -ErrorAction SilentlyContinue
    Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    $plainKey = $null
    if ($keyPointer -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPointer)
    }
    $secureKey.Dispose()
}
