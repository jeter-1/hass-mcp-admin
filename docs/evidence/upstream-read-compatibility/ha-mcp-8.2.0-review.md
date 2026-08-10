# Exact ha-mcp 8.2.0 compatibility review

## Decision

Exact `ha-mcp` 8.2.0 is acceptable for source-controlled admission as
`ha-mcp-v8.2.0-dbcfc0ee`. Admission remains bound to server name `ha-mcp`,
version `8.2.0`, protocol `2025-03-26`, the complete 78-tool descriptor set,
and the immutable OCI identities below. No version range or fallback is added.

This review admits no new generic write route. Twenty-five tools remain
`automatic_read`, `ha_get_operation_status` remains held, and the other 52
advertised tools remain nondelegated. `ha_manage_security_policy` is present in
source behind an opt-in feature switch but is absent from the reviewed default
78-tool runtime catalog and has no Engineering route.

## Immutable identity

| Evidence | Exact value |
| --- | --- |
| annotated tag | `v8.2.0` / `098540ba22d495fdb1701daf830d54762350fd46` |
| tag signature | unsigned annotated tag |
| source commit | `54c492510d05b1f33c777f1c94bfb6a50a7d7c42` |
| source tree | `3788d2bfefc140364be66a37cb96a67ac73141df` |
| source archive SHA-256 | `945faf6eb7a10c9b687fd6c45f50b09d997d41f5549784f8835f2b29fda181ff` |
| source archive URL | immutable annotated-tag object `codeload.github.com/homeassistant-ai/ha-mcp/legacy.tar.gz/098540ba22d495fdb1701daf830d54762350fd46` |
| skills submodule | `9e4eff281112218953dc708687d24601777f9ccb` |
| standalone index | `sha256:dbcfc0ee8ad02d2190ebde69e5cc6167175c79608bbf1d55cff9034e256face1` |
| standalone amd64 manifest | `sha256:2a53077b16a70a24df176434db8b0dffaa9abaa692290cb73146d0c3d37e7644` |
| standalone arm64 manifest | `sha256:d13b960112c5762260a8e39a2c257cfd82d94692b533c5c7e49a76f188d32215` |
| add-on amd64 index / manifest | `sha256:c86b0414a88b9ee404b6f151ed80419fe1bb120f6bb3baf1d31a6b01a5113e36` / `sha256:8abc94e916b1cc5333e2aee64fcebc749e814b5446980d1c773fa243c56b8c57` |
| add-on arm64 index / manifest | `sha256:72b5f80bcdb614ae3c1ecc04f1f0f31275c8048c6ff8fd5a0859e61b6848adb0` / `sha256:ed614264dee86264a8d08d9bb3e9e8dab2cbcae82734ce73d4213983916b0ef6` |
| OCI revision label | `767dc9de45302ab2efbddefdc55ea84f20ed446c` |

The published standalone index declares `linux/amd64` and `linux/arm64` image
manifests. Unknown/unknown entries are attestations rather than runnable
platforms. No arm/v7 upstream image is claimed.

## Complete runtime comparison

Two byte-identical loopback-only captures from the exact tag, including its
pinned skills submodule, produced:

- 78 unique advertised tools;
- catalog fingerprint
  `97d88718be4542a60fc2911411da0ff0172ba0dfef821a9c83e998809dcaf4a2`;
- strict full-contract fingerprint
  `b51121944e27158ba98072f65af34f5942af5e5d43e4c18e82598d9704ff5776`;
- normalized aggregate fingerprint
  `912c68f50271b5b45639453c75931aa80bb42bb5a1e6249defe6777017c7da70`;
- the unchanged reviewed bounded error-envelope fingerprint.

The exact 8.1.1-to-8.2.0 descriptor diff is 77 unchanged tools and one changed
tool. `ha_manage_hacs` adds `action="update_information"` and corresponding
description text. The action makes an open-world HACS/GitHub request and
refreshes persistent repository release metadata. It therefore remains
`persistent_write`; generic delegation stays prohibited. Its annotations and
output contract are unchanged.

Source review also covered default-hidden security-policy registration,
redaction and sentinel hardening, registry reference validation, HACS startup
refresh behavior, dashboard resolver hardening, and embedded self-reload
shielding. None changes an automatic-read classification or creates a generic
Engineering route.

Exact immutable-image capture and all platform/runtime reproduction remain CI
acceptance requirements. Local source capture is evidence, not a substitute for
the exact-image lane.

## Dashboard read and write review

`ha_config_get_dashboard` and `ha_config_set_dashboard` retain their reviewed
8.1.1 schemas, descriptions, annotations, outputs, and operational runtime
descriptors. The setter input-schema fingerprint remains
`a7d11d72710f1c39937bfc864291f6d0936b2d4feb68dc4ff049eda3b91a3ac1`.

The exact 8.2.0 source contains dashboard correction commit
`801c22d0eaa59bfcbf44b51c257dafc635a075d3`. The resolver reads the dashboard
registry before the hyphen rule:

- exact existing `url_path="map"` is accepted as an update target;
- an existing hyphenated target remains accepted;
- a new hyphenless target remains `VALIDATION_INVALID_PARAMETER`;
- a new hyphenated target retains upstream creation support;
- an internal-ID-only match cannot exempt a different hyphenless path;
- an unreadable registry fails closed;
- strategy-dashboard full replacement and Python transforms fail closed.

The focused upstream test set passed 49/49. Exact-image CI separately runs the
four target cases against a synthetic Home Assistant fixture that refuses every
save/create frame. Reaching that refusal proves resolver acceptance without
mutating a fixture dashboard.

Engineering continues to expose dashboard writes only through the governed
dashboard path: fresh exact preread, immutable plan, external approval, complete
locks, one setter dispatch maximum, no raw/direct Home Assistant fallback, no
blind redispatch, and exact complete reread verification. This review does not
reinterpret a short hash or provider success response as verification.

## Compatibility boundary

The exact 8.1.1-only pre-plan rejection for an existing hyphenless target is
retained. It is not applied to exact 8.2.0 because the exact source and runtime
contract prove the correction. Unknown 8.2.x and future releases remain
unadmitted.
