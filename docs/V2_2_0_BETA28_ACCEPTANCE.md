# HA MCP Engineering 2.2.0-beta.28 governed dashboard-update acceptance

## Release boundary

- baseline: `9fdb5004bcda3e66df9d08ab8b21928279ddb8c0` on current
  `origin/main` when the branch was created;
- published Engineering version: `2.2.0-beta.27`;
- staged version: `2.2.0-beta.28`;
- upstream: exact reviewed `ha-mcp` 8.1.1, compatibility entry
  `ha-mcp-v8.1.1-e1d76a6e`;
- Home Assistant lanes: 2026.8 contract and exact-image validation;
- stable version: `1.1.2`; and
- merge, publication, tagging, deployment, live dashboard mutation, and other
  dashboard write families are excluded.

## Source and deterministic acceptance

Require all of the following before merge:

- register exactly one new proposal-only tool,
  `create_dashboard_update_plan`, without exposing
  `ha_config_set_dashboard` or `ha_get_skill_guide` through normal dynamic
  delegation;
- accept only one exact existing storage-mode dashboard and a non-empty ordered
  patch of no more than 16 canonical JSON Pointer `add`, `replace`, or `remove`
  operations;
- reject missing, ambiguous, implicit, YAML-mode, malformed, stale, tampered,
  unreviewed, schema-drifted, annotation-drifted, output-drifted, and
  runtime-contract-drifted inputs before mutating dispatch;
- keep complete dashboard configurations and setter payloads private while
  showing bounded sanitized changed-value previews in the approval review;
- require existing external administrator approval, policy binding, plan and
  artifact integrity, sequence authority, and the complete F3 lock set;
- bind planning and execution to exact reviewed `ha-mcp` 8.1.1 and the active
  compatibility entry with no fallback;
- immediately reread identity and configuration hashes while the lock is held,
  then persist durable intent before exactly one setter invocation;
- verify success only through exact complete post-write readback of the approved
  compiled result and both expected hash models;
- represent lost response or transport uncertainty truthfully, never redispatch
  after possible invocation, and use readback-only recovery after restart;
- disclose the operator-accepted non-atomic race and require no concurrent UI
  or external-client edit during execution;
- prove rollback is unavailable and that creation, deletion, resource writes,
  metadata/sidebar changes, screenshots, Python, service calls, physical
  actions, reloads, restarts, and arbitrary forwarding remain unreachable;
- preserve existing dashboard reads, exact 8.1.1 delegated admission and held
  classification, approval and F3 authority, zero fallback, and stable v1.1.2;
  and
- pass focused dashboard/provider/governance/F3 tests, the complete Full and
  Evidence gates, exact-image tests, and both Home Assistant compatibility
  lanes.

## Later deployment acceptance

After separate merge, promotion, publication, and deployment authorization,
verify the deployed server identifies as exact Beta 28 with independently
available image/revision provenance. Confirm normal health, provider/catalog,
audit, governance, task/recovery, and fallback state before any write canary.

Use a disposable or easily reversible existing storage-mode dashboard. Create
one patch changing a harmless visible text field. Confirm the approval page
shows the exact target, bounded before/after preview, non-atomic warning, no
rollback, and the instruction not to edit the dashboard concurrently. Approve
once, apply once, read the dashboard back independently, and require
`succeeded_verified`, one setter invocation, exact resulting hashes, no
fallback, and no unrelated dashboard or metadata change.

Separately exercise a stale-hash plan and an invalid patch without approval or
dispatch. Where a controlled fixture is available, exercise a lost provider
response and prove readback-only completion with no redispatch. Hold if the
setter becomes public, a missing dashboard can be created, raw configurations
or receipt material appear in audit/health, a possibly dispatched write is
retried, a mismatch is reported as verified, or any excluded operation becomes
reachable.
