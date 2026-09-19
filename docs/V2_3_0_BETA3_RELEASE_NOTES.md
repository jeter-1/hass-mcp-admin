# Engineering 2.3.0-beta.3 release notes

Engineering 2.3.0-beta.3 adds `control_power`: exact light and switch ON/OFF
through the existing authenticated Engineering connector and reviewed internal
ha-mcp provider. It retains the technical Beta installation identity and the
existing fan, read and governed configuration capabilities.

## Ordinary light and switch controls

One request names one exact `light.` or `switch.` entity and either
`turn_on` or `turn_off`. The assistant manages and retains the operation ID.
Ordinary authenticated connector authorization applies; these requests do not
create configuration plans, notifications or panel approvals.

Generic services, toggle, brightness/color, extra service data, bulk selectors,
area/device targets and other domains are unavailable through this tool. Exact
compiled power contracts cover Core 2026.9.2 with admitted ha-mcp 8.4.3 or 8.5.0.
Separately configured valid signed Core authority remains required. Installation
does not configure trust, upgrade either dependency or admit future versions.

Power can affect critical loads, integration-defined groups and automation
consumers. Coverage of downstream effects remains incomplete. Light ON may
restore integration defaults; the tool neither sends brightness/color parameters
nor promises their preservation. Verification establishes HA-reported ON/OFF,
not independent physical or electrical feedback.

## Execution and compatibility

The existing shared execution machinery owns at-most-once dispatch. Duplicate
requests with the same operation ID and arguments reconcile the existing receipt;
changed arguments refuse. Already-satisfied requests are verified no-ops.
Uncertain results retain protection for the exact target. Recovery after durable
intent is read-only and never authorizes redispatch, provider substitution or
direct-Core fallback. Restoration is a separate exact owner request.

Receipts and audit identify the provider, selected contract, outcome and available
dispatch/verification evidence. Attempt counts and intent alone are not proof
of delivery. Background recovery retains original request attribution.
Power receipts use a separate `ordinary-power-v1` namespace; existing fan,
governed-plan and lock formats are preserved. Historical fan receipts remain
readable without migration or relabeling.

The healthy catalog adds one descriptor: **53 static plus 25 delegated reads,
78 total**. All 52 previous static descriptors remain unchanged. Existing fan
semantics, blueprint completeness disclosures, dashboard approval requirements,
Host/Origin enforcement and secret-path authentication are preserved.

The add-on name, `hass_mcp_engineering_beta` directory/slug, image repository,
ports 8100/8110, ingress, configuration and controlled dependency inputs remain
unchanged. Images support linux/amd64 and linux/arm64. Stable-v1 1.1.2, signed
trust data and merge/publication permissions remain unchanged.

## Validation and operational limits

Independent implementation review at
`931cc21a190ddf465d6cb3d08c8675fc7d2e4a4e` reported no actionable findings.
It completed 223 focused tests, including four successful retries after local
sandbox socket restrictions, and two additional ownership/recovery probes.
Historical descriptor and fan-receipt preservation were checked independently.

Candidate validation requires clean-head Evidence and the complete CI aggregate.
The container lanes exercise exact upstream 8.4.3 and 8.5.0, standalone and
add-on packaging, on native amd64 and arm64 against disposable Core 2026.9.2.
They require light/switch ON/OFF, independent service counters and state readback,
duplicate suppression, exact OFF restoration and resource settlement. Existing
fan/dashboard and historical compatibility lanes remain required. Interpreter
execution against containers, Engineering image checks and installed acceptance
are distinct evidence.

[Beta.3 acceptance](V2_3_0_BETA3_ACCEPTANCE.md) defines final publication,
installed identity, fresh catalog and separately authorized action checks.
Earlier acceptance retains its original source, pairing and time. Publication
does not establish deployment or household acceptance.

Complete Android navigation, cache-only startup during an outage and independent
backup-content verification are not newly established. Preserve uncertain tasks
and holds when considering rollback: an older binary cannot reconcile the new
power namespace. A source revert cannot undo a physical action or deployment.
