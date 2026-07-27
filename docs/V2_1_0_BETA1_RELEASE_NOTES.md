# 2.1.0-beta.1 2.1A Dev1 release notes

Version: `2.1.0-beta.1`

Status: unpublished development candidate; no image, tag, release, or
deployment is authorized by this document.

Dev1 starts operational administration with one proposal-only
`create_backup_plan` tool and the existing approval/apply lifecycle. It adds
contract-v3 operational plans, exact constrained backup dispatch over reviewed
`ha-mcp` 7.14.1 and 7.14.2, independent `backup/info` verification,
verification-only ambiguous-outcome recovery, persistent audit evidence, and
operational health counters.

The fully admitted catalog is 42 Engineering tools plus 26 delegated reads,
for 68 total. Existing delegated policies, 78-tool release ledgers,
fingerprints, admission, dashboard constraints, and zero-fallback behavior are
unchanged.

The selected snapshot-create contract includes Home Assistant configuration
and supervised add-ons, excludes the recorder database, uses Home Assistant's
configured default backup password internally, and provides no archive-content
integrity proof. Restore, delete, export, partial selection, retention,
external storage, generic administration, reload, and restart remain absent.

`check_config` remains read-only and unchanged publicly. Its strict bounded
internal result interpretation is reusable for later fresh planning and
pre-apply reload/restart checks.

Dev1 is based on annotated `v2.0.1` tag object
`60969502d63e0926c956b179dcad987058dece2b`, targeting
`4942770a2fd80fed613eb1f42ed84ba9fa1c134c`. Stable v1.1.2 and the immutable
2.0.1 release evidence are unchanged.

## Downgrade preservation

Contract-v3 operational plans are stored under the versioned
`operational-administration-v3` persistence namespace, separate from legacy
configuration plans. Downgrading to exact 2.0.1 with retained `/data`
preserves those records without making them visible to 2.0.1. The older
release does not enumerate, quarantine, modify, or delete them, while legacy
configuration plans continue to operate normally.

Reinstalling 2.1 restores access to the exact preserved plan IDs, hashes,
approval and dispatch evidence, lifecycle states, and verification records. A
preserved `verification_required` plan resumes readback only and never
redispatches backup creation. Operational plans cannot be approved, applied,
or recovered on 2.0.1. Do not move their files into the legacy namespace or
manually recreate a pending operation during the downgrade.

## Call-time provider drift

The reviewed provider validates the live catalog again in the same MCP session
immediately before dispatch. Tool removal, reviewed-contract drift, catalog
drift, or an unreviewed upstream version returns bounded
`backup_provider_unavailable` evidence with `failure_stage=pre_dispatch`,
`provider_dispatch_occurred=false`, and zero fallback. Approval remains
unconsumed; refreshed exact provider evidence and replanning are required.
Provider health owns the single precise failure-category count. Governance
owns the unchanged plan lifecycle plus one bounded audit event and public error
mapping; transport does not add a second generic failure count.
