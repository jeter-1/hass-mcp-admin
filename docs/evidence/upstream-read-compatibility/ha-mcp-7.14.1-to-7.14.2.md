# Reviewed upstream catalog comparison

- Old version: `7.14.1`
- New version: `7.14.2`
- Old tools: `78`
- New tools: `78`
- Old fingerprint: `c6bd074d9ee1e832bd90318398c00efd9a9ffd983d5444817bc830208cbfc47c`
- New fingerprint: `c6bd074d9ee1e832bd90318398c00efd9a9ffd983d5444817bc830208cbfc47c`

| Tool | Comparison | Policy | Delegation impact |
| --- | --- | --- | --- |
| `ha_bulk_control` | `unchanged_exact` | `physical_or_high_risk_action` | `none` |
| `ha_call_event` | `unchanged_exact` | `physical_or_high_risk_action` | `none` |
| `ha_call_service` | `unchanged_exact` | `mixed_or_requires_wrapper` | `none` |
| `ha_config_delete_dashboard` | `unchanged_exact` | `persistent_write` | `none` |
| `ha_config_delete_dashboard_resource` | `unchanged_exact` | `persistent_write` | `none` |
| `ha_config_get_automation` | `unchanged_exact` | `automatic_read` | `none` |
| `ha_config_get_calendar_events` | `unchanged_exact` | `automatic_read` | `none` |
| `ha_config_get_category` | `unchanged_exact` | `automatic_read` | `none` |
| `ha_config_get_dashboard` | `unchanged_exact` | `mixed_or_requires_wrapper` | `none` |
| `ha_config_get_label` | `unchanged_exact` | `automatic_read` | `none` |
| `ha_config_get_scene` | `unchanged_exact` | `automatic_read` | `none` |
| `ha_config_get_script` | `unchanged_exact` | `automatic_read` | `none` |
| `ha_config_list_dashboard_resources` | `unchanged_exact` | `automatic_read` | `none` |
| `ha_config_list_groups` | `unchanged_exact` | `automatic_read` | `none` |
| `ha_config_list_helpers` | `unchanged_exact` | `automatic_read` | `none` |
| `ha_config_remove_automation` | `unchanged_exact` | `persistent_write` | `none` |
| `ha_config_remove_calendar_event` | `unchanged_exact` | `persistent_write` | `none` |
| `ha_config_remove_category` | `unchanged_exact` | `persistent_write` | `none` |
| `ha_config_remove_group` | `unchanged_exact` | `persistent_write` | `none` |
| `ha_config_remove_label` | `unchanged_exact` | `persistent_write` | `none` |
| `ha_config_remove_scene` | `unchanged_exact` | `persistent_write` | `none` |
| `ha_config_remove_script` | `unchanged_exact` | `persistent_write` | `none` |
| `ha_config_set_automation` | `unchanged_exact` | `persistent_write` | `none` |
| `ha_config_set_calendar_event` | `unchanged_exact` | `persistent_write` | `none` |
| `ha_config_set_category` | `unchanged_exact` | `persistent_write` | `none` |
| `ha_config_set_dashboard` | `unchanged_exact` | `persistent_write` | `none` |
| `ha_config_set_dashboard_resource` | `unchanged_exact` | `persistent_write` | `none` |
| `ha_config_set_group` | `unchanged_exact` | `persistent_write` | `none` |
| `ha_config_set_helper` | `unchanged_exact` | `persistent_write` | `none` |
| `ha_config_set_label` | `unchanged_exact` | `persistent_write` | `none` |
| `ha_config_set_scene` | `unchanged_exact` | `persistent_write` | `none` |
| `ha_config_set_script` | `unchanged_exact` | `persistent_write` | `none` |
| `ha_eval_template` | `unchanged_exact` | `automatic_read` | `none` |
| `ha_get_addon` | `unchanged_exact` | `mixed_or_requires_wrapper` | `none` |
| `ha_get_automation_traces` | `unchanged_exact` | `automatic_read` | `none` |
| `ha_get_blueprint` | `unchanged_exact` | `automatic_read` | `none` |
| `ha_get_camera_image` | `unchanged_exact` | `unsupported` | `none` |
| `ha_get_device` | `unchanged_exact` | `automatic_read` | `none` |
| `ha_get_entity` | `unchanged_exact` | `automatic_read` | `none` |
| `ha_get_entity_exposure` | `unchanged_exact` | `automatic_read` | `none` |
| `ha_get_hacs_info` | `unchanged_exact` | `automatic_read` | `none` |
| `ha_get_history` | `unchanged_exact` | `automatic_read` | `none` |
| `ha_get_integration` | `unchanged_exact` | `mixed_or_requires_wrapper` | `none` |
| `ha_get_logs` | `unchanged_exact` | `mixed_or_requires_wrapper` | `none` |
| `ha_get_operation_status` | `unchanged_exact` | `automatic_read` | `none` |
| `ha_get_overview` | `unchanged_exact` | `automatic_read` | `none` |
| `ha_get_skill_guide` | `unchanged_exact` | `automatic_read` | `none` |
| `ha_get_state` | `unchanged_exact` | `automatic_read` | `none` |
| `ha_get_system_health` | `unchanged_exact` | `mixed_or_requires_wrapper` | `none` |
| `ha_get_todo` | `unchanged_exact` | `automatic_read` | `none` |
| `ha_get_zone` | `unchanged_exact` | `automatic_read` | `none` |
| `ha_import_blueprint` | `unchanged_exact` | `persistent_write` | `none` |
| `ha_list_floors_areas` | `unchanged_exact` | `automatic_read` | `none` |
| `ha_list_services` | `unchanged_exact` | `automatic_read` | `none` |
| `ha_manage_addon` | `unchanged_exact` | `mixed_or_requires_wrapper` | `none` |
| `ha_manage_backup` | `unchanged_exact` | `mixed_or_requires_wrapper` | `none` |
| `ha_manage_energy_prefs` | `unchanged_exact` | `mixed_or_requires_wrapper` | `none` |
| `ha_manage_hacs` | `unchanged_exact` | `mixed_or_requires_wrapper` | `none` |
| `ha_manage_pipeline` | `unchanged_exact` | `mixed_or_requires_wrapper` | `none` |
| `ha_manage_radio` | `unchanged_exact` | `mixed_or_requires_wrapper` | `none` |
| `ha_manage_theme` | `unchanged_exact` | `mixed_or_requires_wrapper` | `none` |
| `ha_manage_updates` | `unchanged_exact` | `mixed_or_requires_wrapper` | `none` |
| `ha_reload_core` | `unchanged_exact` | `physical_or_high_risk_action` | `none` |
| `ha_remove_area_or_floor` | `unchanged_exact` | `persistent_write` | `none` |
| `ha_remove_device` | `unchanged_exact` | `persistent_write` | `none` |
| `ha_remove_entity` | `unchanged_exact` | `persistent_write` | `none` |
| `ha_remove_helpers_integrations` | `unchanged_exact` | `persistent_write` | `none` |
| `ha_remove_todo_item` | `unchanged_exact` | `persistent_write` | `none` |
| `ha_remove_zone` | `unchanged_exact` | `persistent_write` | `none` |
| `ha_report_issue` | `unchanged_exact` | `prohibited` | `none` |
| `ha_restart` | `unchanged_exact` | `physical_or_high_risk_action` | `none` |
| `ha_search` | `unchanged_exact` | `automatic_read` | `none` |
| `ha_set_area_or_floor` | `unchanged_exact` | `persistent_write` | `none` |
| `ha_set_device` | `unchanged_exact` | `persistent_write` | `none` |
| `ha_set_entity` | `unchanged_exact` | `persistent_write` | `none` |
| `ha_set_integration` | `unchanged_exact` | `persistent_write` | `none` |
| `ha_set_todo_item` | `unchanged_exact` | `persistent_write` | `none` |
| `ha_set_zone` | `unchanged_exact` | `persistent_write` | `none` |
