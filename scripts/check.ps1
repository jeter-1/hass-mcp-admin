[CmdletBinding()]
param(
    [ValidateSet("Fast", "Full", "Evidence")]
    [string]$Tier = "Fast",
    [ValidateSet("Workflow", "Context", "Evidence", "Validation", "Instructions", "Deployment", "Metadata")]
    [string]$Area,
    [string[]]$TestTarget,
    [string]$PythonExecutable,
    [string]$BaseRef,
    [string[]]$AuthorizedProtectedPath = @(),
    [ValidateRange(1, 4)]
    [int]$Parallel
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
# HAMCP-356: the deployment-metadata version check compares with the deployed
# line, origin/main, exactly as CI's "Validate deployment metadata" step does.
# It is a separate value from the pull-request base above and never stands in
# for it, or the other way round.
$VersionCheckBaseRef = "origin/main"
$script:VersionCheckBaseSha = $null
$script:GitIdentitySnapshot = $null
$script:GitIdentityPaths = $null
$previousGitCeilings = $env:GIT_CEILING_DIRECTORIES
$script:UnitTestExecution = if ($Parallel) {
    [ordered]@{ mode = "sharded"; sharded = $true; workers = $Parallel; executed = $false }
}
else {
    [ordered]@{ mode = "single_process"; sharded = $false; workers = 1; executed = $false }
}

if (($Area -or $TestTarget) -and $Tier -ne "Fast") {
    throw "-Area and -TestTarget are supported only with -Tier Fast."
}
if ($Area -and $TestTarget) {
    throw "Use either -Area or -TestTarget, not both."
}
if ($Parallel -and $Tier -eq "Fast") {
    throw "-Parallel is supported only with -Tier Full or -Tier Evidence."
}

# HAMCP-356: an explicit -BaseRef always wins. Without one, the base is
# resolved in the "Verify base reference" step (Resolve-BaseReference).
$script:BaseRefExplicit = $PSBoundParameters.ContainsKey("BaseRef")
if ($script:BaseRefExplicit -and -not $BaseRef) {
    throw "-BaseRef must name a Git reference, for example origin/main or origin/release/2.6.0."
}
$GhPullRequestTimeoutMilliseconds = 10000
$GhAnswerBound = 4096
# A branch name safe to pass to gh and to append to origin/.
$SafeBranchNamePattern = '^(?!-)(?!.*\.\.)(?!.*//)(?!.*@\{)[A-Za-z0-9._/-]{1,200}(?<![./])$'

if (-not $PythonExecutable) {
    $pythonCommand = Get-Command python.exe, python -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($pythonCommand) {
        $PythonExecutable = $pythonCommand.Source
    }
    else {
        # HAMCP-356: with no -PythonExecutable and no python on PATH (for
        # example under systemd-run), use this worktree's Python 3.12 venv
        # from docs/CODEX_WORKFLOW.md "Sandbox-local scratch", and only if it
        # really is Python 3.12.
        $venvPython = if ($env:OS -eq "Windows_NT") {
            Join-Path $ArtifactsPath "venv\Scripts\python.exe"
        }
        else {
            Join-Path $ArtifactsPath "venv/bin/python"
        }
        if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
            throw "Python was not found on PATH and the worktree venv $venvPython does not exist. Provide -PythonExecutable with a Python 3.12 environment containing the repository test dependencies, or build .artifacts/venv (docs/CODEX_WORKFLOW.md, Sandbox-local scratch)."
        }
        $previousErrorPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = "Continue"
            $venvVersion = @(& $venvPython -I -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null | ForEach-Object { "$_" })
            $venvExit = $LASTEXITCODE
        }
        catch {
            $venvVersion = @()
            $venvExit = 1
        }
        finally {
            $ErrorActionPreference = $previousErrorPreference
        }
        $venvVersionText = [string]($venvVersion | Select-Object -First 1)
        if ($venvExit -ne 0 -or $venvVersion.Count -ne 1 -or $venvVersionText -ne "3.12") {
            $observed = if ($venvExit -eq 0 -and $venvVersionText -match '^[0-9]{1,2}\.[0-9]{1,3}$') { "Python $venvVersionText" } else { "an interpreter that did not report its version" }
            throw "Python was not found on PATH, and the worktree venv $venvPython is $observed, not Python 3.12. Rebuild .artifacts/venv with Python 3.12 or provide -PythonExecutable."
        }
        Write-Host "No -PythonExecutable and no python on PATH: using the worktree venv Python 3.12 at $venvPython."
        $PythonExecutable = $venvPython
    }
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

function Invoke-ShardedUnitTestStep {
    # Opt-in (-Parallel): CI's shard plan run as local workers. The helper
    # reuses scripts/unit_test_shards.py for assignment and the completeness
    # proof; this step passes only when the helper exits 0 and its summary
    # reports a proven, passing run for this exact head and worker count.
    $label = "Run complete unittest suite"
    # One owned receipt, overwritten under the helper's exclusive writer lock.
    $relativeRunDirectory = ".artifacts/unit-test-shards/latest"
    $arguments = @(
        "scripts/local_unit_test_shards.py", "run", "--workers", "$Parallel", "--output-dir", $relativeRunDirectory, "--replace-output"
    )
    Write-Step $label
    $commandText = Format-Command -Executable $PythonExecutable -Arguments $arguments
    Write-Host $commandText
    $timer = [Diagnostics.Stopwatch]::StartNew()
    $output = @()
    $exitCode = 127
    $previousErrorPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = @(& $PythonExecutable @arguments 2>&1 | ForEach-Object { "$_" })
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
    $problems = New-Object System.Collections.Generic.List[string]
    if ($exitCode -ne 0) {
        $problems.Add("sharded run exited $exitCode")
    }
    $execution = [ordered]@{
        mode = "sharded"
        sharded = $true
        workers = $Parallel
        executed = $true
        result = "failed"
        summary = "$relativeRunDirectory/summary.json"
    }
    $testCount = $null
    $skipped = $null
    $summaryPath = Join-Path $RepoRoot "$relativeRunDirectory/summary.json"
    try {
        if (-not (Test-Path -LiteralPath $summaryPath -PathType Leaf)) {
            throw "no sharded summary was written"
        }
        $summary = [IO.File]::ReadAllText($summaryPath) | ConvertFrom-Json
        foreach ($field in @("shard_count", "general_shards", "serial_shards", "run_id", "source_sha", "discovered_count", "discovered_sha256", "plan_sha256", "tests_run", "skipped")) {
            $execution[$field] = $summary.$field
        }
        if ($summary.result -ne "passed") {
            throw "sharded summary result is $($summary.result)"
        }
        if ($summary.mode -ne "sharded" -or [int]$summary.workers -ne $Parallel) {
            throw "sharded summary does not match the requested $Parallel worker(s)"
        }
        if ($null -eq $summary.tests_run -or $null -eq $summary.skipped -or $null -eq $summary.discovered_count) {
            throw "sharded summary has no proven totals"
        }
        if ($script:ResolvedHeadSha -and $summary.source_sha -ne $script:ResolvedHeadSha) {
            throw "sharded proof is bound to $($summary.source_sha), not the Evidence head"
        }
        $testCount = [int]$summary.tests_run
        $skipped = [int]$summary.skipped
    }
    catch {
        $problems.Add($_.Exception.Message)
    }
    $status = if ($problems.Count -eq 0) { "passed" } else { "failed" }
    if ($status -eq "failed" -and $exitCode -eq 0) {
        $exitCode = 1
    }
    $execution.result = $status
    $script:UnitTestExecution = $execution
    $note = "sharded workers=$Parallel"
    if ($status -eq "passed") {
        $note = "skipped_tests=$skipped; $note shards=$($execution.shard_count) serial_real_time_shards=$(@($execution.serial_shards) -join ',')"
    }
    else {
        $note = "$note; $($problems -join '; ')"
        Write-Host "Sharded unittest step failed: $($problems -join '; ')" -ForegroundColor Red
    }
    Add-Result -Name $label -Command $commandText -Status $status -ExitCode $exitCode -DurationSeconds $timer.Elapsed.TotalSeconds -TestCount $testCount -Note $note
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

function Read-GitIdentityState {
    param([string[]]$Arguments, [switch]$AllowMissing)
    # Never echo native stderr or identity values, even for malformed config.
    $previousErrorPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = @(& git -C $RepoRoot @Arguments 2>$null)
        $code = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $previousErrorPreference }
    if ($code -ne 0 -and -not ($AllowMissing -and $code -eq 1)) {
        throw "repository git identity could not be verified during Evidence"
    }
    return ($output -join "`n")
}

function Get-GitIdentitySnapshot {
    # Bind to this checkout, not an inherited GIT_DIR or command-scope config.
    # This is process-only environment isolation, never a config mutation.
    $saved = @{}
    foreach ($entry in @(Get-ChildItem Env: | Where-Object { $_.Name -like "GIT_*" })) {
        $saved[$entry.Name] = $entry.Value
        Remove-Item -LiteralPath "Env:$($entry.Name)"
    }
    try {
        if ($null -eq $script:GitIdentityPaths) {
            $common = Read-GitIdentityState -Arguments @("rev-parse", "--path-format=absolute", "--git-common-dir")
            $current = Read-GitIdentityState -Arguments @("rev-parse", "--absolute-git-dir")
            $script:GitIdentityPaths = @{
                local = Join-Path $common "config"
                worktree = Join-Path $current "config.worktree"
            }
        }
        $snapshot = @{}
        foreach ($scope in @("local", "worktree")) {
            $snapshot[$scope] = Read-GitIdentityState -AllowMissing -Arguments @(
                "config", "--null", "--show-origin", "--includes", "--file", $script:GitIdentityPaths[$scope],
                "--get-regexp", '^user\.(name|email)$'
            )
        }
        return $snapshot
    }
    finally {
        foreach ($name in $saved.Keys) { Set-Item -LiteralPath "Env:$name" -Value $saved[$name] }
    }
}

function Test-GitIdentitySnapshot {
    $current = Get-GitIdentitySnapshot
    $changed = @($script:GitIdentitySnapshot.Keys | Sort-Object | Where-Object {
        $current[$_] -cne $script:GitIdentitySnapshot[$_]
    })
    if ($changed.Count -gt 0) {
        throw "repository git identity changed during Evidence (scopes: $($changed -join ', ')); config NOT restored."
    }
}

function Set-ScratchGitCeilings {
    # Shared calculation with CI's scratch_session, also covering serial/Fast
    # unittest and Evidence scripts outside unittest. Restore caller env below.
    $value = @(& $PythonExecutable "scripts/unit_test_shards.py" ceilings --repo-root $RepoRoot 2>$null)
    if ($LASTEXITCODE -ne 0 -or $value.Count -ne 1 -or -not $value[0]) {
        throw "Could not establish scratch Git discovery boundaries."
    }
    $env:GIT_CEILING_DIRECTORIES = [string]$value[0]
}

function Get-ChangedPaths {
    $values = New-Object System.Collections.Generic.List[string]
    # HAMCP-356: compare only with the base commit pinned at the start.
    if (-not $script:ResolvedBaseSha) {
        throw "The base commit has not been pinned; refusing to compare changed paths."
    }
    $comparisonBase = $script:ResolvedBaseSha
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

function Invoke-GitProbe {
    # Like Invoke-GitRead, but a failing command returns $null instead of throwing.
    param([string[]]$Arguments)
    $previousErrorPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = @(& git -C $RepoRoot @Arguments 2>$null | ForEach-Object { "$_" })
        $exitCode = $LASTEXITCODE
    }
    catch {
        return $null
    }
    finally {
        $ErrorActionPreference = $previousErrorPreference
    }
    if ($exitCode -ne 0 -or $output.Count -eq 0) {
        return $null
    }
    return [string]$output[0]
}

function Get-PullRequestBase {
    # Ask gh for the open pull request whose head is this branch. Any reason gh
    # cannot answer (absent, unauthenticated, offline, no pull request, or no
    # answer within the timeout) returns $null with a note. An answer that is
    # inconsistent with the request is refused instead of trusted or ignored.
    param(
        [string]$Branch,
        [System.Collections.Generic.List[string]]$Notes
    )
    $gh = Get-Command gh -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $gh) {
        $Notes.Add("gh is not on PATH")
        return $null
    }
    $start = New-Object System.Diagnostics.ProcessStartInfo
    $start.FileName = $gh.Source
    $start.Arguments = "pr view $Branch --json number,state,baseRefName,headRefName,isCrossRepository"
    $start.WorkingDirectory = $RepoRoot
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardInput = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $start.EnvironmentVariables["GH_PROMPT_DISABLED"] = "1"
    $start.EnvironmentVariables["GH_NO_UPDATE_NOTIFIER"] = "1"
    $start.EnvironmentVariables["NO_COLOR"] = "1"
    try {
        $process = [System.Diagnostics.Process]::Start($start)
    }
    catch {
        $Notes.Add("gh could not be started")
        return $null
    }
    try {
        $process.StandardInput.Close()
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $null = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($GhPullRequestTimeoutMilliseconds)) {
            try { $process.Kill() } catch { }
            $Notes.Add("gh pr view did not answer within $($GhPullRequestTimeoutMilliseconds / 1000) s")
            return $null
        }
        $process.WaitForExit()
        if ($process.ExitCode -ne 0) {
            # gh's own message is not printed: it is not needed to choose a base.
            $Notes.Add("gh pr view $Branch failed with exit code $($process.ExitCode) (not authenticated, offline, or no pull request)")
            return $null
        }
        $text = [string]$stdout.Result
    }
    finally {
        $process.Dispose()
    }
    if ($text.Length -gt $GhAnswerBound) {
        throw "gh pr view returned more than $GhAnswerBound characters; refusing to choose a base. Pass -BaseRef."
    }
    try {
        $answer = $text | ConvertFrom-Json
    }
    catch {
        throw "gh pr view returned output that is not JSON; refusing to choose a base. Pass -BaseRef."
    }
    $names = @(if ($answer -is [System.Management.Automation.PSCustomObject]) { $answer.PSObject.Properties | ForEach-Object { $_.Name } })
    foreach ($name in @("number", "state", "baseRefName", "headRefName", "isCrossRepository")) {
        if ($names -notcontains $name) {
            throw "gh pr view did not report $name; refusing to choose a base. Pass -BaseRef."
        }
    }
    if (-not ($answer.state -is [string]) -or $answer.state -cnotmatch '^[A-Z]{1,20}$') {
        throw "gh pr view reported an unusable pull-request state; refusing to choose a base. Pass -BaseRef."
    }
    if ($answer.state -cne "OPEN") {
        $Notes.Add("the pull request for $Branch is not open")
        return $null
    }
    if (-not ($answer.number -is [int] -or $answer.number -is [long]) -or $answer.number -le 0) {
        throw "gh pr view reported an invalid pull-request number; refusing to choose a base. Pass -BaseRef."
    }
    if (-not ($answer.headRefName -is [string]) -or $answer.headRefName -cne $Branch -or -not ($answer.isCrossRepository -is [bool]) -or $answer.isCrossRepository) {
        throw "gh pr view reported pull request #$($answer.number), which is not this repository's branch $Branch; refusing to choose a base. Pass -BaseRef."
    }
    if (-not ($answer.baseRefName -is [string]) -or $answer.baseRefName -cnotmatch $SafeBranchNamePattern) {
        throw "gh pr view reported an unusable base branch for pull request #$($answer.number); refusing to choose a base. Pass -BaseRef."
    }
    return [ordered]@{ ref = "origin/$($answer.baseRefName)"; source = "base of pull request #$($answer.number)" }
}

function Resolve-BaseReference {
    if ($script:BaseRefExplicit) {
        Write-Host "Base reference $BaseRef (explicit -BaseRef)."
        return
    }
    $notes = New-Object System.Collections.Generic.List[string]
    $found = $null
    $branch = Invoke-GitProbe -Arguments @("symbolic-ref", "--quiet", "--short", "HEAD")
    if (-not $branch) {
        $notes.Add("HEAD is detached")
    }
    elseif ($branch -cnotmatch $SafeBranchNamePattern) {
        $notes.Add("the branch name is not one this script passes to gh")
    }
    else {
        $found = Get-PullRequestBase -Branch $branch -Notes $notes
        if (-not $found) {
            # Offline fallback: an upstream that is main or a release branch,
            # never the branch's own pushed copy (git push -u resets upstream).
            $upstream = Invoke-GitProbe -Arguments @("rev-parse", "--abbrev-ref", "--symbolic-full-name", '@{upstream}')
            if ($upstream -and $upstream -cmatch '^origin/(main|release/[A-Za-z0-9._-]{1,100})$') {
                if ($Matches[1] -cne $branch) {
                    $found = [ordered]@{ ref = $upstream; source = "upstream of branch $branch" }
                }
                else {
                    $notes.Add("the branch upstream $upstream is the branch's own remote copy")
                }
            }
            elseif ($upstream) {
                $notes.Add("the branch upstream $upstream is not origin/main or an origin/release/* branch")
            }
            else {
                $notes.Add("the branch has no upstream")
            }
        }
    }
    if ($found) {
        if (-not (Invoke-GitProbe -Arguments @("rev-parse", "--verify", "--quiet", "$($found.ref)^{commit}"))) {
            throw "The $($found.source) is $($found.ref), which is not available locally. Fetch it (git fetch origin) or pass -BaseRef."
        }
        $script:BaseRef = $found.ref
        Write-Host "Base reference $BaseRef (detected: $($found.source); pass -BaseRef to override)."
        return
    }
    $script:BaseRef = "origin/main"
    Write-Host "Base reference origin/main (default: no pull-request base was detected because $($notes -join '; ')). Pass -BaseRef if the pull request targets another branch." -ForegroundColor Yellow
}

function Test-BaseReference {
    Resolve-BaseReference
    $resolved = @(Invoke-GitRead -Arguments @("rev-parse", "--verify", "$BaseRef^{commit}"))
    $script:ResolvedBaseSha = [string]($resolved | Select-Object -First 1)
    if (-not $script:ResolvedBaseSha) {
        throw "Git returned no commit for base reference $BaseRef."
    }
    Write-Host "Verified base commit $BaseRef at $($script:ResolvedBaseSha)."
    Write-Host "Every comparison in this run uses $($script:ResolvedBaseSha); a later move of $BaseRef does not change it."
    $versionBase = Invoke-GitProbe -Arguments @("rev-parse", "--verify", "--quiet", "$VersionCheckBaseRef^{commit}")
    if (-not $versionBase -or $versionBase -notmatch '^[0-9a-f]{40}([0-9a-f]{24})?$') {
        throw "The version-check base $VersionCheckBaseRef does not resolve to a commit. CI checks the deployment-metadata version against $VersionCheckBaseRef; refusing to validate deployment metadata against another base. Fetch it (git fetch origin main)."
    }
    $script:VersionCheckBaseSha = $versionBase
    Write-Host "Version-check base $VersionCheckBaseRef at $($script:VersionCheckBaseSha) (deployment-metadata version check, as in CI)."
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
    # HAMCP-356: the run used only the pinned base commits, so a reference
    # that moved meanwhile (a fetch in another worktree) does not change the
    # result. The pinned commits must still exist.
    foreach ($pinned in @($script:ResolvedBaseSha, $script:VersionCheckBaseSha)) {
        if (-not (Invoke-GitProbe -Arguments @("rev-parse", "--verify", "--quiet", "$pinned^{commit}"))) {
            throw "The pinned base commit $pinned is no longer available."
        }
    }
    foreach ($pair in @(@($BaseRef, $script:ResolvedBaseSha), @($VersionCheckBaseRef, $script:VersionCheckBaseSha))) {
        $current = Invoke-GitProbe -Arguments @("rev-parse", "--verify", "--quiet", "$($pair[0])^{commit}")
        if ($current -ne $pair[1]) {
            $now = if ($current) { $current } else { "nothing" }
            Write-Host "$($pair[0]) moved to $now during the run; every comparison used the pinned base $($pair[1])." -ForegroundColor Yellow
        }
    }
    $currentHead = [string]((Invoke-GitRead -Arguments @("rev-parse", "--verify", "HEAD^{commit}")) | Select-Object -First 1)
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
        return "retired stable v1.1.2"
    }
    if ($Path.StartsWith("hass_mcp_engineering_beta/ha_mcp_engineering/", [StringComparison]::Ordinal)) {
        return "Engineering runtime"
    }
    if ($Path -match '^\.github/workflows/.+\.ya?ml$') {
        return "workflow authority"
    }
    if ($Path.StartsWith(".github/codex/", [StringComparison]::Ordinal)) {
        return "independent-review authority"
    }
    if ($Path -eq ".coderabbit.yaml") {
        return "CodeRabbit review authority"
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
        $Path.StartsWith(".github/codex/", [StringComparison]::Ordinal) -or
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
        # HAMCP-305: a key's lowercase sk- prefix starts a token, and its
        # random body has a digit or capital. Neither the ADR-012 filename's
        # RISK-AND-ELEVATED-APPROVAL nor a lowercase kebab-case anchor matches.
        # The scoped (?-i:...) group also parses in Python re, which the
        # committed-file scan tests compile these patterns with.
        '(?-i:(?<![A-Za-z0-9])sk-(?=[A-Za-z0-9_-]*[0-9A-Z])[A-Za-z0-9_-]{20,})',
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
        version_check_base_ref = $VersionCheckBaseRef
        version_check_base_sha = $script:VersionCheckBaseSha
        head_sha = $headSha
        working_tree = $workingTree
        unit_test_execution = $script:UnitTestExecution
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
    $versionBaseShaText = if ($script:VersionCheckBaseSha) { $script:VersionCheckBaseSha } else { "unresolved" }
    $summary.Add("- Version-check base: ``$VersionCheckBaseRef`` at ``$versionBaseShaText``")
    $summary.Add("- Head: ``$headShaText``")
    $summary.Add("- Working tree: **$workingTree**")
    $authorizedPathText = if ($script:NormalizedAuthorizedProtectedPaths.Count -gt 0) {
        $script:NormalizedAuthorizedProtectedPaths -join ", "
    }
    else {
        "none"
    }
    $summary.Add("- Authorized protected paths: $authorizedPathText")
    $execution = $script:UnitTestExecution
    $executionText = if ($execution.sharded) {
        "sharded, $($execution.workers) worker(s)"
    }
    else {
        "single process"
    }
    if (-not $execution.executed) {
        $executionText += " (not executed)"
    }
    elseif ($execution.sharded -and $execution.Contains("shard_count")) {
        $executionText += ", $($execution.shard_count) shards, real-time shard(s) $(@($execution.serial_shards) -join ',') run serially; result $($execution.result)"
    }
    $summary.Add("- Unit test execution: $executionText")
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
    if ($Tier -eq "Evidence") {
        Invoke-LocalStep -Label "Snapshot repository Git identity" -CommandText "capture local/worktree identity without printing values" -Action {
            $script:GitIdentitySnapshot = Get-GitIdentitySnapshot
        }
    }
    $baseCommandText = if ($script:BaseRefExplicit) { "git rev-parse --verify $BaseRef^{commit}" } else { "resolve the pull-request base (gh, then upstream, then origin/main), then git rev-parse --verify <base>^{commit}" }
    $baseCommandText += "; pin the version-check base with git rev-parse --verify $VersionCheckBaseRef^{commit}"
    Invoke-LocalStep -Label "Verify base reference" -CommandText $baseCommandText -Action { Test-BaseReference }
    if (-not $script:HadFailure -and $Tier -eq "Evidence") {
        Invoke-LocalStep -Label "Bind clean Evidence snapshot" -CommandText "resolve HEAD and require a clean working tree" -Action { Test-EvidenceSnapshot }
    }
    if (-not $script:HadFailure) {
        Invoke-LocalStep -Label "Verify protected-path scope" -CommandText "compare protected paths and explicit declarations against $BaseRef at $($script:ResolvedBaseSha)" -Action { Test-ScopeBoundaries }
    }
    if ($script:HadFailure) {
        Write-Host "`nValidation stopped during safe preflight. No repository code was executed." -ForegroundColor Red
        exit 1
    }

    Invoke-LocalStep -Label "Bound scratch Git discovery" -CommandText "set child-process Git ceilings for scratch" -Action { Set-ScratchGitCeilings }
    if ($script:HadFailure) { throw "Scratch Git boundary setup failed; validation not started." }

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
            "hass_mcp_admin/retired-addon-config.yaml",
            "hass_mcp_engineering_beta/config.yaml"
        )
        if ($Area -eq "Metadata" -or @($fastChangedPaths | Where-Object { $_ -in $metadataPaths }).Count -gt 0) {
            Invoke-NativeStep -Label "Validate add-on metadata" -Executable $PythonExecutable -Arguments @(
                "scripts/validate_addon_metadata.py", "--repo-root", ".", "--base-ref", $script:VersionCheckBaseSha
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
        if ($Parallel) {
            Invoke-ShardedUnitTestStep
        }
        else {
            Invoke-NativeStep -Label "Run complete unittest suite" -Executable $PythonExecutable -Arguments @(
                "-m", "unittest", "discover", "-s", "tests", "-v"
            )
            $script:UnitTestExecution.executed = $true
        }
        Invoke-NativeStep -Label "Validate add-on metadata" -Executable $PythonExecutable -Arguments @(
            "scripts/validate_addon_metadata.py", "--repo-root", ".", "--base-ref", $script:VersionCheckBaseSha
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
    }
}
catch {
    $script:HadFailure = $true
    throw
}
finally {
    try {
        if ($Tier -eq "Evidence") {
            if ($null -ne $script:GitIdentitySnapshot) {
                Invoke-LocalStep -Label "Recheck repository Git identity" -CommandText "compare local/worktree identity without restoring config" -Action { Test-GitIdentitySnapshot }
            }
            Write-Evidence
        }
    }
    finally {
        if ($null -eq $previousGitCeilings) { Remove-Item Env:GIT_CEILING_DIRECTORIES -ErrorAction SilentlyContinue }
        else { $env:GIT_CEILING_DIRECTORIES = $previousGitCeilings }
        Pop-Location
    }
}

if ($script:HadFailure) {
    Write-Host "`nValidation failed. Review the failed steps above." -ForegroundColor Red
    exit 1
}

Write-Host "`n$Tier validation passed." -ForegroundColor Green
