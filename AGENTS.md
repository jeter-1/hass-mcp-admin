# Repository Instructions for Codex

These instructions apply repository-wide. Codex combines them with any closer
`AGENTS.md` that applies to a subtree; if guidance conflicts, the closer file
takes precedence.

## Repository Authority

- GitHub `main` is the software source of truth.
- Begin every task by fetching when authorized and reporting the exact base SHA,
  HEAD SHA, branch, worktree status, and relevant stable and Engineering version
  metadata.
- Historical chat, old release notes, prior pull-request descriptions, and
  remembered hard-coded values are not proof of current source or runtime
  behavior. Resolve conflicts from current source, tests, executable validation,
  manifests, workflows, and current architecture or acceptance documents.

## Default Workflow

- Inspect before editing. Inventory instructions, scripts, tests, workflows, and
  current release declarations before choosing an implementation.
- Use one branch and preferably one worktree per logical pull request. Never
  overwrite unrelated or uncommitted work.
- Make the smallest coherent change and add regression coverage for defects.
- Prepare draft pull requests by default. Review the complete final diff before
  declaring completion.
- Do not merge, release, publish, promote, deploy, or change live systems without
  explicit authorization for that distinct action.

## Stable and Engineering Boundaries

- Stable v1.1.2 under `hass_mcp_admin/` is frozen unless a task explicitly
  includes it. Current v2 Engineering work remains isolated from stable v1.
- Preserve fail-closed behavior, reviewed provider boundaries, bounded output,
  sanitization, audit attribution, governance, and zero unreviewed fallback or
  write reachability.
- Treat public input/output schemas, tool registration, provider routing,
  provider admission, upstream trust, read-gateway policy, governance, audit,
  redaction, and external approval as security-sensitive compatibility surfaces.

## Security Requirements

- No live Home Assistant or deployed MCP endpoint may be accessed during ordinary
  source implementation or review. Do not access production credentials,
  Supervisor tokens, access secrets, signing seeds, or other production secrets.
- Do not implicitly expand filesystem, network, GitHub, deployment, workflow, or
  MCP permissions.
- Before an authorized remote push, verify GitHub readiness with the bounded
  status commands in `docs/CODEX_WORKFLOW.md`. Missing authentication is a stop
  condition; never inspect or expose credential material.
- A live upstream description, annotation, or advertised schema is observation,
  never a trust decision by itself.
- Any newly reachable write, physical action, service call, reload, restart,
  deletion, arbitrary forwarding, or fallback path is a security architecture
  change that requires explicit scope, negative tests, and focused review.

## Change Discipline

- Preserve failure behavior and negative reachability, not only successful paths.
- Keep source, tests, metadata, schemas, documentation, release declarations, and
  acceptance guidance consistent.
- Never claim a validation that was not executed. Distinguish local evidence,
  CI-only evidence, unavailable checks, and not-applicable checks.
- Derive changing values from authoritative source where safe. If a value cannot
  be derived, report `unknown`, identify the missing source, and do not guess.
- Before completion, compare the diff against the base for stable-v1 files,
  runtime schemas and registration, provider routes, workflow permissions,
  release declarations, versions, tags, images, and deployment metadata.

## Local Commands

- Show context: `python scripts/codex-context.py --format markdown`
- Show machine-readable context: `python scripts/codex-context.py --format json`
- Fast workflow check: `.\scripts\check.ps1 -Tier Fast -Area Workflow`
- Full local gate: `.\scripts\check.ps1 -Tier Full`
- Full gate with validation evidence: `.\scripts\check.ps1 -Tier Evidence`
- Protected-path gate for an explicitly scoped file:
  `.\scripts\check.ps1 -Tier Full -AuthorizedProtectedPath 'hass_mcp_admin/example.py'`
- Generate a PR draft: `python scripts/pr-evidence.py --base origin/main --head HEAD --output .artifacts/pr-evidence.md`
- Open the active acceptance document: run the context command and read the
  explicit `active_acceptance_document` field. Continue only when
  `resolution_status` is `exact` and the acceptance document is known. Stop when
  resolution is `missing`, `partial`, `unsupported`, or unknown; never substitute
  a historical reference.

Fast-check areas are `Workflow`, `Context`, `Evidence`, `Validation`,
`Instructions`, `Deployment`, and `Metadata`. Omitting `-Area` uses bounded
changed-file inference; it stops and requires `-Area` or an explicit
`-TestTarget` when a changed path has no safe focused mapping.

When the external task explicitly includes a protected path, pass each exact
repository-relative file or a directory ending in `/` through
`-AuthorizedProtectedPath`. The declaration is checked on every tier, must match
every protected change, cannot be unused, and is recorded in Evidence output. It
documents task scope for review; it does not grant permission or waive any other
gate.

## Frozen or Protected Paths

- `hass_mcp_admin/` - stable v1.1.2 source and packaging
- `hass_mcp_engineering_beta/ha_mcp_engineering/` - Engineering runtime, schemas, registration, routing, providers, and policy
- `.github/workflows/*.yml` and `*.yaml` - CI, publication, signing, and deployment authority
- `.release/` - release declarations
- `repository.yaml` - add-on repository metadata
- `hass_mcp_engineering_beta/config.yaml` - Engineering version and deployment metadata
- `hass_mcp_engineering_beta/Dockerfile` - Engineering build and deployment metadata

Changes to a protected path require the external task to name and authorize that
surface explicitly. Nested instruction files and other non-runtime documentation
do not change the runtime merely because they live below a protected directory.

## Prohibited Actions

- Access live Home Assistant or a deployed MCP environment during ordinary development
- Read, print, store, or transmit production credentials or secrets
- Merge, approve, or mark a draft pull request ready without explicit authorization
- Create or move a release, tag, image, attestation, provenance record, or promotion
- Deploy an add-on or change production or beta deployment configuration
- Change GitHub secrets, variables, environments, permissions, or repository settings
- Add unreviewed upstream dispatch, direct-HA fallback, write reachability, or arbitrary forwarding

## Completion Contract

Every Codex completion report must address each applicable item below. If an item
was not created, not run, pending, unavailable, or outside the task's scope,
state that status explicitly rather than inventing a value:

- exact base and head SHAs, when a Git base and head exist;
- branch and draft pull-request number/URL, when one exists;
- changed files grouped by purpose, or an explicit statement that no files
  changed;
- exact tests and commands run, with counts and results, plus checks not run or
  not applicable;
- CI results, or an explicit `pending`, `unavailable`, or `not applicable` status;
- runtime, security, and compatibility impact;
- stable-v1 comparison;
- known limitations and rollback when a change was made;
- explicit non-actions; and
- the next human decision, when one is required.

## Code Review Rules

Reviewers must flag each of the following, with file-and-line evidence:

- newly reachable writes, physical actions, service calls, reloads, restarts, or
  deletions;
- provider-boundary bypass, direct-HA fallback, upstream fallback, or unreviewed
  upstream dispatch;
- public-schema or stable-v1 changes outside scope;
- workflow permission expansion, secret exposure, or mutable security-sensitive
  action/image references;
- claims unsupported by executed evidence;
- version, tool-count, fingerprint, or release assertions that conflict with
  authoritative source; and
- tests that prove only success while omitting required refusal, failure
  preservation, or negative reachability.

Classify findings as Critical, High, Medium, or Low. Include evidence, impact,
cause, correction, and the test that proves the correction. Unresolved findings
do not prevent opening or updating a draft pull request when they are clearly
disclosed. Resolve all Critical and High findings attributable to the change
before marking the pull request ready for review, approving it, or merging it.
