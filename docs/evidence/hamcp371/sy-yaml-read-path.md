# SY: the YAML read path, v9.1.2 to v9.2.0

Identities, method and claim marks are in `README.md`. Upstream paths are
relative to `src/ha_mcp/` unless they start with `custom_components/`. "A" is
v9.1.2, "B" is v9.2.0.

This is the review named in item 4 of the owner rulings record of 2026-11-11
(21:36): "The YAML read is reviewed in this admission (v9.1.2 to v9.2.0 diff
of its path); hold it only if the review finds a change, and report that."

**Result: the review finds a change.** The server-side tool is byte-identical
at both tags. The Home Assistant component it calls is not: the three services
on the read path are registered differently and the file is decoded
differently. Details and limits follow. Whether the change is acceptable for
admission is not decided here.

## 1. What the path is

The upstream YAML read tool is `ha_config_get_yaml`, defined in
`tools/tools_yaml_read.py`. `tools/tools_yaml_config.py` holds only the setter
(`ha_config_set_yaml`) and is not on the read path. **[source]**

The read tool imports (`tools/tools_yaml_read.py:28-45`) **[source]**:

- `refuse_reading_hidden_definitions` from `tools/config_file_visibility.py`;
- `_assert_mcp_tools_available`, `call_mcp_tools_service` and
  `is_filesystem_tools_enabled` from `tools/tools_filesystem.py`;
- `unwrap_service_response` from `tools/util_helpers.py`;
- helpers from `tools/helpers.py` and `tools/tool_hints.py`.

It reaches Home Assistant only through three services of the `ha_mcp_tools`
custom component **[source]**:

| Service | Called at |
|---|---|
| `read_file` | `tools/tools_yaml_read.py:221`, `:570-572` |
| `find_yaml_key` | `:275-278` |
| `list_files` | `:348-358` |

So the YAML read exists only where the component is installed (profile B of
the capture plan). **[source]**

## 2. Server side: unchanged

| File | A blob | B blob | Result |
|---|---|---|---|
| `tools/tools_yaml_read.py` | `6de7b679fec4` | `6de7b679fec4` | identical |
| `tools/config_file_visibility.py` | `8fa625c0165a` | `8fa625c0165a` | identical |
| `tools/util_helpers.py` | `77eaf8f039e9` | `77eaf8f039e9` | identical |
| `custom_components/ha_mcp_tools/yaml_rt.py` | `7c11fe3d15f1` | `7c11fe3d15f1` | identical |
| `tools/tools_filesystem.py` | `94ba73f467cd` | `e511266ef0ac` | changed (+149 -168) |
| `tools/tools_yaml_config.py` | `86f95682aec2` | `2b60bc1c51c3` | changed (+3 -4), not on the read path |

**[source]**

`tools/tools_filesystem.py` changed, but the three functions the read tool
imports from it did not. Each function's source text was extracted with the
Python `ast` module (`python3 -I`, source read as data) and hashed at both
tags; the hashes are equal. **[source]**

| Function | A lines | B lines |
|---|---|---|
| `call_mcp_tools_service` | `:363-395` | `:366-398` |
| `is_filesystem_tools_enabled` | `:411-420` | `:414-423` |
| `_assert_mcp_tools_available` | `:484-530` | `:513-559` |

The `tools/tools_yaml_config.py` change is imports only: helpers moved to a
new module `tools/yaml_extra_keys.py`, which the read tool does not import.
**[source]**

`tools/helpers.py` and `tools/tool_hints.py` were not compared function by
function here. The read tool's descriptor and one call per mode are compared
on the pinned images in the capture step. **[capture]** (P2)

## 3. Component side: changed

Files: `custom_components/ha_mcp_tools/__init__.py` (+127 -181),
`yaml_find.py` (+6 -5), `services.yaml` (+37 -0). Commits between the tags
that touch them: `97fdc18e` (#3009), `1249d229` (#2997), `0311641e` (#2976).
**[source]**

### 3a. All three read services become admin-only

- At A the services are registered with `hass.services.async_register`
  (`__init__.py` A `:3557`; `yaml_find.py` A `:93`). An explicit
  `_caller_is_admin` check (A `:602`) is applied at A `:3128`, `:3202`,
  `:3239`, `:3279` and `:3310` only, which are the caller-token,
  allowed-paths and extra-YAML-keys handlers. `read_file`, `list_files` and
  `find_yaml_key` have no admin check at A. **[source]**
- At B every service in the domain is registered with
  `ha_service.async_register_admin_service` (`__init__.py` B `:3501`;
  `yaml_find.py` B `:93`), and `_caller_is_admin` is removed. The comment at
  B `:3445` reads "Every service registered below is admin-only; calls
  without a user context pass Core's check." **[source]**

What this means **[source, inference]**:

- A read made with a token whose user is an administrator behaves as before.
- A read made with a token whose user is not an administrator succeeded at A
  and is refused by Core at B, before the component's handler runs. The
  refusal reaches ha-mcp as a failed service call, and its text and shape are
  Core's, not the component's.
- Which user the peer's token belongs to is a deployment fact and was not
  looked at. In the lane the disposable Core's token is created by the lane
  and its role is known, so both cases can be exercised.

To be settled by observation: the same YAML read on both images with an
administrator token (expected equal) and, at B, with a non-administrator
token (expected refusal; record the error shape Engineering sees and how it
classifies). **[capture]** (P6, profile B)

### 3b. The file is decoded explicitly as UTF-8

- A `:1361-1368`: `_read_file_sync(target_file)` reads with
  `target_file.read_text()`. **[source]**
- B `:1357-1370`: `_read_file_sync(target_file, keep_line_endings=False)`
  reads with `target_file.read_text(encoding="utf-8", newline="" if
  keep_line_endings else None)`. **[source]**
- `SERVICE_READ_FILE_SCHEMA` at B adds
  `vol.Optional("keep_line_endings", default=False)` (B `:203`), and the
  handler passes `call.data.get("keep_line_endings", False)` (B `:2898`).
  `services.yaml` documents the new field and a new `edit_file` service.
  **[source]**

What this means **[source, inference]**:

- The read tool does not send `keep_line_endings` (its source is unchanged),
  so the default applies and line endings are translated as at A.
- At A the decoding followed the Core process's locale. Where that locale is
  already UTF-8 the bytes decode the same way at both tags. Where it is not,
  a file with non-ASCII content can read differently, or fail at A and
  succeed at B.
- The Core container's locale was not read. **[capture]** (P6: one YAML file
  with non-ASCII content read on both images, results compared.)

### 3c. Smaller changes

- `list_files` handler: return annotation only. **[source]**
- `yaml_find.py`: type annotations and the admin-only registration in 3a.
  **[source]**

## 4. Engineering side

The Engineering YAML reader is `P/providers/upstream_yaml_reader.py`; its
per-version rows are at `:2181-2195`, and the policy module refers to it at
`P/upstream_tool_policy.py:435-440`, `:1373-1382` and `:1571`. Nothing in the
Engineering source was edited for this review. **[source]**

## 5. What was not read

| Item | Why | Settled by |
|---|---|---|
| Core's `async_register_admin_service` and the refusal it raises | Core source is not in the clone | P6 (observed refusal) |
| `tools/helpers.py`, `tools/tool_hints.py` function by function | not needed to establish that a change exists | P2 (descriptor), P6 (calls) |
| Core container locale | not in source | P6 (non-ASCII file) |
| which user the deployed peer's token belongs to | deployment fact; no deployment was accessed | the add-on upgrade decision record |

## Summary

| Finding | Mark |
|---|---|
| the server-side read tool and the three helper functions it imports are identical at both tags | source |
| `read_file`, `list_files` and `find_yaml_key` are registered admin-only at B and were not at A | source; effect by P6 |
| `_read_file_sync` decodes as UTF-8 at B and by locale at A; new optional `keep_line_endings` defaults to the old line-ending behaviour | source; effect by P6 |
| item 4's condition "hold it only if the review finds a change" is met: a change was found | source |
