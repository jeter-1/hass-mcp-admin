# Codex Development Workflow

Repository policy lives in [`../AGENTS.md`](../AGENTS.md). This guide is the
operator playbook for applying that policy locally or remotely.

Codex in the ChatGPT desktop app supports
[local environments](https://learn.chatgpt.com/docs/environments/local-environment)
with worktree setup scripts and reusable actions. Configure them in the app;
Codex stores the generated configuration in the repository-root `.codex/`
folder, where it can be checked in and shared. This repository does not
currently supply that configuration, so use the explicit, testable commands
below.

## Keyboard Workflow

1. Open the repository or a clean Codex-managed worktree. Use one worktree and
   one chat per logical pull request.
2. Fetch `origin` when authorized, then run:

   ```powershell
   python scripts/codex-context.py --format markdown
   ```

3. Confirm the exact `origin/main` base, HEAD, branch, worktree state, stable and
   Engineering versions, derived tool-count expectations, and active acceptance
   document. Stop if the base moved unexpectedly or the context reports an
   unexplained inconsistency.
4. Inspect before editing. During implementation run a supported focused area,
   for example:

   ```powershell
   .\scripts\check.ps1 -Tier Fast -Area Workflow
   .\scripts\check.ps1 -Tier Fast -TestTarget tests.test_readonly_upstream_gateway
   ```

   Use an explicit test target for source areas outside the documented workflow
   areas; bounded inference stops instead of guessing when a path has no safe
   mapping.

5. Review `git diff`, `git diff --cached`, and the base-to-HEAD diff. Resolve
   security-boundary, compatibility, and unsupported-evidence concerns.
6. Before pushing, run one of:

   ```powershell
   .\scripts\check.ps1 -Tier Full
   .\scripts\check.ps1 -Tier Evidence
   ```

   If the external task explicitly names a protected file or subtree, declare
   that exact repository-relative scope on any tier, for example:

   ```powershell
   .\scripts\check.ps1 -Tier Full -AuthorizedProtectedPath 'hass_mcp_engineering_beta/ha_mcp_engineering/provider.py'
   ```

   Directory declarations must end in `/`. Pass a PowerShell array for multiple
   paths:

   ```powershell
   .\scripts\check.ps1 -Tier Full -AuthorizedProtectedPath @(
       'hass_mcp_admin/example.py',
       '.github/workflows/ci.yml'
   )
   ```

   The wrapper rejects unmatched, unused, absolute, parent-relative, and wildcard
   declarations. This parameter records bounded task scope for human review; it
   does not grant authorization or waive tests, review, release, or deployment
   restrictions.

7. Generate or refresh the bounded draft body if needed:

   ```powershell
   python scripts/pr-evidence.py --base origin/main --head HEAD --output .artifacts/pr-evidence.md
   ```

8. Push only the named task branch and open a draft pull request. Stop before
   merge, release, publication, promotion, or deployment.

If `python` is not on PATH, pass the trusted interpreter explicitly to
`check.ps1` with `-PythonExecutable` and use that same interpreter for the Python
commands. If Windows policy blocks repository scripts, use an approved
process-scoped invocation such as `powershell.exe -NoProfile -ExecutionPolicy
Bypass -File .\scripts\check.ps1 -Tier Fast`; do not weaken the machine-wide
policy.

## Remote and Mobile Workflow

- For connected-host work, leave the trusted Windows host online, at a clean
  named worktree, with dependencies already available. Prefer repository-contained
  cloud work when local Windows state is unnecessary.
- Preauthorize only the named branch push and draft-PR creation. Do not
  preauthorize merge, release, image publication, deployment, secret changes, or
  live-Home-Assistant access.
- Stop when the base moved unexpectedly, the environment is incomplete,
  unrelated failures appear, or the requested work would cross a provider,
  permission, runtime, release, or deployment trust boundary.
- Return exact local and CI evidence. Do not improvise around missing permissions
  or describe an unavailable/CI-only check as passed.

## Authorization Profiles

These are distinct profiles; “Codex access” is not one universal permission.

1. **Read-only review** - inspect source, history, diffs, and existing remote
   metadata without editing or external writes.
2. **Local implementation** - edit the named worktree and run non-destructive
   offline validation; no push or other external write.
3. **Named-branch draft preparation** - profile 2 plus push of one named branch
   and creation/update of its draft PR; no ready, approval, merge, or release.
4. **Release or deployment** - separately authorized, tightly scoped publication,
   signing, promotion, or deployment work with its own evidence and stop points.

## Reusable Task Templates

### Implementation

> From current `origin/main`, create one clean worktree and branch for `<scope>`.
> Report base/HEAD/version context, inspect before editing, make the smallest
> coherent change, run Fast during development and Full/Evidence before pushing,
> open a draft PR, and stop before merge/release/deployment. Do not cross `<named
> trust boundaries>`.

### Independent Review

> Review `<branch or draft PR>` against its exact base. Apply the root Code Review
> Rules, classify findings Critical/High/Medium/Low, and provide evidence, impact,
> cause, correction, and a proving test. Do not edit, approve, merge, release, or
> deploy.

### Corrective Follow-up

> On `<existing draft branch>`, verify each accepted review finding against
> source, implement only attributable corrections, add regression coverage, rerun
> the affected Fast area and Full/Evidence, update the draft PR, and stop.

### Remote Implementation

> Use the prepared trusted host/worktree for `<scope>`. Confirm the base has not
> moved. External writes are limited to pushing `<exact branch>` and creating or
> updating its draft PR. Stop on missing environment, unrelated failure, moved
> base, secret need, or trust-boundary expansion.

### Release Preparation

> Prepare release evidence for `<version>` without publishing. Verify authoritative
> version declarations, active acceptance guidance, image/tag/provenance
> preconditions, and rollback. Report CI-only checks accurately and stop for a
> separate human publication/deployment decision.
