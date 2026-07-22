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

## Instruction Discovery

Codex builds its instruction chain from the project root through the session's
current working directory. Opening a session at the repository root reliably
loads the root `AGENTS.md`, but a root session does not automatically load a
nested instruction file merely because it later reads or edits a file in that
subtree. Before specialized work from a root session, explicitly read the
applicable nested file mapped in the root **Subtree Instructions** section into
the task context and follow it.

Starting a session with its current working directory inside a subtree can
include the applicable nested instructions in the normal discovery chain from
the project root to that current directory. Do not rely on file-edit location
alone to activate nested guidance. This rule applies equally to Desktop keyboard
work, CLI or IDE work, connected-host Remote or mobile work, and Codex cloud work
when the environment starts at the repository root.

## Keyboard Workflow

1. Open the repository or a clean Codex-managed worktree. Use one worktree and
   one chat per logical pull request. Before working in Engineering runtime,
   tests, or workflow files, read the corresponding nested `AGENTS.md` listed in
   the root **Subtree Instructions** section.
2. Fetch `origin` when authorized, then run:

   ```powershell
   python scripts/codex-context.py --format markdown
   ```

3. Confirm the exact `origin/main` base, HEAD, branch, worktree state, stable and
   Engineering versions, and derived tool-count expectations. For release or
   deployment work, continue only when document `resolution_status` is `exact`
   and `active_acceptance_document` is known. A `missing`, `partial`,
   `unsupported`, or unknown result is a stop condition. Historical references
   cannot authorize current acceptance, and release notes are not acceptance
   instructions. Also stop if the base moved unexpectedly or the context reports
   an unexplained inconsistency.
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

   `-Tier Evidence` runs the full local gate and writes schema-v2 validation
   evidence. It does not generate a PR draft; `scripts/pr-evidence.py` is the
   separate bounded PR-draft generator.

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

   The generator trusts only schema-v2 Evidence from this exact clean repository
   root, base reference and commit, and head commit. If the artifact is stale,
   foreign, malformed, from another tier, or the working tree is dirty, the draft
   reports local validation as unavailable. Rerun the Evidence tier after the
   final commit before generating the draft body. These checks prevent accidental
   stale or foreign reuse; the local JSON is not cryptographically signed and can
   still be forged by someone who can edit it.

8. Push only the named task branch and open a draft pull request. Stop before
   merge, release, publication, promotion, or deployment.

If `python` is not on PATH, pass the trusted interpreter explicitly to
`check.ps1` with `-PythonExecutable` and use that same interpreter for the Python
commands. If Windows policy blocks repository scripts, use an approved
process-scoped invocation such as `powershell.exe -NoProfile -ExecutionPolicy
Bypass -File .\scripts\check.ps1 -Tier Fast`; do not weaken the machine-wide
policy.

## GitHub Authentication Readiness

### Local Windows and connected-host Remote

Local Windows work and connected-host Remote work use the trusted host's GitHub
CLI and Git credentials. On that Windows host, run only these bounded readiness
checks:

```powershell
gh auth status
git fetch --prune origin
gh repo view jeter-1/hass-mcp-admin
```

Run them before leaving the keyboard for remote work, before authorizing a remote
branch push or draft-PR creation, and whenever authentication may have expired.
Missing or expired authentication is an environment-readiness failure: stop and
authenticate interactively on the trusted Windows host, for example with
`gh auth login`. Do not work around the failure by putting a token in a prompt or
script.

Authentication checks may report status and account identity, but not secret
values. Never print or export GitHub tokens; read or dump Git credential-store
contents; read or copy SSH private keys; commit credentials; put tokens in
`AGENTS.md`, prompts, setup scripts, repository files, or checked-in environment
files; or ask Codex to expose credential material. This repository must not add
credential automation or a `.codex` configuration merely for authentication.

### Codex cloud

Codex cloud uses its separately authorized GitHub connection. It does not inherit
the Windows host's GitHub CLI login, so connected-host readiness and Codex-cloud
authorization are distinct checks. Configure cloud repository access through the
Codex GitHub connection; do not add credentials or authentication automation to
this repository.

## Remote and Mobile Workflow

- For connected-host work, leave the trusted Windows host online, at a clean
  named worktree, with dependencies already available. Prefer repository-contained
  cloud work when local Windows state is unnecessary.
- Do not authorize remote work in a specialized subtree until the applicable
  nested instruction file has been read into the task context.
- Before release or deployment preparation, run `codex-context.py` and continue
  only when document resolution is `exact` and `active_acceptance_document` is
  known. Missing exact acceptance authority is a stop condition. Historical
  references cannot authorize current acceptance, and release notes are not
  acceptance instructions.
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
> version declarations, require document resolution to be `exact`, and read only
> the known `active_acceptance_document` as acceptance authority. Missing exact
> acceptance is a stop condition; historical references cannot authorize current
> acceptance, and release notes are not acceptance instructions. Verify
> image/tag/provenance preconditions and rollback, report CI-only checks
> accurately, and stop for a separate human publication/deployment decision.
