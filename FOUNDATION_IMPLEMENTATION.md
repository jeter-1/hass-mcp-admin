# Foundation Implementation

## Status

Implemented in version 1.1.0.

## Scope

This foundation slice adds server identity, capability discovery, build metadata,
initial tests, continuous integration, and exact dependency constraints. It does
not change existing Home Assistant operations or remove any existing tool.

## New tools

### `server_info`

Read-only. Returns:

- Stable server ID and display name.
- Semantic version and response schema version.
- Build SHA and build timestamp when supplied by the image build.
- Runtime mode (`home_assistant_addon` or `standalone`).
- Home Assistant URL.
- Optional live read-only HA connectivity probe, latency, HA version, location,
  and timezone.

### `list_capabilities`

Read-only. Returns the public tool catalog with lifecycle classification:

- `native`
- `transitional`
- `delegated`
- `deprecated`

It also lists planned engineering capabilities separately. Optional exact filters
are available for status and category.

## Build metadata

Docker builds may provide:

```bash
docker build \
  --build-arg HAMCP_BUILD_SHA="$(git rev-parse HEAD)" \
  --build-arg HAMCP_BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  -t ha-mcp-engineering ./hass_mcp_admin
```

Unknown values are reported explicitly as `unknown`; they are never invented.

## Tests and CI

The initial suite validates:

- Stable server identity and version.
- Capability count and uniqueness.
- Capability filtering.
- Add-on YAML parsing and version consistency.
- Required architecture/audit documents.
- Python compilation.

GitHub Actions runs these checks on pushes and pull requests.

## Compatibility

- Existing tool names and arguments are unchanged.
- Existing endpoint and add-on slug are unchanged.
- Tool count increases from 23 to 25.
- Add-on version increases from 1.0.0 to 1.1.0.
