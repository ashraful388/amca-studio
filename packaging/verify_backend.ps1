param(
    [string]$Backend = "",
    [int]$TimeoutSeconds = 45,
    [switch]$KeepDatabase
)

$ErrorActionPreference = "Stop"
$softwareRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
if (-not $Backend) {
    $Backend = Join-Path $softwareRoot "backend-dist\amca-backend.exe"
}
$Backend = (Resolve-Path -LiteralPath $Backend).Path

# Some Windows installations expose both Path and PATH to child-process APIs.
# Normalize the process copy before Start-Process constructs its environment.
$pathValue = $env:Path
Remove-Item Env:Path -ErrorAction SilentlyContinue
$env:Path = $pathValue

$listener = [System.Net.Sockets.TcpListener]::new(
    [System.Net.IPAddress]::Loopback,
    0
)
$listener.Start()
$port = ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port
$listener.Stop()

$smokeRoot = Join-Path (
    [System.IO.Path]::GetTempPath()
) ("amca-backend-smoke-" + [System.Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $smokeRoot | Out-Null
$database = Join-Path $smokeRoot "amca.sqlite3"
$baseUrl = "http://127.0.0.1:$port"
$process = $null

try {
    $process = Start-Process `
        -FilePath $Backend `
        -ArgumentList @(
            "--host", "127.0.0.1",
            "--port", $port,
            "--database", ('"' + $database + '"')
        ) `
        -PassThru `
        -WindowStyle Hidden

    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    $health = $null
    while ([DateTime]::UtcNow -lt $deadline) {
        if ($process.HasExited) {
            throw "Packaged backend exited during startup with code $($process.ExitCode)."
        }
        try {
            $health = Invoke-RestMethod -Uri "$baseUrl/api/health" -TimeoutSec 2
            break
        }
        catch {
            Start-Sleep -Milliseconds 150
        }
    }
    if ($null -eq $health -or $health.status -ne "ok") {
        throw "Packaged backend did not become healthy within $TimeoutSeconds seconds."
    }
if ($health.protocol_version -ne 15) {
        throw "Packaged backend reported an incompatible desktop protocol."
    }

    # 28B identity handshake: the installed desktop verifies these fields before
    # attaching to a backend, so the packaged contract asserts them here.
    $expectedVersion = (
        Select-String -LiteralPath (Join-Path $softwareRoot "pyproject.toml") `
            -Pattern '^version = "([^"]+)"' |
        Select-Object -First 1
    ).Matches[0].Groups[1].Value
    if (-not $expectedVersion) {
        throw "Could not read the expected version from pyproject.toml."
    }
    if ($health.app_version -ne $expectedVersion) {
        throw "Packaged backend reported app_version '$($health.app_version)' instead of '$expectedVersion'."
    }
    if ($health.build_sha -notmatch '^[0-9a-f]{40}$') {
        throw "Packaged backend did not publish a 40-hex build SHA (got '$($health.build_sha)')."
    }
    if (-not ($health.pid -as [int]) -or [int]$health.pid -le 0) {
        throw "Packaged backend did not publish its process id."
    }

    $session = Invoke-RestMethod -Uri "$baseUrl/api/session" -TimeoutSec 5
    if (-not $session.session_token) {
        throw "Session endpoint did not return a token."
    }
    $headers = @{
        "Origin" = "http://localhost:1420"
        "X-AMCA-Session" = [string]$session.session_token
    }

    $benchmarks = Invoke-RestMethod `
        -Uri "$baseUrl/api/benchmarks" `
        -Headers $headers `
        -TimeoutSec 5
    if (@($benchmarks.tasks).Count -lt 1) {
        throw "Bundled benchmark fixtures were not readable."
    }

    $dashboard = Invoke-RestMethod `
        -Uri "$baseUrl/api/dashboard" `
        -Headers $headers `
        -TimeoutSec 5
    if ($null -eq $dashboard.projects -or $null -eq $dashboard.runs) {
        throw "Dashboard response did not contain projects and runs."
    }

    $conversationBody = @{
        mode = "solo"
        participant_ids = @()
    } | ConvertTo-Json
    $conversation = Invoke-RestMethod `
        -Method Post `
        -Uri "$baseUrl/api/conversations" `
        -Headers $headers `
        -ContentType "application/json" `
        -Body $conversationBody `
        -TimeoutSec 5
    if (-not $conversation.conversation.conversation_id) {
        throw "Packaged conversation store did not create a durable chat."
    }
    $conversationList = Invoke-RestMethod `
        -Uri "$baseUrl/api/conversations" `
        -Headers $headers `
        -TimeoutSec 5
    if (@($conversationList.conversations).Count -ne 1) {
        throw "Packaged conversation store did not list the created chat."
    }

    $credentialStatus = Invoke-RestMethod `
        -Uri "$baseUrl/api/credentials/status" `
        -Headers $headers `
        -TimeoutSec 5
    if ($credentialStatus.persistent_store -ne "windows_credential_manager_with_dpapi_fallback") {
        throw "Packaged credential vault did not report its protected Windows store."
    }
    if (@($credentialStatus.credentials).Count -ne 4) {
        throw "Packaged credential vault did not expose the four model-scoped statuses."
    }
    $credentialJson = $credentialStatus | ConvertTo-Json -Depth 5 -Compress
    if ($credentialJson -match 'secret|api[_-]?key|bearer') {
        throw "Packaged credential status exposed a secret-bearing field."
    }

    $profileCanary = "packaged-model-profile-canary-value"
    $profileBody = @{
        provider_kind = "anthropic"
        label = "Packaged Anthropic profile"
        model = "claude-packaged-test"
        endpoint = "https://api.anthropic.com"
        pricing_basis = "free"
        secret = $profileCanary
    } | ConvertTo-Json
    $createdProfile = Invoke-RestMethod `
        -Method Post `
        -Uri "$baseUrl/api/model-profiles" `
        -Headers $headers `
        -ContentType "application/json" `
        -Body $profileBody `
        -TimeoutSec 5
    if ($createdProfile.network_started -ne $false) {
        throw "Adding a packaged model profile unexpectedly started network work."
    }
    $createdProfileJson = $createdProfile | ConvertTo-Json -Depth 6 -Compress
    if ($createdProfileJson -match [regex]::Escape($profileCanary)) {
        throw "Packaged model profile endpoint returned its write-only credential."
    }
    $profileId = [string]$createdProfile.profile.profile_id
    Invoke-RestMethod `
        -Method Delete `
        -Uri "$baseUrl/api/model-profiles/$profileId" `
        -Headers $headers `
        -TimeoutSec 5 | Out-Null

    $demoBody = @{ goal = "Verify the packaged AMCA backend." } | ConvertTo-Json
    $run = Invoke-RestMethod `
        -Method Post `
        -Uri "$baseUrl/api/demo" `
        -Headers $headers `
        -ContentType "application/json" `
        -Body $demoBody `
        -TimeoutSec 10
    if (-not $run.run_id) {
        throw "Demo endpoint did not return a run identifier."
    }

    while (
        $run.status -in @("queued", "running") -and
        [DateTime]::UtcNow -lt $deadline
    ) {
        Start-Sleep -Milliseconds 100
        $run = Invoke-RestMethod `
            -Uri "$baseUrl/api/runs/$($run.run_id)" `
            -Headers $headers `
            -TimeoutSec 5
    }
    if ($run.status -notin @("succeeded", "partial")) {
        throw "Packaged demo did not finish successfully (status: $($run.status))."
    }
    if (-not $run.project_id -or $null -eq $run.result) {
        throw "Packaged demo did not expose its project and result."
    }

    $events = Invoke-RestMethod `
        -Uri "$baseUrl/api/projects/$($run.project_id)/events" `
        -Headers $headers `
        -TimeoutSec 5
    if (@($events.events).Count -lt 1) {
        throw "Packaged demo did not persist canonical events."
    }

    # One blank page generated by pypdf 6.16.0. Keeping the fixture inline lets
    # this verify the standalone executable without importing host Python code.
    $pdfBase64 = "JVBERi0xLjMKJeLjz9MKMSAwIG9iago8PAovUHJvZHVjZXIgKHB5cGRmKQo+PgplbmRvYmoKMiAwIG9iago8PAovVHlwZSAvUGFnZXMKL0NvdW50IDEKL0tpZHMgWyA0IDAgUiBdCj4+CmVuZG9iagozIDAgb2JqCjw8Ci9UeXBlIC9DYXRhbG9nCi9QYWdlcyAyIDAgUgo+PgplbmRvYmoKNCAwIG9iago8PAovVHlwZSAvUGFnZQovUmVzb3VyY2VzIDw8Cj4+Ci9NZWRpYUJveCBbIDAuMCAwLjAgMjAwIDIwMCBdCi9QYXJlbnQgMiAwIFIKPj4KZW5kb2JqCnhyZWYKMCA1CjAwMDAwMDAwMDAgNjU1MzUgZiAKMDAwMDAwMDAxNSAwMDAwMCBuIAowMDAwMDAwMDU0IDAwMDAwIG4gCjAwMDAwMDAxMTMgMDAwMDAgbiAKMDAwMDAwMDE2MiAwMDAwMCBuIAp0cmFpbGVyCjw8Ci9TaXplIDUKL1Jvb3QgMyAwIFIKL0luZm8gMSAwIFIKPj4Kc3RhcnR4cmVmCjI1NgolJUVPRgo="
    $pdfBody = @{
        project_id = [string]$run.project_id
        filename = "packaged-evidence.pdf"
        data_base64 = $pdfBase64
    } | ConvertTo-Json
    $uploaded = Invoke-RestMethod `
        -Method Post `
        -Uri "$baseUrl/api/assets" `
        -Headers $headers `
        -ContentType "application/json" `
        -Body $pdfBody `
        -TimeoutSec 10
    if ($uploaded.asset.media_type -ne "application/pdf") {
        throw "Packaged PDF ingestion did not identify application/pdf."
    }
    if (@($uploaded.asset.chunks).Count -ne 1) {
        throw "Packaged PDF ingestion did not return one page chunk."
    }
    if ($uploaded.asset.chunks[0].locator -ne "page:1") {
        throw "Packaged PDF ingestion lost its exact page locator."
    }
    $assets = Invoke-RestMethod `
        -Uri "$baseUrl/api/projects/$($run.project_id)/assets" `
        -Headers $headers `
        -TimeoutSec 5
    if (@($assets.assets).Count -lt 1) {
        throw "Packaged PDF asset was not listed after import."
    }
    $approvals = Invoke-RestMethod `
        -Uri "$baseUrl/api/approvals?project_id=$($run.project_id)" `
        -Headers $headers `
        -TimeoutSec 5
    if ($null -eq $approvals.approvals) {
        throw "Packaged approvals endpoint did not return its queue."
    }
    if (-not (Test-Path -LiteralPath $database -PathType Leaf)) {
        throw "Packaged demo did not create the requested SQLite database."
    }

    $file = Get-Item -LiteralPath $Backend
    $hash = (Get-FileHash -LiteralPath $Backend -Algorithm SHA256).Hash
    [ordered]@{
        status = "passed"
        protocol_version = $health.protocol_version
        app_version = $health.app_version
        build_sha = $health.build_sha
        backend_pid = [int]$health.pid
        backend = $file.FullName
        size_bytes = $file.Length
        sha256 = $hash
        benchmark_tasks = @($benchmarks.tasks).Count
        demo_status = $run.status
        project_id = $run.project_id
        persisted_events = @($events.events).Count
        pdf_media_type = $uploaded.asset.media_type
        pdf_locator = $uploaded.asset.chunks[0].locator
        approval_queue_count = @($approvals.approvals).Count
        credential_models = @($credentialStatus.credentials).Count
        custom_model_profile = "anthropic-created-without-network-and-deleted"
        conversations = @($conversationList.conversations).Count
        database = $database
    } | ConvertTo-Json -Depth 4
}
finally {
    if ($null -ne $process -and -not $process.HasExited) {
        # PyInstaller one-file uses a bootloader parent and a Python worker.
        # Stop the exact tree rooted at the process started above.
        $previousErrorPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        $taskkillOutput = & (Join-Path $env:SystemRoot "System32\taskkill.exe") `
            /PID $process.Id /T /F 2>&1
        $taskkillExitCode = $LASTEXITCODE
        $ErrorActionPreference = $previousErrorPreference
        if ($taskkillExitCode -ne 0) {
            throw "Failed to terminate packaged backend process tree: $taskkillOutput"
        }
        $process.WaitForExit(5000) | Out-Null
    }
    $shutdownDeadline = [DateTime]::UtcNow.AddSeconds(5)
    $portReleased = $false
    do {
        $probe = [System.Net.Sockets.TcpClient]::new()
        try {
            $probe.Connect("127.0.0.1", $port)
            Start-Sleep -Milliseconds 100
        }
        catch {
            $portReleased = $true
            break
        }
        finally {
            $probe.Dispose()
        }
    } while ([DateTime]::UtcNow -lt $shutdownDeadline)
    if (-not $portReleased) {
        throw "Packaged backend process tree did not release loopback port $port."
    }
    if (-not $KeepDatabase -and (Test-Path -LiteralPath $smokeRoot)) {
        Remove-Item -LiteralPath $smokeRoot -Recurse -Force
    }
}
