# Engineering 2.4.0-beta.13 release notes

Beta.13 carries forward the reviewed **offline automation-baseline validator and
comparator** and includes the merged Alarmo responsiveness-test isolation
correction. It supersedes the unpublished beta.12 candidate. Beta.12's protected
source merged, but publication validation failed before its publish job ran;
the failed evidence and historical source remain unchanged.

## Offline comparison

From a verified source checkout and validated Python environment:

```sh
python scripts/compare_automation_baselines.py EARLIER.json LATER.json \
  --output comparison.json
```

The utility reads two explicitly named local baselines and optionally writes a
local report. It does not contact Home Assistant or expose a new MCP tool.

- Definitive `UNCHANGED`/`CHANGED` requires established matching installation and
  scope, verified canonical identities, complete readable records, continuous
  capture authority and the same supported exact fingerprint model.
- `ADDED`/`REMOVED` additionally requires authoritative inventory absence.
  Failed reads and incomplete inventory remain `UNKNOWN` where evidence is
  insufficient.
- Captures must be distinct and chronologically ordered: reversed or overlapping
  intervals, equal baseline IDs or equal non-null source-artifact digests produce
  `UNKNOWN` classifications with fixed reasons. Reports include both intervals.
  Exact adjacent boundaries are permitted; missing source digests are not proof
  of duplication. Verified entity-ID and definite enabled-state changes are
  separate annotations, subject to the same pair validity.
- `automation-audit-config-json-python-v1` hashes finite JSON-native configuration
  values using sorted object keys, preserved array order, compact Python JSON,
  `ensure_ascii=True` and UTF-8. Enabled state and provider envelopes are excluded.
- Strict bounded inputs and output retain deterministic accounting and fixed
  diagnostics without reflecting rejected content.

See the [format, comparison rules and limitations](AUTOMATION_AUDIT_BASELINE.md).
The preserved October 1 baseline lacks established installation identity, complete
inventory and an exact original serialization contract. Its normalization stays
legacy/unresolved; self-comparison remains **100 `UNKNOWN`**. Nothing upgrades
those old hashes or manufactures a second household capture. Native collection,
complete indexed inventory and a public baseline/diff tool remain future work.

## Validation correction

Full-suite discovery left unrelated objects in the process running Alarmo's
maximum-inventory timing test. Recorded major-GC pauses accompanied alternating
pass/fail results on unchanged source. PR #226 runs the same fixture in a fresh
isolated interpreter with natural GC enabled, unchanged workload and all ten
assertions, including the **100 ms CPU / 300 ms wall** limits. Child failure and
timeout fail the parent without retry; diagnostics and full-suite test accounting
are preserved.

This changes the test environment. It does not change runtime GC or establish a
production-wide latency guarantee. See the
[measurement boundary](INTEGRATION_INSPECTION.md#responsiveness-regression-environment).

## Release boundaries

The preparation base is protected main
`b8669a306e5cb05932bce38b6f36e81eb55a3fb3`. Relative to that base, beta.13 changes
only version metadata and release/acceptance documentation. The healthy catalog
remains **81 tools: 56 static + 25 delegated**. Existing descriptors, 20 Core
profile fingerprints, signed registry/trust roots, providers, approval/execution,
dependencies, workflows, persistent formats and deployment options are unchanged.
No new signing, capability activation, installed-Core pin or migration is needed.
Stable v1.1.2 is frozen; 2.3.0 remains the last accepted stable release.

Publication, deployment and installed acceptance are separate gates. A future
beta.13 artifact must bind to its own protected version-transition merge, never
to a replacement beta.12 identity. Follow the
[beta.13 acceptance contract](V2_4_0_BETA13_ACCEPTANCE.md). Preserve completed
Alarmo activation, household canaries and F028 supplementary verification;
no repeat write canary or new 100-automation capture is required for this release.
