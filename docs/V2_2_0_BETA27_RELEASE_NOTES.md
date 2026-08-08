# HA MCP Engineering 2.2.0-beta.27 configuration-metadata release notes

Beta 27 corrects the governed automation failure observed after Beta 26
deployment. Home Assistant and `ha-mcp` may enrich an automation or script read
with the entity-registry `category`. That field is not part of the Home
Assistant automation/script REST configuration body. Beta 26 treated it as
behavioral configuration, sent it to Home Assistant, and then reported the
provider rejection as an indeterminate dispatch that became a verification
mismatch.

Automation and script `category` values are now explicitly classified as
read-only registry metadata. They remain visible as bounded planning context,
produce an approval warning, and are excluded from behavioral diffs,
fingerprints, provider-argument binding, dispatch bodies, and exact readback
verification. This configuration path does not change a category. Supplying a
category on a create is rejected because no existing registry value can be
preserved.

Automation validation now fails closed on unknown top-level fields. Reviewed
Home Assistant configuration fields, identity metadata, and the one bounded
read-only `category` field remain accepted. Nested automation validation and
all existing risk, approval, F3, locking, one-dispatch, recovery, and
verification requirements remain in force.

A Home Assistant 4xx response to the exact automation/script configuration
endpoint is reported as a received provider rejection with zero provider
mutations. Network loss, timeout, 5xx responses, and other cases that do not
prove absence of effect remain indeterminate and readback-only; Beta 27 does
not add a retry after durable dispatch intent.

The automation normalization contract advances from 2 to 3 and the
configuration-resource normalization contract advances from 1 to 2. Existing
terminal history remains readable. Outstanding plans created under an older
normalization contract must be recreated after upgrade and cannot silently
gain authority under the corrected metadata semantics.

Beta 27 changes no public tool schema, provider admission, exact `ha-mcp` 8.1.1
classification, Dashboard route, approval authority, F3 authority, physical
action boundary, fallback behavior, or stable v1.1.2 source. The published
Engineering version remains `2.2.0-beta.26` until a separately authorized
promotion consumes the staged declaration.
