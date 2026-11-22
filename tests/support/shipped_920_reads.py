"""The shipped ha-mcp 9.2.0 delegated read set (HAMCP-437).

Moved from ``test_beta58_ha_mcp_8_4_3_continuity`` (ANSWER 20261122-0005-01
A3.4): ``test_hamcp129_platform_reads`` compares it with the read routes of
the shipped 9.2.0 compiled row, so it is the shipped read set, not frozen
fixture data. It is pinned by name rather than derived so that a policy change
that adds or drops a read fails those tests. 9.2.0 classifies the retired
``ha_get_hacs_info`` unsupported, so it is not a read route and not listed.
"""

from __future__ import annotations

READS_920 = frozenset(
    {
        "ha_config_get_automation",
        "ha_config_get_calendar_events",
        "ha_config_get_category",
        "ha_config_get_label",
        "ha_config_get_scene",
        "ha_config_get_script",
        "ha_config_list_dashboard_resources",
        "ha_config_list_groups",
        "ha_config_list_helpers",
        "ha_eval_template",
        "ha_get_app",
        "ha_get_automation_traces",
        "ha_get_blueprint",
        "ha_get_device",
        "ha_get_entity",
        "ha_get_entity_exposure",
        "ha_get_history",
        "ha_get_integration",
        "ha_get_overview",
        "ha_get_skill_guide",
        "ha_get_state",
        "ha_get_system_health",
        "ha_get_todo",
        "ha_get_zone",
        "ha_list_floors_areas",
        "ha_list_services",
        "ha_search",
    }
)
