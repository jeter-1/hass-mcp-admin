# RETIRED: HA MCP Engineering Server stable v1 (v1.1.2)

**Do not install or deploy this code. It is no longer a Home Assistant add-on.**

## What happened

- **When.** Josh uninstalled stable v1 from production on 2026-10-16. On
  2026-10-19 Josh decided to retire it from this add-on repository, keep its
  code here, and mark it retired (HAMCP-203).
- **Why.** Stable v1 is superseded. Its known security issues will not be
  fixed (see below). Leaving it installable would offer an unmaintained server
  next to the supported one.
- **Replacement.** The supported add-on is the Engineering server in
  [`hass_mcp_engineering_beta/`](../hass_mcp_engineering_beta/) (slug
  `hass_mcp_engineering_beta`). It replaces stable v1 completely.

## How it was made uninstallable

The Home Assistant Supervisor discovers add-ons in a repository clone by
searching for `config.yaml`, `config.yml` or `config.json` at any depth. Only
paths containing a directory that starts with `.` or is named `rootfs` are
skipped (Supervisor 2026.09.1, `supervisor/store/data.py`,
`StoreData._find_app_configs`). `repository.yaml` holds only the repository
name, URL and maintainer. It does not list add-ons.

This directory's add-on configuration was therefore renamed from `config.yaml`
to [`retired-addon-config.yaml`](retired-addon-config.yaml). The Supervisor no
longer finds, lists, builds or offers stable v1. The rest of the code is
unchanged.

`tests/test_stable_v1_retirement.py` runs the exact vendored Supervisor
discovery code against this repository's tracked files and checks that only
the Engineering add-on is found. `scripts/validate_addon_metadata.py` fails if
a discoverable `config.*` file reappears in this directory.

## Known issues: won't fix

The independent static security scan of 2026-10-17 found these stable v1
issues (findings 4, 6, 9, 10 and 13). They are closed as retired-surface,
won't fix:

- route traversal;
- oversized request bodies forwarded upstream;
- trusting the `CF-Connecting-IP` header;
- audit records that keep tool arguments unredacted;
- a mutable, unhashed container build.

## Why the code is kept

The code stays as a frozen historical reference. Repository tests still use it
to check Engineering compatibility (tool parity and argument schemas). It is a
protected path: changes require explicit authorization. Do not rename
`retired-addon-config.yaml` back to `config.yaml`. That would make stable v1
installable again.
