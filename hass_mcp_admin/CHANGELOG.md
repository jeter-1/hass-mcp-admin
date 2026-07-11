# Changelog

## 1.1.1

- Fixed authenticated MCP requests being redirected from `/<access_secret>/mcp` to the unauthenticated `/mcp/` path.
- Added support for authenticated MCP paths with or without a trailing slash.
- Kept unauthenticated `/mcp` and `/mcp/` paths blocked.
- Redacted the complete access secret from audited request paths.
- Added regression coverage for gateway routing, MCP initialization, unauthenticated paths, and secret redaction.

## 1.1.0

- Added `server_info` and `list_capabilities` foundation tools.
- Added centralized server and build metadata.
- Added initial unit tests and GitHub Actions validation.
- Documented the engineering-server architecture and capability boundaries.
