# ha-mcp 8.4.1 compatibility evidence provenance

Review date: 2026-09-02

Source authority is `homeassistant-ai/ha-mcp` tag `v8.4.1`. The annotated tag
object is `030d1437462b2cdf24b274d1463510dea6c472e1`, its commit is
`701a7c26ac0e2309c7883a627d31873ab1510077`, and the source tree is
`1f782b05b51919b86d4fc72fd46c65bf5b77f349`. The official OCI revision is
`10cd3d1207f8270ae6e35c0c40d7fc6dc411e9e3`.

The controlling runtime catalog was captured from the immutable standalone OCI
index
`sha256:7823b36587a6e62efed271b26f3f72380b49f47364e5385580584e7ab2c60722`
against the repository's synthetic, read-only Home Assistant fixture. The
capture used the repository `review_upstream_read_release.py` pipeline:
bounded MCP initialization and paginated `tools/list`, the four declared error
probes, runtime-capture import, canonical normalization, per-tool policy and
contract generation, and reviewed-registry validation. The normalized capture
was generated twice from the same immutable image observation and compared
byte-for-byte before it became evidence.

The normalized capture sorts descriptors by tool name for deterministic
source review. Exact-image acceptance separately preserved the OCI runtime's
registration order. Both the standalone and App artifacts produced the same
78-name order, bound by the binary-owned canonical-JSON fingerprint
`b78ae4ddde97e9db9250830c96a666fbaa8561abe747fcab1223d43754bead34`.
The corresponding order-sensitive strict contract fingerprints are
`eac26329c51d57f33f02e1861227843c83cb1dd161f71a67f4c5f818483199dd`
for standalone and
`43f7372a8398227ed2eecf977778cb3be904dbbb0665ad80cbda5db68314b0e8`
for the App.

The exact standalone architecture manifests are:

- linux/amd64:
  `sha256:5b1641a073ba3ab0696e41402e85b621d23b53912bc36849220eec1f2b25db13`
- linux/arm64:
  `sha256:d25d6defb4f87e9ce5c3cd62f166e3f34cb7a9e6a2dc04db4ceb3de124e9a965`

The exact Home Assistant App artifacts are:

- linux/amd64 index:
  `sha256:2c80b35c599ca3222e1312cb9d4d9227a405d60b3bf1c2e4cf062e12033f397b`
- linux/amd64 manifest:
  `sha256:3fbe577a9e50ecdb91291b7c1346fff8a2f46a515e29f4a21cfebd8de7e588c3`
- linux/arm64 index:
  `sha256:40f6762f85fe7f228c929c9fe6185eaf4bd23ce28887a7e52027bcd068c3dc78`
- linux/arm64 manifest:
  `sha256:69a738be869064819788d5db84691045ae78f60e092335e3c194f5dfe7ba5031`

The published image advertised 78 tools with raw catalog fingerprint
`4303ead3f32c46658530a422ae37eec0d34d3f2e494a2122a7011593a568bf59`.
The separately observed source-checkout-only fingerprint
`850b221c4bf5cd4c3aa452db088859b960471b1648f66230ebffe79b7a0d725d`
was not used because that checkout lacked the populated skills-vendor
submodule and did not represent the published image.

Committed evidence digests are:

- normalized runtime capture:
  `sha256:16c5a16b3f7289ae1bbf4246f272a7fc6b4293ac988fe298029114f5cc15a05a`
- exact artifact/contract review:
  `sha256:062f53a2b0a295025bca49abcbc024d80093882af7fc24cf1496cb612d37ed88`
- compiled 8.4.1 per-tool policy:
  `sha256:97a857f437675b760a71bb120f0725e8349a4fef10b7d4215d10da0017a54441`

The normalized aggregate error-contract fingerprint is
`03000635a7b0a506c12a6f99ce86433a09683693a0e61d4265b1f11ec52b2d46`.
The individual probe codes and bounded shape fingerprints are preserved in the
normalized capture. No household data, credential, live endpoint, raw
production record, private signing key, or registry publication was used.

The reviewed provider decision admits only the read-gateway surface. Dashboard,
backup, and lifecycle surfaces are held. Dashboard remains explicitly
quarantined because its exact getter/setter contract changed and complete
Engineering provider identity and readback authority was not established in
this change.

The `ha_get_device` descriptor is admitted through the ordinary delegated-read
gateway. The separate Engineering-owned Home Assistant 2026.8 composite-device
response adapter remains limited to the exact 8.1.0, 8.1.1, and 8.2.0 evidence
set. No exact 8.4.1 single-device response capture established that the older
response transformation is still valid, so Beta 56 does not apply it to 8.4.1.
