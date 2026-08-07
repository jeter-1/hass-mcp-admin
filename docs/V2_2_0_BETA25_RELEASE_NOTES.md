# HA MCP Engineering 2.2.0-beta.25 `ha_search` promotion release notes

Beta 25 promotes only `ha_search` from `held_for_canary` to
`automatic_read` for exact compatibility entry
`ha-mcp-v8.1.1-e1d76a6e`. The promotion is based on the completed Beta 24
positive-path live canary: the input schema, description, annotations and
security contract, runtime contract, and output contract all matched; the
complete non-truncated result used `upstream_read_gateway`; fallback was
`none`; and the canary itself performed no promotion or admission mutation.

`ha_get_operation_status` remains the only `held_for_canary` tool. Its live
structured error-contract canary is not sufficient positive-path evidence,
and Beta 25 does not alter `run_held_read_canary`.

For exact `ha-mcp` 8.1.1, all 78 advertised tools remain classified and
accounted: 25 automatic reads, one held read, 13 mixed/wrapper-required, 33
persistent writes, four physical/high-risk actions, one prohibited tool, and
one unsupported tool. With 49 Engineering-local tools, the runtime catalog is
74 tools. The normalized reviewed catalog fingerprint is
`389c33d95537d93ad96d33f2859716611c60fa53313c6d56a598fb3c9034a82b`.

Ordinary `ha_search` calls follow the same dynamically registered
`upstream_read_gateway` path as every other admitted read. Exact release,
protocol, input, description, annotation/security, output, and runtime checks
remain mandatory. There is no fallback, direct Home Assistant route, policy
inheritance, wildcard 8.x admission, governance change, Dashboard change, or
write expansion.

Exact 8.0.0 and 8.1.0 remain at 24 automatic reads and two held reads. Beta 25
preserves `aiohttp==3.14.3`, `cryptography==50.0.0`, stable v1.1.2, and the
published Beta 24 runtime version until the protected promotion workflow
materializes the staged release. The separate Beta 24 upstream-timing
observability issue is intentionally outside this release.
