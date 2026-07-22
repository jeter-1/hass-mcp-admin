# Repository Instructions for Codex

These instructions apply to the whole repository. A closer `AGENTS.md` adds
requirements for its subtree without weakening this file.

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
- Full gate with PR evidence: `.\scripts\check.ps1 -Tier Evidence`
- Generate a PR draft: `python scripts/pr-evidence.py --base origin/main --head HEAD --output .artifacts/pr-evidence.md`
- Open the active acceptance document: run the context command and open the first
  path under `Active release and acceptance documents`; never guess from history.

Fast-check areas are `Workflow`, `Context`, `Evidence`, `Validation`,
`Instructions`, `Deployment`, and `Metadata`. Omitting `-Area` uses bounded
changed-file inference; it stops and requires `-Area` or an explicit
`-TestTarget` when a changed path has no safe focused mapping.

## Frozen or Protected Paths

- `hass_mcp_admin/` - stable v1.1.2 source and packaging
- `hass_mcp_engineering_beta/ha_mcp_engineering/` - Engineering runtime, schemas, registration, routing, providers, and policy
- `.github/workflows/*.yml` - CI, publication, signing, and deployment authority
- `.release/` - release declarations
- `repository.yaml` - add-on repository metadata
- `hass_mcp_engineering_beta/config.yaml` - Engineering version and deployment metadata

Changes to a protected path require the task to name that surface explicitly.
Nested instruction files and other non-runtime documentation do not change the
runtime merely because they live below a protected directory.

## Prohibited Actions

- Access live Home Assistant or a deployed MCP environment during ordinary development
- Read, print, store, or transmit production credentials or secrets
- Merge, approve, or mark a draft pull request ready without explicit authorization
- Create or move a release, tag, image, attestation, provenance record, or promotion
- Deploy an add-on or change production or beta deployment configuration
- Change GitHub secrets, variables, environments, permissions, or repository settings
- Add unreviewed upstream dispatch, direct-HA fallback, write reachability, or arbitrary forwarding

## Completion Contract

Every Codex completion report must include:

- exact base and head SHAs;
- branch and draft pull-request number/URL;
- changed files grouped by purpose;
- exact tests, commands, counts, and results;
- CI results or clearly pending checks;
- runtime, security, and compatibility impact;
- stable-v1 comparison;
- known limitations and rollback;
- explicit non-actions; and
- the next human decision.

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
cause, correction, and the test that proves the correction. Resolve all Critical
and High findings attributable to the change before opening or updating a draft
pull request.
