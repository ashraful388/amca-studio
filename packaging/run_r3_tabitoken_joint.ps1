param(
    [string]$OutputDirectory = ".amca"
)

$ErrorActionPreference = "Stop"
$entries = @(
    @{ Model = "claude-opus-4-8"; Variable = "TABITOKEN_OPUS_4_8_API_KEY"; Price = "0.50" },
    @{ Model = "claude-opus-4-8-thinking"; Variable = "TABITOKEN_OPUS_4_8_THINKING_API_KEY"; Price = "0.50" },
    @{ Model = "claude-opus-5"; Variable = "TABITOKEN_OPUS_5_API_KEY"; Price = "0.80" },
    @{ Model = "claude-opus-5-thinking"; Variable = "TABITOKEN_OPUS_5_THINKING_API_KEY"; Price = "0.80" }
)
$secrets = @()
try {
    foreach ($entry in $entries) {
        $secure = Read-Host (
            'Enter key for {0} (${1}/request, hidden)' -f $entry.Model, $entry.Price
        ) -AsSecureString
        $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
        if ([string]::IsNullOrWhiteSpace($plain)) {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
            $secure.Dispose()
            throw "No credential was entered for $($entry.Model)."
        }
        [Environment]::SetEnvironmentVariable($entry.Variable, $plain, "Process")
        $plain = $null
        $secrets += @{ Secure = $secure; Pointer = $pointer; Variable = $entry.Variable }
    }

    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $database = Join-Path $OutputDirectory "r3-tabitoken-joint-$stamp.sqlite3"
    $output = Join-Path $OutputDirectory "r3-tabitoken-joint-$stamp.json"
    $env:PYTHONPATH = "src"
    & .\.venv\Scripts\python.exe -m amca.r3_joint_smoke `
        --database $database --output $output
    $exitCode = $LASTEXITCODE
    Write-Output "Sanitized report: $output"
    if ($exitCode -ne 0) {
        throw "The bounded AMCA Joint run did not pass all integration gates."
    }
}
finally {
    Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    foreach ($secret in $secrets) {
        [Environment]::SetEnvironmentVariable($secret.Variable, $null, "Process")
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($secret.Pointer)
        $secret.Secure.Dispose()
    }
}
