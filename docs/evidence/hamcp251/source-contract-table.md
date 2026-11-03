# HAMCP-251 raw source contract table

Source-derived declarations, **not a captured MCP catalog or admission fixture**. Every row is tied to an exact upstream commit. `Field` descriptions/titles are omitted below for readability; their changes still invalidate exact input/descriptor hashes. Aliases and capability transforms are discussed in the main report. `self` and injected `ctx` are excluded. Named type aliases/coercion hooks are preserved, not guessed. Returns are annotations; a generic dictionary output schema does not guarantee payload compatibility.

Extraction: Python 3.13 AST over each exact source tree, selecting the named function, reading `arguments`/`returns`, dropping description/title keywords only. No upstream function body, server, app, or Home Assistant was started. SHA-256 below hashes the source file, not a runtime contract.

## ha_call_service

**8.5.0:** [src/ha_mcp/tools/tools_service.py:1828–2158](https://github.com/homeassistant-ai/ha-mcp/blob/311d6dc273fb4e9a5b8cde0de15f69472a64fe44/src/ha_mcp/tools/tools_service.py#L1828); return `dict[str, Any]`.
Source SHA-256: `4cc47219bea7e19c707ca529f98f75e202a24d6638ae656f17bfccad13906d57`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `domain` | `Annotated[str &#124; None, Field()]` | `None` |
| `service` | `Annotated[str &#124; None, Field()]` | `None` |
| `entity_id` | `Annotated[str &#124; None, Field()]` | `None` |
| `data` | `Annotated[dict[str, Any] &#124; None, JSON_STRING_COERCION, Field()]` | `None` |
| `return_response` | `Annotated[bool, Field()]` | `False` |
| `wait` | `Annotated[bool, Field()]` | `True` |
| `verbose` | `Annotated[bool, Field()]` | `False` |
| `result_fields` | `Annotated[str &#124; list[str] &#124; None, JSON_STRING_COERCION, Field(default=None)]` | `None` |
| `result_attribute_keys` | `Annotated[str &#124; list[str] &#124; None, JSON_STRING_COERCION, Field(default=None)]` | `None` |
| `ws_command` | `Annotated[str &#124; None, Field(default=None)]` | `None` |

**9.0.0:** [src/ha_mcp/tools/tools_service.py:1817–2117](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_service.py#L1817); return `dict[str, Any]`.
Source SHA-256: `88165f98467fc29f7ce6ef3b99acfebe7f8b0adcdc5e48ced4e369b739280b2b`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `domain` | `Annotated[str &#124; None, Field()]` | `None` |
| `service` | `Annotated[str &#124; None, Field()]` | `None` |
| `entity_id` | `Annotated[str &#124; None, Field()]` | `None` |
| `data` | `Annotated[dict[str, Any] &#124; None, JSON_STRING_COERCION, Field()]` | `None` |
| `return_response` | `Annotated[bool, Field()]` | `False` |
| `wait` | `Annotated[bool, Field()]` | `True` |
| `verbose` | `Annotated[bool, Field()]` | `False` |
| `result_fields` | `Annotated[str &#124; list[str] &#124; None, JSON_STRING_COERCION, Field(default=None)]` | `None` |
| `result_attribute_keys` | `Annotated[str &#124; list[str] &#124; None, JSON_STRING_COERCION, Field(default=None)]` | `None` |
| `ws_command` | `Annotated[str &#124; None, Field(default=None)]` | `None` |

## ha_config_get_automation

**8.5.0:** [src/ha_mcp/tools/tools_config_automations.py:413–481](https://github.com/homeassistant-ai/ha-mcp/blob/311d6dc273fb4e9a5b8cde0de15f69472a64fe44/src/ha_mcp/tools/tools_config_automations.py#L413); return `dict[str, Any]`.
Source SHA-256: `ee7d7b7dd8f62588ff1989de01aae4050e76189e2de0f6744d335bf76c6b4af0`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `identifier` | `Annotated[str, Field()]` | `required` |

**9.0.0:** [src/ha_mcp/tools/tools_config_automations.py:535–603](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_config_automations.py#L535); return `dict[str, Any]`.
Source SHA-256: `38438ccb3add799054ec6012544ffb853a9d0eb5a00375ef0697ed27790d5811`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `identifier` | `Annotated[str, Field()]` | `required` |

## ha_config_get_calendar_events

**8.5.0:** [src/ha_mcp/tools/tools_calendar.py:54–177](https://github.com/homeassistant-ai/ha-mcp/blob/311d6dc273fb4e9a5b8cde0de15f69472a64fe44/src/ha_mcp/tools/tools_calendar.py#L54); return `dict[str, Any]`.
Source SHA-256: `08a63d523b8237d6c0c215bb3ef4d75e94070da75e1d33a425596cd3716f2a64`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `entity_id` | `Annotated[str, Field()]` | `required` |
| `start` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `end` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `max_results` | `Annotated[int, Field(default=20)]` | `20` |

**9.0.0:** [src/ha_mcp/tools/tools_calendar.py:167–274](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_calendar.py#L167); return `dict[str, Any]`.
Source SHA-256: `cb3eda2d2204bc583b42bf228ef652e0794442fec97d792eb4c2c7992a614003`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `entity_id` | `Annotated[str, Field()]` | `required` |
| `start` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `end` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `max_results` | `Annotated[int, Field(default=20)]` | `20` |

## ha_config_get_category

**8.5.0:** [src/ha_mcp/tools/tools_categories.py:49–170](https://github.com/homeassistant-ai/ha-mcp/blob/311d6dc273fb4e9a5b8cde0de15f69472a64fe44/src/ha_mcp/tools/tools_categories.py#L49); return `dict[str, Any]`.
Source SHA-256: `378792692a26c9a54f89fee8264f691b513a0a75a8769387d43add3c279368f5`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `scope` | `Annotated[str, Field()]` | `required` |
| `category_id` | `Annotated[str &#124; None, Field(default=None)]` | `None` |

**9.0.0:** [src/ha_mcp/tools/tools_categories.py:45–159](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_categories.py#L45); return `dict[str, Any]`.
Source SHA-256: `617d36a965803b30e58060c5ad21e41b44457eff2dae60153016476011c9dd19`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `scope` | `Annotated[str, Field()]` | `required` |
| `category_id` | `Annotated[str &#124; None, Field(default=None)]` | `None` |

## ha_config_get_dashboard

**8.5.0:** [src/ha_mcp/tools/tools_config_dashboards.py:1794–2079](https://github.com/homeassistant-ai/ha-mcp/blob/311d6dc273fb4e9a5b8cde0de15f69472a64fe44/src/ha_mcp/tools/tools_config_dashboards.py#L1794); return `'dict[str, Any] | ToolResult'`.
Source SHA-256: `372d448c3d11a1722c6e824041a621d7adc8b9d86962f29a663298a148e38dc7`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `url_path` | `Annotated[str &#124; None, Field()]` | `None` |
| `list_only` | `Annotated[bool, Field()]` | `False` |
| `force_reload` | `Annotated[bool, Field()]` | `False` |
| `entity_id` | `Annotated[str &#124; None, Field()]` | `None` |
| `card_type` | `Annotated[str &#124; None, Field()]` | `None` |
| `heading` | `Annotated[str &#124; None, Field()]` | `None` |
| `include_config` | `Annotated[bool, Field()]` | `False` |
| `include_screenshot` | `Annotated[bool, Field()]` | `False` |
| `view_path` | `Annotated[str &#124; None, Field()]` | `None` |
| `mode` | `Annotated[Literal['search'] &#124; None, Field()]` | `None` |
| `query` | `Annotated[str &#124; None, Field()]` | `None` |

**9.0.0:** [src/ha_mcp/tools/tools_config_dashboards.py:1647–1867](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_config_dashboards.py#L1647); return `'dict[str, Any] | ToolResult'`.
Source SHA-256: `40797d5537359ab1f6c8a52950065c8798ca65574bebe683dffe8f56c9eb8bcd`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `url_path` | `Annotated[str &#124; None, Field()]` | `None` |
| `list_only` | `Annotated[bool, Field()]` | `False` |
| `force_reload` | `Annotated[bool, Field()]` | `False` |
| `entity_id` | `Annotated[str &#124; None, Field()]` | `None` |
| `card_type` | `Annotated[str &#124; None, Field()]` | `None` |
| `heading` | `Annotated[str &#124; None, Field()]` | `None` |
| `include_config` | `Annotated[bool, Field()]` | `False` |
| `include_screenshot` | `Annotated[bool, Field()]` | `False` |
| `view_path` | `Annotated[str &#124; None, Field()]` | `None` |
| `query` | `Annotated[str &#124; None, Field()]` | `None` |
| `describe` | `Annotated[bool, Field()]` | `False` |

## ha_config_get_label

**8.5.0:** [src/ha_mcp/tools/tools_labels.py:113–203](https://github.com/homeassistant-ai/ha-mcp/blob/311d6dc273fb4e9a5b8cde0de15f69472a64fe44/src/ha_mcp/tools/tools_labels.py#L113); return `dict[str, Any]`.
Source SHA-256: `0f8435769c9aec98f3f627ad82da06c592e4acf028f916e3e9bd8a5e67a78d3f`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `label_id` | `Annotated[str &#124; None, Field(default=None)]` | `None` |

**9.0.0:** [src/ha_mcp/tools/tools_labels.py:592–679](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_labels.py#L592); return `dict[str, Any]`.
Source SHA-256: `6f9cd359d27bce2afc6ad9d351e239a6e8745e8667a65099b46d8a543a85c88e`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `label_id` | `Annotated[str &#124; None, Field(default=None)]` | `None` |

## ha_config_get_scene

**8.5.0:** [src/ha_mcp/tools/tools_config_scenes.py:278–356](https://github.com/homeassistant-ai/ha-mcp/blob/311d6dc273fb4e9a5b8cde0de15f69472a64fe44/src/ha_mcp/tools/tools_config_scenes.py#L278); return `dict[str, Any]`.
Source SHA-256: `87407d167138ce0e958b57c28c06673d5deabfd21c428d4f860920b0919af5d0`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `scene_id` | `Annotated[str, Field()]` | `required` |

**9.0.0:** [src/ha_mcp/tools/tools_config_scenes.py:306–416](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_config_scenes.py#L306); return `dict[str, Any]`.
Source SHA-256: `5d6f5133436f9c05d37e61e083298f0a8e57eaf2cc93d932739b5b6675f8aa9e`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `scene_id` | `Annotated[str &#124; None, Field()]` | `None` |
| `query` | `Annotated[str &#124; None, Field()]` | `None` |
| `search_in_config` | `Annotated[bool, Field()]` | `False` |
| `limit` | `Annotated[int, Field(ge=1, le=100)]` | `20` |
| `offset` | `Annotated[int, Field(ge=0)]` | `0` |

## ha_config_get_script

**8.5.0:** [src/ha_mcp/tools/tools_config_scripts.py:117–194](https://github.com/homeassistant-ai/ha-mcp/blob/311d6dc273fb4e9a5b8cde0de15f69472a64fe44/src/ha_mcp/tools/tools_config_scripts.py#L117); return `dict[str, Any]`.
Source SHA-256: `6aba40b81a78ac632558ab2f15e0b7a89e91e1f01eb5afb3204c7148806b3a0f`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `script_id` | `Annotated[str, Field()]` | `required` |

**9.0.0:** [src/ha_mcp/tools/tools_config_scripts.py:119–196](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_config_scripts.py#L119); return `dict[str, Any]`.
Source SHA-256: `992225f1018b810dbc031cc7199dda2e08e85c8fcf4f1f8934035bb6a5381497`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `script_id` | `Annotated[str, Field()]` | `required` |

## ha_config_get_yaml

**8.5.0:** [src/ha_mcp/tools/tools_yaml_read.py:375–520](https://github.com/homeassistant-ai/ha-mcp/blob/311d6dc273fb4e9a5b8cde0de15f69472a64fe44/src/ha_mcp/tools/tools_yaml_read.py#L375); return `dict[str, Any]`.
Source SHA-256: `55745479c2f30753fdcefa9c609e95a2f984169a479ee0941f602db2a0184649`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `yaml_path` | `Annotated[str, Field()]` | `required` |
| `file` | `Annotated[str, Field(default='configuration.yaml')]` | `'configuration.yaml'` |
| `include_content` | `Annotated[bool, Field(default=True)]` | `True` |
| `include_parsed` | `Annotated[bool, Field(default=False)]` | `False` |

**9.0.0:** [src/ha_mcp/tools/tools_yaml_read.py:374–523](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_yaml_read.py#L374); return `dict[str, Any]`.
Source SHA-256: `922ba616fa816e6b5475d5cbe0d86bd70964f88412aa74ee7e1bc2316f45b801`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `yaml_path` | `Annotated[str, Field()]` | `required` |
| `file` | `Annotated[str, Field(default='configuration.yaml')]` | `'configuration.yaml'` |
| `include_content` | `Annotated[bool, Field(default=True)]` | `True` |
| `include_parsed` | `Annotated[bool, Field(default=False)]` | `False` |

## ha_config_list_dashboard_resources

**8.5.0:** [src/ha_mcp/tools/tools_resources.py:363–478](https://github.com/homeassistant-ai/ha-mcp/blob/311d6dc273fb4e9a5b8cde0de15f69472a64fe44/src/ha_mcp/tools/tools_resources.py#L363); return `dict[str, Any]`.
Source SHA-256: `c9c13440a6a0c31a580858fa6eaad4242ecfd7e91325d6846e99a75bffde5942`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `include_content` | `Annotated[bool, Field()]` | `False` |
| `limit` | `Annotated[int, Field(default=100, ge=1, le=500)]` | `100` |
| `offset` | `Annotated[int, Field(default=0, ge=0)]` | `0` |

**9.0.0:** [src/ha_mcp/tools/tools_resources.py:368–473](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_resources.py#L368); return `dict[str, Any]`.
Source SHA-256: `a1472ff8fa23998f1a101bee28c340f415b8b96570438e95ac24b61440461727`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `include_content` | `Annotated[bool, Field()]` | `False` |
| `limit` | `Annotated[int, Field(default=100, ge=1, le=500)]` | `100` |
| `offset` | `Annotated[int, Field(default=0, ge=0)]` | `0` |

## ha_config_list_groups

**8.5.0:** [src/ha_mcp/tools/tools_groups.py:148–242](https://github.com/homeassistant-ai/ha-mcp/blob/311d6dc273fb4e9a5b8cde0de15f69472a64fe44/src/ha_mcp/tools/tools_groups.py#L148); return `dict[str, Any]`.
Source SHA-256: `73b6bd9b4e3504729416ab775c254cbf7457c98bae9b908da43b3ba64a9d2c6b`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `limit` | `Annotated[int, Field(default=100, ge=1, le=500)]` | `100` |
| `offset` | `Annotated[int, Field(default=0, ge=0)]` | `0` |

**9.0.0:** [src/ha_mcp/tools/tools_groups.py:110–197](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_groups.py#L110); return `dict[str, Any]`.
Source SHA-256: `a4c1cf36d33b2e6785cb6084b9799e1039de439b1f68df4a2744364e8a6d8fc1`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `limit` | `Annotated[int, Field(default=100, ge=1, le=500)]` | `100` |
| `offset` | `Annotated[int, Field(default=0, ge=0)]` | `0` |

## ha_config_list_helpers

**8.5.0:** [src/ha_mcp/tools/tools_config_helpers.py:3885–4070](https://github.com/homeassistant-ai/ha-mcp/blob/311d6dc273fb4e9a5b8cde0de15f69472a64fe44/src/ha_mcp/tools/tools_config_helpers.py#L3885); return `dict[str, Any]`.
Source SHA-256: `91de3a14d222ddf74434f8c824d964aa7eb9d7af37b572b40a845e5c36a35f90`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `helper_type` | `Annotated[Literal['input_button', 'input_boolean', 'input_select', 'input_number', 'input_text', 'input_datetime', 'counter', 'timer', 'schedule', 'zone', 'person', 'tag', 'all'] &#124; SUPPORTED_HELPERS, Field()]` | `required` |
| `limit` | `Annotated[int, Field(default=100, ge=1, le=500)]` | `100` |
| `offset` | `Annotated[int, Field(default=0, ge=0)]` | `0` |

**9.0.0:** [src/ha_mcp/tools/tools_config_helpers.py:91–280](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_config_helpers.py#L91); return `dict[str, Any]`.
Source SHA-256: `6fdd2ae1487cc004a9fa69af227778df2556138c1f1401cf856af0a34e28c3f1`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `helper_type` | `Annotated[StorageHelperType &#124; Literal['all'] &#124; str, Field()]` | `required` |
| `limit` | `Annotated[int, Field(default=100, ge=1, le=500)]` | `100` |
| `offset` | `Annotated[int, Field(default=0, ge=0)]` | `0` |
| `describe` | `Annotated[bool, Field()]` | `False` |
| `menu_choice` | `Annotated[str &#124; None, Field()]` | `None` |
| `helper_id` | `Annotated[str &#124; None, Field()]` | `None` |

## ha_config_set_dashboard

**8.5.0:** [src/ha_mcp/tools/tools_config_dashboards.py:2765–3120](https://github.com/homeassistant-ai/ha-mcp/blob/311d6dc273fb4e9a5b8cde0de15f69472a64fe44/src/ha_mcp/tools/tools_config_dashboards.py#L2765); return `'dict[str, Any] | ToolResult'`.
Source SHA-256: `372d448c3d11a1722c6e824041a621d7adc8b9d86962f29a663298a148e38dc7`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `url_path` | `Annotated[str, Field()]` | `required` |
| `config` | `Annotated[dict[str, Any] &#124; None, JSON_STRING_COERCION, Field()]` | `None` |
| `python_transform` | `Annotated[str &#124; None, Field()]` | `None` |
| `config_hash` | `Annotated[str &#124; None, Field()]` | `None` |
| `title` | `Annotated[str &#124; None, Field()]` | `None` |
| `icon` | `Annotated[str &#124; None, Field()]` | `None` |
| `require_admin` | `Annotated[bool &#124; None, Field()]` | `None` |
| `show_in_sidebar` | `Annotated[bool &#124; None, Field()]` | `None` |
| `MandatoryBPS` | `Annotated[bool, Field(default=True)]` | `True` |
| `BestPracticeKey` | `BestPracticeKeyParam` | `None` |
| `return_screenshot` | `Annotated[bool, Field()]` | `False` |
| `view_path` | `Annotated[str &#124; None, Field()]` | `None` |
| `patch` | `Annotated[list[dict[str, Any]] &#124; None, JSON_STRING_COERCION, Field()]` | `None` |

**9.0.0:** [src/ha_mcp/tools/tools_config_dashboards.py:2470–2725](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_config_dashboards.py#L2470); return `'dict[str, Any] | ToolResult'`.
Source SHA-256: `40797d5537359ab1f6c8a52950065c8798ca65574bebe683dffe8f56c9eb8bcd`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `url_path` | `Annotated[str, Field()]` | `required` |
| `config` | `Annotated[dict[str, Any] &#124; None, JSON_STRING_COERCION, Field()]` | `None` |
| `python_transform` | `Annotated[str &#124; None, Field()]` | `None` |
| `config_hash` | `Annotated[str &#124; None, Field()]` | `None` |
| `title` | `Annotated[str &#124; None, Field()]` | `None` |
| `icon` | `Annotated[str &#124; None, Field()]` | `None` |
| `require_admin` | `Annotated[bool &#124; None, Field()]` | `None` |
| `show_in_sidebar` | `Annotated[bool &#124; None, Field()]` | `None` |
| `MandatoryBPS` | `Annotated[bool, Field(default=True)]` | `True` |
| `BestPracticeKey` | `BestPracticeKeyParam` | `None` |
| `return_screenshot` | `Annotated[bool, Field()]` | `False` |
| `view_path` | `Annotated[str &#124; None, Field()]` | `None` |
| `patch` | `Annotated[list[dict[str, Any]] &#124; None, JSON_STRING_COERCION, Field()]` | `None` |

## ha_eval_template

**8.5.0:** [src/ha_mcp/tools/tools_utility.py:129–278](https://github.com/homeassistant-ai/ha-mcp/blob/311d6dc273fb4e9a5b8cde0de15f69472a64fe44/src/ha_mcp/tools/tools_utility.py#L129); return `dict[str, Any]`.
Source SHA-256: `9971b73732a2dc0e548f8703bef1eee9bb8ace233d50b0698963e0a2686adc76`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `template` | `str` | `required` |
| `timeout` | `int` | `3` |
| `report_errors` | `bool` | `True` |

**9.0.0:** [src/ha_mcp/tools/tools_utility.py:330–421](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_utility.py#L330); return `dict[str, Any]`.
Source SHA-256: `c3e27335e18f27fb34ec3185a2a1a995e453ad7e8204a5d936a3da6b1a4e1055`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `template` | `Annotated[str &#124; None, Field()]` | `None` |
| `condition` | `Annotated[dict[str, Any] &#124; None, JSON_STRING_COERCION, Field()]` | `None` |
| `variables` | `Annotated[dict[str, Any] &#124; None, JSON_STRING_COERCION, Field()]` | `None` |
| `strict` | `Annotated[bool, Field()]` | `False` |
| `timeout` | `Annotated[int, Field(ge=1, le=60)]` | `3` |
| `report_errors` | `Annotated[bool, Field()]` | `True` |

## ha_get_addon

**8.5.0:** no function under this name; see lifecycle alias analysis.

**9.0.0:** no function under this name; see lifecycle alias analysis.

## ha_get_app

**8.5.0:** [src/ha_mcp/tools/tools_addons.py:3758–3818](https://github.com/homeassistant-ai/ha-mcp/blob/311d6dc273fb4e9a5b8cde0de15f69472a64fe44/src/ha_mcp/tools/tools_addons.py#L3758); return `dict[str, Any]`.
Source SHA-256: `f15b3208df00ad991e46f0b2d4be4c2af0eed93bc4d555a8090d28ab6df100c6`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `source` | `Annotated[Literal['installed', 'available'] &#124; None, Field(default=None)]` | `None` |
| `slug` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `include_stats` | `Annotated[bool, Field(default=False)]` | `False` |
| `repository` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `query` | `Annotated[str &#124; None, Field(default=None)]` | `None` |

**9.0.0:** [src/ha_mcp/tools/tools_addons.py:3702–3760](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_addons.py#L3702); return `dict[str, Any]`.
Source SHA-256: `b7185a5bb0ff5bd1abb260f055a9c9c7d962dde73a4f7f36617477f0b3ef74db`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `source` | `Annotated[Literal['installed', 'available'] &#124; None, Field(default=None)]` | `None` |
| `slug` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `include_stats` | `Annotated[bool, Field(default=False)]` | `False` |
| `repository` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `query` | `Annotated[str &#124; None, Field(default=None)]` | `None` |

## ha_get_automation_traces

**8.5.0:** [src/ha_mcp/tools/tools_traces.py:49–261](https://github.com/homeassistant-ai/ha-mcp/blob/311d6dc273fb4e9a5b8cde0de15f69472a64fe44/src/ha_mcp/tools/tools_traces.py#L49); return `dict[str, Any]`.
Source SHA-256: `194f86f3408e3799ab5e9a0a7ffe0cdb3560f17a507be02e7dfc102d4b1c4d18`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `automation_id` | `Annotated[str, Field()]` | `required` |
| `run_id` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `limit` | `Annotated[int, Field(default=10, ge=1, le=50)]` | `10` |
| `deduplicate` | `Annotated[bool, Field(default=True)]` | `True` |
| `detailed` | `Annotated[bool, Field(default=False)]` | `False` |
| `sections` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `offset` | `Annotated[int, Field(default=0, ge=0)]` | `0` |
| `order` | `Annotated[Literal['newest', 'oldest'], Field(default='newest')]` | `'newest'` |

**9.0.0:** [src/ha_mcp/tools/tools_traces.py:44–224](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_traces.py#L44); return `dict[str, Any]`.
Source SHA-256: `01fca982133e12c5f61e37d71bdd5a7820f93b127876ac0a0f6221d34664b039`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `automation_id` | `Annotated[str, Field()]` | `required` |
| `run_id` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `limit` | `Annotated[int, Field(default=10, ge=1, le=50)]` | `10` |
| `deduplicate` | `Annotated[bool, Field(default=True)]` | `True` |
| `detailed` | `Annotated[bool, Field(default=False)]` | `False` |
| `sections` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `offset` | `Annotated[int, Field(default=0, ge=0)]` | `0` |
| `order` | `Annotated[Literal['newest', 'oldest'], Field(default='newest')]` | `'newest'` |

## ha_get_device

**8.5.0:** [src/ha_mcp/tools/tools_registry.py:890–1018](https://github.com/homeassistant-ai/ha-mcp/blob/311d6dc273fb4e9a5b8cde0de15f69472a64fe44/src/ha_mcp/tools/tools_registry.py#L890); return `dict[str, Any]`.
Source SHA-256: `a61d8e0671ec9f871b5882d42ccff4b547dabe636992c59d83eaf30ac1e86577`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `device_id` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `entity_id` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `integration` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `area_id` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `manufacturer` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `limit` | `Annotated[int, Field(default=50, ge=1, le=200)]` | `50` |
| `offset` | `Annotated[int, Field(default=0, ge=0)]` | `0` |
| `detail_level` | `Annotated[Literal['summary', 'full'], Field(default='summary')]` | `'summary'` |

**9.0.0:** [src/ha_mcp/tools/tools_registry.py:774–903](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_registry.py#L774); return `dict[str, Any]`.
Source SHA-256: `11dba943cca9836619f662d1fb0f1f492f9c4e34cebdeec286b63400b1a2a2c3`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `device_id` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `entity_id` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `integration` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `area_id` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `manufacturer` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `limit` | `Annotated[int, Field(default=50, ge=1, le=200)]` | `50` |
| `offset` | `Annotated[int, Field(default=0, ge=0)]` | `0` |
| `detail_level` | `Annotated[Literal['summary', 'full'], Field(default='summary')]` | `'summary'` |

## ha_get_entity

**8.5.0:** [src/ha_mcp/tools/tools_entities.py:1877–2024](https://github.com/homeassistant-ai/ha-mcp/blob/311d6dc273fb4e9a5b8cde0de15f69472a64fe44/src/ha_mcp/tools/tools_entities.py#L1877); return `dict[str, Any]`.
Source SHA-256: `8baa98305838c790d1ffe827e6798f1bc3855ccdf0cbb9194ff2d41ba35617d4`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `entity_id` | `Annotated[str &#124; list[str] &#124; None, JSON_STRING_COERCION, Field(default=None)]` | `None` |
| `unique_id` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `domain` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `platform` | `Annotated[str &#124; None, Field(default=None)]` | `None` |

**9.0.0:** [src/ha_mcp/tools/tools_entities.py:1822–1946](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_entities.py#L1822); return `dict[str, Any]`.
Source SHA-256: `00d95b2d035e9407c9979f51a296914ab8f8ea6b4f3310644c1e7faa7876295c`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `entity_id` | `Annotated[str &#124; list[str] &#124; None, JSON_STRING_COERCION, Field(default=None)]` | `None` |
| `unique_id` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `domain` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `platform` | `Annotated[str &#124; None, Field(default=None)]` | `None` |

## ha_get_entity_exposure

**8.5.0:** [src/ha_mcp/tools/tools_voice_assistant.py:210–332](https://github.com/homeassistant-ai/ha-mcp/blob/311d6dc273fb4e9a5b8cde0de15f69472a64fe44/src/ha_mcp/tools/tools_voice_assistant.py#L210); return `dict[str, Any]`.
Source SHA-256: `f0013c7795e05cf6705671b60b5355af6c3d8e51e258e80debabb258bbc4199f`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `entity_id` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `assistant` | `Annotated[str &#124; None, Field(default=None)]` | `None` |

**9.0.0:** [src/ha_mcp/tools/tools_voice_assistant.py:206–322](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_voice_assistant.py#L206); return `dict[str, Any]`.
Source SHA-256: `f12d513d89bf4ed07fda49bbaa563f1a5a01c4033e27ba3eb12898ccf86772bd`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `entity_id` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `assistant` | `Annotated[str &#124; None, Field(default=None)]` | `None` |

## ha_get_hacs_info

**8.5.0:** [src/ha_mcp/tools/tools_hacs.py:143–234](https://github.com/homeassistant-ai/ha-mcp/blob/311d6dc273fb4e9a5b8cde0de15f69472a64fe44/src/ha_mcp/tools/tools_hacs.py#L143); return `dict[str, Any]`.
Source SHA-256: `c69cf672769ea01a04c7b91d57425ccf48f5a3bec8482eaa205c0df3df49f9ad`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `action` | `Annotated[Literal['search', 'info'], Field()]` | `required` |
| `query` | `Annotated[str, Field()]` | `''` |
| `category` | `Annotated[Literal['integration', 'lovelace', 'theme', 'appdaemon', 'python_script'] &#124; None, Field()]` | `None` |
| `installed_only` | `Annotated[bool, Field()]` | `False` |
| `max_results` | `Annotated[int, Field(ge=1, le=100)]` | `10` |
| `offset` | `Annotated[int, Field(ge=0)]` | `0` |
| `repository_id` | `Annotated[str &#124; None, Field()]` | `None` |

**9.0.0:** [src/ha_mcp/tools/tools_hacs.py:148–236](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_hacs.py#L148); return `dict[str, Any]`.
Source SHA-256: `c717f818c325832a0050e97ae8fccd22a9c54ca923e45ec2e009750db9d203e3`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `action` | `Annotated[Literal['search', 'info'], Field()]` | `required` |
| `query` | `Annotated[str, Field()]` | `''` |
| `category` | `Annotated[Literal['integration', 'lovelace', 'theme', 'appdaemon', 'python_script'] &#124; None, Field()]` | `None` |
| `installed_only` | `Annotated[bool, Field()]` | `False` |
| `max_results` | `Annotated[int, Field(ge=1, le=100)]` | `10` |
| `offset` | `Annotated[int, Field(ge=0)]` | `0` |
| `repository_id` | `Annotated[str &#124; None, Field()]` | `None` |

## ha_get_history

**8.5.0:** [src/ha_mcp/tools/tools_history.py:221–516](https://github.com/homeassistant-ai/ha-mcp/blob/311d6dc273fb4e9a5b8cde0de15f69472a64fe44/src/ha_mcp/tools/tools_history.py#L221); return `dict[str, Any]`.
Source SHA-256: `fc8f9c505dd97372ea4f3d27c8fa9b786b32955da1147cb4b1ca3ec2ac848b90`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `entity_ids` | `Annotated[str &#124; list[str], JSON_STRING_COERCION, Field()]` | `required` |
| `source` | `Annotated[Literal['history', 'statistics'], Field(default='history')]` | `'history'` |
| `start_time` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `end_time` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `minimal_response` | `Annotated[bool, Field(default=True)]` | `True` |
| `significant_changes_only` | `Annotated[bool, Field(default=True)]` | `True` |
| `limit` | `Annotated[int &#124; None, Field(default=None, ge=1, le=1000)]` | `None` |
| `offset` | `Annotated[int &#124; None, Field(default=None, ge=0)]` | `None` |
| `period` | `Annotated[str, Field(default='day')]` | `'day'` |
| `statistic_types` | `Annotated[str &#124; list[str] &#124; None, JSON_STRING_COERCION, Field(default=None)]` | `None` |
| `order` | `Annotated[Literal['asc', 'desc'], Field(default='desc')]` | `'desc'` |
| `fields` | `Annotated[str &#124; list[str] &#124; None, JSON_STRING_COERCION, Field(default=None)]` | `None` |

**9.0.0:** [src/ha_mcp/tools/tools_history.py:209–540](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_history.py#L209); return `dict[str, Any]`.
Source SHA-256: `24489252f460fb131cd56baab767b263eaff0377d1537da43a7f514dce3857e7`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `entity_ids` | `Annotated[str &#124; list[str], JSON_STRING_COERCION, Field()]` | `required` |
| `source` | `Annotated[Literal['history', 'statistics'], Field(default='history')]` | `'history'` |
| `start_time` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `end_time` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `minimal_response` | `Annotated[bool, Field(default=True)]` | `True` |
| `significant_changes_only` | `Annotated[bool, Field(default=True)]` | `True` |
| `limit` | `Annotated[int &#124; None, Field(default=None, ge=1, le=1000)]` | `None` |
| `offset` | `Annotated[int &#124; None, Field(default=None, ge=0)]` | `None` |
| `period` | `Annotated[str, Field(default='day')]` | `'day'` |
| `statistic_types` | `Annotated[str &#124; list[str] &#124; None, JSON_STRING_COERCION, Field(default=None)]` | `None` |
| `order` | `Annotated[Literal['asc', 'desc'], Field(default='desc')]` | `'desc'` |
| `summary` | `Annotated[bool, Field(default=False)]` | `False` |
| `fields` | `Annotated[str &#124; list[str] &#124; None, JSON_STRING_COERCION, Field(default=None)]` | `None` |
| `include_schema` | `Annotated[bool, Field()]` | `False` |
| `core_options` | `Annotated[dict[str, Any] &#124; None, JSON_STRING_COERCION, Field()]` | `None` |

## ha_get_integration

**8.5.0:** [src/ha_mcp/tools/tools_integrations.py:487–893](https://github.com/homeassistant-ai/ha-mcp/blob/311d6dc273fb4e9a5b8cde0de15f69472a64fe44/src/ha_mcp/tools/tools_integrations.py#L487); return `dict[str, Any]`.
Source SHA-256: `3bbb13e8516e00022431d4ba18db900c9f1ac47b0819de8007f9bb1b7d00d7d6`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `entry_id` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `query` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `domain` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `include_options` | `Annotated[bool, Field(default=False)]` | `False` |
| `include_schema` | `Annotated[bool, Field(default=False)]` | `False` |
| `include_subentries` | `Annotated[bool, Field(default=False)]` | `False` |
| `include_subentry_schema` | `Annotated[bool, Field(default=False)]` | `False` |
| `subentry_type` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `subentry_id` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `show_advanced_options` | `Annotated[bool, Field(default=False)]` | `False` |
| `exact_match` | `Annotated[bool, Field(default=True)]` | `True` |
| `limit` | `Annotated[int, Field(default=50, ge=1, le=200)]` | `50` |
| `offset` | `Annotated[int, Field(default=0, ge=0)]` | `0` |
| `include_diagnostics` | `Annotated[bool, Field(default=False)]` | `False` |
| `include_knx_project` | `Annotated[bool, Field(default=False)]` | `False` |
| `device_id` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `diagnostics_fields` | `Annotated[list[str] &#124; str &#124; None, JSON_STRING_COERCION, Field(default=None)]` | `None` |
| `diagnostics_truncate_at_bytes` | `Annotated[int &#124; None, Field(default=None, ge=1)]` | `None` |
| `diagnostics_data_path` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `diagnostics_data_offset` | `Annotated[int &#124; None, Field(default=0, ge=0)]` | `0` |
| `diagnostics_data_limit` | `Annotated[int &#124; None, Field(default=None, ge=1)]` | `None` |

**9.0.0:** [src/ha_mcp/tools/tools_integrations.py:410–781](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_integrations.py#L410); return `dict[str, Any]`.
Source SHA-256: `b42429c8e7b622b97abd55ed79dc543d17bb88600362137e4d9f76a473b07e1e`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `entry_id` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `query` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `domain` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `include_options` | `Annotated[bool, Field(default=False)]` | `False` |
| `include_schema` | `Annotated[bool, Field(default=False)]` | `False` |
| `include_subentries` | `Annotated[bool, Field(default=False)]` | `False` |
| `include_subentry_schema` | `Annotated[bool, Field(default=False)]` | `False` |
| `subentry_type` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `subentry_id` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `show_advanced_options` | `Annotated[bool, Field(default=False)]` | `False` |
| `exact_match` | `Annotated[bool, Field(default=True)]` | `True` |
| `limit` | `Annotated[int, Field(default=50, ge=1, le=200)]` | `50` |
| `offset` | `Annotated[int, Field(default=0, ge=0)]` | `0` |
| `include_diagnostics` | `Annotated[bool, Field(default=False)]` | `False` |
| `include_knx_project` | `Annotated[bool, Field(default=False)]` | `False` |
| `device_id` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `diagnostics_fields` | `Annotated[list[str] &#124; str &#124; None, JSON_STRING_COERCION, Field(default=None)]` | `None` |
| `diagnostics_truncate_at_bytes` | `Annotated[int &#124; None, Field(default=None, ge=1)]` | `None` |
| `diagnostics_data_path` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `diagnostics_data_offset` | `Annotated[int &#124; None, Field(default=0, ge=0)]` | `0` |
| `diagnostics_data_limit` | `Annotated[int &#124; None, Field(default=None, ge=1)]` | `None` |

## ha_get_logs

**8.5.0:** [src/ha_mcp/tools/tools_logs.py:171–317](https://github.com/homeassistant-ai/ha-mcp/blob/311d6dc273fb4e9a5b8cde0de15f69472a64fe44/src/ha_mcp/tools/tools_logs.py#L171); return `dict[str, Any]`.
Source SHA-256: `d3a52ff326694ddd852a2558271ef895f134d56363ae8ce32e25f3f88c3f54eb`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `source` | `Literal['logbook', 'system', 'error_log', 'supervisor', 'system_service', 'logger', 'fault_log']` | `'logbook'` |
| `limit` | `int &#124; None` | `None` |
| `search` | `str &#124; None` | `None` |
| `order` | `Annotated[Literal['newest', 'oldest'], Field()]` | `'newest'` |
| `hours_back` | `Annotated[int, Field(ge=1)]` | `1` |
| `entity_id` | `str &#124; None` | `None` |
| `end_time` | `str &#124; None` | `None` |
| `offset` | `Annotated[int, Field(ge=0)]` | `0` |
| `compact` | `bool` | `True` |
| `level` | `str &#124; None` | `None` |
| `structured` | `Annotated[bool, Field()]` | `False` |
| `top_n` | `Annotated[int &#124; None, Field(ge=1)]` | `None` |
| `slug` | `str &#124; None` | `None` |

**9.0.0:** [src/ha_mcp/tools/tools_logs.py:242–471](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_logs.py#L242); return `dict[str, Any]`.
Source SHA-256: `1c792f273013220d3e8229b6af933a72b5a17762d3b9d49e824a8065a6a271c0`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `source` | `Annotated[Literal['logbook', 'system', 'error_log', 'supervisor', 'system_service', 'logger', 'fault_log'], Field()]` | `'logbook'` |
| `limit` | `Annotated[int &#124; None, Field()]` | `None` |
| `search` | `Annotated[str &#124; None, Field()]` | `None` |
| `order` | `Annotated[Literal['newest', 'oldest'], Field()]` | `'newest'` |
| `hours_back` | `Annotated[int, Field(ge=1)]` | `1` |
| `entity_id` | `Annotated[str &#124; None, Field()]` | `None` |
| `device_id` | `Annotated[str &#124; None, Field()]` | `None` |
| `context_id` | `Annotated[str &#124; None, Field()]` | `None` |
| `end_time` | `Annotated[str &#124; None, Field()]` | `None` |
| `offset` | `Annotated[int &#124; str, Field()]` | `0` |
| `compact` | `Annotated[bool, Field()]` | `True` |
| `level` | `Annotated[str &#124; None, Field()]` | `None` |
| `structured` | `Annotated[bool, Field()]` | `False` |
| `top_n` | `Annotated[int &#124; None, Field(ge=1)]` | `None` |
| `slug` | `Annotated[str &#124; None, Field()]` | `None` |

## ha_get_overview

**8.5.0:** [src/ha_mcp/tools/tools_search.py:4001–4287](https://github.com/homeassistant-ai/ha-mcp/blob/311d6dc273fb4e9a5b8cde0de15f69472a64fe44/src/ha_mcp/tools/tools_search.py#L4001); return `dict[str, Any]`.
Source SHA-256: `6ca22976cdd331a20b22ef9047adc61a56998c8792793e651cfafa954f930e56`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `detail_level` | `Annotated[Literal['minimal', 'standard', 'full'], Field(default='minimal')]` | `'minimal'` |
| `domains` | `Annotated[str &#124; list[str] &#124; None, JSON_STRING_COERCION, Field(default=None)]` | `None` |
| `limit` | `Annotated[int &#124; None, Field(default=None, ge=1)]` | `None` |
| `offset` | `Annotated[int, Field(default=0, ge=0)]` | `0` |
| `max_entities_per_domain` | `Annotated[int &#124; None, Field(default=None)]` | `None` |
| `include_state` | `Annotated[bool &#124; None, Field(default=None)]` | `None` |
| `include_entity_id` | `Annotated[bool &#124; None, Field(default=None)]` | `None` |
| `include_notifications` | `Annotated[bool &#124; None, Field(default=True)]` | `True` |
| `include_dismissed_repairs` | `Annotated[bool &#124; None, Field(default=False)]` | `False` |
| `fields` | `Annotated[str &#124; list[str] &#124; None, JSON_STRING_COERCION, Field(default=None)]` | `None` |

**9.0.0:** [src/ha_mcp/tools/tools_search.py:430–695](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_search.py#L430); return `dict[str, Any]`.
Source SHA-256: `92e0fd22e93f73adb9acf59d8b072d040c64f08a67e3b6a069b09c9c2e3441fe`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `detail_level` | `Annotated[Literal['minimal', 'standard', 'full'], Field(default='minimal')]` | `'minimal'` |
| `domains` | `Annotated[str &#124; list[str] &#124; None, JSON_STRING_COERCION, Field(default=None)]` | `None` |
| `limit` | `Annotated[int &#124; None, Field(default=None, ge=1)]` | `None` |
| `offset` | `Annotated[int, Field(default=0, ge=0)]` | `0` |
| `max_entities_per_domain` | `Annotated[int &#124; None, Field(default=None)]` | `None` |
| `include_state` | `Annotated[bool &#124; None, Field(default=None)]` | `None` |
| `include_entity_id` | `Annotated[bool &#124; None, Field(default=None)]` | `None` |
| `include_notifications` | `Annotated[bool &#124; None, Field(default=True)]` | `True` |
| `include_dismissed_repairs` | `Annotated[bool &#124; None, Field(default=False)]` | `False` |
| `fields` | `Annotated[str &#124; list[str] &#124; None, JSON_STRING_COERCION, Field(default=None)]` | `None` |

## ha_get_skill_guide

**8.5.0:** [src/ha_mcp/server.py:1679–1707](https://github.com/homeassistant-ai/ha-mcp/blob/311d6dc273fb4e9a5b8cde0de15f69472a64fe44/src/ha_mcp/server.py#L1679); return `dict[str, Any]`.
Source SHA-256: `53359c33184df5ffb363b3145dc9d5fc707407d6b49a2a711516c4556b61f4cf`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `skill` | `Annotated[str &#124; None, Field()]` | `None` |
| `file` | `Annotated[str &#124; None, Field()]` | `None` |

**9.0.0:** [src/ha_mcp/server.py:956–974](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/server.py#L956); return `dict[str, Any]`.
Source SHA-256: `32dad7812aeeadace05e38a94f5f2feabd9010a41f421ffbca34843b7930eb38`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `file` | `Annotated[str, Field()]` | `'SKILL.md'` |

## ha_get_state

**8.5.0:** [src/ha_mcp/tools/tools_search.py:4800–4906](https://github.com/homeassistant-ai/ha-mcp/blob/311d6dc273fb4e9a5b8cde0de15f69472a64fe44/src/ha_mcp/tools/tools_search.py#L4800); return `dict[str, Any]`.
Source SHA-256: `6ca22976cdd331a20b22ef9047adc61a56998c8792793e651cfafa954f930e56`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `entity_id` | `Annotated[str &#124; list[str], JSON_STRING_COERCION, Field()]` | `required` |
| `fields` | `Annotated[str &#124; list[str] &#124; None, JSON_STRING_COERCION, Field(default=None)]` | `None` |
| `attribute_keys` | `Annotated[str &#124; list[str] &#124; None, JSON_STRING_COERCION, Field(default=None)]` | `None` |

**9.0.0:** [src/ha_mcp/tools/tools_search.py:703–796](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_search.py#L703); return `dict[str, Any]`.
Source SHA-256: `92e0fd22e93f73adb9acf59d8b072d040c64f08a67e3b6a069b09c9c2e3441fe`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `entity_id` | `Annotated[str &#124; list[str], JSON_STRING_COERCION, Field()]` | `required` |
| `fields` | `Annotated[str &#124; list[str] &#124; None, JSON_STRING_COERCION, Field(default=None)]` | `None` |
| `attribute_keys` | `Annotated[str &#124; list[str] &#124; None, JSON_STRING_COERCION, Field(default=None)]` | `None` |

## ha_get_system_health

**8.5.0:** [src/ha_mcp/tools/tools_system.py:584–861](https://github.com/homeassistant-ai/ha-mcp/blob/311d6dc273fb4e9a5b8cde0de15f69472a64fe44/src/ha_mcp/tools/tools_system.py#L584); return `dict[str, Any]`.
Source SHA-256: `a79a45f709082af457e95e30868919025eb0b40535ff68dd16f2132aff27878d`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `include` | `str &#124; None` | `None` |
| `include_dismissed_repairs` | `bool &#124; None` | `False` |
| `config_entry_id` | `str &#124; None` | `None` |
| `device_id` | `str &#124; None` | `None` |
| `diagnostics_fields` | `Annotated[list[str] &#124; str &#124; None, JSON_STRING_COERCION]` | `None` |
| `diagnostics_truncate_at_bytes` | `Annotated[int, Field(ge=1)] &#124; None` | `None` |
| `diagnostics_data_path` | `str &#124; None` | `None` |
| `diagnostics_data_offset` | `Annotated[int, Field(ge=0)] &#124; None` | `0` |
| `diagnostics_data_limit` | `Annotated[int, Field(ge=1)] &#124; None` | `None` |

**9.0.0:** [src/ha_mcp/tools/tools_system.py:481–767](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_system.py#L481); return `dict[str, Any]`.
Source SHA-256: `6a19e4a6ac43bfbb809138cddbf3a35be2d8a731c00183d1b2ccee172bf0fcc3`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `include` | `Annotated[str &#124; None, Field()]` | `None` |
| `include_dismissed_repairs` | `Annotated[bool &#124; None, Field()]` | `False` |
| `config_entry_id` | `Annotated[str &#124; None, Field()]` | `None` |
| `device_id` | `Annotated[str &#124; None, Field()]` | `None` |
| `diagnostics_fields` | `Annotated[list[str] &#124; str &#124; None, JSON_STRING_COERCION, Field()]` | `None` |
| `diagnostics_truncate_at_bytes` | `Annotated[int &#124; None, Field(ge=1)]` | `None` |
| `diagnostics_data_path` | `Annotated[str &#124; None, Field()]` | `None` |
| `diagnostics_data_offset` | `Annotated[int &#124; None, Field(ge=0)]` | `0` |
| `diagnostics_data_limit` | `Annotated[int &#124; None, Field(ge=1)]` | `None` |

## ha_get_todo

**8.5.0:** [src/ha_mcp/tools/tools_todo.py:49–213](https://github.com/homeassistant-ai/ha-mcp/blob/311d6dc273fb4e9a5b8cde0de15f69472a64fe44/src/ha_mcp/tools/tools_todo.py#L49); return `dict[str, Any]`.
Source SHA-256: `966710a232b716dc0d978bb978ef2146b2440005e7f5d578155300b31448772f`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `entity_id` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `status` | `Annotated[Literal['needs_action', 'completed'] &#124; None, Field(default=None)]` | `None` |

**9.0.0:** [src/ha_mcp/tools/tools_todo.py:45–180](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_todo.py#L45); return `dict[str, Any]`.
Source SHA-256: `0f9ef4b3db66499ddeaffc10c80e5a8a1116634385bcff9d07c84209c2c8ffe0`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `entity_id` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `status` | `Annotated[Literal['needs_action', 'completed'] &#124; None, Field(default=None)]` | `None` |

## ha_get_zone

**8.5.0:** [src/ha_mcp/tools/tools_zones.py:139–215](https://github.com/homeassistant-ai/ha-mcp/blob/311d6dc273fb4e9a5b8cde0de15f69472a64fe44/src/ha_mcp/tools/tools_zones.py#L139); return `dict[str, Any]`.
Source SHA-256: `d653dfed40fb1514005489504cd267c58ab4a0669b06f009ca11a52b24cf70d7`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `zone_id` | `Annotated[str &#124; None, Field(default=None)]` | `None` |

**9.0.0:** [src/ha_mcp/tools/tools_zones.py:170–221](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_zones.py#L170); return `dict[str, Any]`.
Source SHA-256: `17c75c1f796ede0b959f6788c59cdbc682d462c6f63565432e7d8358e535bb88`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `zone_id` | `Annotated[str &#124; None, Field(default=None)]` | `None` |

## ha_list_files

**8.5.0:** [src/ha_mcp/tools/tools_filesystem.py:552–653](https://github.com/homeassistant-ai/ha-mcp/blob/311d6dc273fb4e9a5b8cde0de15f69472a64fe44/src/ha_mcp/tools/tools_filesystem.py#L552); return `dict[str, Any]`.
Source SHA-256: `1a84730b3b0199b9f3038cf65da4f8defc865c4a9c94fec8eb4af0a2ad520355`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `path` | `Annotated[str, Field()]` | `required` |
| `pattern` | `Annotated[str &#124; None, Field(default=None)]` | `None` |

**9.0.0:** [src/ha_mcp/tools/tools_filesystem.py:547–629](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_filesystem.py#L547); return `dict[str, Any]`.
Source SHA-256: `0ff9d5f437fc6258e4b6152a00437fdc56a1cce4a192bba4f3ac2126ea765e83`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `path` | `Annotated[str, Field()]` | `required` |
| `pattern` | `Annotated[str &#124; None, Field(default=None)]` | `None` |

## ha_list_floors_areas

**8.5.0:** [src/ha_mcp/tools/tools_areas.py:262–396](https://github.com/homeassistant-ai/ha-mcp/blob/311d6dc273fb4e9a5b8cde0de15f69472a64fe44/src/ha_mcp/tools/tools_areas.py#L262); return `dict[str, Any]`.
Source SHA-256: `481dcb24600429ac98abe7b1a11341a7feef3cc3cde1c7608b0420d499d64659`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `fields` | `Annotated[str &#124; list[str] &#124; None, JSON_STRING_COERCION, Field(default=None)]` | `None` |
| `area_fields` | `Annotated[str &#124; list[str] &#124; None, JSON_STRING_COERCION, Field(default=None)]` | `None` |

**9.0.0:** [src/ha_mcp/tools/tools_areas.py:142–274](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_areas.py#L142); return `dict[str, Any]`.
Source SHA-256: `a4d83da7131fae539696cf72a0b24499317ff94c932bf90d4b3973f706398361`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `fields` | `Annotated[str &#124; list[str] &#124; None, JSON_STRING_COERCION, Field(default=None)]` | `None` |
| `area_fields` | `Annotated[str &#124; list[str] &#124; None, JSON_STRING_COERCION, Field(default=None)]` | `None` |

## ha_list_services

**8.5.0:** [src/ha_mcp/tools/tools_services.py:70–233](https://github.com/homeassistant-ai/ha-mcp/blob/311d6dc273fb4e9a5b8cde0de15f69472a64fe44/src/ha_mcp/tools/tools_services.py#L70); return `dict[str, Any]`.
Source SHA-256: `9626962851ac18d33f2f9beb339dd95c90fdb0c014c49cf58f4ac8b19dee8c29`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `domain` | `str &#124; None` | `None` |
| `query` | `str &#124; None` | `None` |
| `limit` | `Annotated[int, Field(default=50, ge=1, le=200)]` | `50` |
| `offset` | `Annotated[int, Field(default=0, ge=0)]` | `0` |
| `detail_level` | `Annotated[Literal['summary', 'full'], Field(default='summary')]` | `'summary'` |
| `service_fields` | `Annotated[str &#124; list[str] &#124; None, JSON_STRING_COERCION, Field(default=None)]` | `None` |
| `fields` | `Annotated[str &#124; list[str] &#124; None, JSON_STRING_COERCION, Field(default=None)]` | `None` |

**9.0.0:** [src/ha_mcp/tools/tools_services.py:65–214](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_services.py#L65); return `dict[str, Any]`.
Source SHA-256: `7dc427625b00e34b7560c2460047674b99b55399bbbf96c2ab8e28c5c97324ae`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `domain` | `Annotated[str &#124; None, Field()]` | `None` |
| `query` | `Annotated[str &#124; None, Field()]` | `None` |
| `limit` | `Annotated[int, Field(default=50, ge=1, le=200)]` | `50` |
| `offset` | `Annotated[int, Field(default=0, ge=0)]` | `0` |
| `detail_level` | `Annotated[Literal['summary', 'full'], Field(default='summary')]` | `'summary'` |
| `service_fields` | `Annotated[str &#124; list[str] &#124; None, JSON_STRING_COERCION, Field(default=None)]` | `None` |
| `fields` | `Annotated[str &#124; list[str] &#124; None, JSON_STRING_COERCION, Field(default=None)]` | `None` |

## ha_manage_addon

**8.5.0:** no function under this name; see lifecycle alias analysis.

**9.0.0:** no function under this name; see lifecycle alias analysis.

## ha_manage_app

**8.5.0:** [src/ha_mcp/tools/tools_addons.py:3831–4117](https://github.com/homeassistant-ai/ha-mcp/blob/311d6dc273fb4e9a5b8cde0de15f69472a64fe44/src/ha_mcp/tools/tools_addons.py#L3831); return `dict[str, Any]`.
Source SHA-256: `f15b3208df00ad991e46f0b2d4be4c2af0eed93bc4d555a8090d28ab6df100c6`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `slug` | `Annotated[str, Field(default='')]` | `''` |
| `path` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `method` | `Annotated[str, Field(default='GET')]` | `'GET'` |
| `body` | `Annotated[dict[str, Any] &#124; str &#124; None, Field(default=None)]` | `None` |
| `debug` | `Annotated[bool, Field(default=False)]` | `False` |
| `port` | `Annotated[int &#124; None, Field(default=None)]` | `None` |
| `offset` | `Annotated[int, Field(default=0)]` | `0` |
| `limit` | `Annotated[int &#124; None, Field(default=None)]` | `None` |
| `websocket` | `Annotated[bool, Field(default=False)]` | `False` |
| `wait_for_close` | `Annotated[bool, Field(default=True)]` | `True` |
| `message_limit` | `Annotated[int &#124; None, Field(default=None)]` | `None` |
| `message_offset` | `Annotated[int, Field(default=0)]` | `0` |
| `summarize` | `Annotated[bool, Field(default=True)]` | `True` |
| `python_transform` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `options` | `Annotated[dict[str, Any] &#124; None, JSON_STRING_COERCION, Field(default=None)]` | `None` |
| `network` | `Annotated[dict[str, Any] &#124; None, JSON_STRING_COERCION, Field(default=None)]` | `None` |
| `boot` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `auto_update` | `Annotated[bool &#124; None, Field(default=None)]` | `None` |
| `watchdog` | `Annotated[bool &#124; None, Field(default=None)]` | `None` |
| `array_patch` | `Annotated[dict[str, Any] &#124; None, JSON_STRING_COERCION, Field(default=None)]` | `None` |
| `request_headers` | `Annotated[dict[str, str] &#124; None, JSON_STRING_COERCION, Field(default=None)]` | `None` |
| `action` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `repository` | `Annotated[str &#124; None, Field(default=None)]` | `None` |

**9.0.0:** [src/ha_mcp/tools/tools_addons.py:3769–4046](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_addons.py#L3769); return `dict[str, Any]`.
Source SHA-256: `b7185a5bb0ff5bd1abb260f055a9c9c7d962dde73a4f7f36617477f0b3ef74db`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `slug` | `Annotated[str, Field(default='')]` | `''` |
| `path` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `method` | `Annotated[str, Field(default='GET')]` | `'GET'` |
| `body` | `Annotated[dict[str, Any] &#124; str &#124; None, Field(default=None)]` | `None` |
| `debug` | `Annotated[bool, Field(default=False)]` | `False` |
| `port` | `Annotated[int &#124; None, Field(default=None)]` | `None` |
| `offset` | `Annotated[int, Field(default=0)]` | `0` |
| `limit` | `Annotated[int &#124; None, Field(default=None)]` | `None` |
| `websocket` | `Annotated[bool, Field(default=False)]` | `False` |
| `wait_for_close` | `Annotated[bool, Field(default=True)]` | `True` |
| `message_limit` | `Annotated[int &#124; None, Field(default=None)]` | `None` |
| `message_offset` | `Annotated[int, Field(default=0)]` | `0` |
| `summarize` | `Annotated[bool, Field(default=True)]` | `True` |
| `python_transform` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `options` | `Annotated[dict[str, Any] &#124; None, JSON_STRING_COERCION, Field(default=None)]` | `None` |
| `network` | `Annotated[dict[str, Any] &#124; None, JSON_STRING_COERCION, Field(default=None)]` | `None` |
| `boot` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `auto_update` | `Annotated[bool &#124; None, Field(default=None)]` | `None` |
| `watchdog` | `Annotated[bool &#124; None, Field(default=None)]` | `None` |
| `array_patch` | `Annotated[dict[str, Any] &#124; None, JSON_STRING_COERCION, Field(default=None)]` | `None` |
| `request_headers` | `Annotated[dict[str, str] &#124; None, JSON_STRING_COERCION, Field(default=None)]` | `None` |
| `action` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `repository` | `Annotated[str &#124; None, Field(default=None)]` | `None` |

## ha_manage_backup

**8.5.0:** [src/ha_mcp/tools/backup.py:1689–1809](https://github.com/homeassistant-ai/ha-mcp/blob/311d6dc273fb4e9a5b8cde0de15f69472a64fe44/src/ha_mcp/tools/backup.py#L1689); return `dict[str, Any]`.
Source SHA-256: `1b0021367e2c3a8504fdcedb59189085f4679d2312d5be14ef04388459b5ea52`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `scope` | `Annotated[Literal['snapshot', 'edits'], Field()]` | `required` |
| `action` | `Annotated[Literal['create', 'restore', 'list', 'view', 'diff', 'delete'], Field()]` | `required` |
| `name` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `backup_id` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `restore_database` | `Annotated[bool, Field(default=False)]` | `False` |
| `confirm` | `Annotated[bool, Field(default=False)]` | `False` |
| `domain` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `entity_id` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `backup_name` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `older_than_days` | `Annotated[int &#124; None, Field(default=None, ge=0)]` | `None` |
| `limit` | `Annotated[int, Field(default=200, ge=1, le=10000)]` | `200` |

**9.0.0:** [src/ha_mcp/tools/backup.py:137–340](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/backup.py#L137); return `dict[str, Any]`.
Source SHA-256: `7ab274db7f5b6c067b7742b1303ea1bdfbda7355874b897ed6afb3fe416d65b5`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `scope` | `Annotated[Literal['snapshot', 'edits'], Field()]` | `required` |
| `action` | `Annotated[Literal['create', 'restore', 'list', 'view', 'diff', 'delete'], Field()]` | `required` |
| `name` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `backup_id` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `restore_database` | `Annotated[bool &#124; None, Field(default=None)]` | `None` |
| `restore_addons` | `Annotated[list[str] &#124; None, JSON_STRING_COERCION, Field(default=None)]` | `None` |
| `restore_folders` | `Annotated[list[str] &#124; None, JSON_STRING_COERCION, Field(default=None)]` | `None` |
| `include_homeassistant` | `Annotated[bool &#124; None, Field(default=None)]` | `None` |
| `include_database` | `Annotated[bool &#124; None, Field(default=None)]` | `None` |
| `include_addons` | `Annotated[list[str] &#124; None, JSON_STRING_COERCION, Field(default=None)]` | `None` |
| `include_all_addons` | `Annotated[bool &#124; None, Field(default=None)]` | `None` |
| `include_folders` | `Annotated[list[str] &#124; None, JSON_STRING_COERCION, Field(default=None)]` | `None` |
| `safety_backup` | `Annotated[bool, Field(default=False)]` | `False` |
| `confirm` | `Annotated[bool, Field(default=False)]` | `False` |
| `domain` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `entity_id` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `backup_name` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `older_than_days` | `Annotated[int &#124; None, Field(default=None, ge=0)]` | `None` |
| `limit` | `Annotated[int, Field(default=200, ge=1, le=10000)]` | `200` |

## ha_manage_blueprints

**8.5.0:** [src/ha_mcp/tools/tools_blueprints.py:167–368](https://github.com/homeassistant-ai/ha-mcp/blob/311d6dc273fb4e9a5b8cde0de15f69472a64fe44/src/ha_mcp/tools/tools_blueprints.py#L167); return `dict[str, Any]`.
Source SHA-256: `23c8f70380e9ed80fc1d985aa0fd5c3b5ae3a12e5522e11ec9fd3ca0a2a29e30`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `action` | `Annotated[Literal['list', 'get', 'import', 'save', 'delete', 'substitute'], Field()]` | `required` |
| `domain` | `Annotated[str, Field(default='automation')]` | `'automation'` |
| `path` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `url` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `yaml` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `source_url` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `overwrite` | `Annotated[bool, Field(default=False)]` | `False` |
| `input` | `Annotated[dict[str, Any] &#124; None, JSON_STRING_COERCION, Field(default=None)]` | `None` |
| `confirm` | `Annotated[bool, Field(default=False)]` | `False` |

**9.0.0:** [src/ha_mcp/tools/tools_blueprints.py:164–346](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_blueprints.py#L164); return `dict[str, Any]`.
Source SHA-256: `a2cc9b0f3cce39d8826256d39c1b823f39331642991ccc5155c9d07d6f882bef`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `action` | `Annotated[Literal['list', 'get', 'import', 'save', 'delete', 'substitute'], Field()]` | `required` |
| `domain` | `Annotated[str, Field(default='automation')]` | `'automation'` |
| `path` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `url` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `yaml` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `source_url` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `overwrite` | `Annotated[bool, Field(default=False)]` | `False` |
| `input` | `Annotated[dict[str, Any] &#124; None, JSON_STRING_COERCION, Field(default=None)]` | `None` |
| `confirm` | `Annotated[bool, Field(default=False)]` | `False` |

## ha_read_file

**8.5.0:** [src/ha_mcp/tools/tools_filesystem.py:665–798](https://github.com/homeassistant-ai/ha-mcp/blob/311d6dc273fb4e9a5b8cde0de15f69472a64fe44/src/ha_mcp/tools/tools_filesystem.py#L665); return `dict[str, Any]`.
Source SHA-256: `1a84730b3b0199b9f3038cf65da4f8defc865c4a9c94fec8eb4af0a2ad520355`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `path` | `Annotated[str, Field()]` | `required` |
| `tail_lines` | `Annotated[int &#124; None, Field(default=None, ge=1, le=10000)]` | `None` |
| `yaml_path` | `Annotated[str &#124; None, Field(default=None)]` | `None` |

**9.0.0:** [src/ha_mcp/tools/tools_filesystem.py:637–744](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_filesystem.py#L637); return `dict[str, Any]`.
Source SHA-256: `0ff9d5f437fc6258e4b6152a00437fdc56a1cce4a192bba4f3ac2126ea765e83`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `path` | `Annotated[str, Field()]` | `required` |
| `tail_lines` | `Annotated[int &#124; None, Field(default=None, ge=1, le=10000)]` | `None` |
| `yaml_path` | `Annotated[str &#124; None, Field(default=None)]` | `None` |

## ha_reload_core

**8.5.0:** [src/ha_mcp/tools/tools_system.py:300–437](https://github.com/homeassistant-ai/ha-mcp/blob/311d6dc273fb4e9a5b8cde0de15f69472a64fe44/src/ha_mcp/tools/tools_system.py#L300); return `dict[str, Any]`.
Source SHA-256: `a79a45f709082af457e95e30868919025eb0b40535ff68dd16f2132aff27878d`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `target` | `str` | `'all'` |
| `entry_id` | `str &#124; None` | `None` |

**9.0.0:** [src/ha_mcp/tools/tools_system.py:216–336](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_system.py#L216); return `dict[str, Any]`.
Source SHA-256: `6a19e4a6ac43bfbb809138cddbf3a35be2d8a731c00183d1b2ccee172bf0fcc3`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `target` | `Annotated[str, Field()]` | `'all'` |
| `entry_id` | `Annotated[str &#124; None, Field()]` | `None` |

## ha_restart

**8.5.0:** [src/ha_mcp/tools/tools_system.py:175–288](https://github.com/homeassistant-ai/ha-mcp/blob/311d6dc273fb4e9a5b8cde0de15f69472a64fe44/src/ha_mcp/tools/tools_system.py#L175); return `dict[str, Any]`.
Source SHA-256: `a79a45f709082af457e95e30868919025eb0b40535ff68dd16f2132aff27878d`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `confirm` | `bool` | `False` |

**9.0.0:** [src/ha_mcp/tools/tools_system.py:171–203](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_system.py#L171); return `dict[str, Any]`.
Source SHA-256: `6a19e4a6ac43bfbb809138cddbf3a35be2d8a731c00183d1b2ccee172bf0fcc3`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `confirm` | `Annotated[bool, Field()]` | `False` |
| `safe_mode` | `Annotated[bool, Field()]` | `False` |

## ha_search

**8.5.0:** [src/ha_mcp/tools/tools_search.py:2095–2505](https://github.com/homeassistant-ai/ha-mcp/blob/311d6dc273fb4e9a5b8cde0de15f69472a64fe44/src/ha_mcp/tools/tools_search.py#L2095); return `dict[str, Any]`.
Source SHA-256: `6ca22976cdd331a20b22ef9047adc61a56998c8792793e651cfafa954f930e56`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `query` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `domain_filter` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `area_filter` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `search_types` | `Annotated[str &#124; list[str] &#124; None, JSON_STRING_COERCION, Field(default=None)]` | `None` |
| `limit` | `Annotated[int, Field(default=10, ge=1)]` | `10` |
| `offset` | `Annotated[int, Field(default=0, ge=0)]` | `0` |
| `exact_match` | `Annotated[bool, Field(default=True)]` | `True` |
| `include_hidden` | `Annotated[bool, Field(default=True)]` | `True` |
| `include_config` | `Annotated[bool, Field(default=False)]` | `False` |
| `group_by_domain` | `Annotated[bool, Field(default=False)]` | `False` |
| `per_domain_limit` | `Annotated[int &#124; None, Field(default=None)]` | `None` |
| `state_filter` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `result_fields` | `Annotated[str &#124; list[str] &#124; None, JSON_STRING_COERCION, Field(default=None)]` | `None` |
| `fields` | `Annotated[str &#124; list[str] &#124; None, JSON_STRING_COERCION, Field(default=None)]` | `None` |
| `config_time_budget` | `Annotated[float &#124; None, Field(default=None, ge=0.001, le=300)]` | `None` |

**9.0.0:** [src/ha_mcp/tools/tools_search.py:60–422](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_search.py#L60); return `dict[str, Any]`.
Source SHA-256: `92e0fd22e93f73adb9acf59d8b072d040c64f08a67e3b6a069b09c9c2e3441fe`.

| Parameter | Source type / constraints | Default |
|---|---|---|
| `query` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `domain_filter` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `area_filter` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `search_types` | `Annotated[str &#124; list[str] &#124; None, JSON_STRING_COERCION, Field(default=None)]` | `None` |
| `limit` | `Annotated[int, Field(default=10, ge=1)]` | `10` |
| `offset` | `Annotated[int, Field(default=0, ge=0)]` | `0` |
| `exact_match` | `Annotated[bool, Field(default=True)]` | `True` |
| `include_hidden` | `Annotated[bool, Field(default=True)]` | `True` |
| `include_config` | `Annotated[bool, Field(default=False)]` | `False` |
| `group_by_domain` | `Annotated[bool, Field(default=False)]` | `False` |
| `per_domain_limit` | `Annotated[int &#124; None, Field(default=None)]` | `None` |
| `state_filter` | `Annotated[str &#124; None, Field(default=None)]` | `None` |
| `result_fields` | `Annotated[str &#124; list[str] &#124; None, JSON_STRING_COERCION, Field(default=None)]` | `None` |
| `fields` | `Annotated[str &#124; list[str] &#124; None, JSON_STRING_COERCION, Field(default=None)]` | `None` |
| `config_time_budget` | `Annotated[float &#124; None, Field(default=None, ge=0.001, le=300)]` | `None` |
