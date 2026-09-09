# Engineering 2.2.0-rc.4 release notes

RC4 prepares two independently reviewed post-Core corrections. Final materialized
source declares `2.2.0-rc.4` in all three authoritative locations and consumes
`.release/next-version`. Stable remains 1.1.2. This document does not establish
publication, installation or household incident resolution.

## Device display text preserves compatible authority

RC3 applied target-identifier restrictions to device `name` and `name_by_user`
display fields. Legitimate empty or long strings could therefore withdraw three
device/dependency Core capabilities and five delegated reads despite an exact
admitted ha-mcp provider.

RC4 accepts the applicable string-or-null display contract without truncating or
rewriting evidence. Registry byte/count bounds and identity, uniqueness, parent,
cycle, timestamp, shape and supported-version checks remain enforced. Wrong-type
or otherwise invalid evidence retains its fail-closed capability consequences.
Valid evidence restores the existing five reads through normal admission and
registration, with unchanged public descriptors and provider attribution.

## Failed reconciliation is paced

Recurring failed supervisor observations or monitor attachment could schedule
their own immediate retry chain and repeatedly invalidate dependency evidence.
RC4 retires unusable authority immediately, coalesces redundant invalidations
while authority is absent, and waits the existing 300-second reconciliation
interval before retrying a failed observation. Real authority transitions still
notify consumers, and healthy connection changes remain prompt.

Recovery can take one interval plus probe/attachment time; further failures
extend it. This is not a global request-rate limit or a remote-abort guarantee.
Startup/direct reconciliation remain separate. External changes during a delay
still retire authority immediately and are collected for the next current-epoch
probe. Fresh authentication, version/epoch fences, per-read revalidation,
cancellation, cleanup and automatic recovery remain required.

## Preserved behavior and evidence

The independent source verdict covers correction
`a56ba457ee40ca32c904f63f139e43cfbd2efd36` against published RC3
`a4c18864b1b6092f7f70255a6b7f37192a5bf100`. The release-preparation delta needs
its own bounded independent review and complete final-head validation. The earlier
unchanged-RC3 metadata failure remains historical evidence; RC4 must pass the
legitimate RC3-to-RC4 release gate without changing its baseline or rules.

Exact reviewed Core compatibility, including 2026.9.0/2026.9.1, and internal
ha-mcp 8.4.3 remain unchanged. Engineering presents one public endpoint through
Nabu Casa and selects admitted or native providers internally. The healthy
pairing retains 51 static plus 25 delegated tools, 76 total;
`ha_get_operation_status` remains held and absent from ordinary registration.
There is no new schema, provider contract, admission policy, route, permission,
dependency, workflow, Dockerfile, write, forwarding or fallback surface.

RC3's manager-owned shared dependency builds remain intact, including startup,
foreground and retained soft refresh, shared waiters, cancellation/shutdown,
exact authority cleanup, source/post-lock fences and the separate 300-second
cooperative build deadline. Evidence TTLs remain 600 seconds soft and 3600
seconds hard. Already-dispatched reads may finish after caller cancellation.
Earlier raw nonfinite REST rejection, capability-local failures/timeouts,
bounded fresh WebSocket replacement, composite-read authority and R182-1 held-read
cancellation cleanup are preserved. Governance task schema remains 1 and approval
authority remains 3; provider attribution and zero fallback remain required.

## Acceptance on the already-updated Core

The last reported household Core is 2026.9.1, subject to fresh authorized readback.
[RC4 acceptance](V2_2_0_RC4_ACCEPTANCE.md) controls this corrected-build path;
it does not instruct another Core update. The original failing household payload
is unobserved, so source correction does not prove live incident resolution.

Keep independent correction review, release preparation, local validation,
candidate CI/build, future publication, installed-image binding and live
acceptance separate. Record exact candidate run/attempt IDs, tested revisions,
merge parents where applicable, conclusions and artifacts. Earlier RC3 CI and
live receipts do not establish RC4 results.

After separately authorized corrected deployment, require exact installed image
and build identity, a fresh complete public catalog, the five restored delegated
reads and expected Core authority, useful unrelated reads, natural dependency
refresh with fresh replacement evidence after the initiating request returns,
settled resources and zero fallback. Retain prior acceptance receipts as history.
Canaries require exact authorization, shipped panel approvals, authoritative
readback and helper/dashboard restoration. Navigation and restart recovery retain
their separate scope, backup and verification requirements.

## Immutable history and recovery

Published RC3 remains immutable at
`a4c18864b1b6092f7f70255a6b7f37192a5bf100`. Returning to it restores the known
display-validation and reconciliation defects. Returning to RC2 additionally
restores its shared-refresh ownership defect. Neither is a complete recovery
procedure for an already-updated Core installation.

Before deployment or recovery, bind the exact compatible Engineering/Core
artifact, configuration/database state, verified full backup and off-host copy,
recovery access and overwrite consequences. Backup, restart, rollback or any later
Core update requires its own authorization. Before merge, release preparation can
be left unused or reverted while preserving the independently reviewed correction
and historical evidence. Later fixes must preserve immutable published artifacts.

Leave the PR draft for Josh. His later Ready action may authorize controlled
merge/publication under repository policy. This preparation grants no Ready,
approval, merge, publication, deployment or live-test authority.
