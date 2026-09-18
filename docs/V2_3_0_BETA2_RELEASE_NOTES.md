# Engineering 2.3.0-beta.2 release notes

Engineering 2.3.0-beta.2 adds exact ha-mcp 8.5.0 compatibility for the existing
read, governed dashboard and ordinary fan capabilities. It retains the technical
Beta installation identity and existing 8.4.3 behavior. This prerelease does not
upgrade ha-mcp, change production trust or establish installed acceptance.

## Existing operations on an exact new provider

The public `ha_get_blueprint` name and schema remain unchanged. Engineering maps
only its existing list/get requests to the reviewed 8.5.0 mixed blueprint tool.
Import, save, delete, substitute, caller-selected actions and arbitrary forwarding
remain unavailable. Standalone metadata-only responses disclose partial coverage;
add-on installed configuration/YAML retain their provenance. Downloaded source
YAML does not establish the installed file. Engineering adds no external fetch.

Typed fan actions retain one exact target, ordinary authenticated connector
authorization, automatic operation-ID management, at-most-once dispatch,
independent state/percentage verification, target-local uncertainty protection and
audit attribution. The static capability catalog describes both compiled contracts;
each task identifies its actually selected contract. Generic services remain closed.

Governed dashboard updates retain complete prereads, exact hashes, full approval
disclosures, authenticated approval, one intent and independent complete readback.
New upstream JSON Patch/transform operations are not exposed. Provider
acknowledgement is not Engineering verification. Both upstream native-component
and legacy saves remain non-atomic against concurrent external editors.

## Compatibility and preservation

Exact upstream source: `311d6dc273fb4e9a5b8cde0de15f69472a64fe44`, reviewed entry
`ha-mcp-v8.5.0-e1538bcd`. The internal protocol remains `2025-03-26`.
See the [compatibility evidence and limits](HA_MCP_8_5_0_COMPATIBILITY.md) for
immutable standalone/add-on artifacts and source/image-label reconciliation.

Fan semantics remain limited to Core 2026.9.2 with exact ha-mcp 8.4.3 or 8.5.0.
Core 2026.9.2 still requires separately configured, valid signed Core authority.
The 17 existing compiled references and probe fingerprint are unchanged.
Signed data alone cannot enable an uncompiled blueprint wrapper or fan contract.
There is no blanket future-Core or future-ha-mcp admission.

Healthy Engineering registration remains 52 static plus 25 delegated tools,
77 total, with existing public MCP descriptors preserved. The upstream 8.5.0
catalog separately contains 77 descriptors; these counts describe different
catalogs. `ha_get_operation_status` remains withheld from ordinary registration.

Historical ordinary-fan receipts retain their format, bytes and prepared hashes.
Loading selects their original exact contract without migration or relabeling.
This does not prove an older binary can interpret new 8.5.0 receipts.

The add-on name, directory/slug `hass_mcp_engineering_beta`, image repository,
ports 8100/8110, ingress, options, dependencies and controlled build inputs remain
unchanged. Images support linux/amd64 and linux/arm64. Stable-v1 1.1.2, signed
trust data and publication/merge permissions are unchanged.

## Validation and operational limits

Independent implementation review at `ad53c62e55f18e33cc489bf7461ec54f8a5ea7d2`
reported one Low metadata inconsistency and no Critical, High or Medium findings.
It ran 246 focused tests and two passing controls, and reproduced the metadata
defect. The bounded correction derives the catalog from the compiled bindings;
its desired-behavior regression failed before correction and passes afterward.

Final-candidate Evidence, architecture builds and disposable integration CI have
separately bound receipts. The CI extension exercises both exact upstream
packaging variants on native amd64/arm64 with disposable Core 2026.9.2,
synthetic authority, blueprint completeness, governed dashboard restoration,
fan dispatch accounting and resource cleanup. Earlier upstream-only assessment
results are supporting evidence, not candidate CI.

Publication identifies the actual release source and artifacts. It does not
establish deployment or acceptance of an installed 8.5.0 pairing.
[Beta.2 acceptance](V2_3_0_BETA2_ACCEPTANCE.md) defines those separate checks.
Preserve historical acceptance under its original version, pairing and time.

Do not repeat an uncertain mutation to recover information. Reconcile task,
operation and current state first; restoration needs an exact owner request.
Physical feedback, complete Android navigation, cache-only startup during an
outage and independently verified backup contents are not newly established.
A source revert cannot undo a deployment or physical action; rollback must
account for retained receipts, holds, compatible artifacts and configuration.
