[CmdletBinding()]
param(
    [switch]$SkipTests,
    [switch]$FullTests,
    [switch]$SkipDockerBuild,
    [string]$HealthHost,
    [ValidateRange(1, 300)]
    [int]$HealthTimeoutSeconds = 15,
    [string]$ExpectedVersion,
    [string]$DeployedVersion,
    [string]$PythonExecutable,
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$BetaConfigPath = Join-Path $RepoRoot "hass_mcp_engineering_beta\config.yaml"
$MetadataValidator = Join-Path $RepoRoot "scripts\validate_addon_metadata.py"
$BetaPort = 8100

function Write-Step {
    param([string]$Message)
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Invoke-External {
    param(
        [string]$Label,
        [string]$Executable,
        [string[]]$Arguments,
        [switch]$AlwaysRun,
        [switch]$SuppressOutput
    )

    Write-Step $Label
    if ($DryRun -and -not $AlwaysRun) {
        Write-Host "DRY RUN: command skipped."
        return
    }

    $previousErrorPreference = $ErrorActionPreference
    try {
        # Windows PowerShell wraps native stderr as error records even when the
        # process exits successfully. Exit codes remain the source of truth.
        $ErrorActionPreference = "Continue"
        if ($SuppressOutput) {
            $capturedOutput = @(& $Executable @Arguments 2>&1)
        }
        else {
            & $Executable @Arguments
        }
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorPreference
    }
    if ($exitCode -ne 0) {
        throw "$Label failed with exit code $exitCode."
    }
    if ($SuppressOutput) {
        Write-Host "$Label passed."
    }
}

function Get-BetaVersion {
    $match = Select-String -Path $BetaConfigPath -Pattern '^version:\s*["'']?([^"''\s]+)' | Select-Object -First 1
    if (-not $match) {
        throw "Unable to read the beta version from add-on metadata."
    }
    return $match.Matches[0].Groups[1].Value
}

function Get-BuildProvenance {
    $previousErrorPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $buildSha = (& git -C $RepoRoot rev-parse HEAD 2>&1 | Out-String).Trim()
        $gitExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorPreference
    }
    if ($gitExitCode -ne 0 -or $buildSha -notmatch '^[0-9a-f]{40}$') {
        throw "Unable to determine the exact source commit for the beta/RC image."
    }
    return @{
        Sha = $buildSha
        Time = [DateTime]::UtcNow.ToString(
            "yyyy-MM-ddTHH:mm:ssZ",
            [Globalization.CultureInfo]::InvariantCulture
        )
    }
}

function Assert-CleanWorkingTree {
    Write-Step "Validate repository working tree"
    $previousErrorPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $diffCheck = @(& git -C $RepoRoot diff --check 2>&1)
        $diffExitCode = $LASTEXITCODE
        $changes = @(& git -C $RepoRoot status --porcelain 2>&1)
        $statusExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorPreference
    }
    if ($diffExitCode -ne 0) {
        throw "Working tree contains whitespace errors."
    }
    if ($statusExitCode -ne 0) {
        throw "Unable to inspect the repository working tree."
    }
    if ($changes.Count -gt 0) {
        throw "Working tree is not clean ($($changes.Count) changed path(s)). Commit or stash changes before deployment."
    }
    Write-Host "Working tree is clean."
}

function Test-BetaHealth {
    param([string]$HostName)

    if ($HostName -notmatch '^(localhost|[A-Za-z0-9.-]+|\[[0-9A-Fa-f:]+\])$') {
        throw "HealthHost must be a hostname or IP address only; do not include a scheme, path, credentials, or secret."
    }

    Write-Step "Verify beta health on port $BetaPort"
    if ($DryRun) {
        Write-Host "DRY RUN: health request skipped."
        return
    }

    $uri = "http://${HostName}:$BetaPort/health"
    try {
        $response = Invoke-RestMethod -Method Get -Uri $uri -TimeoutSec $HealthTimeoutSeconds
    }
    catch {
        throw "Beta health verification failed on port $BetaPort."
    }
    if ($response.status -ne "ok") {
        throw "Beta health endpoint did not report status 'ok'."
    }
    Write-Host "Beta health endpoint reports ok."
}

if ($SkipTests -and $FullTests) {
    throw "SkipTests and FullTests cannot be used together."
}

if (-not $DeployedVersion) {
    $DeployedVersion = $env:HA_MCP_BETA_DEPLOYED_VERSION
}
if (-not $DeployedVersion) {
    throw "Provide -DeployedVersion (or HA_MCP_BETA_DEPLOYED_VERSION) so the version bump can be verified."
}

if (-not $PythonExecutable) {
    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if (-not $pythonCommand) {
        throw "Python was not found. Provide -PythonExecutable with the Python 3.12 executable path."
    }
    $PythonExecutable = $pythonCommand.Source
}

Push-Location $RepoRoot
try {
    Assert-CleanWorkingTree

    $validatorArgs = @(
        $MetadataValidator,
        "--repo-root", $RepoRoot,
        "--base-ref", "origin/main",
        "--deployed-version", $DeployedVersion
    )
    if ($ExpectedVersion) {
        $validatorArgs += @("--expected-version", $ExpectedVersion)
    }
    Invoke-External -Label "Validate add-on metadata and version bump" -Executable $PythonExecutable -Arguments $validatorArgs -AlwaysRun

    $betaVersion = Get-BetaVersion
    Write-Host "Validated beta version: $betaVersion"

    Invoke-External -Label "Compile beta Python files" -Executable $PythonExecutable -Arguments @(
        "-m", "compileall", "-q", "hass_mcp_engineering_beta"
    )

    if ($SkipTests) {
        Write-Step "Beta tests"
        Write-Host "Skipped by request."
    }
    elseif ($FullTests) {
        Invoke-External -Label "Run complete test suite" -Executable $PythonExecutable -Arguments @(
            "-m", "unittest", "discover", "-s", "tests", "-v"
        ) -SuppressOutput
    }
    else {
        Invoke-External -Label "Run beta-focused tests" -Executable $PythonExecutable -Arguments @(
            "-m", "unittest", "discover", "-s", "tests", "-p", "test_beta*.py", "-v"
        ) -SuppressOutput
        Invoke-External -Label "Run deployment metadata tests" -Executable $PythonExecutable -Arguments @(
            "-m", "unittest", "discover", "-s", "tests", "-p", "test_deployment_metadata.py", "-v"
        ) -SuppressOutput
    }

    if ($SkipDockerBuild) {
        Write-Step "Build beta Docker image"
        Write-Host "Skipped by request."
    }
    else {
        $build = Get-BuildProvenance
        Invoke-External -Label "Build beta Docker image" -Executable "docker" -Arguments @(
            "build", "--pull",
            "--build-arg", "HAMCP_BUILD_SHA=$($build.Sha)",
            "--build-arg", "HAMCP_BUILD_TIME=$($build.Time)",
            "-t", "hass-mcp-engineering-beta:$betaVersion", "./hass_mcp_engineering_beta"
        )
    }

    if ($HealthHost) {
        Test-BetaHealth -HostName $HealthHost
    }

    Write-Step "Beta release checklist"
    @(
        "[ ] Merge the pull request.",
        "[ ] Refresh the Home Assistant add-on repository.",
        "[ ] Update HA MCP Engineering Server Beta only.",
        "[ ] Confirm /health on port 8100.",
        "[ ] Run scripts/codex-context.py --format markdown with the validated Python environment and record its derived static, delegated-read, and connector tool-count expectations.",
        "[ ] Confirm the context document resolution status is exact.",
        "[ ] Confirm active_acceptance_document is known before release or deployment work.",
        "[ ] Stop when exact acceptance authority is missing, partial, unsupported, or unknown.",
        "[ ] Never substitute a historical reference or release notes for the active acceptance document.",
        "[ ] Use MCP Inspector tools/list and compare the callable catalog with the context report (reconnect only if the connector cache is stale; fail-closed runtime admission may lower the delegated-read count).",
        "[ ] Call server_info and verify version $betaVersion.",
        "[ ] Call list_capabilities.",
        "[ ] Call get_server_health.",
        "[ ] Verify no active pre-Beta-25 approval was silently migrated; recreate the plan before use.",
        "[ ] Verify forwarded-header trust is disabled unless trusted proxy CIDRs were intentionally configured.",
        "[ ] Open the admin-only Home Assistant Ingress approval panel; confirm internal port 8110 is not host mapped.",
        "[ ] Confirm approve_change_plan returns approval_pending and MCP-only apply fails external_approval_required.",
        "[ ] Execute any governed description-only apply and rollback only after separate human Ingress approvals."
    ) | ForEach-Object { Write-Host $_ }

    Write-Host "`nValidation complete. No production deployment was performed." -ForegroundColor Green
}
finally {
    Pop-Location
}
