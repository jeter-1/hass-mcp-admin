# Downgrade versus backup restore decision

Status: future recovery decision; not implemented

## Current source-derived facts

E1 reports whether rollback and restore are available. It does not infer that
either is safe, choose an artifact, modify a durable task, dispatch a provider,
downgrade software, or restore a backup.

## Proposed decision inputs

- exact failed target, installed version, candidate version, and update task;
- failure stage and whether dispatch or disruption occurred;
- compatibility evidence for a downgrade artifact;
- backup identity, age, scope, location, and restore-drill evidence;
- data or schema migrations introduced by the candidate;
- configuration and persistence formats read or written after the update;
- target-specific rollback support and vendor constraints;
- current critical repairs, errors, storage, and power stability; and
- the recovery objective and acceptable data-loss window.

## Proposed decision matrix

| Condition | Preferred future decision |
| --- | --- |
| Exact prior artifact is supported and no incompatible migration occurred | consider downgrade |
| Candidate changed persistent schema not readable by the prior version | prefer compatible restore or vendor recovery |
| Configuration/data corruption predates the update | do not assume downgrade will recover it; review restore |
| Backup omits required data or restore was not validated | do not claim restore viability |
| Firmware has no supported downgrade | use the reviewed device recovery path or block |
| Both paths are viable but have different data-loss windows | elevated manual decision |
| Neither path is proven viable | block and escalate manual recovery |

## Proposed safeguards

The selected path must identify one exact artifact or backup, bind a new
recovery approval to it, persist intent before dispatch, permit at most the
reviewed action, and verify the recovered version, identity, health, repairs,
logs, configuration, and persistence. An ambiguous outcome must continue
readback only and must not dispatch the alternate recovery path automatically.

Downgrade is not rollback merely because a lower version string exists. Backup
restore is not viable merely because an archive exists.

## Non-actions

This document does not select, download, install, downgrade, restore, migrate,
delete, restart, or invoke any provider.
