# Engineering 2.4.1-beta.4 release notes

Engineering 2.4.1-beta.4 adds governed entity-registry metadata administration
(HAMCP-069A-1) on top of beta.3. One new Engineering-native proposal tool,
`create_entity_metadata_plan`, plans a single entity's registry `name`, `icon`,
`aliases`, `area_id`, `hidden`, existing `labels` and `categories`. Approval,
execution and reversal use the existing `approve_change_plan`,
`apply_change_plan` and `rollback_change` tools. The approved design and every
implementation deviation (V1-V10) are in the
[HAMCP-069A design](HAMCP_069A_ENTITY_METADATA_DESIGN.md).

**The capability is dormant on every supported Core.** It requires the new
Core profile `core.entity_registry_metadata_operation`, which no signed Core
registry entry selects. Until a separate owner-signed registry change admits
it, planning and execution fail closed with `CoreCapabilityUnavailable` and
send nothing to Home Assistant. Installing beta.4 therefore makes no entity
metadata write reachable. The disposable Real HA lanes keep signing exactly
their previous capability sets; the dormant profile is excluded there.

When admitted, a plan is an immutable configuration-family (contract v2) plan
bound to the entity's current registry fingerprint, the referenced label, area
and category definitions and the consumer-evidence fingerprint. Execution sends
exactly one closed `config/entity_registry/update` message built from an
allowlist of the touched fields, after a stale-state recheck, and verifies the
result by exact readback. It does not run Home Assistant's configuration check
(V8), because a registry update reads no YAML. Unknown or unsupported fields
(entity renames, enable/disable, exposure, device fields, multiple targets,
label creation) are refused before any Home Assistant read with
`entity_metadata_field_not_supported`. The tool rejects undeclared arguments
instead of silently ignoring them.

Policy uses the existing approval classes. Label and area changes on
actuator-class entities and alias changes on locks, alarm panels and garage,
gate or door covers require elevated approval with an explicit consequence
acknowledgement. Other changes are standard, with disclosure. A request whose
final state already equals the current state is refused as a no-op without
creating a plan.

If the approved final state is already present when the plan is applied, the
result reads `already_present` (`desired_state_already_present_not_dispatched`),
states that Engineering did not make the change, and dispatches nothing. That
plan is terminal: re-applying it never dispatches, even after the entity
changes back to its original state, and it cannot be re-approved or rolled
back. Rollback of a dispatched plan restores the recorded pre-state through a
new governed plan and refuses after a later edit.

The healthy catalog is **84 tools: 59 native and 25 delegated reads** (beta.3:
83 and 58). No existing public descriptor changes. The Core inventory now
represents 22 capability profiles; the probe fingerprints, the 21 existing
capability contracts and every signed registry entry are unchanged. Upstream
`ha_set_entity` is not used.

Stable v1.1.2, dependency locks, build inputs, durable formats, workflow
permissions, the support window and the signed Core and upstream registries are
unchanged. Label creation (HAMCP-069A-2/HAMCP-165), enable/disable and exposure
(069B), and the shared no-dispatch projection for other capabilities
(HAMCP-167) are not included.

Follow the [beta.4 acceptance contract](V2_4_1_BETA4_ACCEPTANCE.md).
