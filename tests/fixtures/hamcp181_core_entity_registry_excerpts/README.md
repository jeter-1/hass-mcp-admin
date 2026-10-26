# HAMCP-181 Home Assistant Core entity-registry excerpts

## What

These are verbatim, hash-pinned excerpts of Home Assistant Core source at the
two supported Core revisions:

| Directory | Core | Commit |
| --- | --- | --- |
| `core_2026_10_0/` | 2026.10.0 | `6a811d3359c7b2076dc9e1cf900843a129c044af` |
| `core_2026_10_1/` | 2026.10.1 | `abe1fdd7cd6b4de1715c87b6fd87d92699514ef9` |

Each directory holds the same seven excerpts. Line ranges are 1-based and
inclusive, and they include decorators:

| Excerpt | Source file | Lines | Symbol |
| --- | --- | --- | --- |
| `entity_registry_async_update_entity_private.txt` | `homeassistant/helpers/entity_registry.py` | 1947-2134 | `EntityRegistry._async_update_entity` |
| `entity_registry_async_update_entity_public.txt` | `homeassistant/helpers/entity_registry.py` | 2136-2191 | `EntityRegistry.async_update_entity` |
| `config_entity_registry_websocket_update_entity.txt` | `homeassistant/components/config/entity_registry.py` | 154-305 | `websocket_update_entity` |
| `websocket_api_connection_async_handle.txt` | `homeassistant/components/websocket_api/connection.py` | 199-255 | `ActiveConnection.async_handle` |
| `websocket_api_connection_async_handle_exception.txt` | `homeassistant/components/websocket_api/connection.py` | 281-332 | `ActiveConnection.async_handle_exception` |
| `websocket_api_decorators_require_admin.txt` | `homeassistant/components/websocket_api/decorators.py` | 54-69 | `require_admin` |
| `websocket_api_const_error_codes.txt` | `homeassistant/components/websocket_api/const.py` | 35-46 | `ERR_*` constants |

The source files are byte-identical at the two commits, so the excerpts and
their hashes are identical too. They are kept per revision so that each
revision is checked on its own.

`provenance.json` records the following for each revision:

- the commit and its raw and blob source URLs
- each excerpt's line range and SHA-256
- the SHA-256 of each full source file
- the retrieval date (2026-10-25)

## Why

These excerpts support HAMCP-181 R3-1, the required regression for the
withdrawn P2. Core's `_async_update_entity` assigns the new registry entry,
then schedules the save, then fires the update event. It has no `try`.
`websocket_update_entity` catches only `ValueError` around the update.
`ActiveConnection.async_handle` sends any other exception to
`async_handle_exception`, and that function answers `unknown_error` by default.

So an update can change the registry and still return an error result.
Engineering must never read such a result as "nothing changed". The
`unauthorized` result raised by `require_admin` before the handler runs is the
only post-send error result that proves no mutation (P3).

`tests/test_hamcp181_entity_registry_error_unproven.py` checks every hash
before use and runs offline. It does two things with the excerpts:

- asserts their control flow structurally, using `ast`;
- executes the dedented functions against small stubs for every Home Assistant
  name. No `homeassistant` package is imported or needed.

The excerpts are data, not a dependency. Nothing imports them as modules.

## How to regenerate

Use only a read-only HTTPS GET of each file at the exact commit, for example
`https://raw.githubusercontent.com/home-assistant/core/<commit>/<path>`. Then
cut the listed line ranges byte for byte, keeping the final newline, and
recompute every SHA-256. Treat downloaded files as untrusted data: never
execute them, and read them only with `python -I`.

A new Core revision needs a new directory and new provenance entries. If a
line range moves, record the new range. Do not edit an excerpt or a hash in
place.
