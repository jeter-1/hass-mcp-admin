# Codex Development Workflow

Engineering packaging follows [controlled build inputs](BUILD_INPUTS.md): use
complete hash locks, retain both architecture inventories/smoke results, and
verify source-bound base/runtime evidence before publication. Historical
artifacts retain their original contracts. Packaging changes still require an
authorized release transition; passing checks never authorize republishing an
existing version.

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

## Home Assistant source authority

Before making or reviewing a claim about Home Assistant semantics, browser
frontend or dashboard behavior, a delegated `ha-mcp` contract, MCP protocol or
pinned-SDK behavior, webhook and Home Assistant Cloud transport ownership,
Supervisor behavior, Engineering container construction or provenance, or the
HAOS platform, use the boundary-specific hierarchy in
[`ADR-021`](architecture/ADR-021-HOME-ASSISTANT-SOURCE-AUTHORITY.md). Record the
exact upstream repository, tag or commit, relevant paths, and supported-version
relationship in review evidence. Moving branches, documentation examples, and
skills are useful discovery or guidance, but they do not replace exact source or
executable validation.

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
   Engineering versions, staged version, and derived tool-count expectations.
   For deployment of the advertised version, require `documents` resolution to
   be `exact` and `documents.active_acceptance_document` to be known. For
   promotion of a declared next version, require `staged_release.documents`
   resolution to be `exact` and its `active_acceptance_document` to be known.
   A `missing`, `partial`, `unsupported`, or unknown result is a stop condition.
   Historical references cannot authorize current acceptance, and release notes
   are not acceptance instructions. Also stop if the base moved unexpectedly or
   the context reports an unexplained inconsistency.
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

8. Push only the named task branch and open a draft pull request. Agents stop
   before marking it ready, merging, publication, or deployment. Josh may mark
   the same-repository pull request ready after its final state is reviewable;
   that action authorizes the bounded review and exact-head merge contract
   described below.

For a pull request that declares `.release/next-version`, materialize the release
state in that same branch before the pull request is marked ready:

```powershell
python scripts/promote_next_release.py
python scripts/promote_next_release.py --apply
```

Review and commit the resulting authoritative version updates and deletion of
`.release/next-version`, rerun Full/Evidence, and update the draft. Pull-request
CI fails closed while an unmaterialized declaration remains, so a second
promotion pull request is neither created nor required.

## Required CI check

The public required check remains `validate`. It is an aggregate over every
required CI job family, not the source/unit-test worker alone:

- `validate_prerequisites`: source, dependency audit, historical compatibility
  fixtures, metadata/release and syntax checks;
- `validate_source`: complete unit discovery after successful prerequisites;
- `validate_packaging`: frozen-v1 and Engineering packaging, embedded build
  identity and all declared architectures, after successful prerequisites;
- `real-ha-contract-tests`: every configured disposable Core contract lane;
- `prepare_exact_image_matrix`: reviewed gateway-matrix preparation;
- `exact-image-read-gateway`: every prepared exact-image gateway lane; and
- `exact-addon-runtime-acceptance`: every configured exact add-on runtime lane.

The aggregate and its result-checking step use `always()` so a failed dependency
does not merely skip the required gate. Every expected job must report exactly
`success`. Failure, cancellation, an unexpected skip, missing/malformed results,
or a different result set makes the gate fail. A final cancellation-refusal step
uses `cancelled()` in its supported step condition and fails the gate even if
the dependency result check succeeded. Matrix preparation is independently
required; its success cannot override a failed or skipped gateway job. Existing
matrix coverage and failure collection remain unchanged.

Ordinary code and documentation-only pull requests run the same required jobs;
there is no path-based exemption. Intentional version-specific step skips inside
a successful matrix job remain distinct from a skipped required job family.
Candidate evidence must identify the unique `validate` check, its actual producer
and CI run, and the exact PR/base/head or verified synthetic merge checkout.
The required status must be bound in the main ruleset to the observed GitHub
Actions app (`integration_id: 15368`). This settings change is separate from the
aggregate implementation and requires explicit owner authorization. Preserve the
exported prior ruleset and verify that no other rule, bypass, or enforcement
setting changes. App binding alone does not identify the owning workflow.

The protected-base `scripts/validate_ci_provenance.py` therefore verifies the
unique check's app, PR/base/head association, and check suite; resolves that
suite through this repository's `.github/workflows/ci.yml`; and binds its current
run attempt's `validate` job to the exact check. Only a successful PR-triggered
CI run and job for the authorized base/head are accepted. Another app or
workflow, foreign repository, ambiguous/incomplete evidence, obsolete attempt,
unsuccessful conclusion, or changed evidence refuses. Only an absent or pending
check/run may remain pending within the existing bounded wait. GitHub API reads
are limited to fixed GET routes, 100-item complete collections, 4 MB responses,
and 30 seconds per request, with secret-safe failure categories. The merge job
adds only `actions: read` for this metadata; existing merge permissions remain.

The provenance check runs again immediately before the head-matched merge, after
Ready/lifecycle and current PR revalidation. GitHub's strict base protection and
required checks still enforce the final merge boundary; separate API reads are
not an atomic transaction. Rollout evidence must distinguish the active ruleset
binding from the workflow change, which takes effect only after its reviewed
merge. A draft PR's passing fixtures do not prove a live negative merge test.

The publisher continues to call the entire reusable CI workflow before release
detection or publication. Its enclosing job is also named `validate`; it is not
the renamed `validate_source` worker. The aggregate adds no publishing authority.

### CI scheduling and bounds

Standalone PR CI uses a repository/PR-number concurrency group in the dedicated
`hass-mcp-ci-` namespace. A newer run replaces obsolete validation for that PR;
another PR has a different group even when its branch name matches. The guard
checks the PR event and exact caller workflow reference for `ci.yml`. Reusable
workflows inherit their caller's GitHub context, so the guard does not infer
standalone CI from the display name or a hypothetical `workflow_call` event.

Other invocations use a run-ID/attempt-specific group and disable in-progress
cancellation. Unique groups also prevent replacement of pending publication or
recovery validation; `cancel-in-progress: false` alone would not prevent that
replacement in a shared group. Publication retains its separate serialized
concurrency policy. No triggers, merge authority or permissions are added.

After prerequisites succeed, complete unit discovery and packaging run in
parallel. Every worker checks out the same event revision without overriding
the ref. Source tests install the same declared dependencies in their own job;
packaging uses its existing Docker builds. Fixture regeneration and generated
registry comparison finish inside the prerequisite job and require no transfer
to later workers. No built image or validation result is reused from another run.

Publication may reuse the successful full unittest discovery from its own
completed reusable CI call. `scripts/publication_validation.py` binds the job
output to the repository, run and attempt, caller workflow/ref/commit, event,
tested source commit/tree, Python version, both dependency locks and the verifier
and workflow files. The publisher requires complete CI success independently,
checks the lock actually selected for its dependency installation, and requires a
clean release checkout at that same commit. Missing, malformed, failed, skipped,
canceled or mismatched evidence refuses publication; it is not permission to
replace complete CI with an isolated test run. This receipt is trusted job output
within the existing protected workflow, not an independent signature.

When historical recovery selects a different release commit, full discovery runs
on that checkout even if its tree happens to match. The verifier is loaded from
the current protected workflow-authority commit, so an older release need not
contain it. Release metadata, ancestry, staging, version, artifact-absence, image
and provenance guards still run. The publisher records whether discovery was
reused or executed; reuse must not be counted as a second test execution. Actual
wall-time savings require a subsequent authorized publication observation.

Explicit limits are 15 minutes for prerequisites, 40 for unit discovery, 30 for
packaging and 10 for matrix preparation. These allow headroom over the inspected
successful pre-change CI run 34798268513: about 16 minutes for unit discovery and
seven minutes for the following packaging steps, with short prerequisite and
matrix-preparation work. Existing 50/25/20-minute runtime-family limits and the
five-minute aggregate limit are unchanged. A timeout cannot satisfy `validate`.
Existing exit-trap and `always()` cleanup remain; forced runner termination can
prevent cleanup completion and must not be reported as verified cleanup.

Measure elapsed time, unit/build overlap, summed job durations and cancellation
of obsolete work separately. Parallel jobs can reduce elapsed time while adding
setup work and runner usage. Offline scheduling tests establish the intended
policy; they do not establish GitHub cancellation behavior or billed savings.

If a gate correction is needed, keep the failed evidence and use a reviewed
corrective or revert PR with all checks intact. Before rolling out a gate change,
identify Josh's existing repository-admin recovery access. If a malformed gate
cannot validate its own correction, stop and prepare an exact, separately
authorized owner recovery covering the affected protection, correction SHA,
restoration and verification. Do not automatically disable the gate, use an
administrative merge bypass, or infer that reverting source undoes publication
or deployment.

## Independent review and Ready automation

Review begins only after implementation reaches a stable candidate and
Full/Evidence validation has completed. Use this bounded default sequence:

1. A separate Codex session or worktree reviews the exact base/head without
   editing. Findings state severity, file-and-line evidence, impact, cause,
   correction, and a proving test.
2. The implementer verifies and batches accepted findings into one correction
   pass.
3. One focused delta rereview verifies every accepted finding, the
   reviewed-head-to-final-head delta, and affected surrounding contracts. A full
   rereview is warranted only when corrections materially alter architecture or
   scope.
4. Provider, write, workflow, cryptographic, persistence, and release-authority
   changes include a security section in that independent review. A second
   specialized reviewer is exceptional rather than the default.

The implementer must not serve as its own repeated independent reviewer. The
default review budget is one full independent review and at most one delta
rereview, not a fresh exact-head review after every corrective commit.

Native Codex and CodeRabbit remain available as optional manual escalation
tools. Neither runs automatically and neither is a merge prerequisite. Josh may
request native Codex by posting the exact comment `@codex review`; the optional
`codex-review-receipt` observer then validates GitHub evidence for the exact
current head. It does not run a model itself, authorize merge, replace Josh's
Ready action, parse review prose as executable data, accept stale evidence, or
count a setup notice as completed review. The observer and validator load only
from the protected base commit, never candidate code. Each observer is isolated
by the exact Josh-authored request comment, so the native Codex summary comment
cannot cancel the observer that is waiting for it. CodeRabbit automatic and
incremental reviews are disabled; `@coderabbitai review` remains available for
a manual review. CodeRabbit Autofix, CI fixing, merge-conflict resolution,
branch-writing finishing touches, unsolicited chat, web search, knowledge
retention, Jira/Linear integrations, and code-generation features remain
disabled.

Josh's `Ready for review` action on a same-repository `jeter-1` pull request
targeting `main` attests that the independent review is complete and acceptable.
Ready does not start another CI run for an unchanged head. The authorization
workflow reuses the existing exact-head aggregate `validate` result produced when
the pull request was opened, reopened, or synchronized; a missing, failed, stale-base, or
ambiguous result remains a hard stop.
The protected-base authorization workflow verifies the repository, base, actor,
and exact authorized head, waits only for that head's deterministic `validate`
check whose producer and owning CI attempt are verified and whose pull-request
association is bound to the same protected base and head, then revalidates the complete Ready lifecycle, open/non-draft disposition,
protected base commit, same-repository head, and exact head immediately before
one non-administrative, head-matched merge. A new base therefore requires a new
base-bound validation result. Remaining required statuses and review-thread
resolution stay enforced by GitHub at that merge boundary. The workflow never
force-pushes and never polls model-review evidence.

The workflow deliberately does not leave a persistent native auto-merge request
armed while checks run. GitHub guarantees automatic revocation after a head push
only when the pusher lacks write permission; therefore persistent auto-merge is
not a commit-bound authorization for this same-repository owner workflow. See
GitHub's [auto-merge behavior](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/automatically-merging-a-pull-request).

A push, force-push, draft conversion, closure/reopening, head restoration, or
base change withdraws Ready authorization, cancels the in-flight merge run, and
disarms any persistent auto-merge request inherited from the retired workflow.
The corrected head must receive the bounded delta rereview before Josh marks it
Ready again. An exact Josh-authored `@merge` comment may retry a transient merge
workflow failure only when the latest applicable Ready action still authorizes
the unchanged current head. It does not request or directly inspect model review
evidence. `@codex review` requests only the optional review receipt and cannot
authorize merge.

If the reviewed merge includes a final Engineering version transition, the
protected-main publication workflow validates and publishes that exact merge
commit. Because materialization occurred before review, publication does not
trigger another model review. It does not deploy the add-on or modify Home
Assistant.

The publisher also receives the successful completion of `Merge owner-authorized
pull request` through `workflow_run`. This bridges the `GITHUB_TOKEN` push-event
suppression without another credential or routine owner approval. The merge
workflow retains a bounded JSON receipt in an immutable, run/attempt-named
artifact. A read-only publisher job independently verifies the workflow ID/path,
repository, source event, owner and rerun actor, completed merge job, current
attempt, artifact digest, PR, Ready lifecycle, reviewed base/head, two-parent
merge and recomputed tree. It compares the producer policy with protected base
policy. The exact merge must remain on main's first-parent history; Engineering
source and staged state must agree with current authority. The receipt is data,
not executable code or a substitute for these checks.

An ordinary merge has no version transition and produces no publication writes.
A release handoff must pass complete CI, exact release-document authority,
materialized version/metadata checks and all existing publisher guards. Protected
main is fenced again before registry access, final image tags, the release Git
tag and GitHub Release. Movement stops the run; a later owner-authorized recovery
can reconcile the same release SHA. Publication never installs or deploys.

The workflow installation itself may be merged by the older producer, which has
no handoff artifact. That receiving run must refuse rather than invent a receipt.
The first controlled automatic publication is the next owner-approved release
whose protected base already contains this producer. Offline tests and PR CI do
not establish that the cross-workflow GitHub event has run successfully.

This workflow pull request must cross the preexisting receipt-required pipeline
one final time. After it merges, removing `codex-review-receipt` from the active
ruleset and changing the repository's native Codex setting from automatic to
manual/off are separate administrative actions requiring Josh's authorization.
Until both are verified, do not claim that the manual-review steady state is
fully active.

### Missed protected-main publication recovery

GitHub does not start new workflow runs for most events created with the
repository `GITHUB_TOKEN`. Consequently, a merge performed by the protected
merge workflow can reach `main` without starting the publication workflow's
ordinary `push` event. This is a GitHub event-suppression boundary, not evidence
that the reviewed release failed validation. See GitHub's
[workflow-trigger documentation](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow).

The publication workflow has one recovery-only manual entry point. It does not
publish its own workflow-fix commit. A dispatch either performs the original
fresh digest build when no attempt has been consumed or, only when all three
recovery fields are supplied, resumes
one exact digest from one exact failed publication run. It accepts an earlier
release commit only when all of these conditions remain true:

- Josh dispatches the workflow from the current protected `main` ref with an
  exact lowercase 40-character release SHA and exact expected version, and Josh
  remains the triggering actor for any rerun of that dispatch;
- that SHA appears on the current protected-main first-parent chain, so a
  same-tree feature-branch commit cannot substitute for its reviewed merge;
- the entire `hass_mcp_engineering_beta/` tree is unchanged between that SHA and
  current protected main;
- the release SHA still contains a bounded version transition from its first
  parent, and neither it nor current protected main contains a staged release
  declaration; and
- digest resume names a completed failed `workflow_dispatch`, verified
  `workflow_run`, or claimed protected `push` publication run of this exact workflow on `main`, owned and
  triggered by Josh in this repository, plus its exact lowercase digest and UTC
  build timestamp; newer runs also require the matching immutable attempt record;
  optional `recovery_run_attempt` selects the exact failed attempt through the
  attempt-specific API, so a later rerun cannot hide the original digest evidence;
  and
- the immutable version tag, commit tag, GitHub Release and image tags are all
  proven absent before any registry login or write.

Any mismatch fails before publication. Existing concurrency, complete-test,
metadata, dependency-audit, ancestry, anonymous multi-architecture verification,
provenance/SBOM, tag and GitHub Release checks remain mandatory. The recovery is
rechecked against the current protected-main SHA, first-parent chain,
Engineering tree, advertised version and staged state immediately before
registry authentication. The immutable Git tag, GitHub Release, and both image
tags are also probed again at that boundary; an identity appearing, or an
ambiguous GitHub or registry response, stops before login and any fresh build.
Fresh publication pushes the multi-architecture image without a temporary tag,
only under its content-addressed digest. Digest resume skips QEMU, registry
login, and the build action entirely. The publication job has a bounded timeout,
and a failure at any later phase therefore cannot leave a predictable staging
tag or delete a digest shared by completed release tags. The exact OCI index,
source-declared platform child digests, per-platform image labels, attestation subjects,
and SLSA provenance are verified anonymously and without Docker image pulls
before any final image tag is created. Each attestation manifest and raw in-toto
statement is fetched by its descriptor digest; its byte hash, bounded size,
predicate type, manifest subject, and statement subject must all bind the same
platform digest. The provenance must bind the release SHA,
version, timestamp, protected ref, repository/owner, prior run ID and attempt,
workflow SHA/path, source tree, build arguments, and BuildKit VCS identity. The
complete protected-main, actor,
source-tree, version, staged-state, Git-tag, GitHub-Release, and registry-tag
authority guard is then repeated after the digest-addressed build and
verification, immediately before the final registry writes. GHCR does not
enforce conditional create-only manifest PUTs. The guarded publisher therefore
proves each target absent immediately before its ordinary manifest PUT, creates
the release-commit tag first, verifies its exact digest, and only then repeats
that sequence for the deployable version tag. Repository workflow concurrency
serializes these publications, but an external package writer can still race the
check-to-write interval. Ambiguous reads or writes stop with an explicit partial
or unknown disposition; exact postconditions are required before continuing.
After anonymous verification of those final image tags, the complete manual
actor, ref, exact protected-main, first-parent,
Engineering-tree, advertised-version and staged-state authority guard is fetched
and repeated directly before the annotated Git tag push, and again directly
before GitHub Release creation. The corresponding remote identity is also
reprobed at each write boundary. Protected-main movement at either point stops
the metadata write; already-created image tags remain an explicit partial state
requiring reconciliation. Any partial or unknown write disposition is retained
in reconciliation output, including the case where a registry applies a write
but its response is lost. The recovery is not a retry mechanism after partial
publication; an existing, partial, unknown, or ambiguous artifact is a stop
condition requiring reconciliation.

Before any fresh build, the publisher atomically creates the annotated Git ref
`refs/tags/engineering-publication-attempt/v<VERSION>`. Its JSON binds the exact
release SHA/version, workflow SHA, run ID/attempt and event. The existing
`contents: write` permission creates this audit marker; no Actions-write grant,
new secret or repository setting is introduced. The Git refs **create** operation
rejects an existing ref, even one with the same object ID. Exact readback is
required before registry login. This is at-most-once fresh-build authority for
cooperating repository workflows, not atomic publication across GitHub and GHCR.
Privileged external ref deletion or package writes remain outside that guarantee.

Automatic `workflow_run` claims use schema 2 and additionally retain the
independently verified merge-workflow run, attempt, workflow ID, source event,
source head SHA and resulting release SHA. Push and owner-dispatch claims retain
schema 1. The image verifier checks the real completed-workflow event payload
against that binding; it does not require a nonexistent top-level event `ref`.
The publisher's protected-main ref, workflow SHA, run/attempt, owner, image digest
and release-source checks still apply. Push/manual event ref checks and exact
manual inputs remain required.

Digest recovery reads that original binding from the create-only claim, after
matching the failed publisher run and attempt, and verifies the original image
without a new build. An automatic claim lacking this binding is refused; it is
not upgraded, deleted or reconstructed from the image's own assertions. Such a
record requires explicit reconciliation. These event shapes follow the
[GitHub workflow_run payload contract](https://docs.github.com/en/webhooks/webhook-events-and-payloads#workflow_run);
Buildx v0.37.0's [exact context implementation](https://github.com/docker/buildx/blob/ac30b249211430b85fb8f37b6e7154b5c47ba0b6/util/ghutil/ghutil.go)
copies the event payload without adding a ref. Offline regressions exercise
raw digest-linked provenance for automatic publication and original-attempt
recovery. First automatic publication on an approved beta remains a separate
operational acceptance check.

Concurrent, delayed and rerun events cannot consume the same version again.
Unknown write acknowledgment or failed readback stops without retrying, deleting
or moving the marker. A rejected duplicate may leave an unreachable annotated
tag object; it cannot create another attempt ref or image. Keep the marker after
failure. Inspect the original run and digest; owner-authorized digest recovery
must match its exact claim and pass all existing artifact-absence and provenance
checks. A failure before a recoverable digest exists requires a concrete owner
reconciliation decision; blindly rerunning the build or deleting the marker is
not recovery. Existing partial image tags/releases also remain a reconciliation
stop. Recovery of historical owner-dispatch runs whose protected workflow code
predates attempt records remains supported; new automatic runs cannot use that
exception.

Lost or expired handoff evidence does not authorize a guessed merge or a generic
redispatch. Inspect the original PR, merge and publication runs. If no claim or
publication artifacts exist, Josh may authorize the existing manual entry point
with that same exact retained merge SHA/version. If a claim exists, inspect that
run and use digest recovery only when its complete evidence is available. GitHub
workflow concurrency can replace an older pending run; the immutable claim, not
queue ordering or artifact retention, supplies duplicate suppression.

Fresh manual recovery still builds only the exact historical release checkout.
Digest resume cannot invoke that build. Failed-run metadata is read and
validated in a separate job with only `actions: read` and `contents: read`; the
publication job has no Actions-read permission. The source verifier and guarded
registry helper are separately materialized into runner temp from the exact
current protected-main workflow-authority SHA after that SHA has passed the
actor, ref, current-head, first-parent, source-tree and staged-state guards. They
are publication tooling only and never enter the historical image context.

After the recovery workflow change itself is merged and exact-head review and CI
are complete, use the GitHub Actions page for **Publish reviewed Engineering
release**, choose **Run workflow**, select `main`, and enter the reviewed release
SHA and expected version. Beta 53 run `33379623142` completed every validation
job and created the untagged digest below before its old local-Docker verifier
failed while switching platforms. The bounded completion inputs reuse only that
digest; they do not rebuild it:

```text
release_sha: 153b4dd7e2e60806c7117bb83c6c83b8adf02ff8
expected_version: 2.2.0-beta.53
recovery_run_id: 33379623142
recovery_source_digest: sha256:2cd84c96aec6c1772e8933f9c78127bc8a13c02743fc8056b23d3f9275eb8b40
recovery_build_time: 2026-08-31T10:10:06Z
```

The equivalent authenticated GitHub CLI command is:

```powershell
gh workflow run publish-rc-image.yml --repo jeter-1/hass-mcp-admin --ref main -f release_sha=153b4dd7e2e60806c7117bb83c6c83b8adf02ff8 -f expected_version=2.2.0-beta.53 -f recovery_run_id=33379623142 -f recovery_source_digest=sha256:2cd84c96aec6c1772e8933f9c78127bc8a13c02743fc8056b23d3f9275eb8b40 -f recovery_build_time=2026-08-31T10:10:06Z
```

Dispatch is a distinct publication action and still requires Josh's explicit
authorization after the workflow fix is merged. Successful publication does not
authorize Home Assistant backup, update, restart, deployment or canary work.

Only Josh may mark the draft ready. Converting the pull request back to draft,
closing it, or pushing a new head withdraws the immediate merge path. The
protected-base workflow cancels any in-flight merge run on every `synchronize`,
draft, close/reopen, or relevant base-change event and retains no persistent
auto-merge request. Josh must mark the revised pull request Ready again so the
new exact head receives fresh authorization after the bounded delta rereview.

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
- Before deployment preparation, run `codex-context.py` and require exact current
  document resolution with `documents.active_acceptance_document` known. Before
  promotion preparation, require the declared staged version and exact
  `staged_release.documents` resolution with its `active_acceptance_document`
  known. Missing exact acceptance authority is a stop condition. Historical
  references cannot authorize current acceptance, and release notes are not
  acceptance instructions.
- Preauthorize only the named branch push and draft-PR creation. Agents do not
  mark pull requests ready. Josh's later Ready action may authorize bounded
  merge and source publication under the repository contract; it never
  authorizes deployment, secret changes, or live-Home-Assistant access.
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
> cause, correction, and a proving test. Include a security section for provider,
> write, workflow, cryptographic, persistence, or release-authority changes. Do
> not edit, approve, merge, release, or deploy. This is the one default full
> independent review.

### Corrective Follow-up

> On `<existing draft branch>`, verify each accepted review finding against
> source, implement only attributable corrections, add regression coverage, rerun
> the affected Fast area and Full/Evidence, update the draft PR, then request one
> focused delta rereview of the accepted findings and reviewed-head-to-final-head
> delta. Request a full rereview only if architecture or scope materially changed.

### Remote Implementation

> Use the prepared trusted host/worktree for `<scope>`. Confirm the base has not
> moved. External writes are limited to pushing `<exact branch>` and creating or
> updating its draft PR. Stop on missing environment, unrelated failure, moved
> base, secret need, or trust-boundary expansion.

### Release Preparation

> Prepare release evidence for `<version>` without publishing. Verify authoritative
> current and staged version declarations, require
> `staged_release.documents.resolution_status` to be `exact`, and read only the
> known `staged_release.documents.active_acceptance_document` as promotion
> acceptance authority. Missing exact staged acceptance is a stop condition;
> historical references cannot authorize current acceptance, and release notes
> are not acceptance instructions. Verify image/tag/provenance preconditions and
> rollback, report CI-only checks accurately, and stop for a separate human
> publication/deployment decision.

### One-pull-request protected release

A release declaration is an authoring aid on the feature branch, not mergeable
release state. Before Ready, `promote_next_release.py --apply` updates the three
authoritative version locations and consumes `.release/next-version` in the
original pull request. The exact implementation, tests, acceptance documents,
release notes, and final version state therefore receive the same independent
review and CI validation. Publication of that reviewed state does not trigger
another model review.

After that pull request is merged through the protected path, the publication
workflow compares the exact new `main` commit with its prior protected commit,
requires the bounded version transition and exact document authority, reruns
complete validation, and publishes from the current `main` commit. It may create
the immutable image, annotated tag, and GitHub Release, but it never writes a
promotion commit or branch to `main`, opens a second pull request, deploys the
add-on, or changes live Home Assistant.

Publication remains bound to the reviewed triggering commit. If an ordinary
pull request merges while that release is being validated or published, the
release continues only when the triggering commit remains an ancestor of current
protected `main`; a divergent or removed commit fails closed. The publication
concurrency group serializes release runs, so a later release transition cannot
overtake an earlier one.

### Engineering architecture retirement

The next Engineering release advertises `amd64` and `aarch64`; published 2.2.0
retains its original three-platform artifacts. HAOS retired 32-bit armv7 support.
This changes future package availability, not existing data or installation identity.
Do not rewrite historical release notes, signed upstream architecture evidence,
frozen-v1 packaging or old verification fixtures to match the new set.

Publication resolves its complete expected platform set from
`hass_mcp_engineering_beta/config.yaml` at the exact guarded release SHA.
The current protected verifier reads that committed blob, not mutable checkout
bytes or the received manifest's own platform list. Both 64-bit platforms and
all their attestations are mandatory; historical three-platform releases still
require arm/v7 and its attestation. Missing, extra or duplicate coverage refuses.
An old release remains subject to every existing recovery eligibility guard;
retaining artifact verification does not authorize republishing it.
