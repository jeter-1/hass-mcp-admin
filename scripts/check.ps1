[CmdletBinding()]
param(
    [ValidateSet("Fast", "Full", "Evidence")]
    [string]$Tier = "Fast",
    [ValidateSet("Workflow", "Context", "Evidence", "Validation", "Instructions", "Deployment", "Metadata")]
    [string]$Area,
    [string[]]$TestTarget,
    [string]$PythonExecutable,
    [string]$BaseRef = "origin/main",
    [string[]]$AuthorizedProtectedPath = @()
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ArtifactsPath = Join-Path $RepoRoot ".artifacts"
$script:Results = New-Object System.Collections.Generic.List[object]
$script:HadFailure = $false
$script:NormalizedAuthorizedProtectedPaths = @()
$script:ResolvedBaseSha = $null
$script:ResolvedHeadSha = $null

if (($Area -or $TestTarget) -and $Tier -ne "Fast") {
    throw "-Area and -TestTarget are supported only with -Tier Fast."
}
if ($Area -and $TestTarget) {
    throw "Use either -Area or -TestTarget, not both."
}

if (-not $PythonExecutable) {
    $pythonCommand = Get-Command python.exe, python -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $pythonCommand) {
        throw "Python was not found. Provide -PythonExecutable with a Python 3.12 environment containing the repository test dependencies."
    }
    $PythonExecutable = $pythonCommand.Source
}

function Write-Step {
    param([string]$Message)
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Format-Command {
    param([string]$Executable, [string[]]$Arguments)
    $rendered = @($Executable)
    foreach ($argument in $Arguments) {
        if ($argument -match '\s') {
            $rendered += '"' + $argument.Replace('"', '\"') + '"'
        }
        else {
            $rendered += $argument
        }
    }
    return $rendered -join " "
}

function Add-Result {
    param(
        [string]$Name,
        [string]$Command,
        [string]$Status,
        [int]$ExitCode,
        [double]$DurationSeconds,
        [AllowNull()][Nullable[int]]$TestCount,
        [string]$Note = ""
    )
    $script:Results.Add([ordered]@{
        name = $Name
        command = $Command
        status = $Status
        exit_code = $ExitCode
        test_count = $(if ($null -eq $TestCount) { "unknown" } else { [int]$TestCount })
        duration_seconds = [Math]::Round($DurationSeconds, 3)
        note = $Note
    })
    if ($Status -eq "failed") {
        $script:HadFailure = $true
    }
}

function Invoke-NativeStep {
    param(
        [string]$Label,
        [string]$Executable,
        [string[]]$Arguments
    )
    Write-Step $Label
    $commandText = Format-Command -Executable $Executable -Arguments $Arguments
    Write-Host $commandText
    $timer = [Diagnostics.Stopwatch]::StartNew()
    $output = @()
    $exitCode = 127
    $previousErrorPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = @(& $Executable @Arguments 2>&1 | ForEach-Object { "$_" })
        $exitCode = $LASTEXITCODE
    }
    catch {
        $output += $_.Exception.Message
    }
    finally {
        $ErrorActionPreference = $previousErrorPreference
        $timer.Stop()
    }
    foreach ($line in $output) {
        Write-Host $line
    }
    $combined = $output -join "`n"
    $testCount = $null
    $note = ""
    if ($combined -match 'Ran\s+(\d+)\s+tests?') {
        $testCount = [int]$Matches[1]
    }
    if ($combined -match 'OK\s+\(skipped=(\d+)\)') {
        $note = "skipped_tests=$($Matches[1])"
    }
    $status = if ($exitCode -eq 0) { "passed" } else { "failed" }
    Add-Result -Name $Label -Command $commandText -Status $status -ExitCode $exitCode -DurationSeconds $timer.Elapsed.TotalSeconds -TestCount $testCount -Note $note
}

function Invoke-LocalStep {
    param(
        [string]$Label,
        [string]$CommandText,
        [scriptblock]$Action
    )
    Write-Step $Label
    Write-Host $CommandText
    $timer = [Diagnostics.Stopwatch]::StartNew()
    $exitCode = 0
    $note = ""
    try {
        & $Action
    }
    catch {
        $exitCode = 1
        $note = $_.Exception.Message
        Write-Host $note -ForegroundColor Red
    }
    finally {
        $timer.Stop()
    }
    $status = if ($exitCode -eq 0) { "passed" } else { "failed" }
    Add-Result -Name $Label -Command $CommandText -Status $status -ExitCode $exitCode -DurationSeconds $timer.Elapsed.TotalSeconds -TestCount $null -Note $note
}

function Invoke-GitRead {
    param([string[]]$Arguments)
    $previousErrorPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = @(& git -C $RepoRoot @Arguments 2>&1 | ForEach-Object { "$_" })
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorPreference
    }
    if ($exitCode -ne 0) {
        throw "Git state command failed: git $($Arguments -join ' ')"
    }
    return @($output | Where-Object { $_ })
}

function Get-ChangedPaths {
    $values = New-Object System.Collections.Generic.List[string]
    $comparisonBase = if ($script:ResolvedBaseSha) { $script:ResolvedBaseSha } else { $BaseRef }
    $comparisonHead = if ($script:ResolvedHeadSha) { $script:ResolvedHeadSha } else { "HEAD" }
    $commands = @(
        @("diff", "--name-only", "$comparisonBase...$comparisonHead"),
        @("diff", "--name-only"),
        @("diff", "--cached", "--name-only"),
        @("ls-files", "--others", "--exclude-standard")
    )
    foreach ($command in $commands) {
        foreach ($path in (Invoke-GitRead -Arguments $command)) {
            $normalized = $path.Trim().Replace("\", "/")
            if ($normalized -and -not $values.Contains($normalized)) {
                $values.Add($normalized)
            }
        }
    }
    return @($values)
}

function Test-BaseReference {
    $resolved = @(Invoke-GitRead -Arguments @("rev-parse", "--verify", "$BaseRef^{commit}"))
    $script:ResolvedBaseSha = [string]($resolved | Select-Object -First 1)
    if (-not $script:ResolvedBaseSha) {
        throw "Git returned no commit for base reference $BaseRef."
    }
    Write-Host "Verified base commit $BaseRef at $($script:ResolvedBaseSha)."
}

function Get-WorkingTreeState {
    $changes = @(Invoke-GitRead -Arguments @("status", "--porcelain"))
    if ($changes.Count -eq 0) {
        return "clean"
    }
    return "dirty"
}

function Test-EvidenceSnapshot {
    $resolved = @(Invoke-GitRead -Arguments @("rev-parse", "--verify", "HEAD^{commit}"))
    $script:ResolvedHeadSha = [string]($resolved | Select-Object -First 1)
    if (-not $script:ResolvedHeadSha) {
        throw "Git returned no commit for HEAD."
    }
    $workingTree = Get-WorkingTreeState
    if ($workingTree -ne "clean") {
        throw "Evidence requires a clean working tree so the recorded HEAD identifies the tested files."
    }
    Write-Host "Evidence snapshot is clean at $($script:ResolvedHeadSha)."
}

function Test-EvidenceSnapshotConsistency {
    $currentBase = [string]((Invoke-GitRead -Arguments @("rev-parse", "--verify", "$BaseRef^{commit}")) | Select-Object -First 1)
    $currentHead = [string]((Invoke-GitRead -Arguments @("rev-parse", "--verify", "HEAD^{commit}")) | Select-Object -First 1)
    if ($currentBase -ne $script:ResolvedBaseSha) {
        throw "The Evidence base moved during validation."
    }
    if ($currentHead -ne $script:ResolvedHeadSha) {
        throw "HEAD moved during Evidence validation."
    }
    if ((Get-WorkingTreeState) -ne "clean") {
        throw "The working tree changed during Evidence validation."
    }
    Write-Host "Evidence snapshot identity remained stable and clean."
}

function Get-ProtectedPathLabel {
    param([string]$Path)
    if ($Path.StartsWith("hass_mcp_admin/", [StringComparison]::Ordinal)) {
        return "stable v1.1.2"
    }
    if ($Path.StartsWith("hass_mcp_engineering_beta/ha_mcp_engineering/", [StringComparison]::Ordinal)) {
        return "Engineering runtime"
    }
    if ($Path -match '^\.github/workflows/.+\.ya?ml$') {
        return "workflow authority"
    }
    if ($Path.StartsWith(".release/", [StringComparison]::Ordinal)) {
        return "release declaration"
    }
    if ($Path -in @(
        "repository.yaml",
        "hass_mcp_engineering_beta/config.yaml",
        "hass_mcp_engineering_beta/Dockerfile"
    )) {
        return "release/deployment metadata"
    }
    return $null
}

function Test-DirectoryAuthorizationIsProtected {
    param([string]$Path)
    return (
        $Path.StartsWith("hass_mcp_admin/", [StringComparison]::Ordinal) -or
        $Path.StartsWith("hass_mcp_engineering_beta/ha_mcp_engineering/", [StringComparison]::Ordinal) -or
        $Path.StartsWith(".github/workflows/", [StringComparison]::Ordinal) -or
        $Path.StartsWith(".release/", [StringComparison]::Ordinal)
    )
}

function ConvertTo-AuthorizedProtectedPath {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) {
        throw "-AuthorizedProtectedPath entries cannot be empty."
    }
    $normalized = $Path.Trim().Replace("\", "/")
    if (
        $normalized.StartsWith("/", [StringComparison]::Ordinal) -or
        [IO.Path]::IsPathRooted($normalized) -or
        $normalized -match '^[A-Za-z]:'
    ) {
        throw "Authorized protected paths must be repository-relative: $Path"
    }
    if ($normalized -match '[*?\[]') {
        throw "Authorized protected paths cannot contain wildcards: $Path"
    }
    $isDirectory = $normalized.EndsWith("/", [StringComparison]::Ordinal)
    $body = if ($isDirectory) { $normalized.Substring(0, $normalized.Length - 1) } else { $normalized }
    if (-not $body -or $body.Contains("//")) {
        throw "Authorized protected paths must use normalized repository-relative syntax: $Path"
    }
    foreach ($segment in $body.Split('/')) {
        if (-not $segment -or $segment -in @(".", "..")) {
            throw "Authorized protected paths cannot contain empty, '.' or '..' segments: $Path"
        }
    }
    if ($isDirectory) {
        $normalized = "$body/"
        if (-not (Test-DirectoryAuthorizationIsProtected -Path $normalized)) {
            throw "Directory authorization is not contained in a protected surface: $normalized"
        }
    }
    elseif (-not (Get-ProtectedPathLabel -Path $normalized)) {
        throw "File authorization does not name a protected path: $normalized"
    }
    return $normalized
}

function Test-AuthorizationMatchesPath {
    param([string]$Authorization, [string]$Path)
    if ($Authorization.EndsWith("/", [StringComparison]::Ordinal)) {
        return $Path.StartsWith($Authorization, [StringComparison]::Ordinal)
    }
    return [string]::Equals($Authorization, $Path, [StringComparison]::Ordinal)
}

function Test-PowerShellSyntax {
    $paths = @(Get-ChildItem -LiteralPath (Join-Path $RepoRoot "scripts") -Filter "*.ps1" -File)
    foreach ($path in $paths) {
        [scriptblock]::Create([IO.File]::ReadAllText($path.FullName)) | Out-Null
    }
    Write-Host "Parsed $($paths.Count) PowerShell script(s)."
}

function Test-ScopeBoundaries {
    $normalizedAuthorizations = New-Object System.Collections.Generic.List[string]
    foreach ($candidate in @($AuthorizedProtectedPath)) {
        $normalized = ConvertTo-AuthorizedProtectedPath -Path $candidate
        if ($normalizedAuthorizations.Contains($normalized)) {
            throw "Duplicate authorized protected path: $normalized"
        }
        $normalizedAuthorizations.Add($normalized)
    }
    $script:NormalizedAuthorizedProtectedPaths = @($normalizedAuthorizations.ToArray())

    $protectedChanges = New-Object System.Collections.Generic.List[object]
    foreach ($path in (Get-ChangedPaths)) {
        $label = Get-ProtectedPathLabel -Path $path
        if ($label) {
            $protectedChanges.Add([pscustomobject]@{ path = $path; label = $label })
        }
    }

    $unauthorized = New-Object System.Collections.Generic.List[string]
    foreach ($change in $protectedChanges) {
        $matched = @($script:NormalizedAuthorizedProtectedPaths | Where-Object {
            Test-AuthorizationMatchesPath -Authorization $_ -Path $change.path
        }).Count -gt 0
        if (-not $matched) {
            $unauthorized.Add("[$($change.label)] $($change.path)")
        }
    }
    $unused = New-Object System.Collections.Generic.List[string]
    foreach ($authorization in $script:NormalizedAuthorizedProtectedPaths) {
        $matched = @($protectedChanges | Where-Object {
            Test-AuthorizationMatchesPath -Authorization $authorization -Path $_.path
        }).Count -gt 0
        if (-not $matched) {
            $unused.Add($authorization)
        }
    }
    $problems = New-Object System.Collections.Generic.List[string]
    if ($unauthorized.Count -gt 0) {
        $problems.Add("protected changes not declared by -AuthorizedProtectedPath: $($unauthorized -join '; ')")
    }
    if ($unused.Count -gt 0) {
        $problems.Add("unused or over-broad declarations: $($unused -join ', ')")
    }
    if ($problems.Count -gt 0) {
        throw "Protected-path scope check failed: $($problems -join '. '). The parameter records explicit task scope; it does not grant authorization."
    }
    if ($protectedChanges.Count -eq 0) {
        Write-Host "No stable, runtime, workflow-YAML, release, or deployment-metadata path changed."
        return
    }
    foreach ($change in $protectedChanges) {
        Write-Host "Authorized protected path [$($change.label)]: $($change.path)"
    }
}

function Test-ChangedTextWhitespace {
    $textExtensions = @(".md", ".py", ".ps1", ".yml", ".yaml", ".json", ".txt")
    $problems = New-Object System.Collections.Generic.List[string]
    foreach ($relative in (Get-ChangedPaths)) {
        $path = Join-Path $RepoRoot $relative
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            continue
        }
        if ([IO.Path]::GetExtension($path).ToLowerInvariant() -notin $textExtensions -and [IO.Path]::GetFileName($path) -ne "AGENTS.md") {
            continue
        }
        $lineNumber = 0
        foreach ($line in [IO.File]::ReadAllLines($path)) {
            $lineNumber += 1
            if ($line -match '[ \t]+$') {
                $problems.Add("${relative}:$lineNumber")
            }
        }
    }
    if ($problems.Count -gt 0) {
        throw "Trailing whitespace found: $($problems -join ', ')"
    }
    Write-Host "Changed text files contain no trailing whitespace."
}

function Test-SecretPatterns {
    $patterns = @(
        'github_pat_[A-Za-z0-9_]{20,}',
        'gh[pousr]_[A-Za-z0-9]{36,}',
        'sk-[A-Za-z0-9_-]{20,}',
        ('-----BEGIN ' + '(?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----')
    )
    $matchedFiles = New-Object System.Collections.Generic.List[string]
    $files = @((Invoke-GitRead -Arguments @("ls-files"))) + @(Invoke-GitRead -Arguments @("ls-files", "--others", "--exclude-standard"))
    foreach ($relative in ($files | Select-Object -Unique)) {
        $path = Join-Path $RepoRoot $relative
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            continue
        }
        $item = Get-Item -LiteralPath $path -Force
        if ($item.Length -gt 2MB) {
            continue
        }
        try {
            $text = [IO.File]::ReadAllText($path)
        }
        catch {
            continue
        }
        foreach ($pattern in $patterns) {
            if ($text -match $pattern) {
                $matchedFiles.Add($relative)
                break
            }
        }
    }
    if ($matchedFiles.Count -gt 0) {
        throw "Potential credential pattern found in: $($matchedFiles -join ', ')"
    }
    Write-Host "No bounded high-confidence credential pattern was found."
}

function Get-FastTargets {
    if ($TestTarget) {
        return @($TestTarget)
    }
    if ($Area) {
        switch ($Area) {
            "Context" { return @("tests.test_codex_workflow.ContextToolTests") }
            "Evidence" { return @("tests.test_codex_workflow.PrEvidenceTests") }
            "Validation" {
                return @(
                    "tests.test_codex_workflow.PowerShellValidationTests",
                    "tests.test_codex_workflow.CheckScriptExecutionTests"
                )
            }
            "Instructions" { return @("tests.test_codex_workflow.InstructionFileTests") }
            "Deployment" { return @("tests.test_codex_workflow.DeploymentChecklistTests") }
            "Metadata" { return @("tests.test_codex_workflow.ScopeBoundaryTests") }
            "Workflow" { return @("tests.test_codex_workflow") }
        }
    }

    $targets = New-Object System.Collections.Generic.List[string]
    $fallback = $false
    foreach ($path in (Get-ChangedPaths)) {
        switch -Regex ($path) {
            '^scripts/codex-context\.py$' { $targets.Add("tests.test_codex_workflow.ContextToolTests"); continue }
            '^scripts/pr-evidence\.py$' { $targets.Add("tests.test_codex_workflow.PrEvidenceTests"); continue }
            '^scripts/check\.ps1$' {
                $targets.Add("tests.test_codex_workflow.PowerShellValidationTests")
                $targets.Add("tests.test_codex_workflow.CheckScriptExecutionTests")
                continue
            }
            '(^|/)AGENTS\.md$|^docs/CODEX_WORKFLOW\.md$|^README\.md$|^\.gitignore$' { $targets.Add("tests.test_codex_workflow.InstructionFileTests"); continue }
            '^scripts/deploy-beta\.ps1$' { $targets.Add("tests.test_codex_workflow.DeploymentChecklistTests"); continue }
            '^tests/test_codex_workflow\.py$' { $fallback = $true; continue }
            default { $fallback = $true }
        }
    }
    if ($fallback) {
        throw "Fast test inference is intentionally bounded. Provide -Area or -TestTarget for the changed paths."
    }
    if ($targets.Count -eq 0) {
        return @("tests.test_codex_workflow")
    }
    return @($targets | Select-Object -Unique)
}

function Write-Evidence {
    New-Item -ItemType Directory -Path $ArtifactsPath -Force | Out-Null
    $headSha = $script:ResolvedHeadSha
    if (-not $headSha) {
        try {
            $headSha = [string]((Invoke-GitRead -Arguments @("rev-parse", "HEAD")) | Select-Object -First 1)
        }
        catch {
            $headSha = $null
        }
    }
    $baseSha = $script:ResolvedBaseSha
    $workingTree = "unknown"
    try {
        $workingTree = Get-WorkingTreeState
    }
    catch {
        $workingTree = "unknown"
    }
    $localGateStatus = if ($script:HadFailure) { "failed_locally" } else { "executed_locally" }
    $coverage = @(
        [ordered]@{ check = "full_local_gate"; status = $localGateStatus; evidence = "See recorded steps and overall_status." },
        [ordered]@{ check = "docker_image_build"; status = "delegated_to_ci"; evidence = ".github/workflows/ci.yml validate job" },
        [ordered]@{ check = "multiarchitecture_build"; status = "delegated_to_ci"; evidence = ".github/workflows/ci.yml validate job" },
        [ordered]@{ check = "disposable_home_assistant"; status = "delegated_to_ci"; evidence = ".github/workflows/ci.yml real-ha-contract-tests job" },
        [ordered]@{ check = "exact_image"; status = "delegated_to_ci"; evidence = ".github/workflows/ci.yml exact-image-read-gateway job" },
        [ordered]@{ check = "publication"; status = "not_applicable"; evidence = "This validation tier has no publication authority." },
        [ordered]@{ check = "provenance"; status = "delegated_to_ci"; evidence = ".github/workflows/ci.yml image build provenance assertion" },
        [ordered]@{ check = "deployment"; status = "not_applicable"; evidence = "This validation tier performs no deployment." }
    )
    $payload = [ordered]@{
        schema_version = 2
        generated_at_utc = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ", [Globalization.CultureInfo]::InvariantCulture)
        tier = "Evidence"
        repository_root = $RepoRoot
        base_ref = $BaseRef
        base_sha = $baseSha
        head_sha = $headSha
        working_tree = $workingTree
        authorized_protected_paths = @($script:NormalizedAuthorizedProtectedPaths)
        overall_status = $(if ($script:HadFailure) { "failed" } else { "passed" })
        steps = $script:Results.ToArray()
        coverage = $coverage
    }
    $json = $payload | ConvertTo-Json -Depth 8
    $utf8 = New-Object Text.UTF8Encoding($false)
    [IO.File]::WriteAllText((Join-Path $ArtifactsPath "validation.json"), $json + "`n", $utf8)

    $summary = New-Object System.Collections.Generic.List[string]
    $summary.Add("# Local Validation Evidence")
    $summary.Add("")
    $summary.Add("- Overall status: **$($payload.overall_status)**")
    $baseShaText = if ($baseSha) { $baseSha } else { "unresolved" }
    $headShaText = if ($headSha) { $headSha } else { "unresolved" }
    $summary.Add("- Base: ``$BaseRef`` at ``$baseShaText``")
    $summary.Add("- Head: ``$headShaText``")
    $summary.Add("- Working tree: **$workingTree**")
    $authorizedPathText = if ($script:NormalizedAuthorizedProtectedPaths.Count -gt 0) {
        $script:NormalizedAuthorizedProtectedPaths -join ", "
    }
    else {
        "none"
    }
    $summary.Add("- Authorized protected paths: $authorizedPathText")
    $summary.Add("")
    $summary.Add("| Step | Status | Exit | Tests | Duration (s) | Note |")
    $summary.Add("|---|---:|---:|---:|---:|---|")
    foreach ($result in $script:Results) {
        $summary.Add("| $($result.name.Replace('|', '&#124;')) | $($result.status) | $($result.exit_code) | $($result.test_count) | $($result.duration_seconds) | $($result.note) |")
    }
    $summary.Add("")
    $summary.Add("## CI and external coverage")
    $summary.Add("")
    foreach ($item in $coverage) {
        $summary.Add("- $($item.check): **$($item.status)** - $($item.evidence)")
    }
    [IO.File]::WriteAllText((Join-Path $ArtifactsPath "validation.md"), ($summary -join "`n") + "`n", $utf8)
    Write-Host "Wrote .artifacts/validation.json and .artifacts/validation.md"
}

Push-Location $RepoRoot
try {
    Invoke-LocalStep -Label "Verify base reference" -CommandText "git rev-parse --verify $BaseRef^{commit}" -Action { Test-BaseReference }
    if (-not $script:HadFailure -and $Tier -eq "Evidence") {
        Invoke-LocalStep -Label "Bind clean Evidence snapshot" -CommandText "resolve HEAD and require a clean working tree" -Action { Test-EvidenceSnapshot }
    }
    if (-not $script:HadFailure) {
        Invoke-LocalStep -Label "Verify protected-path scope" -CommandText "compare protected paths and explicit declarations against $BaseRef" -Action { Test-ScopeBoundaries }
    }
    if ($script:HadFailure) {
        if ($Tier -eq "Evidence") {
            Write-Evidence
        }
        Write-Host "`nValidation stopped during safe preflight. No repository code was executed." -ForegroundColor Red
        exit 1
    }

    if ($Tier -eq "Fast") {
        $fastChangedPaths = @(Get-ChangedPaths)
        $compileTargets = New-Object System.Collections.Generic.List[string]
        foreach ($path in @("scripts/codex-context.py", "scripts/pr-evidence.py", "tests/test_codex_workflow.py")) {
            $compileTargets.Add($path)
        }
        foreach ($path in ($fastChangedPaths | Where-Object { $_.EndsWith(".py") })) {
            if ((Test-Path -LiteralPath (Join-Path $RepoRoot $path) -PathType Leaf) -and -not $compileTargets.Contains($path)) {
                $compileTargets.Add($path)
            }
        }
        Invoke-NativeStep -Label "Compile affected Python" -Executable $PythonExecutable -Arguments (@(
            "-m", "compileall", "-q"
        ) + $compileTargets.ToArray())
        $targets = Get-FastTargets
        Invoke-NativeStep -Label "Run focused workflow tests" -Executable $PythonExecutable -Arguments (@(
            "-m", "unittest", "-v"
        ) + $targets)
        $metadataPaths = @(
            "repository.yaml",
            "hass_mcp_admin/config.yaml",
            "hass_mcp_engineering_beta/config.yaml"
        )
        if ($Area -eq "Metadata" -or @($fastChangedPaths | Where-Object { $_ -in $metadataPaths }).Count -gt 0) {
            Invoke-NativeStep -Label "Validate add-on metadata" -Executable $PythonExecutable -Arguments @(
                "scripts/validate_addon_metadata.py", "--repo-root", ".", "--base-ref", $script:ResolvedBaseSha
            )
        }
        if (@($fastChangedPaths | Where-Object { $_ -match '\.ya?ml$' }).Count -gt 0) {
            $fastYamlProgram = "import pathlib,yaml; paths=sorted([*pathlib.Path('.').rglob('*.yml'),*pathlib.Path('.').rglob('*.yaml')]); [yaml.safe_load(p.read_text(encoding='utf-8')) for p in paths]; print(f'Parsed {len(paths)} YAML file(s).')"
            Invoke-NativeStep -Label "Parse changed repository YAML" -Executable $PythonExecutable -Arguments @("-c", $fastYamlProgram)
        }
    }
    else {
        Invoke-NativeStep -Label "Compile repository Python" -Executable $PythonExecutable -Arguments @(
            "-m", "compileall", "-q", "hass_mcp_admin", "hass_mcp_engineering_beta", "scripts", "tests"
        )
        Invoke-NativeStep -Label "Run complete unittest suite" -Executable $PythonExecutable -Arguments @(
            "-m", "unittest", "discover", "-s", "tests", "-v"
        )
        Invoke-NativeStep -Label "Validate add-on metadata" -Executable $PythonExecutable -Arguments @(
            "scripts/validate_addon_metadata.py", "--repo-root", ".", "--base-ref", $script:ResolvedBaseSha
        )
        $yamlProgram = "import pathlib,yaml; paths=sorted([*pathlib.Path('.').rglob('*.yml'),*pathlib.Path('.').rglob('*.yaml')]); [yaml.safe_load(p.read_text(encoding='utf-8')) for p in paths]; print(f'Parsed {len(paths)} YAML file(s).')"
        Invoke-NativeStep -Label "Parse repository YAML" -Executable $PythonExecutable -Arguments @("-c", $yamlProgram)
        Invoke-NativeStep -Label "Check installed dependency consistency" -Executable $PythonExecutable -Arguments @("-m", "pip", "check")
        Invoke-LocalStep -Label "Scan for high-confidence secret patterns" -CommandText "bounded offline credential-pattern scan" -Action { Test-SecretPatterns }
    }

    Invoke-LocalStep -Label "Parse PowerShell syntax" -CommandText "[scriptblock]::Create for scripts/*.ps1" -Action { Test-PowerShellSyntax }
    Invoke-NativeStep -Label "Check Git whitespace" -Executable "git" -Arguments @("diff", "--check")
    Invoke-NativeStep -Label "Check staged Git whitespace" -Executable "git" -Arguments @("diff", "--cached", "--check")
    $whitespaceHead = if ($script:ResolvedHeadSha) { $script:ResolvedHeadSha } else { "HEAD" }
    Invoke-NativeStep -Label "Check committed Git whitespace" -Executable "git" -Arguments @("diff", "--check", "$($script:ResolvedBaseSha)...$whitespaceHead")
    Invoke-LocalStep -Label "Check changed text whitespace" -CommandText "scan changed text files for trailing whitespace" -Action { Test-ChangedTextWhitespace }

    if ($Tier -eq "Evidence") {
        Invoke-LocalStep -Label "Recheck Evidence snapshot" -CommandText "verify base, HEAD, and working-tree identity" -Action { Test-EvidenceSnapshotConsistency }
        Write-Evidence
    }
}
finally {
    Pop-Location
}

if ($script:HadFailure) {
    Write-Host "`nValidation failed. Review the failed steps above." -ForegroundColor Red
    exit 1
}

Write-Host "`n$Tier validation passed." -ForegroundColor Green
