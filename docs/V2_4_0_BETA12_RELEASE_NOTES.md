# Engineering 2.4.0-beta.12 release notes

Beta.12 adds an **offline automation-baseline validator and comparator**. Given
two retained local baselines, it reports `UNCHANGED`, `CHANGED`, `ADDED`,
`REMOVED` or `UNKNOWN` according to the evidence available. Entity-ID renames and
enabled-state changes are reported separately. This is a source-checkout utility,
not a new MCP tool or an automatic Home Assistant capture.

## Delivered behavior

- A bounded, versioned baseline format binds canonical configuration identity,
  installation and inventory scope, capture times, provider coverage and an
  explicit configuration fingerprint model.
- Definitive shared-object comparisons require established matching identities,
  complete readable records, authority continuity and the same supported exact
  fingerprint model. `ADDED`/`REMOVED` require authoritative inventory absence;
  failed reads and incomplete inventories cannot manufacture deletion evidence.
- The exact fingerprint model is `automation-audit-config-json-python-v1`.
  It uses sorted object keys, preserved array order, compact Python JSON,
  `ensure_ascii=True`, finite JSON values and UTF-8 before SHA-256. Operational
  enabled state and provider envelopes are outside the configuration digest.
- Strict input, timestamp, Unicode and accounting validation returns fixed
  diagnostics without reflecting rejected content. File reads and report output
  are bounded; summary counts include details omitted by output limits.

Run from a verified source checkout with its validated Python environment:

```sh
python scripts/compare_automation_baselines.py EARLIER.json LATER.json \
  --output comparison.json
```

The command reads the two named local files and optionally writes the named local
report. It does not contact Home Assistant. See the
[format, comparison rules and limits](AUTOMATION_AUDIT_BASELINE.md).

## Retained evidence and limits

The retained October 1 baseline is preserved, not recaptured or rewritten. Its
installation identity is unestablished, its 100-result inventory does not prove
absence, and its original fingerprint declaration does not settle Unicode or
numeric serialization. The validator therefore retains an unresolved legacy
model. Self-comparison yields **100 `UNKNOWN`**, not 100 `UNCHANGED`.

Matching configuration hashes cover only the captured representation, not
continuous equality, external blueprint bodies or actual execution. Capture
intervals remain non-atomic. Native baseline collection, complete indexed
inventory, server persistence and a public baseline/diff tool remain future work.

## Preserved release boundaries

The existing healthy catalog remains **81 tools: 56 static and 25 delegated**.
Tool descriptors, provider routing, approval/execution behavior and all 20 Core
profile fingerprints are unchanged. The existing signed registry journal is
unchanged; this release needs no new capability extension, signing or activation.
Installed authority still depends on current exact identity and valid signed
applicability. The offline utility adds no installed-Core version pin.

Beta.11's Alarmo inspector, beta.10's bounded logbook processing, beta.9's
cold-health work and their documented limitations remain. There is no new
read/write endpoint, fallback, dependency, workflow, storage migration or
deployment-option change. Stable v1.1.2 remains frozen; 2.3.0 remains the last
accepted stable release.

These notes describe source behavior, not completed publication or installed
acceptance. Follow the [beta.12 acceptance contract](V2_4_0_BETA12_ACCEPTANCE.md).
Do not repeat completed household canaries, Alarmo activation, supplementary task
verification or a 100-automation capture solely to validate this offline change.
