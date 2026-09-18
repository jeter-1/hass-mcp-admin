# ha-mcp 8.5.0 compatibility candidate

This source candidate adds exact ha-mcp 8.5.0 contracts for Engineering's existing
read, governed-dashboard and typed-fan operations. It does not authorize an
upstream upgrade, change production trust, or establish installed acceptance.
Existing supported releases and the current public tool schemas are retained.

## Admission and provenance

The upstream source is `311d6dc273fb4e9a5b8cde0de15f69472a64fe44`, tree
`e21f9674a057b6aa1d6168e22fabbeaee26a6a3c`. The standalone OCI index is
`sha256:e1538bcdadb13a5467bbb8258fba3262a05518585e18115742da638adec7057c`.
The exact standalone and add-on platform manifests are recorded in the
[contract evidence](evidence/upstream-read-compatibility/ha-mcp-8.5.0-contract-review.json).

The independently reviewed assessment at `bde748c66b57dc696821c05c768b2a8b574a0db0`
ran both packaging variants on native amd64 and arm64 in CI run
[35261708885](https://github.com/jeter-1/hass-mcp-admin/actions/runs/35261708885).
It captured complete, repeated 77-tool catalogs, representative reads, synthetic
fan ON/OFF and dashboard forward/restoration operations, independent Core
readbacks and cleanup. These are upstream artifact observations, not execution
of this new Engineering candidate. The image revision label
`8182c6e6db68b37ae7041dec6dd4ade7b7356fb7` differs from the tag source; the retained
comparison attributes that difference to release metadata/changelog, with runtime
and dependency-lock equivalence qualified. No reproducible-build or independent
attestation-signer authentication claim follows.

The [normalized capture](evidence/upstream-read-compatibility/ha-mcp-8.5.0.json)
retains the complete standalone descriptors. Operational policy-state projection
permits only the existing reviewed standalone/add-on metadata difference. Raw
captures remain preserved. Internal negotiation remains `2025-03-26`.
Error-shape bindings additionally use exact-source execution against a disposable
local fixture. Missing-state and invalid-blueprint errors were observed in the
actual images; the remaining error probes require candidate integration coverage.

## Blueprint read contract

The public name and schema remain `ha_get_blueprint`. An absent/null path maps
to `ha_manage_blueprints(action="list")`; a validated installed relative YAML
path maps to `action="get"`. The compiled adapter constructs both requests.
Caller-supplied action, URL, YAML, overwrite, confirmation and input mappings are
rejected before dispatch. Import, save, delete and substitute remain unavailable.
The upstream tool remains classified mixed; a bounded read projection does not
make the full upstream tool an automatic read.

Metadata-only results remain metadata-only and disclose unavailable installed
configuration. When supplied, complete configuration and YAML retain their
provenance. Component, in-process file and File & YAML Tools reads identify
installed content; a `source_url` result is a fresh upstream download and does
not prove the installed file. Engineering performs no external fetch or fallback.
Existing output bounds, sanitization and partial-result disclosures still apply.

## Fan and dashboard contracts

Fan preparation, state checks, dispatch and verification select the same exact
release/source contract. A release swap refuses; registry denial, expiry and
retirement remain authoritative. Calls still target one fan, use fixed service
arguments with `wait=false`, and retain ordinary connector authorization,
at-most-once dispatch, target-local uncertainty holds and audit attribution.

The historical `ordinary-fan-v1` format is unchanged. A stored prepared hash is
matched against the closed compiled contract projections; exactly one must match.
The original 8.4.3 hash remains interpretable and is never relabeled as 8.5.0.
Reconciliation cannot grant an old operation new dispatch authority. Historical
synthetic bytes were produced by the exact shipped beta.1 writer, with
[fixture provenance](../tests/fixtures/fan_beta1/provenance.json).

Dashboard operations retain complete configuration compilation, exact preread
hashes, full approval disclosure, authenticated approval consumption, one intent
and independent complete readback. The existing full-config setter contract is
used; new upstream JSON Patch/transform operations are not exposed. Native and
legacy upstream saves still require the no-concurrent-editor condition. Provider
acknowledgement is not Engineering verification, and uncertain delivery never
authorizes a second write.

## Validation and adoption boundary

The implementation requires focused source review and clean-candidate Evidence.
Release preparation must close the unchanged-version gate. Actual candidate
container testing must exercise both upstream packaging variants, the supported
architectures and the pinned Core pairing, including blueprint completeness,
fan duplicate/uncertainty/recovery controls, dashboard approval/apply/restoration,
provider drift, errors and cleanup. The earlier two-job upstream assessment is
supporting evidence, not candidate CI.

Production adoption requires a separately authorized release, deployment and
installed-path acceptance. Preserve prior acceptance under its original version
and pairing. No live test, production credential, trust change, canary or upgrade
is performed by this implementation. Local recovery is an ordinary source revert;
it cannot undo a later deployment or a physical action.
