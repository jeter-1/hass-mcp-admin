# Engineering 2.4.0-beta.14 release notes

Beta.14 adds `capture_automation_baseline`, a bounded native read that produces
an export accepted by the existing offline automation-baseline comparator.
It closes the collection gap left by beta.12/beta.13's offline utility. It does
not retroactively strengthen the retained October 1 baseline's incomplete
identity, inventory or fingerprint evidence.

The capture covers loaded automations, including those switched off. Registry-
disabled entities and configurations that are not loaded are outside that scope.
It verifies canonical configuration IDs, hashes the complete returned JSON under
the unchanged exact fingerprint model, and discloses unreadable configurations,
unmapped objects, drift and limits. Raw configuration bodies are not retained.
A removed record means absence from the comparable loaded scope, not deletion
from disk. Captures are bounded intervals, not atomic snapshots.

Installation assurance uses the uniquely loaded Hass.io entry and its persisted
Core device identity. A changed persisted device/config-entry ID changes the lineage; restoring a
tombstoned device with the same IDs preserves it. Restored clones may share it. No owner label or fallback identity substitutes for that evidence.
Continuation pages come from a caller-bound in-memory snapshot and perform no
additional Home Assistant reads. See [capture semantics and limits](AUTOMATION_BASELINE_CAPTURE.md).

The source catalog becomes **82 = 57 static + 25 delegated**. All previous 81
descriptors and 20 Core profile fingerprints are preserved. The new
`core.automation_baseline_metadata_read` profile requires separately reviewed
signed applicability. The current signed journal is unchanged; installation of
beta.14 alone does not activate the new read. Public enumeration and capability
admission are separate observations.

The required existing Core 2026.9.4 disposable lane gains a synthetic native
capture/export/comparison scenario and observed read intervals. Its synthetic
Hass.io registry anchor proves the Core metadata contract, not a running
Supervisor installation. Offline adversarial transport/coverage tests remain
separate evidence. No workflow permissions or image/dependency pins change.

No configuration writes, background captures, automatic second scan, arbitrary
endpoint/provider selection, retry, redirect or fallback are added. Existing
planning, approval, execution, recovery, Alarmo inspection, logbook and health
behavior remain unchanged. Stable v1.1.2 remains frozen. A downgrade discards the
new transient snapshots; it must preserve durable execution and approval history.

This document defines the candidate. Publication, signed activation, deployment
and [installed acceptance](V2_4_0_BETA14_ACCEPTANCE.md) require their own evidence
and authority. Do not repeat completed household operations to test this release.
