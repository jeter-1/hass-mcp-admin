# Engineering 2.3.0-beta.4 release notes

Engineering 2.3.0-beta.4 lets existing typed fan, light and switch operations use
reviewed signed Core compatibility references. Compatible future Core releases
can reuse these semantic profiles without adding another compiled version pair.
It preserves the single Engineering connector and reviewed internal ha-mcp
8.4.3/8.5.0 providers. No new tool, action family or generic service path is added.

## Compatibility and execution

Two explicit profiles, `core.typed_fan_operation` and
`core.typed_power_operation`, extend the runtime profile inventory from 17 to 19.
The original 17 fingerprints are unchanged. Structural observations and existing
read grants cannot authorize either new profile: exact signed references and
the existing base authority remain necessary. Unknown, expired, revoked or
mismatched applicability refuses without provider substitution or fallback.

New prepared operations bind the observed Core version and semantic fingerprint
to the reviewed provider contract. Preflight, dispatch, readback, recovery and
audit retain this binding. Stale identity, state or generation cannot inherit a
prior operation's authority. Selected signed-route acquisition failure cannot
retry the historical route. At-most-once dispatch and read-only uncertain-result
recovery remain unchanged; restoration is a separate exact authorized action.

Existing Core 2026.9.2 behavior and shipped declarations remain readable without
rewriting their bytes, hashes or contract names. Its published immutable registry
entry retains the original 17 references, so beta.4 on that authority reports 19
profiles with two unavailable typed references while legacy typed controls still
work. This is expected compatibility behavior, not missing installation state.

Core 2026.9.3 acceptance uses exact immutable source and images with ha-mcp 8.5.0.
Its separately reviewed production registry entry must cover all 19 references.
Installing beta.4 does not sign, publish, configure or activate that authority,
or update Core. Existing Core 2026.9.2 provider/packaging lanes remain required.

## Persistence and recovery

New Core-bound fan/power declarations use closed versioned models and include
the Core binding in their prepared hash. Beta.3 cannot read those new records.
**Binary downgrade alone is not a storage rollback.** Preserve dispatch identities,
receipts, target holds and uncertain work. Do not delete IDs or restore an older
execution store after a possible dispatch. Retain a compatible reader or use a
separately reviewed restoration procedure that reconciles exact outcomes.

The supported catalog remains **53 static tools plus 25 delegated reads, 78
total**. Tool registration and input schemas, standard configuration approval,
Host/Origin policy, installation identity, ports, controlled dependencies and
amd64/arm64 support are unchanged. Stable-v1 1.1.2 and production trust journals
are unchanged.

## Evidence and limits

The independent implementation review at
`64af6ec213ddb3c6d0203ba534307d6e4ffa8e25`, against
`349e0e49607e44a31eececd9ec457a5e124dbf98`, found no actionable findings.
Its 274 focused tests passed without skips, and historical beta.3 declarations
and provenance regenerated identically. Those results do not replace clean
final-candidate Evidence, complete CI or disposable real Core acceptance.

[Beta.4 acceptance](V2_3_0_BETA4_ACCEPTANCE.md) requires real Core 2026.9.3 with
standalone/add-on ha-mcp 8.5.0 on native amd64 and arm64: exact actions and
readback, duplicate suppression, stale-state refusal, response-loss recovery,
restoration and resource settlement. Fault-injected elapsed time and transport
loss are identified separately from actual process crashes or version upgrades.

Publication, installation, signed production authority and household acceptance
remain separate evidence. Earlier beta.3 acceptance and its raw-catalog
runtime-bracket qualification retain their original scope; later results do not
repair that historical qualification. HA-reported state is not independent
physical feedback. No new household acceptance is established by source changes.
