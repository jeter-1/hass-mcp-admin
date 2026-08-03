# ha-mcp 8.0.0 live add-on runtime-contract diff

All fingerprints use protocol `2025-03-26`. Values are computed from the retained reviewed fixture and the exact local artifact captures named in the machine-readable report.
The committed fixture is name-sorted, so its strict ordered hash is not the retained wire-order strict hash. Exact-image rows preserve wire order and are the authority for strict fingerprints.

## Catalogs

| Source | Tools | Operational fingerprint | Strict ordered fingerprint |
|---|---:|---|---|
| `reviewed_fixture` | 78 | `0bc81aa7bd94416385520b9c4c4f7d9ccbc6a49f8f65b8a2a599135463327316` | `a15fc2ccd9ac6882d19ee26658c3975085d2fc8cfa675bbade4d2d0aa3457fc0` |
| `live_addon_reconstruction` | 78 | `c61b0959e766f3900300dd4dd69a6d799fc113186d91983f21be69f1bc6b8768` | `7d0eb97e09198f5f8e226e3cafb19019a26d32f55a72443321d38013ce91ff2e` |
| `exact_standalone_image` | 78 | `0bc81aa7bd94416385520b9c4c4f7d9ccbc6a49f8f65b8a2a599135463327316` | `ff18cda3ca27abc8cca69685fb5240942cbe24a1508f73b9a26e57e1afe44d5a` |
| `exact_addon_image` | 78 | `c61b0959e766f3900300dd4dd69a6d799fc113186d91983f21be69f1bc6b8768` | `f061e48a5d049a2fe84f8b46451a8c2928e0eb5fc68181cf0cbbe71ae5025727` |

## Reviewed-to-source field differences

### `ha_get_state`

- `live_addon_reconstruction`: 3 changed field(s)
  - `/_meta/ha_mcp/policy/deployment`: `"standalone"` → `"addon"`
  - `/_meta/ha_mcp/policy/enabled`: `false` → `true`
  - `/_meta/ha_mcp/policy/live`: `false` → `true`
- `exact_standalone_image`: 0 changed field(s)
- `exact_addon_image`: 3 changed field(s)
  - `/_meta/ha_mcp/policy/deployment`: `"standalone"` → `"addon"`
  - `/_meta/ha_mcp/policy/enabled`: `false` → `true`
  - `/_meta/ha_mcp/policy/live`: `false` → `true`

### `ha_config_get_automation`

- `live_addon_reconstruction`: 3 changed field(s)
  - `/_meta/ha_mcp/policy/deployment`: `"standalone"` → `"addon"`
  - `/_meta/ha_mcp/policy/enabled`: `false` → `true`
  - `/_meta/ha_mcp/policy/live`: `false` → `true`
- `exact_standalone_image`: 0 changed field(s)
- `exact_addon_image`: 3 changed field(s)
  - `/_meta/ha_mcp/policy/deployment`: `"standalone"` → `"addon"`
  - `/_meta/ha_mcp/policy/enabled`: `false` → `true`
  - `/_meta/ha_mcp/policy/live`: `false` → `true`

### `ha_get_history`

- `live_addon_reconstruction`: 3 changed field(s)
  - `/_meta/ha_mcp/policy/deployment`: `"standalone"` → `"addon"`
  - `/_meta/ha_mcp/policy/enabled`: `false` → `true`
  - `/_meta/ha_mcp/policy/live`: `false` → `true`
- `exact_standalone_image`: 0 changed field(s)
- `exact_addon_image`: 3 changed field(s)
  - `/_meta/ha_mcp/policy/deployment`: `"standalone"` → `"addon"`
  - `/_meta/ha_mcp/policy/enabled`: `false` → `true`
  - `/_meta/ha_mcp/policy/live`: `false` → `true`

### `ha_list_services`

- `live_addon_reconstruction`: 3 changed field(s)
  - `/_meta/ha_mcp/policy/deployment`: `"standalone"` → `"addon"`
  - `/_meta/ha_mcp/policy/enabled`: `false` → `true`
  - `/_meta/ha_mcp/policy/live`: `false` → `true`
- `exact_standalone_image`: 0 changed field(s)
- `exact_addon_image`: 3 changed field(s)
  - `/_meta/ha_mcp/policy/deployment`: `"standalone"` → `"addon"`
  - `/_meta/ha_mcp/policy/enabled`: `false` → `true`
  - `/_meta/ha_mcp/policy/live`: `false` → `true`

## Finding

The exact add-on and the deterministic reconstruction differ from the reviewed standalone fixture only at the shared live policy block. Input schemas, descriptions, annotations, output contracts, titles, tags, LLM exposure, and pinning are unchanged for the four representative tools. The ordinary comparator therefore remains equal while the legacy raw full-descriptor runtime comparator differs. The version-scoped v2 model validates the policy block's exact fields, JSON types, deployment allowlist, and rule-count bound before normalizing only its dynamic values. Every other descriptor field remains exact, and the strict raw fingerprints remain available as evidence.
