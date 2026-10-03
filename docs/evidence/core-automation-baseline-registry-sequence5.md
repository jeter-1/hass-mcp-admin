# Core automation-baseline applicability: sequence 5

This registry-only candidate adds the already shipped, read-only
`core.automation_baseline_metadata_read` contract to Core **2026.9.4**. It does
not change Engineering 2.4.0-beta.14, its image, tool descriptors, or deployment.
Owner signing completed on October 2, 2026; publication and installed admission
remain separate from this signed candidate.

## Exact signed change

| Binding | Value |
| --- | --- |
| Engineering implementation source | `ab200974f40a265b93affffc7de7ffb42eafd6fa` |
| Reviewed implementation candidate | `17201c71af262d8f5b44f28ceb6358bbb58f2c42` |
| Shared implementation tree | `654bab208b2c91ad9125818332fb1487349e1389` |
| Predecessor sequence-4 journal SHA-256 | `176d4b5bcb15762bef2221f2823a945276f6be95b2577daab850786185385a9e` |
| Reviewed unsigned candidate SHA-256 | `bc1d511a1706551068175d8386f46ab89b7eb70fd81a8e8750608902eeb4cb1c` |
| Signed sequence-5 journal SHA-256 | `c39c5ccb740fc09fac934165e735b79cb6f6d375121ddd7f8273ee02bcc0d013` |
| Extension evidence SHA-256 | `94b08bebd7a967d76a110f95e7665e1556868f02c2480a15e0913716b461a0f2` |
| Existing public-key SHA-256 | `7793e80f480b573fbef4e7ca762ff9d1ac783fee1e253db7c35ba8fe21f63dac` |

The sole additional reference is profile
`core_automation_baseline_metadata_read_v1`, version 1, adapter
`compiled-core-automation-baseline-read-v1`, contract fingerprint
`sha256:42ea1b1731aa0d73e01717aabfa99639c29af2755d8d341c39b4789256a3c540`.
The [canonical extension evidence](../../upstream-trust/evidence/ha-core-2026.9.4-94b08bebd7a967d76a110f95e7665e1556868f02c2480a15e0913716b461a0f2.json)
is the exact byte sequence bound by the new Core entry's evidence digest.

Core 2026.9.4 retains its original 20 references as an identical ordered prefix
and gains this twenty-first reference. Its source, image, architecture and probe
identity remain unchanged. Core 2026.9.2 retains 17 references and Core 2026.9.3
retains 19; both entries remain unchanged. All four historical envelopes and the
existing trust key are preserved, and no revocations are added.

The shared envelope is generated at `2026-10-02T18:22:43Z` and expires at
`2026-12-31T18:22:43Z`. This renews the predecessor envelope's
`2026-12-30T00:41:48Z` expiry for retained sibling selections as well; it is not
an unchanged-expiry claim.

## Evidence supporting applicability

- [Exact candidate CI](https://github.com/jeter-1/hass-mcp-admin/actions/runs/37029045017)
  passed all 34 jobs at the reviewed implementation candidate. Source discovery
  ran 4,545 tests: 4,521 passed and 24 skipped. Retained local Full/Evidence at
  that candidate passed 15/15 gates, with 4,525 tests passed and 20 skipped.
- The same CI's Core 2026.9.4 assembled capture lane retained two reconstructed
  123-record exports, all five comparison classifications, zero continuation
  reads, predecessor-admission refusal, bounded command intervals and cleanup.
  Disposable lineage is not proof of an installed Supervisor identity.
- [Protected publication](https://github.com/jeter-1/hass-mcp-admin/actions/runs/37031867676)
  passed all 37 jobs at the implementation source. Its OCI index is
  `sha256:0d69bfd1bb920683e739f3ff151698f8eff2810cd6d4d27d7b898361e6fcca77`.
- The extension evidence binds 16 retained review, correction, source, CI,
  publication and installed-read artifacts by digest. Private receipts remain
  private. Their presence is not a claim that they are independently retrievable
  from this public repository.
- The separate registry-preparation review passed 32 synthetic offline tests
  with no failures, errors or skips and found no actionable defect. Its report
  SHA-256 is `bae792507764b43e04c9651285dae5a01b286044038c6930cdb6d62e0381a289`.
  These checks cover signing and refusal behavior; they are not a new live
  acceptance run.

The owner-produced journal was verified using the pinned public key, every
envelope signature, the outer signature, exact unsigned candidate regeneration
and the shipped runtime parser. No assistant accessed the owner's private key.
Final PR validation and signed-output review are recorded with the PR; the
implementation receipts above are retained evidence rather than that PR's tests.

## Security, activation and recovery boundaries

The compiled adapter permits bounded administrator-visible loaded automation
inventory, canonical mapping and complete configuration hashing, discards raw
bodies, and returns a frozen sanitized export. This extension grants no new
write, service, options-flow, physical-action, arbitrary-forwarding or fallback
path. Stable v1.1.2, workflows, schemas and release metadata are unchanged.

Before publication/activation, reconcile current Core identity, concurrent work,
plans, approvals, dispatches, leases and commits in a settled window. A new
selected entry can replace authority generation and invalidate outstanding
immutable plans or leases. Preserve stale plans without replay or automatic
reapproval. Allow normal verified registry refresh; no forced restart, cache
deletion or configuration change is part of this update.

Installed acceptance still requires observed sequence 5 with 21 admitted
profiles, explained authority transitions, the fresh raw 82-descriptor public
catalog, and one useful baseline capture with only its returned continuations.
Preserve the existing panel/image evidence and historical qualifications. Do not
manufacture a second capture merely for comparison.

Withdrawing an unmerged draft has no runtime effect. After publication, restoring
sequence 4 or deleting a cache is not a supported rollback. Stop using the new
tool while investigating a problem. The supported revoke operation denies the
whole Core 2026.9.4 entry, affecting all 21 profiles; any such withdrawal requires
a separate reviewed owner decision. See the [registry lifecycle and recovery
contract](../CORE_RELEASE_REGISTRY.md).
