# Documentation map

What is in `docs/` and where to start. The product overview, install steps and
the current release are in the [root README](../README.md). Release history is
in the [release index](RELEASES.md); this map lists no per-release history.

## Start here

- [Codex Development Workflow](CODEX_WORKFLOW.md)
- [Beta provider security boundaries](SECURITY.md)
- [Feature Readiness Map](FEATURE_READINESS_MAP.md)
- [Read-Coverage Matrix](READ_COVERAGE_MATRIX.md)
- [Governed operational administration](OPERATIONAL_ADMINISTRATION.md)

## Install, inbound access and build

- [Engineering MCP Host and Origin policy](INBOUND_SECURITY.md)
- [Private inbound topology observation](INBOUND_TOPOLOGY_CAPTURE.md)
- [Engineering build inputs and architecture policy](BUILD_INPUTS.md)
- [Rate limiting and client identity](RATE_LIMITING.md)
- [Governance health cache correctness and responsiveness](HEALTH_PERFORMANCE.md)

## Governed changes, approval and audit

- [Beta automation change governance](CHANGE_GOVERNANCE.md)
- [External human approval authority](EXTERNAL_APPROVAL.md)
- [Governed execution and bounded response recovery](GOVERNED_EXECUTION_RECOVERY.md)
- [Supplementary configuration-task re-verification](CONFIGURATION_REVERIFICATION.md)
- [Audit-log contract](AUDIT_LOG.md)
- [Beta error taxonomy](ERROR_TAXONOMY.md)
- [Token-efficient analytical response contract](TOKEN_EFFICIENCY.md)

## Typed device control

- [Typed ordinary fan control (HAMCP-141)](TYPED_FAN_CONTROL.md)
- [Ordinary light and switch ON/OFF](TYPED_POWER_CONTROL.md)

## Analysis and reads

- [Entity dependency analysis](ENTITY_DEPENDENCY_ANALYSIS.md)
- [Bounded script-call dependency analysis](SCRIPT_CALL_DEPENDENCIES.md)
- [Change-impact analysis](CHANGE_IMPACT_ANALYSIS.md)
- [Configuration Integrity Analysis](CONFIGURATION_INTEGRITY_ANALYSIS.md)
- [Dashboard integrity analysis — implementation checkpoint](DASHBOARD_INTEGRITY_ANALYSIS.md)
- [Single-automation reliability analysis](AUTOMATION_RELIABILITY_ANALYSIS.md)
- [Static automation lifecycle analysis](AUTOMATION_LIFECYCLE_ANALYSIS.md)
- [Native loaded-automation baseline capture](AUTOMATION_BASELINE_CAPTURE.md)
- [Automation audit baseline — offline validation and comparison](AUTOMATION_AUDIT_BASELINE.md)
- [Trigger-condition verification](TRIGGER_CONDITION_VERIFICATION.md)
- [Sun-condition step verification (HAMCP-252)](SUN_CONDITION_VERIFICATION.md)
- [Incident correlation](INCIDENT_CORRELATION.md)
- [Handoff generation contract](HANDOFF_GENERATION.md)
- [Bounded Alarmo configuration inspection](INTEGRATION_INSPECTION.md)
- [Bounded native logbook reads](LOGBOOK_READS.md)
- [Bounded Core log history (2.4.0-beta.2 candidate)](CORE_LOG_HISTORY.md)
- [Held-read canary error taxonomy](HELD_READ_CANARY_ERROR_TAXONOMY.md)

## Core and upstream trust

- [Core compatibility updates without rebuilding Engineering](CORE_RELEASE_REGISTRY.md)
- [Owner runbook: Core registry one-tap cutover (HAMCP-278)](CORE_REGISTRY_ONE_TAP_RUNBOOK.md)
- [Core reconciliation failure attribution](CORE_AUTHORITY_DIAGNOSTICS.md)
- [Upstream trust registry](UPSTREAM_TRUST_REGISTRY.md)
- [Upstream compatibility operator guide](UPSTREAM_COMPATIBILITY_OPERATOR_GUIDE.md)
- [Home Assistant Core capability-scoped readmission](HA_CORE_CAPABILITY_AUTO_READMISSION.md)
- [ha-mcp 8.6.0 compiled compatibility candidate](HA_MCP_8_6_0_COMPATIBILITY.md)
- [ha-mcp 8.5.0 compatibility candidate](HA_MCP_8_5_0_COMPATIBILITY.md)
- [Core 2026.10.0 local compatibility evidence](CORE_2026_10_COMPATIBILITY_EVIDENCE.md)

## Release and publication operations

- [Publication recovery runbook (HAMCP-223)](PUBLICATION_RECOVERY_RUNBOOK.md)
- [Instruction-guidance reconciliation for RC2](INSTRUCTION_GUIDANCE_RECONCILIATION.md)
- [Terminal history projection correction](TERMINAL_HISTORY_PROJECTION_CORRECTION.md)

## Runbooks

Recovery and restore procedures in [`runbooks/`](runbooks/):

- [Downgrade versus backup restore decision](runbooks/DOWNGRADE-VERSUS-BACKUP-RESTORE.md)
- [Last-known-good configuration recovery](runbooks/LAST-KNOWN-GOOD-CONFIGURATION-RECOVERY.md)
- [Manual restore drill](runbooks/MANUAL-RESTORE-DRILL.md)
- [Post-update health, repair, and log verification](runbooks/POST-UPDATE-RECOVERY-VERIFICATION.md)
- [Recover governance and task persistence](runbooks/RECOVER-GOVERNANCE-AND-TASK-PERSISTENCE.md)
- [Safe-mode recovery](runbooks/SAFE-MODE-RECOVERY.md)
- [Update backup prerequisite policy](runbooks/UPDATE-BACKUP-PREREQUISITE-POLICY.md)

## Architecture decisions

[`architecture/`](architecture/) holds 24 ADRs. The two that govern every change are
[ADR-021: Home Assistant source authority](architecture/ADR-021-HOME-ASSISTANT-SOURCE-AUTHORITY.md) and
[ADR-022: owner-authoritative product direction](architecture/ADR-022-OWNER-AUTHORITATIVE-PRODUCT-DIRECTION.md).

## Design records

Per-task designs, investigations and reviews. They record the reasoning at
the time; current behavior is in source and the reference documents above.

- [F2 Historical Policy Projection Compatibility](F2_HISTORICAL_POLICY_PROJECTION_COMPATIBILITY.md)
- [F3-A durable adapter execution and locking core](F3_ADAPTER_LOCK_CORE.md)
- [F3-B governed storage-dashboard write foundation](F3_B_DASHBOARD_WRITE_FOUNDATION.md)
- [F3-C1 configuration-adapter conformance](F3_C1_CONFIGURATION_CONFORMANCE.md)
- [F3 current-state execution inventory](F3_CURRENT_STATE_INVENTORY.md)
- [Governed existing-dashboard update contract](F3_DASHBOARD_WRITE_CONTRACT.md)
- [F3-C2 operational-adapter conformance](F3_OPERATIONAL_CONFORMANCE.md)
- [F3 parallel-development plan](F3_PARALLEL_DEVELOPMENT_PLAN.md)
- [F3-D runtime integration](F3_RUNTIME_INTEGRATION.md)
- [HAMCP-135 bounded configuration eligibility](HAMCP135_CONFIGURATION_ELIGIBILITY.md)
- [HAMCP-135 exact governed inverse](HAMCP135_GOVERNED_ROLLBACK.md)
- [HAMCP-069A governed entity metadata administration — design](HAMCP_069A_ENTITY_METADATA_DESIGN.md)
- [HAMCP-069B governed entity enable/disable and voice-assistant exposure — design](HAMCP_069B_ENABLE_EXPOSE_DESIGN.md)
- [HAMCP-071 governed scene configuration edit (built inactive)](HAMCP_071_SCENE_CONFIGURATION_DESIGN.md)
- [HAMCP-089 exact helper-state action](HAMCP_089_EXACT_HELPER_STATE.md)
- [HAMCP-106 governed dashboard metadata (built inactive)](HAMCP_106_DASHBOARD_METADATA_DESIGN.md)
- [HAMCP-129 delegated platform health, installed apps and integration status — design](HAMCP_129_PLATFORM_READS_DESIGN.md)
- [HAMCP-130 — Backup Inventory (`get_backup_inventory`) — Design Note](HAMCP_130_BACKUP_INVENTORY_DESIGN.md)
- [HAMCP-131: Incident-Window Log Reads and Continuable History](HAMCP_131_INCIDENT_LOGS.md)
- [HAMCP-132 Effective Recorder Settings and Definition Visibility: Design Note](HAMCP_132_DEFINITION_VISIBILITY_DESIGN.md)
- [HAMCP-132 slice 1 implementation notes](HAMCP_132_SLICE1_IMPLEMENTATION_NOTES.md)
- [HAMCP-132 Slice 2, Part 0: `ha_mcp_tools` 2.2.1 Exact-Source Review](HAMCP_132_SLICE2_COMPONENT_REVIEW.md)
- [HAMCP-155 governed deletion of automations and scripts — design](HAMCP_155_GOVERNED_DELETION_DESIGN.md)
- [HAMCP-162 delegated read-only YAML configuration reader — design](HAMCP_162_YAML_READER_DESIGN.md)
- [HAMCP-164 — Core Gateway Fails Closed for Unmapped Tools — Design Note](HAMCP_164_CORE_GATE_FAIL_CLOSED_DESIGN.md)
- [HAMCP-165 governed label create, edit and assignment — design](HAMCP_165_LABELS_DESIGN.md)
- [HAMCP-171 health and dependency-index performance investigation](HAMCP_171_HEALTH_PERFORMANCE_INVESTIGATION.md)
- [HAMCP-181 / HAMCP-167 — Truthful F3 Outcome Taxonomy for No-Call and No-Dispatch Results (Design Report)](HAMCP_181_TRUTHFUL_OUTCOMES_DESIGN.md)
- [HAMCP-184 — Audit Hardening Batch (F-05, F-01, F-02, F-06) — Design Note](HAMCP_184_AUDIT_HARDENING_DESIGN.md)
- [HAMCP-187: upstream ha-mcp 8.7.0 review](HAMCP_187_HA_MCP_870_REVIEW.md)
- [HAMCP-189 PR A — Core authority before "already satisfied", R6, durable cancel](HAMCP_189_PR_A_DESIGN.md)
- [HAMCP-189 PR B + C — design note](HAMCP_189_PR_B_DESIGN.md)
- [HAMCP-189 — Triage of HAMCP-164 Residuals R1–R8 and Codex Lows L1/L2](HAMCP_189_TRIAGE.md)
- [HAMCP-191 implementation notes](HAMCP_191_IMPLEMENTATION_NOTES.md)
- [HAMCP-191: Identity-Only Redaction Markers for Every Tool (Design Sweep)](HAMCP_191_SHARED_SANITIZER_DESIGN.md)
- [HAMCP-192 — Observing-task fix: design note](HAMCP_192_OBSERVING_FIX_DESIGN.md)
- [HAMCP-192 — Typed power/fan tasks left "observing" after a slow device report](HAMCP_192_OBSERVING_INVESTIGATION.md)
- [HAMCP-195 risk-gated fast toggles for input_boolean helpers — design](HAMCP_195_GATED_HELPER_TOGGLE_DESIGN.md)
- [HAMCP-196: Engineering add-on permissions review (read-only)](HAMCP_196_ADDON_PERMISSIONS_REVIEW.md)
- [HAMCP-205: shared administrator verification for Ingress approval pages](HAMCP_205_ADMIN_VERIFICATION_DESIGN.md)
- [HAMCP-209 Fast Dependency Index: Design Note](HAMCP_209_FAST_DEPENDENCY_INDEX_DESIGN.md)
- [HAMCP-210: `pull_request_target` restriction on public repositories (read-only)](HAMCP_210_PULL_REQUEST_TARGET.md)
- [HAMCP-212 Loaded Script Configuration: Design Note](HAMCP_212_LOADED_SCRIPT_CONFIG_DESIGN.md)
- [HAMCP-213: Safety-Critical Owner Approval and Physical-Action Equivalence](HAMCP_213_SAFETY_CRITICAL_APPROVAL_DESIGN.md)
- [HAMCP-215: Core authority recovery and visibility](HAMCP_215_CORE_AUTHORITY_RECOVERY_DESIGN.md)
- [HAMCP-218: What the Supervisor `manager` role unlocks (read-only)](HAMCP_218_SUPERVISOR_MANAGER_ROLE.md)
- [HAMCP-220: Incident-Log Entry Times](HAMCP_220_INCIDENT_LOG_TIMESTAMPS.md)
- [HAMCP-222: signed-coverage audit and proposed seq-13 batch](HAMCP_222_SIGNED_COVERAGE_AUDIT.md)
- [HAMCP-224 host journal sources for `get_incident_logs` (journald option C) — design](HAMCP_224_HOST_JOURNAL_DESIGN.md)
- [HAMCP-225, HAMCP-226, HAMCP-228 Audit Tooling: Design Note](HAMCP_225_226_228_AUDIT_TOOLING_DESIGN.md)
- [HAMCP-232 + HAMCP-145 (input_select): governed edits to existing UI helpers and setting an input_select value — design](HAMCP_232_145_HELPER_OPTIONS_AND_SELECT_DESIGN.md)
- [HAMCP-253: inspect, then acknowledge stuck governance records](HAMCP_253_STUCK_RECORD_ACKNOWLEDGEMENT_DESIGN.md)
- [HAMCP-269: five-capability disposable Core evidence](HAMCP_269_SEQ15_CORE_EVIDENCE.md)

## Evidence

[`evidence/`](evidence/) holds source reviews, mutation runs, benchmarks and
signed-registry evidence. Many files there are hash-bound or recorded by path;
they are never edited or moved.

## Release history

- [Release index](RELEASES.md): every release's notes and acceptance contract,
  legacy-named records, release companions and feature acceptance contracts.
- Historical guidance, not current: [beta deployment](BETA_DEPLOYMENT.md) and
  the [2.1 roadmap](2_1_ROADMAP.md).
