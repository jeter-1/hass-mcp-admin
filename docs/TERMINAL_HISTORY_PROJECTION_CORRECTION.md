# Terminal history projection correction

This correction restores read access to two diagnosed historical record families.
It does not change the installed rc.1 runtime, policy classification, approval or
execution authority, persisted bytes, providers, tool schemas or release version.
Its baseline is rc.1 `df860eeffdc9031bd0596a9a9892b9e1c9c08f1e`.

## Diagnosis and accepted shapes

The expired contract-v3 input-boolean record has a valid immutable f2-v1 snapshot
using `helper_dependency_evidence_incomplete`. The evaluator changed that reason
under the same policy identifier at `d6768ed8bd395e3f3307d0e4e79525af9eba408c`.
The current reader consequently reports `policy_snapshot_mismatch` for that
otherwise intact history. The exception requires the exact elevated/high/indirect
decision, incomplete and ineligible dependency evidence, helper-specific proposal
hash, original baseline binding, expired chronology, invalidated approval and
acknowledgement, and no task, dispatch, verification or recovery evidence.

The two contract-v1 automation records were persisted as `validation_failed` with
prohibited bundles under f2-v1 and f2-v2. Their sole event records the rejected
planning validation. The shared prohibited-plan validator expects a different
status and event sequence, so rc.1 reports `approval_sequence_failure`. This does
not establish a human approval mistake. The read exception requires the exact
prohibited/high/safety-critical decision, immutable subject and decision hashes,
proposal normalization/hash, reproducible rejected validation result, original
single-event chronology, and absence of approval, challenge, task and execution
evidence. Configuration remains invalid; no success is manufactured.
Persistence-safety rejections are excluded because the shipped writer rejects
secret-bearing fields and authenticated MCP URLs before writing any record.
Configured sensitive values likewise cannot qualify for this compatibility path.

These shape checks identify reviewed writer families, not the exact binary that
originally wrote a production record. Record dates and plan IDs are not provenance
or allowlists. A valid unsupported shape remains unsupported; a damaged hash
remains an integrity failure. Neither is silently repaired.

## Implementation and security boundary

`terminal_nonexecution_projection_match` is separate from the older historical
matcher, which also participates in shared prohibited-plan validation. Only read
projection and compatibility accounting call the new matcher. The service checks
task absence before admitting a read, and task-storage failure stays fatal.
The current-policy validator and shared event allowlist are unchanged.
Malformed helper evidence containers remain bounded projection failures, allowing
other valid records to stay readable through startup, list and health reporting.

Public get/list preserve original status, policy and hashes, with no actionable
approval or apply result. Reads, external-review discovery, restart and recovery
reconciliation do not rewrite these records or call providers. Existing contract-v1
get detail remains unchanged; list and bounded observability retain their existing
configuration omission and sanitization boundaries.

Approval, external decision, apply and rollback remain rejected. The existing
apply validator may append a durable refusal event after an explicit rejected
apply attempt. That extended lifecycle is outside these exact writer profiles and
may again be unprojectable. The fix neither suppresses that audit nor broadens the
exception to other lifecycle shapes. Read-only acceptance must not exercise apply.

Compatibility accounting adds three profile keys to the existing profile-count
map, retains its model identifier and `authorization_effect=none_projection_only`,
and remains separate from projection-failure counts. The two earlier retained-
effect compatibility records are a different set from the three rc.1 findings.

## Reproducible fixture provenance and validation

`tests/generate_terminal_projection_fixtures.py` uses sanitized inputs before
hashing, deterministic clock/UUIDs, fake providers and a network prohibition.
Each historical writer runs in a fresh interpreter from an exact clean checkout:

| Fixture | Exact shipped writer | Writer read result |
| --- | --- | --- |
| Expired incomplete helper | beta.38 `171e4769faef9a188e0a57cfa024f401f6313e1a` | Readable |
| Rejected f2-v1 automation | beta.38 `171e4769faef9a188e0a57cfa024f401f6313e1a` | `approval_sequence_failure` |
| Rejected f2-v2 automation | beta.4 `c07bc54ae0cf9d52a3bd205f2a24de704f7b8296` | `approval_sequence_failure` |

The committed fixture bytes are the unchanged historical writer outputs.
`tests/fixtures/terminal_projection_provenance.json` records their digests, writer
commits, generator hash, zero dispatch/tasks and historical reader results. No
production record or secret is included. Generate into a new directory:

```sh
python tests/generate_terminal_projection_fixtures.py \
  --beta38-worktree /path/to/exact-clean-beta38 \
  --beta4-worktree /path/to/exact-clean-beta4 \
  --output .artifacts/terminal-fixture-reproduction
```

Compare all three records and provenance byte-for-byte. Focused regressions cover
useful get/list, mixed failed/compatible accounting, deep audit/restart, secret
boundaries, task-storage failure, tampered hashes, rehashed unsupported lifecycles,
dependency/dispatch/approval evidence and all authority paths. Existing historical
and current approved-change/readback/recovery tests must still pass. Run the
repository Evidence gate with both changed runtime files explicitly declared as
protected scope, then independent review. Source validation does not prove live
readback of the original records.

## Release and acceptance disposition

PR #132's dashboard planner/approval work is outside this correction. Stable-v1,
schemas, workflows, trust registry, packaging and release metadata remain unchanged.
This functional correction needs its own reviewed release decision before stable
promotion; no version transition or deployment is included here.

After a separately authorized deployment, read the same three retained plans once
and verify original status, policy, hashes, no task/approval/dispatch authority and
truthful compatibility/failure counts. Reconcile final health without a device
cycle. Preserve all prior evidence and unrelated acceptance qualifications. Since
the correction has no migration or read-time record rewrite, its removal would
restore the previous read limitation; this is not a claim that arbitrary binary
downgrades are storage-compatible.
