# HAMCP-351 exact ha-mcp 9.1.2 capture evidence

Exact official images (never a mutable tag), run as disposable `--memory 768m`
containers on scriptbox (amd64) against synthetic loopback Home Assistant
fixtures through the heavy-job gate, 2026-11-09/10 (Central). No live Home
Assistant, no production credential, no deployed MCP endpoint.

| Artifact | Identity |
|---|---|
| Standalone index | `ghcr.io/homeassistant-ai/ha-mcp@sha256:381b1e01635d3c51f35a01882863610bc0b76a22bae93835a6a5a5c926d9ecb4` |
| Platform manifests | amd64 `sha256:7ec9a929…e334`, arm64 `sha256:62cc02ab…523c` |
| OCI revision label / version | `b35a3d22a97b262e5d8520cc419cf0acdba9cac1` / `9.1.2` |
| Source tag v9.1.2 | tag object `6cb02b09…`, commit `bab615dfc90cc510f5e19a9118707d0fae03a37c`, tree `85d0ea3c…` |
| App (add-on) amd64 index | `ghcr.io/homeassistant-ai/ha-mcp-addon-amd64@sha256:32c41ad82b3aa4f4a257a73883d5ea963e5a3e98906ecce1c35f4e0e00db07b4` |

Lanes, each `/catalog/full` (strict full selector), two captures per profile:

1. Standalone image, repository fixture (`scripts/fake_ha_read_gateway_contract_server.py`):
   filesystem flag off (77 tools) and on (82 tools); the two captures of each
   profile are byte-identical.
2. Standalone image, the HAMCP-319 minimal fixture behind the committed 9.1.0
   error shapes: catalogs identical to lane 1; error-shape aggregate
   `4ed5b1b4…` equals 9.1.0 (`catalogs/fake-ha-319-error-shapes.json`).
3. App amd64 image (`ha-mcp-web` entrypoint): both profiles byte-identical to
   the standalone captures, so the App and standalone catalogs are equal.
   The aarch64 App image is pinned by digest but was not run locally (no
   native arm64 host); the CI admission lanes run it natively.

Catalog fingerprints: raw `7c8ee48b…`, strict full `c8c44ff3…`, runtime
tool order `0aab7375…` (equal to 9.1.0). No `_meta.ha_mcp.policy` block.
Seven descriptors differ from 9.1.0 (see
`docs/evidence/upstream-read-compatibility/ha-mcp-9.1.2-contract-review.json`).

Files are synthetic-fixture responses only (no secret path or token).
`SHA256SUMS` lists every file here.
