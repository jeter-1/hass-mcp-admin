# Engineering 2.4.1-beta.2 release notes

Engineering 2.4.1-beta.2 adds the reviewed device-registry schema needed for
Home Assistant Core **2026.10.0**, while retaining exact **ha-mcp 8.5.0**.
October adds `next_name_part` to regular and child-device records. Beta.1's
closed September validator correctly refuses that new shape. This release
understands it through a separate code-owned probe; it does not weaken the old
validator or grant October authority automatically.

## Exact change and authority

The new probe is `core-probes-child-device-name-parts-v1`, fingerprint
`sha256:8dad86e454ca58ae558c9229ebd0fd7b748a3a2ff2a9bbb860a1f97a71eaee6b`.
It requires the naming value implied by the record's explicit area and parent.
Naming metadata does not create area, membership or target authority. Missing,
extra, contradictory, malformed or oversized records still refuse.

Existing probe fingerprints and September defaults remain unchanged. The
**21 capability contracts and fingerprints**, **83 complete public descriptors
(58 native + 25 delegated)**, exact ha-mcp 8.5.0 admission, provider boundaries,
durable formats, dependency locks, build inputs and stable v1.1.2 are preserved.
No new service, write, fallback, permission, journal format or compiled October
release entry is introduced. The signed production journals are unchanged.

October admission still requires a separately reviewed, owner-signed exact-Core
registry entry selecting this probe and the applicable existing capabilities,
followed by authenticated runtime observations. A signature cannot install the
parser in beta.1. Installing beta.2 likewise cannot substitute for that signature.
See the [Core registry contract](CORE_RELEASE_REGISTRY.md).

## Evidence and limits

The eight-file implementation at
`badbc08f61237dfeec934c852a3d9e77c953f9aa`, based on protected source
`c4168a61e919996c59d8763b77fcf78ffd99b6f7`, passed independent source review.
The exact Core 2026.10.0 / ha-mcp 8.5.0 native-amd64 disposable lane subsequently
passed, with independent receipt reconciliation across all 21 profiles:

- 11 Script-semantic methods; 326 garage checks / 319 distinct labels.
- Historical-writer migration and general configuration, registry, dependency,
  trace, helper, validation and governed-verification contracts.
- Alarmo inspection and native baseline capture, including withheld authority,
  explicit partial coverage and zero-read frozen continuation.
- Both standalone and add-on provider modes: 25 admitted reads each, typed
  fan/light/switch and governed dashboard operations, stale-state refusal,
  uncertain-outcome recovery, duplicate prevention and clean settlement.

These are retained executions, not new release CI or household acceptance. See
[the source-bound evidence summary](CORE_2026_10_COMPATIBILITY_EVIDENCE.md).
Arm64 execution, actual Supervisor lifecycle, physical device feedback, arbitrary
custom integrations and later October patches were not established by that lane.
The add-on ran through a fixed disposable Core relay; baseline identity used
synthetic Hass.io registry metadata. Captures remain non-atomic intervals.

## Installation and recovery boundaries

Josh's existing development-track installation, technical Beta identity and
single public connector remain the target; no GA or second installation is
required. Publication, installation, registry signing and the household Core
update are distinct decisions. Follow the exact
[beta.2 acceptance contract](V2_4_1_BETA2_ACCEPTANCE.md).

Beta.1 has no October parser. If Core has already changed to October, an
Engineering downgrade alone is not a valid way to restore Core authority.
Preserve independent management access, durable execution evidence and a
compatible Core/Engineering recovery pair. Settle active or uncertain work
before any separately authorized restart or downgrade; never clear journals,
locks or stores to bypass refusal.

Garage forward/inverse behavior and completed household D2 evidence are
unchanged; this release requires no repeat drill or garage operation. Govee,
HAMCP-151 add-on restart and ha-mcp 8.6.x work are excluded. The separately
reported RC1 projection/approval-sequence warning is not repaired here and must
retain its existing disposition during installed acceptance.
