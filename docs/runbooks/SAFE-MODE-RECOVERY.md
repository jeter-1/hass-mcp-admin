# Safe-mode recovery

Status: future recovery design; not implemented

## Current source-derived facts

No E1 source path invokes Home Assistant safe mode, restart, reload, restore, or
a provider. Existing operational restart authority remains unchanged and does
not imply safe-mode authority.

## Proposed entry criteria

Safe mode is a recovery branch for a future governed update task only when
normal post-update startup or health verification cannot complete and
already-collected evidence indicates configuration or custom-component
isolation could restore diagnostic access.

Before a future safe-mode action, require:

- the exact failed update task and immutable target identity;
- the last completed execution and disruption evidence;
- current connectivity and configuration-validation evidence, including known
  gaps;
- an approved recovery objective and operator contact;
- a verified backup or other policy-approved recovery path; and
- an explicit elevated approval scoped to safe mode.

## Proposed recovery sequence

```text
failed post-update verification
  -> preserve evidence
  -> evaluate safe-mode applicability
  -> elevated recovery approval
  -> durable recovery intent
  -> one bounded safe-mode transition
  -> diagnostic readback
  -> choose repair, last-known-good recovery, downgrade, or restore
```

Safe mode is not update success. A future implementation must record it as a
recovery state and must not erase the original task, approval, dispatch, or
failure evidence.

## Exit criteria

Exit only after the exact Home Assistant identity is readable, governance and
audit persistence are available, configuration and repair evidence is
collected, logs cover the disruption window, and the operator has selected a
governed next step. Re-entering normal mode is a separate disruptive action and
requires its own durable intent and verification.

## Failure and refusal behavior

Unknown target identity, missing recovery approval, unavailable persistence,
ambiguous prior dispatch, or absent recovery evidence must stop the path.
Safe-mode unavailability must be surfaced; it must not fall back to generic
restart, service calls, direct Home Assistant access, or arbitrary provider
forwarding.

## Non-actions

This document supplies no safe-mode command, public tool, provider route,
restart implementation, or live-system procedure.
