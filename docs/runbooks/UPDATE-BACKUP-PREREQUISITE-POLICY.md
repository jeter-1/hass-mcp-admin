# Update backup prerequisite policy

Status: proposed future runbook; no executable backup integration

## Current source-derived facts

The existing Engineering operational-administration contract can propose and
perform one narrowly reviewed Home Assistant backup operation after external
approval. Its independent readback does not claim archive-content integrity,
and the reviewed upstream snapshot excludes the recorder database. E1 does not
call, extend, or register that operation.

## Proposed future policy

Before a future update can become a durable update task, already-collected
backup evidence must satisfy the target policy in
[ADR-011](../architecture/ADR-011-GOVERNED-UPDATE-RECOVERY-PREFLIGHT.md).

A required backup is acceptable only when all of the following are supplied:

- status is `current`;
- age is known and within the target's maximum age;
- the location is verified as available; and
- a viable restore or rollback path satisfies the separate recovery policy.

The 24-hour blocking window applies to Home Assistant Core, Supervisor, Home
Assistant OS, Engineering MCP server, and upstream `ha-mcp`. The 72-hour window
applies to add-on/app and both HACS target classes; a stale backup for those
targets requires manual review instead of automatic readiness. Firmware update
entities do not use this Home Assistant backup prerequisite, but still require
a viable target-specific recovery path.

## Decision handling

| Evidence | Proposed preflight result |
| --- | --- |
| Required backup missing or unavailable | blocked |
| Required backup status unknown | blocked |
| Required backup age missing | blocked |
| Backup beyond blocking age window | blocked |
| Backup beyond manual-review age window | manual review required |
| Required backup location not verified | blocked |
| Backup not required by target policy | continue with other evidence |

The evaluator never creates a replacement backup. A future orchestrator must
return to the backup-prerequisite stage, obtain separate authorization when
needed, collect fresh readback evidence, and run a new preflight.

## Evidence record

A later evidence collector should preserve the backup identifier, creation
time, location class, inspection source, completeness, and whether archive
integrity was independently validated. Secrets, encryption material, provider
responses, and unbounded archive manifests must not enter the preflight model.

## Non-actions

This runbook does not authorize backup creation, deletion, download, restore,
retention changes, password handling, provider dispatch, or live-system access.
