# Post-update health, repair, and log verification

Status: future verification design; not implemented

## Current source-derived facts

E1 only validates that one target-appropriate verification profile has been
selected. It performs no health, repair, log, identity, configuration, or
provider reads.

## Proposed common verification contract

Every future update task should preserve the pre-update baseline and collect
bounded post-disruption evidence for:

- exact target identity and installed candidate version;
- expected disruption and subsequent recovery timestamps;
- Home Assistant, Supervisor, or target health as applicable;
- new and unresolved repairs, with critical repairs treated as failures;
- bounded error logs covering the update and recovery window;
- configuration validity where applicable;
- required integrations, entities, add-ons, and user-facing functions;
- governance, audit, and durable task persistence;
- Engineering runtime identity and the unchanged public tool contract; and
- reviewed upstream identity, compatibility admission, catalog, and zero
  fallback when upstream `ha-mcp` is involved.

Current availability alone must not prove that the expected disruption or
update occurred. Provider acknowledgement alone must not prove post-update
health.

## Proposed profiles

| Profile | Additional required evidence |
| --- | --- |
| Home Assistant Core | exact Core version, configuration, repairs, logs, critical integrations |
| Supervisor | exact Supervisor version, managed-system health, add-on inventory readability |
| Home Assistant OS | exact OS version, reboot evidence, Supervisor and Core recovery |
| add-on/app | exact slug/identity, version, startup state, bounded add-on logs |
| HACS | exact repository/component identity, loaded integration or frontend asset, relevant logs |
| Engineering MCP server | exact build identity, governance/audit/task persistence, 48/26/74 contract |
| upstream `ha-mcp` | exact reviewed identity and admission, delegated-read contract, zero fallback |
| firmware | exact entity/device identity, reported firmware, device availability and critical function |

## Outcomes

- `verified`: all required evidence is complete and matches the task.
- `verification_pending`: bounded evidence is temporarily unavailable and
  readback may continue without redispatch.
- `verification_failed`: collected evidence proves mismatch, critical failure,
  or loss of required function.
- `manual_review_required`: evidence is incomplete or ambiguous after its
  bounded collection window.

A failed or manually reviewed result proceeds to the separate downgrade versus
restore decision. It never authorizes an automatic retry of the update.

## Non-actions

This profile does not collect logs, invoke repairs, retry an update, restart a
target, change admission, or perform rollback or restore.
