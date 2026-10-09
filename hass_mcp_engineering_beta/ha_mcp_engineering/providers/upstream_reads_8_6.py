"""Closed 8.6 public reads, generated from exact 8.5 public/8.6 wire schemas.

Full upstream descriptor validation precedes these binary-owned projections.
The sole public migration is skill-guide's file selector and default document.
"""
from copy import deepcopy
import re

ADAPTER = "ha-mcp-8.6.0-public-reads-v1"
SOURCE = "fc54437a804858732e4bc927add98e202d879a09"
INPUT_FINGERPRINTS = {'ha_config_get_automation': 'bae748eb90c04a6f51aaa606d7b2b20afa76ede3bc0b40d4d8e0da847a581200',
 'ha_config_get_calendar_events': 'c57d2fb482783e81da9afc2dfba29c96db9c5049c5ab14a30c932b5918977657',
 'ha_config_get_category': '0ed4ad9eec4d162099f9ad7b80c5bb0d9bbc25912b4f6f19c553ff08ed3c377b',
 'ha_config_get_label': 'e9e05e1cd301936b4c7a7d5b609744602268f3ff19a7871678f7ea5fcb8ae7b5',
 'ha_config_get_scene': 'e471799f1d1761b7112fbe6af84f2769497e1d4a3b6dc476a2ce268a467a00ef',
 'ha_config_get_script': '941d56128c6a1cfc9352c9f6f69db4248431149bb2458b6fd5d9c960886d6dc2',
 'ha_config_list_dashboard_resources': '34cd768d9f43fa2cc9b36d3b7e5831bab489391e1f55ef81e35a72d5249f0fcc',
 'ha_config_list_groups': '329191e6416743be5cf024e6062f3f00cc858b0ce5ddef87771ea52af88c37e3',
 'ha_config_list_helpers': '853fbabc32f6a5d3f5f5da58034bd26bab590b3b95300ee99d26b6fab588187d',
 'ha_eval_template': '1cd46b20bfefc8bd2489de654de78bdb225f7efb42a210f3cfcf7e97040c9a86',
 'ha_get_automation_traces': 'f22754974ac6f44022b7bee3ece027b02e8edb47db76531431adb148f76b68e9',
 'ha_get_device': 'c6700885a55d150e6a7286189c9f00be611fb3bf82b9d37ef103d14b13f4b2f1',
 'ha_get_entity': '503e365334782564ae14bf9672c683033e8a27c6706ba52c559a5882abbe0cbb',
 'ha_get_entity_exposure': '596b9a5acf1ba8028ffe67c56c8de52245c63269e3cedb67dde8f7e43eb2c047',
 'ha_get_hacs_info': 'cc876c93b25541cd003eff296c3ffd1401f324e266f27dae1865decb6c74a15e',
 'ha_get_history': '48bbc5e7253e8896a3fdf8c2a71a7f4b0648590c81a3ab390590c9a83e023ca2',
 'ha_get_overview': 'b23392ab30b1105529f7cb44a7d5471f60e86f7d4766a6f531ae5134d31de9ed',
 'ha_get_skill_guide': '84790a9d6f9da335dc8646e4cbf093948b08b26e74a78e0efc83cc79642d844b',
 'ha_get_state': '079c4fa378e1f5c2b19f8c46bea46d5f572bc5b3724cb605794ff59edb942226',
 'ha_get_todo': 'e502275e89d26044815dd7f9323efd07e9884752bf146952a7e7ac286aa1e8bb',
 'ha_get_zone': 'e2ca348e064549f118a6350f317aafef2b10df955b38cd7cfdbf16f2e307fa6d',
 'ha_list_floors_areas': 'b81375d58db7ba1a03224194d3e054d4124e4261b99d76e54b7dda41cb38b55a',
 'ha_list_services': 'da65206809eafce40f77be1ef87045a4155fd4c085b149b85a41c7d5db280ce1',
 'ha_search': '58ed5cc91f928d9dc33437a97ad0ae46641b17891dfed2f7657f2658c83276c5'}
PUBLIC_SCHEMAS = {'ha_config_get_automation': {'additionalProperties': False,
                              'properties': {'identifier': {'description': 'Automation entity_id '
                                                                           '(e.g., '
                                                                           "'automation.morning_routine') "
                                                                           'or unique_id',
                                                            'type': 'string'}},
                              'required': ['identifier'],
                              'type': 'object'},
 'ha_config_get_calendar_events': {'additionalProperties': False,
                                   'properties': {'end': {'anyOf': [{'type': 'string'},
                                                                    {'type': 'null'}],
                                                          'default': None,
                                                          'description': 'End datetime in ISO '
                                                                         'format (default: 7 days '
                                                                         'from start)'},
                                                  'entity_id': {'description': 'Calendar entity ID '
                                                                               '(e.g., '
                                                                               "'calendar.family')",
                                                                'type': 'string'},
                                                  'max_results': {'default': 20,
                                                                  'description': 'Maximum number '
                                                                                 'of events to '
                                                                                 'return',
                                                                  'type': 'integer'},
                                                  'start': {'anyOf': [{'type': 'string'},
                                                                      {'type': 'null'}],
                                                            'default': None,
                                                            'description': 'Start datetime in ISO '
                                                                           'format (default: '
                                                                           'now)'}},
                                   'required': ['entity_id'],
                                   'type': 'object'},
 'ha_config_get_category': {'additionalProperties': False,
                            'properties': {'category_id': {'anyOf': [{'type': 'string'},
                                                                     {'type': 'null'}],
                                                           'default': None,
                                                           'description': 'ID of the category to '
                                                                          'retrieve. If omitted, '
                                                                          'lists all categories '
                                                                          'for the scope.'},
                                           'scope': {'description': 'Domain scope for categories '
                                                                    "(e.g., 'automation', "
                                                                    "'script', 'scene', "
                                                                    "'helpers').",
                                                     'type': 'string'}},
                            'required': ['scope'],
                            'type': 'object'},
 'ha_config_get_label': {'additionalProperties': False,
                         'properties': {'label_id': {'anyOf': [{'type': 'string'},
                                                               {'type': 'null'}],
                                                     'default': None,
                                                     'description': 'ID of the label to retrieve. '
                                                                    'If omitted, lists all '
                                                                    'labels.'}},
                         'type': 'object'},
 'ha_config_get_scene': {'additionalProperties': False,
                         'properties': {'scene_id': {'description': 'Scene identifier (e.g., '
                                                                    "'movie_night')",
                                                     'type': 'string'}},
                         'required': ['scene_id'],
                         'type': 'object'},
 'ha_config_get_script': {'additionalProperties': False,
                          'properties': {'script_id': {'description': 'Script identifier — bare '
                                                                      'storage key '
                                                                      "('morning_routine') or "
                                                                      'entity_id form '
                                                                      "('script.morning_routine'); "
                                                                      "a leading 'script.' prefix "
                                                                      'is stripped before lookup.',
                                                       'type': 'string'}},
                          'required': ['script_id'],
                          'type': 'object'},
 'ha_config_list_dashboard_resources': {'additionalProperties': False,
                                        'properties': {'include_content': {'default': False,
                                                                           'description': 'Include '
                                                                                          'full '
                                                                                          'decoded '
                                                                                          'content '
                                                                                          'for '
                                                                                          'inline '
                                                                                          'resources. '
                                                                                          'Default '
                                                                                          'False '
                                                                                          'to save '
                                                                                          'tokens '
                                                                                          '(shows '
                                                                                          '150-char '
                                                                                          'preview '
                                                                                          'instead).',
                                                                           'type': 'boolean'},
                                                       'limit': {'default': 100,
                                                                 'description': 'Max resources to '
                                                                                'return per page '
                                                                                '(default: 100)',
                                                                 'maximum': 500,
                                                                 'minimum': 1,
                                                                 'type': 'integer'},
                                                       'offset': {'default': 0,
                                                                  'description': 'Number of '
                                                                                 'resources to '
                                                                                 'skip for '
                                                                                 'pagination '
                                                                                 '(default: 0)',
                                                                  'minimum': 0,
                                                                  'type': 'integer'}},
                                        'type': 'object'},
 'ha_config_list_groups': {'additionalProperties': False,
                           'properties': {'limit': {'default': 100,
                                                    'description': 'Max groups to return per page '
                                                                   '(default: 100)',
                                                    'maximum': 500,
                                                    'minimum': 1,
                                                    'type': 'integer'},
                                          'offset': {'default': 0,
                                                     'description': 'Number of groups to skip for '
                                                                    'pagination (default: 0)',
                                                     'minimum': 0,
                                                     'type': 'integer'}},
                           'type': 'object'},
 'ha_config_list_helpers': {'additionalProperties': False,
                            'properties': {'helper_type': {'anyOf': [{'enum': ['input_button',
                                                                               'input_boolean',
                                                                               'input_select',
                                                                               'input_number',
                                                                               'input_text',
                                                                               'input_datetime',
                                                                               'counter',
                                                                               'timer',
                                                                               'schedule',
                                                                               'zone',
                                                                               'person',
                                                                               'tag',
                                                                               'all'],
                                                                      'type': 'string'},
                                                                     {'enum': ['template',
                                                                               'group',
                                                                               'utility_meter',
                                                                               'derivative',
                                                                               'min_max',
                                                                               'threshold',
                                                                               'integration',
                                                                               'statistics',
                                                                               'trend',
                                                                               'random',
                                                                               'filter',
                                                                               'tod',
                                                                               'generic_thermostat',
                                                                               'switch_as_x',
                                                                               'generic_hygrostat',
                                                                               'history_stats',
                                                                               'mold_indicator'],
                                                                      'type': 'string'}],
                                                           'description': 'Helper type to list. '
                                                                          'Storage types are '
                                                                          'listed on all installs; '
                                                                          'flow-based types '
                                                                          'require the '
                                                                          'ha_mcp_tools custom '
                                                                          "component. Pass 'all' "
                                                                          'to list every helper '
                                                                          'type in one call (also '
                                                                          'requires the '
                                                                          'ha_mcp_tools '
                                                                          'component).'},
                                           'limit': {'default': 100,
                                                     'description': 'Max helpers to return per '
                                                                    'page (default: 100)',
                                                     'maximum': 500,
                                                     'minimum': 1,
                                                     'type': 'integer'},
                                           'offset': {'default': 0,
                                                      'description': 'Number of helpers to skip '
                                                                     'for pagination (default: 0)',
                                                      'minimum': 0,
                                                      'type': 'integer'}},
                            'required': ['helper_type'],
                            'type': 'object'},
 'ha_eval_template': {'additionalProperties': False,
                      'properties': {'report_errors': {'default': True, 'type': 'boolean'},
                                     'template': {'type': 'string'},
                                     'timeout': {'default': 3, 'type': 'integer'}},
                      'required': ['template'],
                      'type': 'object'},
 'ha_get_automation_traces': {'additionalProperties': False,
                              'properties': {'automation_id': {'description': 'Automation or '
                                                                              'script entity_id '
                                                                              '(e.g., '
                                                                              "'automation.motion_light' "
                                                                              'or '
                                                                              "'script.morning_routine')",
                                                               'type': 'string'},
                                             'deduplicate': {'default': True,
                                                             'description': 'Deduplicate variables '
                                                                            'across action steps '
                                                                            '(default: True). Set '
                                                                            'to False to include '
                                                                            'full variables at '
                                                                            'every step.',
                                                             'type': 'boolean'},
                                             'detailed': {'default': False,
                                                          'description': 'Include extra diagnostic '
                                                                         'data: logbook entries '
                                                                         'and context metadata '
                                                                         '(default: False). Use '
                                                                         'when standard trace '
                                                                         'lacks detail for '
                                                                         'debugging.',
                                                          'type': 'boolean'},
                                             'limit': {'default': 10,
                                                       'description': 'Maximum number of traces to '
                                                                      'return when listing '
                                                                      '(default: 10, max: 50).',
                                                       'maximum': 50,
                                                       'minimum': 1,
                                                       'type': 'integer'},
                                             'offset': {'default': 0,
                                                        'description': 'Number of traces to skip '
                                                                       'from the start of the '
                                                                       'requested order. Use with '
                                                                       '`limit` to page through '
                                                                       'stored traces when '
                                                                       '`total_available > limit`.',
                                                        'minimum': 0,
                                                        'type': 'integer'},
                                             'order': {'default': 'newest',
                                                       'description': 'Order traces are returned '
                                                                      "in. 'newest' (default) "
                                                                      'returns most-recent first; '
                                                                      "'oldest' returns "
                                                                      'chronological-first.',
                                                       'enum': ['newest', 'oldest'],
                                                       'type': 'string'},
                                             'run_id': {'anyOf': [{'type': 'string'},
                                                                  {'type': 'null'}],
                                                        'default': None,
                                                        'description': 'Specific trace run_id to '
                                                                       'retrieve detailed trace. '
                                                                       'Omit to list recent '
                                                                       'traces.'},
                                             'sections': {'anyOf': [{'type': 'string'},
                                                                    {'type': 'null'}],
                                                          'default': None,
                                                          'description': 'Comma-separated list of '
                                                                         'trace sections to '
                                                                         'return. Valid values: '
                                                                         'trigger, conditions, '
                                                                         'actions, config, error, '
                                                                         'logbook, context. Omit '
                                                                         'to return all sections. '
                                                                         "Example: 'actions' or "
                                                                         "'trigger,conditions'."}},
                              'required': ['automation_id'],
                              'type': 'object'},
 'ha_get_device': {'additionalProperties': False,
                   'properties': {'area_id': {'anyOf': [{'type': 'string'}, {'type': 'null'}],
                                              'default': None,
                                              'description': 'Filter devices by area ID (e.g., '
                                                             "'living_room')"},
                                  'detail_level': {'default': 'summary',
                                                   'description': "'summary': basic device info "
                                                                  'and protocol identifiers '
                                                                  '(default for list mode). '
                                                                  "'full': include entities and "
                                                                  'all integration details. Single '
                                                                  'device lookups always return '
                                                                  'full detail.',
                                                   'enum': ['summary', 'full'],
                                                   'type': 'string'},
                                  'device_id': {'anyOf': [{'type': 'string'}, {'type': 'null'}],
                                                'default': None,
                                                'description': 'Device ID to retrieve details for. '
                                                               'If omitted, lists devices.'},
                                  'entity_id': {'anyOf': [{'type': 'string'}, {'type': 'null'}],
                                                'default': None,
                                                'description': 'Entity ID to find the associated '
                                                               'device for (e.g., '
                                                               "'light.living_room')"},
                                  'integration': {'anyOf': [{'type': 'string'}, {'type': 'null'}],
                                                  'default': None,
                                                  'description': 'Filter devices by integration: '
                                                                 "'zha', 'zigbee2mqtt', "
                                                                 "'zwave_js', 'mqtt', 'hue', etc."},
                                  'limit': {'default': 50,
                                            'description': 'Max devices to return per page in list '
                                                           'mode (default: 50)',
                                            'maximum': 200,
                                            'minimum': 1,
                                            'type': 'integer'},
                                  'manufacturer': {'anyOf': [{'type': 'string'}, {'type': 'null'}],
                                                   'default': None,
                                                   'description': 'Filter devices by manufacturer '
                                                                  "name (e.g., 'Philips')"},
                                  'offset': {'default': 0,
                                             'description': 'Number of devices to skip for '
                                                            'pagination (default: 0)',
                                             'minimum': 0,
                                             'type': 'integer'}},
                   'type': 'object'},
 'ha_get_entity': {'additionalProperties': False,
                   'properties': {'domain': {'anyOf': [{'type': 'string'}, {'type': 'null'}],
                                             'default': None,
                                             'description': 'Resolver filter (unique_id mode '
                                                            'only): restrict matches to this '
                                                            "entity domain, e.g. 'sensor'."},
                                  'entity_id': {'anyOf': [{'type': 'string'},
                                                          {'items': {'type': 'string'},
                                                           'type': 'array'},
                                                          {'type': 'null'}],
                                                'default': None,
                                                'description': 'Entity ID or list of entity IDs to '
                                                               'retrieve (e.g., '
                                                               "'sensor.temperature' or "
                                                               "['light.living_room', "
                                                               "'switch.porch']). Mutually "
                                                               'exclusive with unique_id.'},
                                  'platform': {'anyOf': [{'type': 'string'}, {'type': 'null'}],
                                               'default': None,
                                               'description': 'Resolver filter (unique_id mode '
                                                              'only): restrict matches to this '
                                                              "integration platform, e.g. 'hue'."},
                                  'unique_id': {'anyOf': [{'type': 'string'}, {'type': 'null'}],
                                                'default': None,
                                                'description': 'Resolve a stable integration '
                                                               'unique_id to its entity_id(s) '
                                                               '(entity_id is mutable, unique_id '
                                                               'is not). Mutually exclusive with '
                                                               'entity_id. Optionally narrow with '
                                                               'domain/platform.'}},
                   'type': 'object'},
 'ha_get_entity_exposure': {'additionalProperties': False,
                            'properties': {'assistant': {'anyOf': [{'type': 'string'},
                                                                   {'type': 'null'}],
                                                         'default': None,
                                                         'description': 'Filter by assistant: '
                                                                        "'conversation', "
                                                                        "'cloud.alexa', or "
                                                                        "'cloud.google_assistant'. "
                                                                        'If not specified, returns '
                                                                        'all.'},
                                           'entity_id': {'anyOf': [{'type': 'string'},
                                                                   {'type': 'null'}],
                                                         'default': None,
                                                         'description': 'Entity ID to check '
                                                                        'exposure settings for. If '
                                                                        'omitted, lists all '
                                                                        'entities with exposure '
                                                                        'settings.'}},
                            'type': 'object'},
 'ha_get_hacs_info': {'additionalProperties': False,
                      'properties': {'action': {'description': "'search' the store, or 'info' for "
                                                               'one repository',
                                                'enum': ['search', 'info'],
                                                'type': 'string'},
                                     'category': {'anyOf': [{'enum': ['integration',
                                                                      'lovelace',
                                                                      'theme',
                                                                      'appdaemon',
                                                                      'python_script'],
                                                             'type': 'string'},
                                                            {'type': 'null'}],
                                                  'default': None,
                                                  'description': 'Filter by category '
                                                                 "(action='search')"},
                                     'installed_only': {'default': False,
                                                        'description': 'Only return installed '
                                                                       'repositories '
                                                                       "(action='search', default: "
                                                                       'False)',
                                                        'type': 'boolean'},
                                     'max_results': {'default': 10,
                                                     'description': 'Maximum number of results '
                                                                    "(action='search', default: "
                                                                    '10, max: 100)',
                                                     'maximum': 100,
                                                     'minimum': 1,
                                                     'type': 'integer'},
                                     'offset': {'default': 0,
                                                'description': 'Results to skip for pagination '
                                                               "(action='search', default: 0)",
                                                'minimum': 0,
                                                'type': 'integer'},
                                     'query': {'default': '',
                                               'description': "Search keyword (action='search')",
                                               'type': 'string'},
                                     'repository_id': {'anyOf': [{'type': 'string'},
                                                                 {'type': 'null'}],
                                                       'default': None,
                                                       'description': 'Numeric HACS ID or '
                                                                      "'owner/repo' path "
                                                                      "(action='info')"}},
                      'required': ['action'],
                      'type': 'object'},
 'ha_get_history': {'additionalProperties': False,
                    'properties': {'end_time': {'anyOf': [{'type': 'string'}, {'type': 'null'}],
                                                'default': None,
                                                'description': 'End time: ISO datetime. Default: '
                                                               'now'},
                                   'entity_ids': {'anyOf': [{'type': 'string'},
                                                            {'items': {'type': 'string'},
                                                             'type': 'array'}],
                                                  'description': 'Entity ID(s) to query. Can be a '
                                                                 'single ID, comma-separated '
                                                                 'string, or JSON array.'},
                                   'fields': {'anyOf': [{'type': 'string'},
                                                        {'items': {'type': 'string'},
                                                         'type': 'array'},
                                                        {'type': 'null'}],
                                              'default': None,
                                              'description': 'Return only the specified top-level '
                                                             'response keys to reduce response '
                                                             'size. None = full response '
                                                             '(default). History keys: success, '
                                                             'source, entities, period, '
                                                             'query_params. Statistics keys: '
                                                             'success, source, entities, '
                                                             'period_type, time_range, '
                                                             'statistic_types, query_params, '
                                                             'warnings.'},
                                   'limit': {'anyOf': [{'maximum': 1000,
                                                        'minimum': 1,
                                                        'type': 'integer'},
                                                       {'type': 'null'}],
                                             'default': None,
                                             'description': 'Max entries per entity. Default: 100, '
                                                            'Max: 1000. For source="history": '
                                                            'state changes. For '
                                                            'source="statistics": aggregated rows. '
                                                            'With multiple entity_ids, offset must '
                                                            'be 0 and total rows returned can '
                                                            'reach limit × len(entity_ids).'},
                                   'minimal_response': {'default': True,
                                                        'description': 'Return only '
                                                                       'states/timestamps without '
                                                                       'attributes. Default: true. '
                                                                       'Ignored when '
                                                                       'source="statistics"',
                                                        'type': 'boolean'},
                                   'offset': {'anyOf': [{'minimum': 0, 'type': 'integer'},
                                                        {'type': 'null'}],
                                              'default': None,
                                              'description': 'Number of entries to skip per entity '
                                                             'for pagination. Default: 0. Offset > '
                                                             '0 requires a single entity_id. Use '
                                                             'with limit and has_more/next_offset '
                                                             'in the response.'},
                                   'order': {'default': 'desc',
                                             'description': 'Sort order for history entries. '
                                                            '"desc" (default): newest first. '
                                                            '"asc": oldest first (chronological, '
                                                            'as returned by HA API). Ignored when '
                                                            'source="statistics".',
                                             'enum': ['asc', 'desc'],
                                             'type': 'string'},
                                   'period': {'default': 'day',
                                              'description': 'Aggregation period: "5minute", '
                                                             '"hour", "day", "week", "month", '
                                                             '"year". Default: "day". Ignored when '
                                                             'source="history"',
                                              'type': 'string'},
                                   'significant_changes_only': {'default': True,
                                                                'description': 'Filter to '
                                                                               'significant state '
                                                                               'changes only. '
                                                                               'Default: true. '
                                                                               'Ignored when '
                                                                               'source="statistics"',
                                                                'type': 'boolean'},
                                   'source': {'default': 'history',
                                              'description': 'Data source: "history" (default) for '
                                                             'raw state changes (~10 day '
                                                             'retention), or "statistics" for '
                                                             'pre-aggregated long-term data '
                                                             '(permanent, requires state_class).',
                                              'enum': ['history', 'statistics'],
                                              'type': 'string'},
                                   'start_time': {'anyOf': [{'type': 'string'}, {'type': 'null'}],
                                                  'default': None,
                                                  'description': 'Start time: ISO datetime or '
                                                                 "relative (e.g., '24h', '7d', "
                                                                 "'30d'). Default: 24h ago for "
                                                                 'history, 30d ago for statistics'},
                                   'statistic_types': {'anyOf': [{'type': 'string'},
                                                                 {'items': {'type': 'string'},
                                                                  'type': 'array'},
                                                                 {'type': 'null'}],
                                                       'default': None,
                                                       'description': 'Statistics types: "mean", '
                                                                      '"min", "max", "sum", '
                                                                      '"state", "change". Default: '
                                                                      'all. Ignored when '
                                                                      'source="history"'}},
                    'required': ['entity_ids'],
                    'type': 'object'},
 'ha_get_overview': {'additionalProperties': False,
                     'properties': {'detail_level': {'default': 'minimal',
                                                     'description': "'minimal': 10 "
                                                                    'entities/domain, top-5 states '
                                                                    "(default); 'standard': 200 "
                                                                    'entities/page, top-10 states '
                                                                    '(use offset for more); '
                                                                    "'full': 200 entities/page + "
                                                                    'entity_id + state + full '
                                                                    "states. Use 'domains', "
                                                                    "'limit', or "
                                                                    'max_entities_per_domain to '
                                                                    'control size',
                                                     'enum': ['minimal', 'standard', 'full'],
                                                     'type': 'string'},
                                    'domains': {'anyOf': [{'type': 'string'},
                                                          {'items': {'type': 'string'},
                                                           'type': 'array'},
                                                          {'type': 'null'}],
                                                'default': None,
                                                'description': 'Filter to specific domains (e.g. '
                                                               "'light,sensor' or "
                                                               "['light','sensor']). None = all "
                                                               'domains. Useful to avoid context '
                                                               'window overload.'},
                                    'fields': {'anyOf': [{'type': 'string'},
                                                         {'items': {'type': 'string'},
                                                          'type': 'array'},
                                                         {'type': 'null'}],
                                               'default': None,
                                               'description': 'Return only the specified top-level '
                                                              'response keys to reduce response '
                                                              'size (e.g. ["system_info", '
                                                              '"domains"]). None = full response '
                                                              '(default). Available keys: success, '
                                                              'system_summary, domain_stats, '
                                                              'area_analysis, ai_insights, '
                                                              'pagination, partial, warnings, '
                                                              'device_types, service_availability, '
                                                              'system_info, notification_count, '
                                                              'notifications, repair_count, '
                                                              'dismissed_repair_count, repairs, '
                                                              'repairs_error, tool_discovery, '
                                                              'settings_url, settings_url_hint, '
                                                              'read_only_mode, '
                                                              'read_only_mode_hint, ha_mcp_update. '
                                                              'Note: ``settings_url`` (stdio '
                                                              'mode), ``settings_url_hint`` '
                                                              '(standalone HTTP/Docker mode), the '
                                                              '``read_only_mode`` / '
                                                              '``read_only_mode_hint`` pair (only '
                                                              'while Read Only Mode is on), and '
                                                              '``ha_mcp_update`` (when an update '
                                                              'check applies) are emitted '
                                                              'regardless of ``fields=`` '
                                                              'projection so the settings page, '
                                                              'the active mode, and a newer ha-mcp '
                                                              'release stay discoverable; see the '
                                                              'tool description.'},
                                    'include_dismissed_repairs': {'anyOf': [{'type': 'boolean'},
                                                                            {'type': 'null'}],
                                                                  'default': False,
                                                                  'description': 'Include '
                                                                                 'user-dismissed/ignored '
                                                                                 'repairs '
                                                                                 '(default: '
                                                                                 'False). Matches '
                                                                                 'the HA Repairs '
                                                                                 'UI which hides '
                                                                                 'dismissed items '
                                                                                 'by default. To '
                                                                                 'dismiss/ignore a '
                                                                                 'repair, call '
                                                                                 'ha_call_service '
                                                                                 'with '
                                                                                 'ws_command="repairs/ignore_issue" '
                                                                                 'and '
                                                                                 'data={"domain": '
                                                                                 '..., "issue_id": '
                                                                                 '..., "ignore": '
                                                                                 'true}.'},
                                    'include_entity_id': {'anyOf': [{'type': 'boolean'},
                                                                    {'type': 'null'}],
                                                          'default': None,
                                                          'description': 'Include entity_id field '
                                                                         'for entities (None = '
                                                                         'auto based on level). '
                                                                         'Full defaults to True.'},
                                    'include_notifications': {'anyOf': [{'type': 'boolean'},
                                                                        {'type': 'null'}],
                                                              'default': True,
                                                              'description': 'Include active '
                                                                             'persistent '
                                                                             'notifications '
                                                                             '(default: True). Set '
                                                                             'False to skip.'},
                                    'include_state': {'anyOf': [{'type': 'boolean'},
                                                                {'type': 'null'}],
                                                      'default': None,
                                                      'description': 'Include state field for '
                                                                     'entities (None = auto based '
                                                                     'on level). Full defaults to '
                                                                     'True.'},
                                    'limit': {'anyOf': [{'minimum': 1, 'type': 'integer'},
                                                        {'type': 'null'}],
                                              'default': None,
                                              'description': 'Max total entities across all '
                                                             'domains (default: unlimited for '
                                                             'minimal, 200 for standard/full). '
                                                             'Counts and states always complete. '
                                                             'Use with offset for pagination.'},
                                    'max_entities_per_domain': {'anyOf': [{'type': 'integer'},
                                                                          {'type': 'null'}],
                                                                'default': None,
                                                                'description': 'Override default '
                                                                               'entity cap per '
                                                                               'domain '
                                                                               '(minimal=10, '
                                                                               'standard/full=unlimited). '
                                                                               '0 = no limit on '
                                                                               'entities or '
                                                                               'states.'},
                                    'offset': {'default': 0,
                                               'description': 'Number of entities to skip for '
                                                              'pagination (default: 0)',
                                               'minimum': 0,
                                               'type': 'integer'}},
                     'type': 'object'},
 'ha_get_skill_guide': {'additionalProperties': False,
                        'properties': {'file': {'default': 'SKILL.md',
                                                'description': 'Path of the file to read, exactly '
                                                               'as SKILL.md links it (e.g. '
                                                               "'references/automation-patterns.md'). "
                                                               'Omit to read SKILL.md.',
                                                'type': 'string'}},
                        'type': 'object'},
 'ha_get_state': {'additionalProperties': False,
                  'properties': {'attribute_keys': {'anyOf': [{'type': 'string'},
                                                              {'items': {'type': 'string'},
                                                               'type': 'array'},
                                                              {'type': 'null'}],
                                                    'default': None,
                                                    'description': 'Return only the specified keys '
                                                                   "from each entity's attributes "
                                                                   'dict (e.g. ["brightness", '
                                                                   '"color_temp_kelvin"] for '
                                                                   'lights). None = full '
                                                                   'attributes (default). Unknown '
                                                                   'keys are silently dropped. '
                                                                   'Requires "attributes" to be '
                                                                   'present in fields= (or '
                                                                   'fields=None).'},
                                 'entity_id': {'anyOf': [{'type': 'string'},
                                                         {'items': {'type': 'string'},
                                                          'type': 'array'}],
                                               'description': 'Entity ID or list of entity IDs to '
                                                              'retrieve state for (e.g., '
                                                              "'light.kitchen' or "
                                                              "['light.kitchen', "
                                                              "'sensor.temperature'])"},
                                 'fields': {'anyOf': [{'type': 'string'},
                                                      {'items': {'type': 'string'},
                                                       'type': 'array'},
                                                      {'type': 'null'}],
                                            'default': None,
                                            'description': 'Return only the specified top-level '
                                                           'entity record keys to reduce response '
                                                           'size (e.g. ["state", "attributes"]). '
                                                           'None = full entity record (default). '
                                                           'Available keys: entity_id, state, '
                                                           'attributes, last_changed, '
                                                           'last_reported, last_updated, '
                                                           'context.'}},
                  'required': ['entity_id'],
                  'type': 'object'},
 'ha_get_todo': {'additionalProperties': False,
                 'properties': {'entity_id': {'anyOf': [{'type': 'string'}, {'type': 'null'}],
                                              'default': None,
                                              'description': 'Todo list entity ID (e.g., '
                                                             "'todo.shopping_list'). If omitted, "
                                                             'lists all todo list entities.'},
                                'status': {'anyOf': [{'enum': ['needs_action', 'completed'],
                                                      'type': 'string'},
                                                     {'type': 'null'}],
                                           'default': None,
                                           'description': "Filter items by status: 'needs_action' "
                                                          "for incomplete, 'completed' for done. "
                                                          'Only applies when entity_id is '
                                                          'provided.'}},
                 'type': 'object'},
 'ha_get_zone': {'additionalProperties': False,
                 'properties': {'zone_id': {'anyOf': [{'type': 'string'}, {'type': 'null'}],
                                            'default': None,
                                            'description': 'Zone ID to get details for (from '
                                                           'ha_get_zone() list). If omitted, lists '
                                                           'all zones.'}},
                 'type': 'object'},
 'ha_list_floors_areas': {'additionalProperties': False,
                          'properties': {'area_fields': {'anyOf': [{'type': 'string'},
                                                                   {'items': {'type': 'string'},
                                                                    'type': 'array'},
                                                                   {'type': 'null'}],
                                                         'default': None,
                                                         'description': 'Project each area record '
                                                                        '(in floors[].areas, '
                                                                        'unassigned_areas, and '
                                                                        'orphaned_areas) to only '
                                                                        'the specified keys. E.g. '
                                                                        '["area_id", "name"] '
                                                                        'returns slim area '
                                                                        'records. None = full '
                                                                        'records (default). '
                                                                        'Unknown keys yield empty '
                                                                        'records. Available keys: '
                                                                        'area_id, name, icon, '
                                                                        'floor_id, aliases, '
                                                                        'picture, labels.'},
                                         'fields': {'anyOf': [{'type': 'string'},
                                                              {'items': {'type': 'string'},
                                                               'type': 'array'},
                                                              {'type': 'null'}],
                                                    'default': None,
                                                    'description': 'Return only the specified '
                                                                   'top-level response keys to '
                                                                   'reduce response size (e.g. '
                                                                   '["floors"]). None = full '
                                                                   'response (default). Available '
                                                                   'keys: success, floor_count, '
                                                                   'area_count, unassigned_count, '
                                                                   'orphaned_count, floors, '
                                                                   'unassigned_areas, '
                                                                   'orphaned_areas, message.'}},
                          'type': 'object'},
 'ha_list_services': {'additionalProperties': False,
                      'properties': {'detail_level': {'default': 'summary',
                                                      'description': "'summary': service name + "
                                                                     'description only (default). '
                                                                     "'full': include parameter "
                                                                     'field schemas.',
                                                      'enum': ['summary', 'full'],
                                                      'type': 'string'},
                                     'domain': {'anyOf': [{'type': 'string'}, {'type': 'null'}],
                                                'default': None,
                                                'description': "Filter by domain (e.g., 'light', "
                                                               "'switch', 'climate')."},
                                     'fields': {'anyOf': [{'type': 'string'},
                                                          {'items': {'type': 'string'},
                                                           'type': 'array'},
                                                          {'type': 'null'}],
                                                'default': None,
                                                'description': 'Return only the specified '
                                                               'top-level response keys to reduce '
                                                               'response size (e.g. ["services"]). '
                                                               'None = full response (default). '
                                                               'Available keys: success, domains, '
                                                               'services, total_count, count, '
                                                               'offset, limit, has_more, '
                                                               'next_offset, detail_level, '
                                                               'filters_applied.'},
                                     'limit': {'default': 50,
                                               'description': 'Max services to return per page '
                                                              '(default: 50)',
                                               'maximum': 200,
                                               'minimum': 1,
                                               'type': 'integer'},
                                     'offset': {'default': 0,
                                                'description': 'Number of services to skip for '
                                                               'pagination (default: 0)',
                                                'minimum': 0,
                                                'type': 'integer'},
                                     'query': {'anyOf': [{'type': 'string'}, {'type': 'null'}],
                                               'default': None,
                                               'description': 'Search in service names and '
                                                              'descriptions.'},
                                     'service_fields': {'anyOf': [{'type': 'string'},
                                                                  {'items': {'type': 'string'},
                                                                   'type': 'array'},
                                                                  {'type': 'null'}],
                                                        'default': None,
                                                        'description': 'Project each service '
                                                                       'record to only the '
                                                                       'specified keys. E.g. '
                                                                       '["name", "description"] '
                                                                       'returns slim service '
                                                                       'records. None = full '
                                                                       'records (default). Unknown '
                                                                       'keys yield empty records. '
                                                                       'Available keys: name, '
                                                                       'description, domain, '
                                                                       'service, fields (full mode '
                                                                       'only), target (full mode '
                                                                       'only).'}},
                      'type': 'object'},
 'ha_search': {'additionalProperties': False,
               'properties': {'area_filter': {'anyOf': [{'type': 'string'}, {'type': 'null'}],
                                              'default': None,
                                              'description': 'Narrow entity-registry results to an '
                                                             'area (id, name, or alias), an exact '
                                                             'floor (id, name, or alias), or an '
                                                             'unambiguous close-spelling floor '
                                                             'match; a floor match expands to all '
                                                             'areas on that floor. Does not affect '
                                                             'configuration search.'},
                              'config_time_budget': {'anyOf': [{'maximum': 300,
                                                                'minimum': 0.001,
                                                                'type': 'number'},
                                                               {'type': 'null'}],
                                                     'default': None,
                                                     'description': 'Per-call override for the '
                                                                    'per-id config-fetch '
                                                                    'wall-clock budget (seconds). '
                                                                    'Replaces the per-type '
                                                                    'HAMCP_*_CONFIG_TIME_BUDGET '
                                                                    'defaults for the automation, '
                                                                    'script, AND scene branches. '
                                                                    'Use when a `partial: True` '
                                                                    'response names time-budget '
                                                                    'skipping. Stateless per-call: '
                                                                    'one caller raising the budget '
                                                                    "doesn't affect others. None = "
                                                                    'use the per-type env '
                                                                    'defaults.'},
                              'domain_filter': {'anyOf': [{'type': 'string'}, {'type': 'null'}],
                                                'default': None,
                                                'description': 'Narrow entity-registry results to '
                                                               "a single domain (e.g. 'light', "
                                                               "'sensor'). Does not affect "
                                                               'configuration search.'},
                              'exact_match': {'default': True,
                                              'description': 'Exact substring matching (default). '
                                                             'Set False for fuzzy matching when '
                                                             'the query may have typos.',
                                              'type': 'boolean'},
                              'fields': {'anyOf': [{'type': 'string'},
                                                   {'items': {'type': 'string'}, 'type': 'array'},
                                                   {'type': 'null'}],
                                         'default': None,
                                         'description': 'Project the response to the named '
                                                        'top-level keys (e.g. ["entities", '
                                                        '"automations"]); None = full response. '
                                                        'Diagnostic / pagination keys are always '
                                                        'retained so projection cannot hide '
                                                        'partial / error state. Distinct from '
                                                        '`result_fields` (which projects each '
                                                        "entity record's keys). Available keys: "
                                                        'success, query, entities, automations, '
                                                        'scripts, scenes, helpers, dashboards, '
                                                        'search_types, search_type, '
                                                        'entity_total_matches, '
                                                        'config_total_matches, count, offset, '
                                                        'limit, has_more, next_offset, '
                                                        'entity_has_more, entity_next_offset, '
                                                        'config_has_more, config_next_offset, '
                                                        'by_domain, state_filter_note, area_names, '
                                                        'domain_filter, area_filter, message, '
                                                        'warnings, errors, partial, '
                                                        'partial_reason.'},
                              'group_by_domain': {'default': False,
                                                  'description': 'Group entity-registry results by '
                                                                 'domain (entity-side only). Adds '
                                                                 'a `by_domain` map to the '
                                                                 'response.',
                                                  'type': 'boolean'},
                              'include_config': {'default': False,
                                                 'description': 'Include full configuration bodies '
                                                                'in body-search results. Default: '
                                                                'False (summary only).',
                                                 'type': 'boolean'},
                              'include_hidden': {'default': True,
                                                 'description': 'Include hidden entities in '
                                                                'registry results (with a score '
                                                                'penalty so they sort below '
                                                                'visible matches). Set False to '
                                                                'exclude entirely.',
                                                 'type': 'boolean'},
                              'limit': {'default': 10,
                                        'description': 'Maximum results per surface (entities, '
                                                       'configs). Default: 10.',
                                        'minimum': 1,
                                        'type': 'integer'},
                              'offset': {'default': 0,
                                         'description': 'Number of results to skip for pagination.',
                                         'minimum': 0,
                                         'type': 'integer'},
                              'per_domain_limit': {'anyOf': [{'type': 'integer'}, {'type': 'null'}],
                                                   'default': None,
                                                   'description': 'When `group_by_domain=True`, '
                                                                  'cap entity-registry results per '
                                                                  'domain to this number. Ignored '
                                                                  'otherwise.'},
                              'query': {'anyOf': [{'type': 'string'}, {'type': 'null'}],
                                        'default': None,
                                        'description': 'What to search for (entity name fragment, '
                                                       'free-text config term, entity_id). '
                                                       'Searches BOTH the entity registry '
                                                       '(entity_ids, friendly names, areas) AND '
                                                       'configuration bodies (automation '
                                                       'triggers/actions, script sequences, scene '
                                                       'contents, helper bodies, dashboard cards) '
                                                       'in one call. Use this for any '
                                                       'find-something-in-HA question — entity OR '
                                                       'config. Pass the exact entity_id, not a '
                                                       'name fragment, when checking what a rename '
                                                       'or delete would break: that form reports '
                                                       'automations, scripts and scenes '
                                                       'referencing it even when their '
                                                       'configuration could not be read. Omit '
                                                       '`query` to enumerate by `domain_filter`, '
                                                       '`area_filter`, and/or `state_filter` alone '
                                                       '(registry-listing mode); '
                                                       'configuration-body search is skipped in '
                                                       'that mode because there is no term to '
                                                       'match against.'},
                              'result_fields': {'anyOf': [{'type': 'string'},
                                                          {'items': {'type': 'string'},
                                                           'type': 'array'},
                                                          {'type': 'null'}],
                                                'default': None,
                                                'description': 'Project each entity-registry '
                                                               'record to only the specified keys '
                                                               '(e.g. ["entity_id", "state"]). '
                                                               'None = full records. Base keys: '
                                                               'entity_id, friendly_name, domain, '
                                                               'state, score, match_type. Opt-in '
                                                               'enrichment/membership keys '
                                                               '(computed on request): area, '
                                                               'floor, labels, aliases, is_group, '
                                                               'member_entity_ids. Membership is '
                                                               'recognized only when HA explicitly '
                                                               'exposes a valid group_entities or '
                                                               'legacy entity_id collection; '
                                                               'member IDs are sorted, direct (not '
                                                               'recursively expanded), and omitted '
                                                               'if visibility/include_hidden '
                                                               'excludes a member. is_group '
                                                               'remains true when member IDs are '
                                                               'withheld; requesting '
                                                               'member_entity_ids also retains '
                                                               'is_group. An unknown key is '
                                                               'rejected.'},
                              'search_types': {'anyOf': [{'type': 'string'},
                                                         {'items': {'type': 'string'},
                                                          'type': 'array'},
                                                         {'type': 'null'}],
                                               'default': None,
                                               'description': 'Configuration types to include in '
                                                              "body search: 'automation', "
                                                              "'script', 'scene', 'helper', "
                                                              "'dashboard'. Default = "
                                                              'automation+script+scene+helper. '
                                                              'Pass as list or JSON-array string.'},
                              'state_filter': {'anyOf': [{'type': 'string'}, {'type': 'null'}],
                                               'default': None,
                                               'description': 'Filter entity-registry results to a '
                                                              'specific state (e.g. "on", "off", '
                                                              '"unavailable"). Case-insensitive. '
                                                              'Can be used standalone (no '
                                                              'query/domain/area) to enumerate '
                                                              'every entity in that state; '
                                                              'entity_total_matches reflects the '
                                                              'filtered count.'}},
               'type': 'object'}}


def is_adapter(entry) -> bool:
    return (
        entry.classification == "automatic_read"
        and entry.exposed_name == entry.upstream_name
        and entry.argument_restrictions == (ADAPTER,)
        and entry.input_schema_fingerprint == INPUT_FINGERPRINTS.get(entry.upstream_name)
        and entry.reviewed_annotations.read_only
        and not entry.reviewed_annotations.destructive
    )


def public_schema(entry, observed: dict) -> dict:
    return deepcopy(PUBLIC_SCHEMAS[entry.upstream_name] if is_adapter(entry) else observed)


def read_arguments(entry, arguments: dict) -> dict:
    if not is_adapter(entry):
        return dict(arguments)
    name = entry.upstream_name
    schema = PUBLIC_SCHEMAS[name]
    if set(arguments) - set(schema.get("properties", {})):
        raise ValueError("read_arguments_invalid")
    if name == "ha_config_get_scene":
        scene = arguments.get("scene_id")
        if not isinstance(scene, str) or not scene.strip():
            raise ValueError("scene_identity_required")
        return {"scene_id": scene}
    if name == "ha_eval_template":
        timeout = arguments.get("timeout", 3)
        if type(timeout) is not int or not 1 <= timeout <= 60:
            raise ValueError("template_timeout_invalid")
        return {
            "template": arguments["template"], "timeout": timeout,
            "report_errors": arguments.get("report_errors", True),
            "condition": None, "variables": None, "strict": False,
        }
    if name == "ha_get_skill_guide":
        file = arguments.get("file", "SKILL.md")
        if (not isinstance(file, str) or not 1 <= len(file) <= 512
                or not re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*", file)
                or any(part in {".", ".."} for part in file.split("/"))):
            raise ValueError("skill_file_invalid")
        return {"file": file}
    return dict(arguments)
