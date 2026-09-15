# Engineering 2.2.2 release notes

Engineering 2.2.2 adds a temporary, private diagnostic observer to help establish
the request boundary needed for Host/Origin hardening. **Observation is disabled
by default. This release does not enforce Host/Origin policy or resolve issue
#62.** Installing it does not arm a capture or authorize live collection.

## Private observation

An explicitly authorized operator can arrange one source-bound capture through
the private file contract in [inbound topology capture](INBOUND_TOPOLOGY_CAPTURE.md).
The observer records selected parser-produced Host/Origin headers and the
immediate peer before Uvicorn applies forwarding-header transformations. It
does not establish earlier proxy transformations or hosted-service behavior.
The authenticated approval listener is outside its scope.

Arming requires a valid, private, source-matched declaration and an exclusive
one-use claim. Capture stops within 180 monotonic seconds of listener readiness
and no later than the arm's absolute expiry. Replay, unsafe file paths and
invalid arms refuse observation. Request/header/export bounds and fixed failure
categories preserve incomplete evidence explicitly. Storage flushes still depend
on kernel progress; the timer is not a hard I/O abort guarantee.

The observer does not read paths or bodies, collect authentication/session
material, modify requests, create a public endpoint or call providers. Selected
hostnames and addresses are private evidence. Unsafe header values are omitted
with bounded reasons, rather than truncated or converted into trust settings.
Recording failure retires the observer while preserving normal application
invocation and downstream failure behavior. Startup-composition, cancellation,
expiry and shutdown paths close its resources.

## Preserved installation and behavior

Use the existing **HA MCP Engineering Server Beta** installation, directory and
slug `hass_mcp_engineering_beta`, and image repository
`ghcr.io/jeter-1/hass-mcp-engineering-beta`. Both `linux/amd64` and `linux/arm64`
remain supported. The controlled base image, dependency locks, ports 8100/8110,
ingress identity, configuration schema/defaults and existing persistence formats
are unchanged. The private diagnostic arm/claim/report are the only new file
formats. Published historical artifacts remain intact. Frozen stable-v1 remains
1.1.2 and is not an Engineering rollback.

Public tool contracts, authentication, rate limiting, provider admission,
governed approval/execution, verification and zero fallback remain unchanged.
The healthy reviewed ha-mcp 8.4.3 pairing exposes 51 static plus 25 delegated
tools, 76 total, with all 17 Core capabilities; `ha_get_operation_status` remains
held. Core 2026.9.2 still requires separately configured, valid signed authority
referencing existing compiled contracts. This release neither activates trust
nor grants compatibility to future Core or ha-mcp versions.

## Evidence and operational limits

The independently reviewed observer source is
`bbd26e7fad2d73180a0a59733a6e99a4a5b99209`. Its review executed 191 tests without
failures or skips. That source review is distinct from final release validation,
publication identity and installed acceptance. The publication record identifies
the actual release source, build time and image digests.

Follow the [2.2.2 acceptance contract](V2_2_2_ACCEPTANCE.md) for final image,
catalog, useful-read, dependency-refresh and resource evidence. Historical
acceptance remains bound to its original build and observation time. Complete
Android navigation, cache-only outage startup and independent backup-content
verification remain unestablished by those records.

A live capture requires its own exact target, image, arm/session, private
tooling, recovery and cleanup authorization. Expiry stops collection but does
not remove compiled instrumentation. Preserve consumed claims against replay;
do not automatically retry a failed attempt. Removing the observer or retaining
it dormant in the eventual security fix requires an explicit review decision.
Any installed recovery must reconcile current execution and compatible
Core/configuration/database state using independent recovery access. Returning
to the previous release retains the known Host/Origin enforcement gap.
