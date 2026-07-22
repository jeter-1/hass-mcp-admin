[CmdletBinding()]
param(
    [ValidateSet("Fast", "Full", "Evidence")]
    [string]$Tier = "Fast",
    [ValidateSet("Workflow", "Context", "Evidence", "Validation", "Instructions", "Deployment", "Metadata")]
    [string]$Area,
    [string[]]$TestTarget,
    [string]$PythonExecutable,
    [string]$BaseRef = "origin/main"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ArtifactsPath = Join-Path $RepoRoot ".artifacts"
$script:Results = New-Object System.Collections.Generic.List[object]
$script:HadFailure = $false

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
    $commands = @(
        @("diff", "--name-only", "$BaseRef...HEAD"),
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

function Test-PowerShellSyntax {
    $paths = Get-ChildItem -LiteralPath (Join-Path $RepoRoot "scripts") -Filter "*.ps1" -File
    foreach ($path in $paths) {
        [scriptblock]::Create([IO.File]::ReadAllText($path.FullName)) | Out-Null
    }
    Write-Host "Parsed $($paths.Count) PowerShell script(s)."
}

function Test-ScopeBoundaries {
    $violations = New-Object System.Collections.Generic.List[string]
    foreach ($path in (Get-ChangedPaths)) {
        if ($path.StartsWith("hass_mcp_admin/")) {
            $violations.Add("stable v1.1.2: $path")
        }
        if ($path.StartsWith("hass_mcp_engineering_beta/ha_mcp_engineering/")) {
            $violations.Add("Engineering runtime: $path")
        }
        if ($path -match '^\.github/workflows/.+\.ya?ml$') {
            $violations.Add("workflow authority: $path")
        }
        if ($path.StartsWith(".release/")) {
            $violations.Add("release declaration: $path")
        }
        if ($path -in @(
            "repository.yaml",
            "hass_mcp_engineering_beta/config.yaml",
            "hass_mcp_engineering_beta/Dockerfile"
        )) {
            $violations.Add("release/deployment metadata: $path")
        }
    }
    if ($violations.Count -gt 0) {
        throw "Protected changes are outside this development-pipeline task: $($violations -join '; ')"
    }
    Write-Host "No stable, runtime, workflow-YAML, release, or deployment-metadata path changed."
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
    $matches = New-Object System.Collections.Generic.List[string]
    $files = @((Invoke-GitRead -Arguments @("ls-files"))) + @(Invoke-GitRead -Arguments @("ls-files", "--others", "--exclude-standard"))
    foreach ($relative in ($files | Select-Object -Unique)) {
        $path = Join-Path $RepoRoot $relative
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            continue
        }
        $item = Get-Item -LiteralPath $path
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
                $matches.Add($relative)
                break
            }
        }
    }
    if ($matches.Count -gt 0) {
        throw "Potential credential pattern found in: $($matches -join ', ')"
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
            "Validation" { return @("tests.test_codex_workflow.PowerShellValidationTests") }
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
            '^scripts/check\.ps1$' { $targets.Add("tests.test_codex_workflow.PowerShellValidationTests"); continue }
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
    $headSha = [string]((Invoke-GitRead -Arguments @("rev-parse", "HEAD")) | Select-Object -First 1)
    $coverage = @(
        [ordered]@{ check = "full_local_gate"; status = "executed_locally"; evidence = "See recorded steps and overall_status." },
        [ordered]@{ check = "docker_image_build"; status = "delegated_to_ci"; evidence = ".github/workflows/ci.yml validate job" },
        [ordered]@{ check = "multiarchitecture_build"; status = "delegated_to_ci"; evidence = ".github/workflows/ci.yml validate job" },
        [ordered]@{ check = "disposable_home_assistant"; status = "delegated_to_ci"; evidence = ".github/workflows/ci.yml real-ha-contract-tests job" },
        [ordered]@{ check = "exact_image"; status = "delegated_to_ci"; evidence = ".github/workflows/ci.yml exact-image-read-gateway job" },
        [ordered]@{ check = "publication"; status = "not_applicable"; evidence = "This validation tier has no publication authority." },
        [ordered]@{ check = "provenance"; status = "delegated_to_ci"; evidence = ".github/workflows/ci.yml image build provenance assertion" },
        [ordered]@{ check = "deployment"; status = "not_applicable"; evidence = "This validation tier performs no deployment." }
    )
    $payload = [ordered]@{
        schema_version = 1
        generated_at_utc = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ", [Globalization.CultureInfo]::InvariantCulture)
        tier = "Evidence"
        repository_root = $RepoRoot
        base_ref = $BaseRef
        head_sha = $headSha
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
    $summary.Add("- Base: ``$BaseRef``")
    $summary.Add("- Head: ``$headSha``")
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
                "scripts/validate_addon_metadata.py", "--repo-root", ".", "--base-ref", $BaseRef
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
            "scripts/validate_addon_metadata.py", "--repo-root", ".", "--base-ref", $BaseRef
        )
        $yamlProgram = "import pathlib,yaml; paths=sorted([*pathlib.Path('.').rglob('*.yml'),*pathlib.Path('.').rglob('*.yaml')]); [yaml.safe_load(p.read_text(encoding='utf-8')) for p in paths]; print(f'Parsed {len(paths)} YAML file(s).')"
        Invoke-NativeStep -Label "Parse repository YAML" -Executable $PythonExecutable -Arguments @("-c", $yamlProgram)
        Invoke-NativeStep -Label "Check installed dependency consistency" -Executable $PythonExecutable -Arguments @("-m", "pip", "check")
        Invoke-LocalStep -Label "Scan for high-confidence secret patterns" -CommandText "bounded offline credential-pattern scan" -Action { Test-SecretPatterns }
        Invoke-LocalStep -Label "Verify protected-path scope" -CommandText "compare protected paths against $BaseRef, index, and working tree" -Action { Test-ScopeBoundaries }
    }

    Invoke-LocalStep -Label "Parse PowerShell syntax" -CommandText "[scriptblock]::Create for scripts/*.ps1" -Action { Test-PowerShellSyntax }
    Invoke-NativeStep -Label "Check Git whitespace" -Executable "git" -Arguments @("diff", "--check")
    Invoke-NativeStep -Label "Check staged Git whitespace" -Executable "git" -Arguments @("diff", "--cached", "--check")
    Invoke-NativeStep -Label "Check committed Git whitespace" -Executable "git" -Arguments @("diff", "--check", "$BaseRef...HEAD")
    Invoke-LocalStep -Label "Check changed text whitespace" -CommandText "scan changed text files for trailing whitespace" -Action { Test-ChangedTextWhitespace }

    if ($Tier -eq "Evidence") {
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
