# Engineering 2.4.0-beta.15 release notes

Release candidate for the owner-approved integration of F3 readiness/recovery,
dashboard analysis and baseline-error corrections. Publication, installed image
binding and installed acceptance are separate from source preparation.

The reviewed dashboard candidate adds `dashboard_integrity_analysis`, an
Engineering-native read for one exact dashboard path. It joins the admitted
upstream dashboard configuration with native states and entity-registry inventory,
reports original pointers, occurrence/unique counts, availability categories and
configured control affordances, and exposes explicit coverage gaps. Configured
controls do not prove current eligibility or authorization. More-info may expose
controls. Dynamic/custom branches stay partial; no template or browser execution
is implied. Raw helper values and action payloads are excluded.

The intended catalog is **83 = 58 native + 25 delegated**. Preserve all preceding
82 descriptors. Reuse the existing `core.dashboard_configuration_read`,
`core.basic_rest_read` and `core.non_device_registry_read` profiles plus exact
upstream admission. There is no new profile, signing requirement, write route,
refresh, retry or fallback for this feature. Existing signed applicability must
already admit these profiles; enumeration alone does not prove availability.

Reports are bounded, non-atomic captures. Caller/path-bound continuation pages
reconstruct the same frozen report and perform zero new provider reads. Refused
collections retain no successful snapshot. Upstream attempt/time telemetry is
balanced for dispatched success, refusal, failure and cancellation; pre-dispatch
refusals and continuations remain zero. Reviewed baseline-error corrections are
included: permanent capture refusals are not retryable, and non-configuration
source failures retain source-unavailable attribution.

F3 request readiness now uses bounded in-memory lifecycle state instead of scanning
retained execution history for every request. Startup must still finish before
requests are admitted. After startup, reads remain available during an execution
fault, while new F3 execution fails closed; `/ready` distinguishes request readiness
from `f3_execution_ready` and reports `ready_f3_execution_unavailable` when applicable.
Exact bounded terminal reconciliation can clear the matching transient completion
fault only after durable child/parent/plan, projection and audit evidence agrees.
This includes verified no-op and terminal pre-dispatch cases with no dispatch
intent. Unrepaired storage, incomplete audit, inconsistent projection and newer
faults stay blocked; reconciliation does not redispatch the original operation.

This release does not solve the separate 1,024-entry F3 namespace-capacity issue.
It does not make deep health/recovery scans cheap, establish an installed latency
SLA or prove the cause of historical disconnects. Existing locks, manual-review
holds, approval consumption and one-dispatch controls remain enforced.

Exact frontend commit 380e9b5a81ada29a1d187b4123c54fb3d6fbcc89 passed 51 tests
across six existing suites. Those suites do not cover every helper/header default;
source-bound original analyzer tests and their gaps remain separately documented.
The reviewed source inputs are dashboard `2791151b27424f686bc16c59f521ce5301517727`
and F3 `28a65f2ce3776ef6efd9f8af458245ce28aa27a3`. Their separate validation does
not substitute for combined-candidate Full/Evidence and exact-head CI, including
the new assembled disposable dashboard lane.

Stable v1.1.2, durable formats, approval policy, trust roots, dependency pins and
deployment options remain unchanged. A reviewed downgrade must preserve durable
governance records; it loses transient snapshots and restores the earlier F3
readiness/recovery defects. Do not treat downgrade as repair of a stored fault.
Publication, deployment and the [installed canary](V2_4_0_BETA15_ACCEPTANCE.md)
require separate authority and evidence.
