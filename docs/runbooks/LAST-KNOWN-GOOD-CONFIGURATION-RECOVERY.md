# Last-known-good configuration recovery

Status: future recovery design; not implemented

## Current source-derived facts

E1 does not read, write, select, copy, validate, reload, or restore Home
Assistant configuration. It does not change existing configuration-governance
contracts.

## Proposed evidence contract

A last-known-good candidate is evidence-backed only when it has:

- an immutable content fingerprint and bounded source reference;
- an exact creation or capture time;
- the Home Assistant identity and version under which it was known good;
- successful configuration validation evidence;
- relevant repair and error status;
- required secret references represented without secret values;
- a provenance record showing how it was captured; and
- an independently verified recovery or rollback route.

“Most recent,” a filename, a mutable directory, or an operator memory is not
proof that configuration was known good.

## Proposed decision sequence

1. Preserve the failed update task, candidate, logs, repairs, and current
   configuration fingerprint.
2. Enumerate only bounded, provenance-backed last-known-good candidates.
3. Compare version and integration compatibility without altering either
   configuration.
4. Require manual review when compatibility, secret availability, or included
   configuration scope is unknown.
5. Create a separate recovery plan tied to one exact fingerprint.
6. Require elevated approval before any replacement, restore, reload, or
   restart.
7. Verify configuration, repairs, logs, core identity, integrations, and
   persistence after recovery.

## Refusal conditions

Refuse selection when the fingerprint is missing, provenance is incomplete,
the candidate was not validated, target identity differs, required secrets are
unavailable, compatibility is unknown without review, or the recovery action
would require an unreviewed provider or fallback.

## Non-actions

This document does not establish a configuration store, copy files, expose
secrets, apply configuration, invoke validation, reload, restart, or restore.
