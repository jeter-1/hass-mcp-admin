# HA MCP Engineering Server Beta

This add-on is the HA MCP Engineering Server: one MCP endpoint through which an
AI client inspects, analyzes and safely changes Home Assistant. Reviewed ha-mcp
tools run as internal delegated providers behind it.

The product overview, install and update steps, the safety model and the
current release are in the [root README](../README.md). Every release's notes
and acceptance contract are in the [release index](../docs/RELEASES.md), and
all other documentation is in the [documentation map](../docs/README.md).

## Installation identity

These values are kept stable across updates; update the existing add-on in
place.

| Surface | Value |
| --- | --- |
| Add-on name | HA MCP Engineering Server Beta |
| Directory and slug | `hass_mcp_engineering_beta` |
| Image repository | `ghcr.io/jeter-1/hass-mcp-engineering-beta` |
| Architectures | `amd64`, `aarch64` |
| MCP port | `8100/tcp` |
| Admin-only approval ingress | Internal port `8110`, panel **HA MCP Approval** |
| Server ID | `hass-mcp-engineering-beta` |

Set `mcp_allowed_hosts` and `mcp_allowed_origins` as described in the
[inbound policy](../docs/INBOUND_SECURITY.md) before relying on a non-loopback
path; invalid options refuse startup. See
[install or update Engineering](../README.md#install-or-update-engineering).

The retired v1 package in `hass_mcp_admin/` is not an Engineering rollback.
Release-by-release changes are in [`CHANGELOG.md`](CHANGELOG.md).
