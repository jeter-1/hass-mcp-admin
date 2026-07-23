# RC2dev14 development notes

Version: `2.0.0-rc2-dev14`
Status: development candidate; not published, deployed, or accepted

Repository version metadata advertises this local candidate identity. This
document does not publish, deploy, or accept it. It records the bounded
development scope that must be reviewed and validated before any separate
release decision.

## Practical configuration plans

Dev14 adds one public tool, `create_configuration_plan`, for one to eight
ordered configuration operations. The nested input contract is explicit:

- required `operation_id`;
- required `resource_type`: `automation`, `script`, or `helper`;
- required `action`: `create` or `update`;
- required `target_id`;
- required `proposed_config`;
- optional `helper_type`: `input_boolean` or `input_number`; and
- optional `depends_on`: identifiers of unique earlier operations.

Unknown operation fields are forbidden. The existing six governance-tool
schemas remain unchanged.

The supported practical resources are automation configuration, script
configuration, `input_boolean`, and `input_number`. Bare IDs identify
automations and scripts. Helpers use their full entity ID and must match their
declared helper type. Delete, rename, enable/disable, and every other resource
or action fail closed.

The development catalog is 41 static tools: 25 canonical plus 16
Engineering-native. Exact admission of the existing 26 reviewed upstream reads
produces 67 tools after a client re-list or reconnect.

Contract-v2 plans bind the ordered operations, dependencies, typed targets,
pre-change fingerprints, proposed configuration hashes, normalization versions,
aggregate risk, expiry, and approval authority into one immutable hash.
Planning performs no write. One Home Assistant administrator decision in the
existing Approval tab authorizes only that exact aggregate hash.

The Approval tab derives a bounded semantic view from the hash-bound
configuration. It shows ordered trigger, condition, service/action, target, and
key primitive-data details for scripts and automations. If that view is
incomplete or exceeds its review bounds, both the button and the decision
endpoint fail closed.

## Ordered apply and truthful partial results

Apply first locks and re-reads every target. Stale state, provider
unavailability, hash mismatch, missing external approval, unsupported content,
or prohibited risk stops before the first write. Once preflight passes, the
approval is consumed once and operations execute in exact order. Each target is
re-read immediately before its individual operation as protection against
Home Assistant UI or API edits during earlier steps. A later stale target is
not overwritten; earlier verified work is retained and the result is partial.

Each write is followed by exact typed-identity and normalized-state readback.
Execution stops on the first write or verification failure. A final Home
Assistant configuration check gates complete success and accepts only explicit
`{"result": "valid", "errors": null}` evidence. Missing, empty, malformed, or
otherwise unknown responses fail closed. Per-operation receipts show attempted,
applied-and-verified, failed, verification-failed, and dependency-blocked work
without returning full stored or proposed configuration.

A transport error does not prove that Home Assistant rejected a write. Dev14
performs one bounded readback. If that proves the exact desired state, the step
is recorded as applied-and-verified, but the aggregate outcome is still
`partial_failure` and later steps are not attempted. If state cannot be proven,
the receipt says so. The consumed approval is never reused.

The plan is intentionally non-atomic. There is no automatic or batch rollback.
Earlier verified changes remain after a later failure. Remediation requires
fresh inspection, a new exact plan, and a new external administrator approval.
Home Assistant configuration writes do not provide compare-and-swap semantics,
so a narrow interval remains between the immediate re-read and write. Any
ambiguous result stops the plan and is reported rather than treated as success.

Helper creation has an additional fail-closed boundary. The approved target
must equal the deterministic ASCII slug of the helper name, and the adapter
checks storage plus the entity-state namespace for collisions before calling
Home Assistant. The upstream API still generates the ID. If a race produces a
suffixed ID, the receipt exposes that exact unexpected ID with `orphan_risk`,
no later operation runs, and no unapproved cleanup or deletion is attempted.

## Provider and safety boundary

The write boundary is a fixed internal resource adapter for automation, script,
`input_boolean`, and `input_number` methods. The public MCP catalog does not
register raw `ha_config_set_*` writers, arbitrary Home Assistant writes,
arbitrary upstream tool dispatch, or a fallback path. The reviewed generic
upstream gateway remains read-only.

Planning, external approval request, apply, exact readback, configuration
validation, partial-failure reporting, and audit remain distinct lifecycle
steps. High-risk content remains reviewable but not executable. Full
configurations, secrets, credentials, approval material, and authenticated URLs
remain excluded from approval projections and audit records.

## Explicit exclusions

Dev14 adds no deletion, rename, enable/disable, backup, reload, Home Assistant
restart, add-on operation, dashboard mutation, scene/group management, registry
mutation, integration option change, image, tag, publication, deployment, or
live-system authority. Those are later milestones or separate decisions.

The intended HVAC acceptance case is not encoded in runtime logic. The
repository contains historical read-only fixture names, not the current desired
change. A deployed acceptance attempt must begin with the user's fresh actual
request and fresh read-only inspection. Historical fixture IDs cannot supply
targets or authorize work.
