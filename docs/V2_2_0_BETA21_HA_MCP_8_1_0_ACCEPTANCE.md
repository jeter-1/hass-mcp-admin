# HA MCP Engineering 2.2.0-beta.21 exact ha-mcp 8.1.0 acceptance

This document freezes the upstream-specific acceptance contract for the exact
8.1.0 admission. The active release-level gate remains
[V2_2_0_BETA21_ACCEPTANCE.md](V2_2_0_BETA21_ACCEPTANCE.md), and the complete
source/artifact findings remain in
[ha-mcp-8.1.0-review.md](evidence/upstream-read-compatibility/ha-mcp-8.1.0-review.md).
This contract is carried on merged Beta 20 main
`e2152911c0f3581c38b6ef42e52a2dd221cd8d96`; it does not replace, disable, or
bypass the activated F3 runtime boundaries from that release.

## Immutable identities

- source tag target: `0683f5ff34e5c71f35bce08d1cedcdee3c0a60b2`;
- standalone index:
  `sha256:4c07e6259a42ed33958ac9d018aba7f4b03ea676388fd3264f8abde5ea767f76`;
- standalone amd64/arm64 manifests:
  `sha256:c1d7eb571a417c5b3765c1d4971cbedb7d2800725bb9bab1a510c876cbacb78c`
  and
  `sha256:4bbb28a184e1a9a307bff2b55fe4423cb011e7ef7c0d4fade407c6460d6481b0`;
- add-on amd64 index/manifest:
  `sha256:2744a11c90f7a66e61fabe8166d058191d236094393c50d976978407c039d45d`
  and
  `sha256:f415b72351d79414a3133c227622633d9c190a3f4f6b849eed93ac524ac1c2d5`;
- add-on arm64 index/manifest:
  `sha256:71bd08ac7ab4272bc226b91d299929949fa24b674e164121566bc1d84666e273`
  and
  `sha256:2dad5c7f8afcfb8c5624d82a7d9c322fc70351d32d9697e07a162ec7015250b0`;
- no published arm/v7 standalone or add-on manifest; and
- excluded executable/MCPB assets: all runtime-identify as 8.0.0.

## Admission gates

- exact server/protocol/version selection only;
- exact 78-name runtime catalog with no duplicates, additions, omissions, or
  unclassified entries;
- exact per-tool classification, schema, description, annotations/security,
  output contract, and release-model runtime contract;
- 24 automatic reads, two held, 13 mixed, 33 persistent writes, four
  physical/high-risk actions, one prohibited, and one unsupported;
- held set exactly `ha_search` and `ha_get_operation_status`;
- HACS read response normalized only through
  `ha-mcp-hacs-info-top-level-success-v1` for exact 8.1.0;
- all `ha_manage_hacs` actions unreachable;
- lifecycle response model unchanged and explicitly bound to 8.1.0;
- Supervisor inventory, not the stale tagged add-on config, supplies installed
  identity;
- exact Dashboard, backup, reload, add-on restart, and Home Assistant restart
  planning contracts with no dispatch; and
- zero fallback.

## Runtime and negative evidence

Acceptance must prove deterministic catalog regeneration, both reviewed HACS
success envelopes, malformed HACS refusal, old tagged-version identity refusal,
bounded installed/server disagreement impact, stable/corrupt/loopback sidecar
behavior, shutdown worker cancellation, Engineering disconnect/readmission,
unknown-version and wrong-protocol refusal, held non-callability, complete
delegated-read exercise, exact-image/add-on admission, and 7.14.2/8.0.0
regressions.

The later controlled canary is separate authorization. It must verify the exact
deployed artifact and all success/refusal/accounting invariants before 8.1.0 is
accepted operationally.
