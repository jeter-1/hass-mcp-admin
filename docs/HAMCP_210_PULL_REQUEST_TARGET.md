# HAMCP-210: `pull_request_target` restriction on public repositories (read-only)

Status: read-only inventory and options (Part 1). This document changes no
workflow, script, test, repository setting, Actions policy, runtime code,
release declaration, or stable-v1 file. The decision belongs to Josh. A
workflow change, if he chooses one, needs his explicit authorization of the
exact files.

Deadline: GitHub enforces the default block on **2026-11-02**.

Origin: a GitHub annotation on the HAMCP-191 merge run #1036 (run id
`39279833312`): `.github/workflows/enable-auto-merge.yml#L1 — On November 2,
2026, GitHub will restrict pull_request_target on public repositories by
default. To continue allowing the event trigger, configure an Actions policy.`

## Revisions and observations

| Item | Value |
| --- | --- |
| Base (`origin/main`) | `939e98c998b3370c9e7c7d3c8903351af8ec2155` |
| Engineering version (`hass_mcp_engineering_beta/config.yaml:2`) | `2.4.1-beta.23` |
| Last change to `enable-auto-merge.yml` | `760962e` (HAMCP-178, runner pin) |
| Repository | `jeter-1/hass-mcp-admin`: public, **user-owned** (`owner.type = User`), 0 forks |
| Actions permissions (`GET .../actions/permissions`, `.../permissions/workflow`) | enabled, all actions allowed; default token `read`; fork PR approval `first_time_contributors` |
| Actions policies (`GET .../actions/policies`, 2026-10-20) | `{"total_count":0,"policies":[]}`. No explicit policy exists, so the default policy applies |
| `main` rules (`GET .../rules/branches/main`) | deletion, non_fast_forward, pull_request, required status check `validate` only |
| Last 400 runs of the workflow | `pull_request_target`: 340 runs, 190 `success`; `issue_comment`: 60 runs, **0** `success` (every `@merge`-less comment is skipped; no `@merge` merge happened in that window) |

Every observation is a GitHub API `GET` made with Josh's local `gh` login.
Nothing was written, dispatched, or reconfigured. No Home Assistant or deployed
MCP endpoint was accessed, and no secret value was read.

## 1. What the workflow does

`.github/workflows/enable-auto-merge.yml` ("Merge owner-authorized pull request").

### Triggers (lines 3-14)

- `issue_comment: [created]`
- `pull_request_target: [closed, converted_to_draft, edited, ready_for_review, reopened, synchronize]`
- Workflow-level `permissions: {}` (line 16)

### Job `retire-stale-ready-run` (lines 19-65)

- **Runs on:** `pull_request_target` for a same-repository head with base `main`, on `synchronize`, `closed`, `converted_to_draft`, `reopened`, or `edited` with a base change.
- **Permissions:** `pull-requests: write` only.
- **Concurrency:** group `authorized-merge-<PR>`, `cancel-in-progress: true`. Sharing the merge job's group is what *cancels an in-flight merge run* when the head moves or the PR changes state.
- **Steps:** no checkout. It runs `gh pr view` / `gh pr merge --disable-auto` with `github.token`, which disarms a legacy persistent auto-merge request.
- **Untrusted input:** none. The only interpolations are the PR number and repository, passed through `env:`.

### Job `merge-authorized-head` (lines 67-243)

- **Runs on:**
  - `pull_request_target` `ready_for_review` by `github.actor == 'jeter-1'`, base `main`, same-repository head; or
  - `issue_comment` by `jeter-1` whose body is exactly `@merge` on a PR.
- **Permissions:** `actions: read`, `checks: read`, `contents: write`, `issues: read`, `pull-requests: write`.
- **Concurrency:** the same group as above.
- **Token:** `github.token` (as `GH_TOKEN`) is its only credential. No repository secrets are referenced.
- **Steps:**
  1. **Resolve trusted context.** Re-reads the PR from the API: open, non-draft, base `main`, same-repository head. On `pull_request_target`, the API head must equal the event's `head.sha`.
  2. **Checkout.** `actions/checkout@fbc6f39…` (pinned SHA, v5.1.0) with `ref: base_sha`, `persist-credentials: false`. **This is the PR's base commit, never the PR head or merge ref.**
  3. **Validate wait.** Polls `scripts/validate_ci_provenance.py` (run from the base checkout) for up to 35 minutes, until the exact base/head `validate` result succeeds. It fails if the base or head moves.
  4. **Ready revalidation and merge.** Reads the PR timeline and runs `scripts/validate_ready_authorization.py`, which requires the latest head lifecycle event to be Josh's `ready_for_review`. It then requires `HEAD == base_sha`, re-reads the PR, requires CI provenance success, and runs `gh pr merge --merge --match-head-commit <head>`. This is non-administrative and head-matched.
  5. **Publication receipt.** `scripts/publication_handoff.py produce` writes a receipt binding the PR, base, head, the actual merge commit, the Ready event id, the run id/attempt, `GITHUB_EVENT_NAME`, and `GITHUB_WORKFLOW_SHA` (which must equal `base_sha`).
  6. **Upload.** The receipt is uploaded as artifact `publication-handoff-<run_id>-<attempt>` with `actions/upload-artifact@b7c566a…` (pinned, v6.0.0), retained for 90 days.

### Classic `pull_request_target` pitfalls: none found

- No step checks out, builds, installs, or executes PR-head or merge-ref code.
- No step interpolates PR-controlled text (title, body, branch name, comment body) into a shell. The comment body is used only inside the job's `if:` equality test. Values reach scripts through `env:`, and the SHAs are re-derived from the API.
- No secrets are exposed; the job-scoped `github.token` is the only credential.
- `persist-credentials: false` keeps the token out of `.git/config`.
- Actions are pinned to full SHAs, and the runner is pinned (`ubuntu-24.04`).
- The scripts that make the decisions execute from the protected base checkout. Since GitHub's 2025-12-08 change, the workflow file itself always comes from the default branch (§2.4).

`pull_request_target` is used here as intended: privileged, default-branch
policy acting on PR *metadata*.

### Role in the merge → publication chain

These couplings mean any trigger change has to be co-designed with
publication:

- **Workflow name.** `publish-rc-image.yml` (lines 4-6) listens for `workflow_run: workflows: [Merge owner-authorized pull request]`, `types: [completed]`. Renaming the workflow silently severs publication.
- **Handoff verification.** `publish-rc-image.yml` "Independently verify completed merge handoff" (lines 91-105) runs `scripts/publication_handoff_verify.py`. That calls `publication_handoff.verify`, which requires:
  - the source run's `path == .github/workflows/enable-auto-merge.yml`;
  - `event ∈ {pull_request_target, issue_comment}`;
  - actor and triggering actor are `jeter-1`;
  - exactly one `merge-authorized-head` job;
  - the artifact name, digest, and shape;
  - `run.head_sha` equals the PR head for `pull_request_target`, or the base for `issue_comment`;
  - for a real release, a producer policy (the workflow plus three scripts) unchanged between base and authority.
- **Hard-coded event set** `{pull_request_target, issue_comment}`:
  - `scripts/publication_handoff.py:126,203,245,263,290`
  - `scripts/publication_claim.py:66`
  - `scripts/verify_publication_source_image.py:958`
- **Tests that pin it:**
  - the exact trigger map in `tests/test_review_automation.py` (around line 125);
  - `tests/test_publication_handoff.py`, `tests/test_publication_eligibility.py`, `tests/test_publication_ci_event_context.py` and `tests/test_publication_validation_reuse.py`.
- **Not coupled:** `ci.yml` (`validate`) runs on `pull_request` and is unaffected. `scripts/validate_ci_provenance.py` requires CI runs with `event == pull_request`, not the merge trigger.

## 2. GitHub's change (official sources)

Quoted text below comes from fetched official pages on 2026-10-20. Re-check the
exact wording on the pages before relying on it elsewhere.

### 2.1 What gets restricted

- **GA changelog, 2026-09-17:** <https://github.blog/changelog/2026-09-17-workflow-execution-protections-in-github-actions-generally-available/>
  - "GitHub is introducing a default rule that disables `pull_request_target`."
  - "This default does not apply to private or internal repositories."
  - The rule applies to "public repositories that do not already have an applicable event policy."
  - "On November 2, 2026, we'll automatically enforce the default rule for affected repositories that were using the default `pull_request_target` policy before general availability."
  - "It initially runs in evaluate mode, so you can see which workflow runs would be affected before enforcement begins."
  - You can "explicitly allow `pull_request_target` in an applicable Actions event policy if your workflows still depend on the trigger. Specific workflows can be allow-listed using the new workflow file targeting."
- **Docs, "Default policy for pull_request_target":** <https://docs.github.com/en/actions/reference/security/securely-using-pull_request_target>
  - The default policy "does not replace an applicable event policy that you have already configured" and "currently runs in **evaluate** mode. In this mode, workflow runs continue, but you can use policy insights to identify runs that would be blocked after enforcement."
  - "If the workflow must continue to use `pull_request_target`, create or update an applicable Actions event policy that explicitly allows `pull_request_target`."
- **Public preview, 2026-06-18:** <https://github.blog/changelog/2026-06-18-control-who-and-what-triggers-github-actions-workflows/>

**Scope.** The block covers the whole event. None of the official text read
exempts same-repository PRs, owner-authored PRs, or non-fork events, so this
repository's owner-only, same-repository use is affected like any other. That
is inference from the absence of an exemption. Only `pull_request_target` is
named; `issue_comment` and `workflow_run` are not part of the default rule.

### 2.2 The "Actions policy" setting

- **Levels.** "Actions policies are available at the enterprise, organization, and repository levels." (<https://docs.github.com/en/actions/concepts/about-actions-policies>)
- **Click path (repository):** **Settings** tab, then **Actions** in the left sidebar, then **Policies**, then create a policy (<https://docs.github.com/en/actions/how-tos/administer/control-workflow-execution>). The fields are:
  - a name;
  - an enforcement status;
  - targeting;
  - the protections: **Restrict events**, **Restrict actors**, and **Require lockfile**.
- **REST:** `GET/POST /repos/{owner}/{repo}/actions/policies` and `GET/PUT/DELETE /repos/{owner}/{repo}/actions/policies/{policy_id}` (<https://docs.github.com/en/rest/actions/policies>). The request body has:
  - `name`;
  - `enforcement`: one of `disabled`, `active`, or `evaluate`;
  - `conditions.workflow_path.{include,exclude}`, which takes workflow file paths or globs (a repository may use only `workflow_path`);
  - `rules[]`, either `{type: "restrict_action_events", parameters: {allowed_events: [...]}}` or `{type: "restrict_actions_actors", parameters: {allowed_actors: [{id, type}]}}`.
- **Personal-account repositories.** The docs are silent. However, `GET /repos/jeter-1/hass-mcp-admin/actions/policies` returned `200 {"total_count":0,"policies":[]}`. That is evidence that the repository-level policy surface exists for this user-owned repository. One fetched page described **Evaluate** as Enterprise Cloud monitoring; whether Evaluate is selectable here is **unverified**, and Option A does not need it.
- **Event rule semantics.** The rule is an **allow list** (`allowed_events`). An explicit policy that targets this workflow therefore has to list **every** event the workflow uses, not only `pull_request_target`. That inference comes from the schema, and the verification plan (§4) tests it.

### 2.3 What happens if nothing is done

"When enforced, disallowed workflow runs will fail with an error." The docs'
example: "Event 'workflow_dispatch' is not allowed to trigger Actions
workflows. Workflow file: '.github/workflows/0-welcome.yml'."
(<https://docs.github.com/en/actions/concepts/about-actions-policies>)

For this repository from 2026-11-02 (inferred from the above plus source):

- Josh's Ready creates a `pull_request_target` run that **fails with the policy error**. No merge happens.
- `publish-rc-image.yml` `detect-release` runs only when `workflow_run.conclusion == 'success'`, so **no publication** follows. The failure is closed and safe, but the Ready-merge automation stops working.
- `retire-stale-ready-run` also fails, so pushes no longer cancel in-flight merge runs. There is nothing in flight to cancel, so this is moot.
- The `@merge` (`issue_comment`) retry still triggers. Today it is only a retry of an already-authorized head, and it has not succeeded in the last 400 runs.
- `main` requires only `validate`, so a failed merge-workflow run does not block a manual merge by Josh. A manual UI merge would go through `publish-rc-image.yml`'s existing `push` path (not analyzed further here).

### 2.4 Related changes

- **2025-11-07, effective 2025-12-08:** <https://github.blog/changelog/2025-11-07-actions-pull_request_target-and-environment-branch-protections-changes/>
  - "The workflow file and checkout commit will always be taken from the repository's default branch, regardless of the pull request's base branch."
  - "GITHUB_REF for pull_request_target will resolve to the default branch, and GITHUB_SHA will point to the latest commit on that branch."
  - Current source already relies on default-branch policy (`GITHUB_WORKFLOW_SHA == base_sha`).
- **2026-06-18:** `actions/checkout` refuses fork PR code in `pull_request_target`/`workflow_run`, backported July 20, 2026; "Same-repository pull requests aren't affected." This does not affect us, because we check out the base. <https://github.blog/changelog/2026-06-18-safer-pull_request_target-defaults-for-github-actions-checkout/>
- **2026-06-26:** the cache token is read-only for untrusted triggers. No impact, because this workflow uses no cache. <https://github.blog/changelog/2026-06-26-read-only-actions-cache-for-untrusted-triggers/>

### 2.5 Privilege of alternative triggers (docs)

<https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows>

- **`pull_request`.** `GITHUB_SHA` is the "Last merge commit on the `GITHUB_REF` branch" (`refs/pull/N/merge`), so **the workflow file comes from the PR's merge ref**. Fork PRs get a read-only token and no secrets. Per the workflow-syntax permissions text, same-repository PRs get the requested `permissions:` (inferred; no explicit quote). `ready_for_review` is a supported type.
- **`workflow_run`.** Runs from the default branch: "able to access secrets and write tokens, even if the previous workflow was not"; "You can't use `workflow_run` to chain together more than three levels of workflows." The docs do not specify `github.actor` semantics.
- **`issue_comment`.** Runs from the default branch: "will only trigger a workflow run if the workflow file exists on the default branch." It is not named in the default rule.
- **GitHub's own advice:** "If the workflow does not need `pull_request_target`, update it to use a safer event where appropriate, such as `pull_request`."

## 3. Options

### (A) Allow `pull_request_target` for this one workflow (settings only)

Josh creates one repository Actions policy. The workflow file stays as it is.

**Clicks:**

1. Go to `github.com/jeter-1/hass-mcp-admin`, open **Settings**, then **Actions** in the sidebar, then **Policies**, and create a new policy.
2. Name it, for example, `allow-owner-merge-workflow`.
3. Set **Enforcement: Active**.
4. Under **Targeting / workflow file**, include exactly `.github/workflows/enable-auto-merge.yml` and exclude nothing.
5. Under **Restrict events**, allow **`pull_request_target` and `issue_comment`**. Both are required: the policy is an allow list, and omitting `issue_comment` would block the `@merge` path.
6. Leave **Restrict actors** and **Require lockfile** off (see the tradeoffs below).
7. Save.

The REST equivalent body, for reference only (do not run without Josh):

```json
{"name": "allow-owner-merge-workflow", "enforcement": "active",
 "conditions": {"workflow_path": {"include": [".github/workflows/enable-auto-merge.yml"], "exclude": []}},
 "rules": [{"type": "restrict_action_events",
            "parameters": {"allowed_events": ["pull_request_target", "issue_comment"]}}]}
```

**Security:**

- Unchanged from today, which already runs in this mode. Per §1, no PR code executes with the token.
- The exemption is scoped to one file by path. Because `pull_request_target` reads the workflow file from the default branch, a PR cannot smuggle a different file under that path into a privileged run before merge. Changing the file requires a reviewed merge to `main`.
- Other workflows remain under the default block. None of them uses `pull_request_target`.

**Effort and risk:**

- A few minutes of owner time, with no code, tests, review cycle, or release involved.
- The publication chain is untouched: same name, path, events, jobs, and artifact.
- It is reversible by deleting or disabling the policy.

**Tradeoffs:**

- It keeps a privileged trigger that GitHub now discourages.
- It adds a repository setting outside source control that future maintainers must know about (this document and the register row record it).
- **Restrict actors** (allow only `jeter-1`) looks attractive but is not recommended initially. `retire-stale-ready-run` must run on pushes by whatever actor pushes, and a blocked retire run would error instead of cancelling. The job `if:` and the Ready-timeline check already restrict authority to `jeter-1`.

### (B) Move off `pull_request_target`

All variants below are workflow and script changes on protected paths, so they
need Josh's explicit authorization of exact files.

**B1: `pull_request` (types `ready_for_review`, `synchronize`, etc.).**

- *Problem:* **the workflow definition comes from the PR's merge ref.** A PR under review could edit `enable-auto-merge.yml` (or the scripts it runs) and have its own modified merge policy execute with `contents: write` when Josh marks it Ready. That inverts the current trust model, where policy always comes from protected `main`.
- *Mitigations are partial:* the `validate` ruleset still binds at merge, but the validate wait, Ready-timeline proof, and receipt would all be PR-controlled.
- *Breakage:* `produce()` requires `GITHUB_WORKFLOW_SHA == base_sha`; under `pull_request` it would be the merge-ref SHA, so this breaks by design.
- **Not recommended** for the merge job.

**B2: an unprivileged `pull_request` signal workflow plus a privileged `workflow_run` merge workflow (GitHub's standard split).**

- *Shape:*
  - A tiny `permissions: {}` workflow on `pull_request` `[ready_for_review, synchronize, closed, converted_to_draft, reopened, edited]` does nothing but complete.
  - The merge workflow listens with `workflow_run` and runs from `main` with the current permissions. It re-derives the PR from the API and keeps every existing check: exact head, the validate wait, Ready-timeline revalidation, the head-matched merge, and the receipt.
- *Security:* this preserves default-branch policy. The signal is PR-controlled, but it carries no authority, and every decision is re-derived from the API and timeline, so a forged, missing, or extra signal fails closed or no-ops.
- *Cost:*
  - The `if:` conditions can no longer use `github.event.pull_request.*` or `github.actor` directly. Actor semantics for `workflow_run` are undocumented, so they must be proven.
  - `publish-rc-image.yml` must listen to the renamed or new producer.
  - The event set and the `head_sha` rule in `publication_handoff.py`, `publication_claim.py`, and `verify_publication_source_image.py` change, along with their tests and `docs/CODEX_WORKFLOW.md`.
  - Concurrency-based cancellation must be re-proven across workflows.
  - The chain becomes signal → merge → publish, which is at the documented three-level `workflow_run` limit.
- *Release interaction:* by `POLICY_PATHS`, the PR that changes the producer cannot also carry a release.
- *Effort:* high, with several protected paths changing, a Full/Evidence run, and independent review. That is tight but possible before 2026-11-02.

**B3: `issue_comment` only (Josh comments `@merge` after Ready).**

- *Pros:* it runs from the default branch, is not named in the default rule, and is already in the event set.
- *Cons:* it changes the owner contract in `AGENTS.md`, where Ready alone is the authorization. That is a product decision under ADR-022.
- *Cancellation:* `retire-stale-ready-run` would still need a non-`pull_request_target` trigger. Under `pull_request` it would be PR-controlled; the residual exposure is limited to losing cancel-on-push, because the merge job revalidates the head anyway.
- **Not recommended** unless Josh wants that UX.

### (C) Combination

- Do A now, as the deadline safety net with zero risk to the publication chain.
- Open B2 as a separately scoped follow-up only if Josh wants to retire the privileged trigger. When B2 lands and is proven on a real merge and publication, delete the policy and update this document.

## 4. Recommendation

**Choose (A) now.** Today's workflow already avoids the pitfalls the default
block targets, and A changes nothing in source or in the publication chain. B2
is a reasonable hardening follow-up, not a deadline fix; register it separately
if wanted (that would make this C). B1 is a security regression. B3 is a product
decision about the Ready contract.

### Verifying A on a real PR before 2026-11-02

An **Active** explicit policy applies as soon as it is saved, so the next
ordinary merge exercises it before the default block exists.

1. **The policy exists as intended.** `GET /repos/jeter-1/hass-mcp-admin/actions/policies` lists one policy with `enforcement: active`, `workflow_path.include == [".github/workflows/enable-auto-merge.yml"]`, and `allowed_events` containing both events.
2. **`issue_comment` is allowed.** On any open PR, any non-`@merge` comment creates an `enable-auto-merge.yml` run that concludes **`skipped`**. A run that **fails** with "Event 'issue_comment' is not allowed…" means the allow list is wrong.
3. **`pull_request_target` is allowed.** A push to any open same-repository PR produces a `retire-stale-ready-run` that succeeds.
4. **The end-to-end chain works.** On the next normal PR Josh marks Ready:
   - `merge-authorized-head` succeeds;
   - the PR merges at the exact head;
   - the `publication-handoff-<run>-<attempt>` artifact exists;
   - the following `publish-rc-image.yml` `workflow_run` run succeeds (`release_action` `none` or `publish`, as the PR warrants).
5. **Policy insights** (Settings → Actions → Policies) show no blocked runs for this workflow.
6. **After 2026-11-02**, repeat step 4 on the first merge, to confirm that the default block does not override the explicit policy.

Rollback for A: disable or delete the policy. Before 2026-11-02 that restores
today's behavior; after that date, the default block applies again.

## Non-actions

No workflow, script, test, setting, Actions policy, ruleset, secret, runtime,
release, tag, image, registry, support-window, or stable-v1 change was made.
No workflow was dispatched. No reviewer session was started. No live Home
Assistant or deployed MCP endpoint was accessed. The pull request carrying
this document is a draft and was not marked Ready.
