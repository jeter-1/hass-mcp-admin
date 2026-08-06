# HA MCP Engineering 2.2.0-beta.21 exact ha-mcp 8.1.0 release notes

The exact 8.1.0 review produced Outcome C. The runtime catalog has no name
addition or removal, but `ha_get_hacs_info` changed its success-envelope
location and `ha_manage_hacs` added a destructive remove action. Engineering
adds one exact HACS-read projector, reclassifies the whole write tool as
persistent, and leaves every write action unreachable.

All 78 runtime-advertised names are classified. Engineering delegates 24,
holds exactly `ha_search` and `ha_get_operation_status`, exposes 48 local tools,
and therefore publishes 72 total tools when exact 8.1.0 admission succeeds.
The source-name inventory is explicitly non-authoritative: conditional and
hidden tools are reported separately from exact runtime `tools/list`.

The exact Dashboard and operational descriptors are independently reviewed.
Lifecycle inventory and detail envelopes are structurally identical to 8.0.0,
so no new generic response model is introduced. Supervisor's endpoint-bound
installed inventory remains identity authority, and the tag's stale add-on
metadata is a tested refusal case.

The immutable standalone/add-on OCI images are admitted by digest. Release-page
executables and MCPB remain excluded because they embed 8.0.0. Runtime evidence
adds settings-sidecar restart/corruption/loopback checks, shutdown cancellation,
and forced Engineering disconnect/readmission with zero fallback.

Beta 21 does not broaden 8.x trust, promote held tools, add fallback, activate
an upstream write, change F3-D, modify stable v1.1.2, weaken secure dependency
pins, deploy, publish, or claim production canary success.
