# Ordinary light and switch ON/OFF

`control_power` adds one exact light or switch ON/OFF action through the public
Engineering connector and its reviewed internal ha-mcp provider. `control_fan`
keeps its existing API, percentage semantics, provider contracts and persisted
receipts. No separate client-facing Home Assistant connector is needed.

The request accepts only `entity_id`, `action` (`turn_on` or `turn_off`) and
`operation_id`. Only `light.` and `switch.` entity IDs are valid. Generic service
calls, toggle, brightness/color, extra service data, bulk selectors, area/device
targets, locks, covers, climate and other domains remain unavailable through this
tool. Existing governed configuration operations still require their own panel
approvals. An exact ordinary power request uses authenticated connector authority
and does not create a plan, notification or panel approval.

The assistant resolves ambiguous names before dispatch and manages the operation
ID automatically: current UTC epoch seconds, a hyphen and 32 lowercase UUID hex
digits. Generate it once for the requested action and retain it. New IDs expire
after five minutes, with 30 seconds of future-clock tolerance. Repeating the same
ID and arguments reconciles its receipt; changing arguments under that ID refuses.
After uncertainty, use `get_execution_task` or repeat the exact original request.
Never generate a replacement ID merely to retry an uncertain mutation.

## Semantics and consequences

ON/OFF can change power to a load, invoke Home Assistant integration defaults,
affect integration-defined group members or trigger automations. A switch may
power a critical load. Consumer/consequence coverage is explicitly incomplete;
that disclosure is not itself a technical veto on an exact owner-authorized
request. The wrapper does not expand a target into arbitrary member commands.

Light defaults can restore brightness, color or transition behavior. The request
sends no such parameters and does not promise their preservation. Verification
establishes the exact entity's HA-reported ON/OFF state, not physical movement,
electrical isolation, or verification of every downstream effect. Already
satisfied requests return verified no-ops with zero attempts and no dispatch
intent. Home Assistant is not an atomic compare-and-set service: an outside
actor can still intervene after the final preread.

## Closed provider authority

Historical compiled power contracts cover Core **2026.9.2** with exact ha-mcp
**8.4.3** or **8.5.0**. Beta.3 carries these independently reviewed contracts;
exact-candidate validation and separately authorized installed acceptance remain
distinct gates. They do not widen generic read admission or treat an advertised
mixed tool as a write grant.

The provider independently validates the complete reviewed catalog, release
identity, protocol `2025-03-26`, source reference and current deny-aware authority.
Each receipt identifies its selected `core-2026.9.2-ha-mcp-<version>-single-power-v1`
contract. The existing signed Core state-read, service-discovery and F3-verification
capabilities remain required. Missing, expired, revoked or retired authority
refuses. No compiled Core references, trust data or trust configuration change.

Future Core releases use an explicit signed reference to the compiled
`core.typed_power_operation` semantic profile. The original 17 Core profiles alone,
or a fan-only reference, cannot authorize this path. Stable provider contracts
`ha-mcp-<reviewed-version>-single-power-v2` retain exact provider identity; the
prepared operation separately binds Core's observed exact version and semantic
fingerprint. There is no new Core version list. Historical contracts and hashes
remain intact. See [Core applicability and storage compatibility](CORE_RELEASE_REGISTRY.md#ordinary-fan-and-power-applicability)
for new declaration formats, recovery limits and downgrade preparation. No new
production authority or deployment accompanies this source change.

The provider constructs `ha_call_service` with the exact entity's domain, the
selected ON/OFF service, empty `data`, `wait=false`, `return_response=false` and
`verbose=false`. The non-waited path uses the reviewed upstream REST service
implementation without component fallback. Engineering never calls Core's service
endpoint directly or retries through another provider. Exact service discovery,
available target state, revision and authority are checked before durable intent.

Source applicability was inspected at these immutable revisions:

- Core `33c3e0cca60e73a8c4970ee677d75b8bc6464cdf`:
  `homeassistant/components/light/__init__.py` SHA256
  `16e15a88d9cfef37f092bd17aed0cfee85f97f1bbc7c5ec3cdc3885c754a6dd0`;
  `homeassistant/components/switch/__init__.py` SHA256
  `d1c2aaabdaa05529831da206575d2a010539587989c9eab8931a5fc4959d5428`.
- ha-mcp 8.4.3 `eac7a3aa7063432e9af17e7d7726040e909c7b8f`:
  `src/ha_mcp/tools/tools_service.py` SHA256
  `3a8e95bc99287c999f71c3debc1dd1e5694b654de7c660db2c225a35c4efbe73`.
- ha-mcp 8.5.0 `311d6dc273fb4e9a5b8cde0de15f69472a64fe44`:
  the same service path SHA256
  `4cc47219bea7e19c707ca529f98f75e202a24d6638ae656f17bfccad13906d57`.

Source inspection is distinct from exact-image execution and installed acceptance.

## Ownership, audit and recovery

The gateway binds and retires exact request authority. The shared ordinary-action
lifecycle preserves fan behavior while power records use a separate
`ordinary-power-v1` namespace beneath the existing governance storage root.
Operation IDs hash to separate task namespaces. Existing fan, governed-plan,
execution and lock formats are unchanged; nothing migrates or relabels old data.

F3 owns the one-dispatch fence. The exact entity is exclusively locked; Core and
ha-mcp dependencies remain shared-locked during execution. An unresolved terminal
operation retains only its exact target hold, freeing unrelated operations.
Fencing and recovery remain unchanged. Owner loss before intent cannot cause
recovery dispatch; after intent, recovery is read-only. There are no implicit
restoration actions or blind retries. A failed hold settlement retains prior locks
until supported reconciliation can settle them.

Receipts preserve IDs, hashes, task outcomes, provider/contract, attempt count,
intent, available response and verification evidence, retained locks and truthful
completeness. Attempts and intent do not independently prove delivery;
`dispatched_at` remains null when not independently recorded. Outer response
success means retrieval succeeded; the task's terminal state determines the
execution outcome. Bounded responses retain supported task-retrieval information.

Request audits and durable lifecycle projections identify `upstream_typed_power`,
no fallback and bounded outcome facts. Background recovery retains the initiating
request correlation. Audit failures are counted separately and cannot authorize
another dispatch. No arbitrary provider payload or exception text enters these
projections. `ordinary_power` health reports its receipts, active work, holds,
locks, recovery failures and audit projection failures separately from fan work.

The power family uses the existing timing bounds: 16 active/preparing requests,
4,096 retained declarations without eviction, 180-second verification evidence
window, six observation/verification attempts, and a recovery sweep of up to eight
records within 45 seconds. Provider exchanges retain 20-second outer and
15-second call limits. No fan limits or TTLs change.

## Validation and acceptance

Focused offline tests exercise both exact upstream versions with synthetic
transport inputs and real gateway, provider, signed Core authority, shared F3,
stores, target holds, cancellation, duplicate reconciliation, bounded receipts
and audit. Existing fan tests and historical writer fixtures remain controls.

The guarded exact-image lanes cover 8.4.3 and 8.5.0, each with standalone
and add-on packaging on native amd64/arm64 and disposable Core 2026.9.2. They
require four power operations per packaging, independent Core state and
service-counter readbacks, no duplicate calls, exact OFF restoration and settled
resources. The 8.5.0 relay additionally requires its six existing fan calls and
both legacy/native dashboard restoration paths. Historical compatibility lanes
remain required. Inputs are immutable and the runner refuses arbitrary versions,
endpoints or images; cleanup remains bounded and unconditional.

A unit test of this runner is not a container result. Record actual candidate CI
execution and artifact identities for all eight combinations. These candidate
interpreter lanes are distinct from Engineering image and installed acceptance.
See [beta.3 acceptance](V2_3_0_BETA3_ACCEPTANCE.md).

The candidate adds one descriptor: 53 static plus the unchanged 25 delegated
reads, totaling 78. Existing descriptors must remain identical. Independent
review, release preparation/versioning and exact-candidate CI precede adoption;
no historical fan or 8.5.0 acceptance receipt proves this new tool's installation.
Later live acceptance requires a separately authorized exact light/switch test,
current-state/consequence reconciliation, one call, task inspection, independent
readback and any separately authorized restoration. No household action occurs
in source validation.

Local recovery is reverting this feature commit before adoption. A source revert
cannot undo an executed physical action. Preserve new receipts and target holds
when considering any later deployment rollback; an older binary cannot reconcile
this new namespace. Resolve uncertain operations before downgrading.
