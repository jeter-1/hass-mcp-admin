# ha-mcp 8.0.0 reviewed compatibility evidence

This public summary records the reproducible subset of the protected offline
review used by Beta 11. It is not evidence of a running production add-on.

## Immutable identities

- source commit: `9dd3ac620e3149cd34ec3c990b6ee81e778191f2`
- standalone index: `sha256:d65630f6a3fd14d8f536c27432d4d2cf3045e6f6a2d196cba754ee8566491ae4`
- standalone amd64 manifest: `sha256:44a4fd8002e81d0fa93c4f8b9c3f200aade754d42bb64676800254283cd1d5a5`
- standalone arm64 manifest: `sha256:dc34a4ecc80b561836dc82deb1db92fac60d10e8f4807f95714d655bf8c52645`
- add-on amd64 index/image: `sha256:693ecd5c68f98e64111fbf58e02547a51b2168a942056684dbe262c550aff9cd` / `sha256:65856752c37e4c1f9093060fbbc4a1a826cac1cbd6a76e856af5f5672a96c404`
- add-on aarch64 index/image: `sha256:150ee09078919a47db19639deaa8c27ec064390054e27b4e618f82eea9cf7f50` / `sha256:a4bc83ed6f1a531d445e8107c77b7e7d5289d25510316dc6698d65383bf2fedb`

Standalone and Home Assistant add-on artifacts remain distinct. Mutable tags
and misleading OCI revision labels are not trust anchors.

## Fingerprint models

The operational admission model normalizes the fields consumed by the runtime
gate: exact tool name, input schema, full runtime description, runtime safety
annotation presence/value, output-schema presence/value and reviewed runtime
contract evidence. Its 7.14.2 identity remains
`c6bd074d9ee1e832bd90318398c00efd9a9ffd983d5444817bc830208cbfc47c`;
the exact 8.0.0 identity is
`0bc81aa7bd94416385520b9c4c4f7d9ccbc6a49f8f65b8a2a599135463327316`.

The separately named `ha-mcp-strict-full-contract-v1` evidence model retains
broader offline normalized contract evidence. Its exact fingerprints are
`7c7c93a356082b96a2ba386afe2389aa53c68fbafab82ad0bd2ad1fe70ff0d51`
for 7.14.2 and
`ff18cda3ca27abc8cca69685fb5240942cbe24a1508f73b9a26e57e1afe44d5a`
for 8.0.0. The models are independently named and tested; neither value is
silently relabeled as the other.

## Policy accounting and limitations

All 78 exact 8.0.0 tools are classified: 24 automatic reads, 14 mixed or
wrapper-required, 32 persistent writes, four physical/high-risk actions, one
prohibited, one unsupported and two held. The held tools are exactly
`ha_search` and `ha_get_operation_status`; they are neither registered nor
callable. Unknown later 8.x versions inherit no trust.

The committed normalized capture, complete policy, reviewed contract diff,
release registry and provider descriptors are validated deterministically.
Production architecture, running add-on digest, held-tool behavior, dashboard
output/not-found behavior, Supervisor backup responses, lifecycle outage and
reconnection behavior remain controlled-canary requirements.
