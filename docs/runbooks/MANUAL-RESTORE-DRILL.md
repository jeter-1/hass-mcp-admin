# Manual restore drill

Status: future offline rehearsal runbook; not executed by E1

## Purpose

A restore path is viable only when its prerequisites and operator procedure are
understood. A future manual drill should test that understanding in an isolated
disposable environment without touching the live Home Assistant system.

## Preconditions

- Name the exact backup and target identity.
- Record backup age, location, scope, encryption prerequisites, and known
  exclusions.
- Use a disposable, isolated environment with synthetic credentials.
- Establish the expected Home Assistant, Supervisor, OS, add-on, Engineering,
  and upstream versions.
- Define pass/fail checks before starting.
- Assign an operator and observer; the drill itself is not automated by MCP.

## Proposed drill

1. Verify that the selected archive is readable without disclosing credentials.
2. Record the archive scope and compare it with the recovery objective.
3. Restore only into the isolated drill environment using an independently
   approved operator procedure.
4. Record elapsed time and every manual prerequisite.
5. Verify identity, startup, configuration, repairs, logs, critical
   integrations, add-ons, Engineering runtime, upstream admission, governance
   persistence, and task persistence where applicable.
6. Record omissions, ambiguous outcomes, and the exact point at which the
   recovery objective was met or failed.
7. Destroy or sanitize the disposable environment under its separate lab
   policy.

## Evidence result

The drill record should state `passed`, `failed`, or `incomplete`; tested
versions; backup fingerprint; environment identity; duration; observed gaps;
and the date after which the result requires review. A successful drill is
evidence, not permanent proof that a later backup or different version will
restore.

## Stop conditions

Stop if the environment is not isolated, production credentials would be
required, archive scope is unknown, a live endpoint would be contacted,
version identity cannot be established, or cleanup authority is absent.

## Non-actions

E1 does not run a drill, restore a backup, access an archive, create a lab,
handle credentials, or contact Home Assistant.
