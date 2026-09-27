# Engineering 2.4.0-beta.5 release notes

Release candidate for bounded automation lifecycle analysis. These notes assert
no publication, deployment or installed acceptance.

The existing read-only `automation_reliability_analysis` adds
`data.lifecycle_analysis` for one automation's triggers and inline actions. It
identifies possible interruption hazards around supported positive state or
numeric-state trigger holds, delays and waits, with exact configuration paths
and conditional consequences. Repeat and scheduling observations are review
notes, not hazard counts. Disabled and provably unreachable steps are excluded.

Dynamic or unsupported syntax, stored-script bodies, unexpanded blueprints and
work/output limits remain explicit coverage gaps. Unresolved container error
continuation retains conditional findings and marks coverage partial. A clean
static result does not prove restart resilience or recovery, and the analysis
does not execute, reload, restart or change an automation.

Tool inputs, provider reads and the expected inventory remain unchanged at
79 tools (54 static + 25 delegated). Legacy severity/root-cause accounting and
foundational error behavior are preserved; the aggregate assessment includes
lifecycle hazards and incomplete coverage. Pagination retains the same lifecycle
snapshot. Provider admission, fallback, execution authority, stored formats,
options, permissions, dependencies and frozen stable-v1 remain unchanged.

This is the first inline-automation increment, not the entire lifecycle-analysis
capability. See the [source contract](AUTOMATION_LIFECYCLE_ANALYSIS.md) and
[acceptance contract](V2_4_0_BETA5_ACCEPTANCE.md). Beta.4 script-call dependency
analysis remains available; earlier acceptance receipts retain their original
scope and qualifications.
