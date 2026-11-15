"""HAMCP-371 S7: generate the ha-mcp 9.2.0 output-key contract from receipts.

Reads the committed S6 real-Core receipts (profile A and B, 9.1.2 and 9.2.0),
checks each against its run's SHA256SUMS, and writes three files under
tests/fixtures/ha_mcp_9_2_0/:

- output-key-contract.json: per read, the observed key names per version, the
  admitted names with their source, the keys new in or removed from 9.2.0 with
  their review status, policy drops, open and map containers, the reviewed
  upstream warnings and the fields that carry upstream text (ruling 2 and
  items 8-15 of the 21:36 record);
- provenance.json: one class C entry per receipt (path, sha256, pointer);
- SHA256SUMS.json: the sha256 of every receipt and call index it read.

Deterministic: the same receipts give the same bytes. Offline; no network, no
Home Assistant. tests/test_hamcp371_real_core_replay.py regenerates the files
and asserts they equal the committed ones.

Usage: python docs/evidence/hamcp371/s7/generate_output_key_contract.py [--check]
"""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[4]
S6 = 'docs/evidence/hamcp371/s6'
OUT = ROOT / 'tests/fixtures/ha_mcp_9_2_0'
GENERATOR = 'docs/evidence/hamcp371/s7/generate_output_key_contract.py'
P = 'hass_mcp_engineering_beta/ha_mcp_engineering/providers'

# Receipt sets: profile/version -> (run, version directory).
SETS = {
    'A/9.1.2': ('run-a-1', '9.1.2'),
    'A/9.2.0': ('run-a-1', '9.2.0'),
    'B/9.1.2': ('run-b-9.1.2-1', '9.1.2'),
    'B/9.2.0': ('run-b-9.2.0-2', '9.2.0'),
}
CALL_DIRS = ('calls', 'calls-after-core-restart')
# Profile B 9.2.0 only: the YAML read as the ordinary (non-administrator) user (N29).
YAML_DIRS = ('yaml-ordinary-user',)
POINTER = '/exchange/response/result'

ENGINEERING_ADDED = ('fields_withheld', 'fields_dropped_by_policy', 'upstream_warnings_replaced', 'engineering_notes')
REPLACED_WARNING_CODE = 'upstream_warning_replaced'
COUNT_CAP = 999
# Item 11: integration- or user-defined content; the key is reviewed, its
# contents pass as values within response bounds and are not counted.
OPEN_RULED = ('config', 'attributes', 'selector', 'filter')
PAGINATION = ('total_count', 'count', 'offset', 'limit', 'has_more', 'next_offset')
# upstream_platform_reads._project_section: the names it reads to build each section.
PLATFORM_STRUCTURE = (
    'issues', 'dismissed_count', 'devices', 'total_count', 'nodes', 'controller', 'border_routers',
    'themes', 'default_theme', 'default_dark_theme', 'summary', 'candidate_total', 'registry_total',
    'config_entry_orphans', 'stale_restored', 'items', 'error')
# S10 recorded review (item 13): ha-mcp 9.2.0 copies Core's service response
# description unchanged (tools_services.py:581-585 at 51e3aac7); receipt
# A/9.2.0 v24-services-response shows calendar.get_events response {optional: false}.
RESPONSE_REVIEW = 'S10 recorded review: Core service response description, tools_services.py:581-585 at 51e3aac7, receipt A/9.2.0 v24-services-response'
# S10 E1-R (2026-11-15 14:45 ruling): a key named only by the shipped 9.1 code
# stays only with the 9.2.0 upstream line that can write it on that read
# (ha-mcp 51e3aac7, src/ha_mcp/tools/; call paths in the S10 README). A named
# key with neither a 9.1.2 receipt nor a citation stops the build.
E1R = 'E1-R 9.2.0 source review, ha-mcp 51e3aac7 src/ha_mcp/tools/'
E1R_SOURCES = {
    'ha_config_get_category': {'category': 'tools_categories.py:124'},
    'ha_config_get_label': {'label': 'tools_labels.py:647'},
    'ha_config_get_scene': {'category': 'tools_config_scenes.py:455', 'warnings': 'tools_config_scenes.py:457'},
    'ha_config_get_script': {'warnings': 'tools_config_scripts.py:255'},
    'ha_config_list_dashboard_resources': {
        'content_truncated': 'tools_resources.py:265', 'content_truncation_note': 'tools_resources.py:266',
        'migration_hint': 'tools_resources.py:274', 'note': 'tools_resources.py:279'},
    'ha_config_list_helpers': {'warnings': 'config_helpers/listing.py:231 (also :182)'},
    'ha_eval_template': {'warnings': 'tools_utility.py:163'},
    'ha_get_automation_traces': {'diagnostics': 'tools_traces.py:596'},
    'ha_get_entity': {'entity_entries': 'tools_entities.py:1997', 'errors': 'tools_entities.py:2000'},
    'ha_get_entity_exposure': {'exposed_entities': 'tools_voice_assistant.py:191'},
    'ha_get_history': {
        'attributes': 'history_response.py:10,36',
        'max': 'statistics_helpers.py:74,84; Core 74cf5a49 recorder/statistics.py:344',
        'mean': 'statistics_helpers.py:74,84; Core 74cf5a49 recorder/statistics.py:342',
        'min': 'statistics_helpers.py:74,84; Core 74cf5a49 recorder/statistics.py:343',
        'warnings': 'tools_history.py:984,1099'},
    'ha_get_overview': {'partial': 'smart_search/_overview.py:556'},
    'ha_get_state': {
        'success': 'search/state.py:89', 'count': 'search/state.py:90', 'states': 'search/state.py:91',
        'errors': 'search/state.py:102', 'partial': 'search/state.py:109'},
    'ha_search': {'dashboards': 'search/component.py:523', 'warnings': 'search/modes.py:688'},
}

C = f'{P}/upstream_reads_9_1_config.py'
E = f'{P}/upstream_reads_9_1_entities.py'
H = f'{P}/upstream_history_9_1.py'
PL = f'{P}/upstream_platform_reads.py'
D = f'{P}/upstream_device_projection.py'
# S10-2 ruling (2026-11-15 13:25): Core keys ha-mcp 9.2.0 copies unchanged,
# admitted with one value type each; any other value type is withheld and counted.
CORE = 'Core 2026.11.0 (74cf5a49)'
CORE_INTEGRATION = f'S10-2 ruling: {CORE} helpers/selector.py:178,212 (entity filter integration: str)'
CORE_COLLAPSED = f'S10-2 ruling: {CORE} helpers/service.py:996,1004 (section collapsed: bool, default False)'
SERVICES_WARNINGS = 'research G ruling 2b (language-note prefix, tools_services.py:227-279 at 51e3aac7)'

# Per read: number, upstream tool, projection kind, the reviewed specification.
# "named": keys the shipped 9.1 projection requires or type-checks (file:line).
# "open"/"maps" beyond OPEN_RULED are agent proposals (S7), flagged in "proposals".
READS = {
    'ha_config_get_automation': dict(n=1, upstream='ha_config_get_automation', named=(f'{C}:128-150', (
        'action', 'automation_id', 'config', 'config_hash', 'entity', 'validation', 'references', 'warnings')),
        drops=('related',), open=('capabilities', 'options'), maps={'categories': 1},
        notes=('profile A carries upstream warnings and the policy drop related; profile B carries references',)),
    'ha_config_get_calendar_events': dict(n=2, upstream='ha_config_get_calendar_events', named=(f'{C}:154-174', (
        'entity_id', 'events', 'count', 'total_available', 'time_range', 'start', 'end', 'message'))),
    'ha_config_get_category': dict(n=3, upstream='ha_config_get_category', named=(f'{C}:178-194', (
        'scope', 'message', 'count', 'categories', 'category_id', 'category'))),
    'ha_config_get_label': dict(n=4, upstream='ha_config_get_label', named=(f'{C}:198-213', (
        'count', 'labels', 'message', 'label_id', 'label'))),
    'ha_config_get_scene': dict(n=5, upstream='ha_config_get_scene', named=(f'{C}:219-230', (
        'action', 'scene_id', 'config', 'config_hash', 'category', 'warnings'))),
    'ha_config_get_script': dict(n=6, upstream='ha_config_get_script', named=(f'{C}:237-247', (
        'action', 'script_id', 'config', 'config_hash', 'warnings'))),
    'ha_config_list_dashboard_resources': dict(n=7, upstream='ha_config_list_dashboard_resources', named=(f'{C}:251-281', (
        'action', 'resources', 'inline_count', 'by_type', 'content_truncated', 'content_truncation_note',
        'migration_hint', 'resource_mode', 'note') + PAGINATION), maps={'by_type': 1}),
    'ha_config_list_groups': dict(n=8, upstream='ha_config_list_groups', named=(f'{C}:285-301', (
        'groups', 'message', 'entity_id', 'object_id', 'entity_ids') + PAGINATION),
        recomputed=('unavailable_fields',)),
    'ha_config_list_helpers': dict(n=9, upstream='ha_config_list_helpers', named=(f'{C}:316-327', (
        'helper_type', 'helpers', 'message', 'warnings') + PAGINATION),
        pending=('read_only', 'secret', 'later_forms'),
        notes=('describe=True is not exercised by the S6 receipts; read_only, secret and later_forms stay withheld until reviewed (research G 2f)',)),
    'ha_eval_template': dict(n=10, upstream='ha_eval_template', named=(f'{C}:333-345', (
        'template', 'result', 'listeners', 'warnings')), open=('result',), recomputed=('unavailable_fields',)),
    'ha_get_automation_traces': dict(n=11, upstream='ha_get_automation_traces', named=(f'{C}:354-390', (
        'automation_id', 'trace_count', 'total_available', 'offset', 'order', 'has_more', 'traces', 'hint',
        'diagnostics', 'run_id', 'timestamp', 'state'))),
    'ha_get_blueprint': dict(n=12, upstream='ha_manage_blueprints', named=(f'{P}/upstream_blueprint.py:81-105', ()),
        notes=('shape-only validation on 8.x and 9.1.x (HAMCP-402); the closed set is drafted from the 9.1.2 receipts',
               'D5-B: on 9.2.0 a key directly under data outside upstream_reads_9_2_blueprint LIST_KEYS (capture) or GET_KEYS '
               '(source review of tools_blueprints.py at 51e3aac7) is refused as invalid_response and never counted; nested keys, '
               'config and yaml are not closed, and a key at the payload top level passes as shipped',)),
    'ha_get_device': dict(n=13, upstream='ha_get_device', kind='rebuild', rebuild=(f'{D}:476-748', (
        '_ENTITY_KEYS', '_NODE_DIAGNOSTICS_KEYS', '_FABRIC_KEYS', '_DEVICE_KEYS', '_SINGLE_KEYS', '_LIST_KEYS', '_BRIDGE_KEYS')),
        notes=('the shared device projection counts unknown keys per occurrence, not distinct names; kept as shipped on 9.2.0 (22:59 record, item 2)',
               'N26 difference from ruling 10: fields_withheld is per occurrence and absent when zero; there is no fields_dropped_by_policy '
               'or upstream_warnings_replaced; an unlisted warning becomes a shipped closed code (enrichment_warning_unrecognized on the '
               'single read; unknown_fields_withheld on the list read, which has no warnings key), not upstream_warning_replaced',)),
    'ha_get_entity': dict(n=14, upstream='ha_get_entity', named=(f'{E}:103-110', (
        'entity_entry', 'entity_entries', 'errors')), open=('options',), maps={'categories': 1}),
    'ha_get_entity_exposure': dict(n=15, upstream='ha_get_entity_exposure', named=(f'{E}:122-131', (
        'exposed_to', 'entity_id', 'exposed_entities')), maps={'exposed_to': 1}),
    'ha_get_history': dict(n=16, upstream='ha_get_history', named=(f'{H}:37-52,206-224,243-245,331-332,421,498-505', (
        'data', 'metadata', 'warnings', 'success', 'source', 'entities', 'period', 'query_params',
        'period_type', 'time_range', 'statistic_types', 'entity_id', 'states', 'statistics',
        'unit_of_measurement', 'total_count', 'offset', 'limit', 'count', 'has_more', 'next_offset',
        'state', 'attributes', 'last_updated', 'last_changed', 'start', 'end',
        'mean', 'min', 'max', 'sum', 'change', 'minimal_response', 'significant_changes_only', 'order',
        'home_assistant_timezone', 'timestamp_format', 'note')),
        drops=('summary', 'statistics_metadata', 'unit_source', 'unit_reason', 'last_reset'),
        notes=('on 9.2.0 an unknown key is withheld and counted, not a whole-read refusal (item 7); offset-free start and end times are refused (research G 2a)',
               f'policy drops are the shipped 8.5-parity drops of {H}:17-20,329-339,388-396 (S9 correction, pending ruling)',
               'a statistics row keeps start and the requested types; its end is removed too, but the name stays admitted through period and time_range, so distinct-name counts do not include it')),
    'ha_get_overview': dict(n=17, upstream='ha_get_overview', named=(f'{E}:45-47,135-140', (
        'system_summary', 'domain_stats', 'area_analysis', 'ai_insights', 'partial')),
        drops=('mcp_client', 'tool_discovery', 'settings_url', 'settings_url_hint', 'python_required', 'upgrade_command'),
        maps={'domain_stats': 1, 'area_analysis': 1, 'states_summary': 1, 'domains': 1},
        ruled={'name': 'research G ruling 2d (overview rows)'},
        notes=('ha_mcp_update is closed to current, latest and update_available; python_required and upgrade_command are policy drops (research G 2d, non-add-on installs only)',)),
    'ha_get_skill_guide': dict(n=18, upstream='ha_get_skill_guide', kind='rebuild', rebuild=(f'{E}:157-179', (
        'skill_content_hint', 'success', 'skill', 'file', 'uri', 'content')), drops=('how_to_use',)),
    'ha_get_state': dict(n=19, upstream='ha_get_state', named=(f'{E}:189-205', (
        'data', 'metadata', 'states', 'success', 'count', 'errors', 'partial', 'attributes'))),
    'ha_get_system_health': dict(n=20, upstream='ha_get_system_health', kind='rebuild', rebuild=(f'{PL}:422-610', (
        '_PLATFORM_KEYS', 'SECTIONS', '_REPAIR_ISSUE', '_ZHA_DEVICE', '_ZWAVE_CONTROLLER', '_ZWAVE_NODE',
        '_BORDER_ROUTER', '_MATTER', '_DEAD_ITEM')), closed_rebuild=True, qualify=('health_info', 'data', 'info'),
        extra_consumed=PLATFORM_STRUCTURE,
        consumed=('success', 'baseline_available', 'health_info', 'type', 'data', 'info', 'warnings'),
        notes=('domains outside homeassistant, hassio and cloud become other_domains',
               'D5: info is not open; each info key is named domain.key and one outside _PLATFORM_KEYS is withheld and counted '
               '(count unit: interpretation, pending ruling); info values are reduced as shipped and not walked',
               'section keys (SECTIONS, the section specs and their structure names) come from the shipped rebuild only; '
               'no S6 receipt requests a section')),
    'ha_get_todo': dict(n=21, upstream='ha_get_todo', named=(f'{E}:236-245', ('todo_lists', 'items', 'entity_id'))),
    'ha_get_zone': dict(n=22, upstream='ha_get_zone', named=(f'{E}:250-259', ('zones', 'zone')),
        ruled={'entity_icon': 'research G ruling 2e', 'persons': 'research G ruling 2e (Q2, with a disclosure note)',
               'device_trackers': 'research G ruling 2e (Q2, with a disclosure note)'},
        notes=('friendly_name is gone from YAML and home zone rows upstream; Engineering restores it from name on 9.2.0 (item 15); the changed icon meaning is documented (research G 2e)',)),
    'ha_list_floors_areas': dict(n=23, upstream='ha_list_floors_areas', named=(f'{E}:263-267', (
        'floors', 'unassigned_areas', 'orphaned_areas'))),
    'ha_list_services': dict(n=24, upstream='ha_list_services', named=(f'{E}:271-274', ('services',)),
        open=('example', 'default'), maps={'services': 1, 'fields': 1},
        ruled={'response': RESPONSE_REVIEW, 'optional': RESPONSE_REVIEW + ' (the only key of response)',
               'warnings': SERVICES_WARNINGS, 'integration': CORE_INTEGRATION, 'collapsed': CORE_COLLAPSED},
        typed={'integration': 'str', 'collapsed': 'bool'},
        notes=('type is removed upstream and not synthesized (item 15); language now follows Core with an English fallback warning, matched by a reviewed fixed prefix (research G 2b)',
               'integration (target.entity[] filters) and collapsed (field sections) are Core keys copied unchanged (S10-2 ruling); a value of another type is withheld and counted; filter stays withheld',)),
    'ha_search': dict(n=25, upstream='ha_search', named=(f'{E}:49-51,282-290', (
        'partial', 'errors', 'warnings', 'entities', 'automations', 'scripts', 'scenes', 'helpers', 'dashboards')),
        ruled={'name': 'research G ruling 2c'}),
    'ha_get_integration': dict(n=26, upstream='ha_get_integration', kind='rebuild', rebuild=(f'{PL}:674-744', (
        '_ENTRY',)), closed_rebuild=True, extra_consumed=('success', 'entries', 'has_more', 'warnings'),
        recomputed=('total_count', 'offset', 'limit', 'count', 'next_offset', 'state_summary', 'query'),
        pending=('read_only', 'secret', 'later_forms'),
        notes=('include_schema and include_subentry_schema are not exercised; read_only, secret and later_forms stay withheld until reviewed (research G 2f)',)),
    'ha_get_app': dict(n=27, upstream='ha_get_app', kind='rebuild', rebuild=(f'{PL}:611-673', ('_APP', '_STATS')),
        closed_rebuild=True, extra_consumed=('success', 'addons', 'stats', 'warnings'),
        notes=('D5: the 9.2.0 projection is tested with a synthetic payload only (disclosed)',
               'no successful receipt: r27-apps errors on both versions (the peer logs show supervisor/api: Unknown command); SR records the body and category',)),
}
PROPOSALS = (
    'map containers (dict keys are data, values are closed rows): services, fields, domain_stats, area_analysis, '
    'states_summary, domains (overview), exposed_to, categories, by_type, data (system health)',
    'extra open containers: capabilities and options (automation entity, entity registry), result (template), '
    'info (system health), example and default (service fields)',
    'S10 E1-R (2026-11-15 14:45 ruling): a key named by the shipped 9.1 projections but not observed in a 9.1.2 receipt '
    'stays only as a source-review key, cited to the 9.2.0 upstream line that can write it; none is observed in a lane receipt',
    'reviewed warnings are the exact upstream strings observed in the 9.1.2 receipts; the upstream source-string review is deferred',
    'ha_get_history keeps the shipped 8.5-parity projection on 9.2.0; the keys it drops (summary, statistics_metadata, '
    'unit_source, unit_reason, last_reset) are policy drops counted in fields_dropped_by_policy (S9 correction of the S7 row)',
    'S10 E1 (2026-11-15 13:25 ruling): a key justified only by the shipped 9.1 code is reported, not kept; the S10 '
    'additions of warnings and partial (entity envelope rows), all and unavailable_fields (groups) and unavailable_fields '
    '(template) are withdrawn; unavailable_fields on groups and template is recomputed by Engineering',
    'S10: engineering_notes is Engineering-added (the zone location and icon notes, the services type note, item 14 and 15); '
    'response and optional on ha_list_services are admitted on a recorded review (item 13); integration and collapsed '
    'are admitted by the S10-2 ruling with one value type each; filter stays withheld pending review',
    'D5: the platform rows are closed rebuilds; no open container applies, so a dropped key and the names inside it are counted',
)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_sums(run):
    sums = {}
    for line in (ROOT / S6 / run / 'SHA256SUMS').read_text(encoding='utf-8').splitlines():
        digest, name = line.split(None, 1)
        sums[name.lstrip('*')] = digest
    return sums


def load_receipts():
    """Bind every recorded call; return [(set, call id, tool, path, sha256, content)] for successful reads."""
    rows, bound = [], {}
    for set_name, (run, version) in SETS.items():
        sums = run_sums(run)
        for sub in CALL_DIRS + YAML_DIRS:
            rel_index = f'out/{version}/{sub}/_index.json'
            index_path = ROOT / S6 / run / rel_index
            if sub in YAML_DIRS and not index_path.exists():
                continue
            if sha(index_path) != sums[rel_index]:
                raise SystemExit(f'index hash drift: {run}/{rel_index}')
            bound[f'{S6}/{run}/{rel_index}'] = sums[rel_index]
            for call in json.loads(index_path.read_text(encoding='utf-8'))['calls']:
                rel = f'out/{version}/{sub}/{call["file"]}'
                path = ROOT / S6 / run / rel
                digest = sha(path)
                if digest != sums[rel] or digest != call['sha256']:
                    raise SystemExit(f'receipt hash drift: {run}/{rel}')
                repo_path = f'{S6}/{run}/{rel}'
                bound[repo_path] = digest
                # Every call is bound; only successful reads feed the key table.
                if call['is_error'] or call['kind'] == 'yaml' or call['public_tool'] not in READS:
                    continue
                result = json.loads(path.read_text(encoding='utf-8'))['exchange']['response']['result']
                rows.append((set_name, call['id'], call['public_tool'], repo_path, digest,
                             result.get('structuredContent')))
    return rows, bound


def spec(tool):
    s = READS[tool]
    ruled = frozenset() if s.get('closed_rebuild') else frozenset(OPEN_RULED)
    return (ruled | frozenset(s.get('open', ())), dict(s.get('maps', {})), frozenset(s.get('drops', ())))


def names(value, tool, acc=None):
    """Distinct key names, not inside open containers, policy drops or map data keys.

    A ``qualify`` rule (outer, map, inner) names each key of ``inner`` under a
    data key ``d`` of ``outer.map`` as ``d.key`` and does not walk its value.
    """
    acc = set() if acc is None else acc
    opened, maps, drops = spec(tool)
    qualify = READS[tool].get('qualify')

    def walk(v, outer=None):
        if isinstance(v, dict):
            for k, x in v.items():
                acc.add(k)
                if k in opened or k in drops:
                    continue
                if qualify and outer == qualify[0] and k == qualify[1] and isinstance(x, dict):
                    qualified(x)
                elif k in maps:
                    rows(x, maps[k])
                else:
                    walk(x, k)
        elif isinstance(v, list):
            for x in v:
                walk(x, outer)

    def qualified(v):
        for data_key, block in v.items():
            if not isinstance(block, dict):
                walk(block)
                continue
            for k, x in block.items():
                acc.add(k)
                if k == qualify[2] and isinstance(x, dict):
                    acc.update(f'{data_key}.{name}' for name in x)
                else:
                    walk(x, k)

    def rows(v, levels):
        if levels == 0 or not isinstance(v, dict):
            walk(v)
            return
        for x in v.values():
            rows(x, levels - 1)

    walk(value)
    return acc


def warnings_of(value, tool, acc):
    opened, maps, drops = spec(tool)
    if isinstance(value, dict):
        for k, x in value.items():
            if k in opened or k in drops:
                continue
            if k == 'warnings' and isinstance(x, list):
                acc.update(w for w in x if isinstance(w, str))
            warnings_of(x, tool, acc)
    elif isinstance(value, list):
        for x in value:
            warnings_of(x, tool, acc)
    return acc


TEXT_KEYS = ('message', 'hint', 'note', 'how_to_use', 'warnings', 'errors', 'usage_hint',
             'content_truncation_note', 'skill_content_hint', 'migration_hint')


def text_fields(value, tool, path, acc):
    opened, maps, drops = spec(tool)
    if isinstance(value, dict):
        for k, x in value.items():
            if k in opened:
                continue
            if k in TEXT_KEYS:
                acc.add(f'{path}{k}')
            if k in maps:
                continue
            text_fields(x, tool, f'{path}{k}.', acc)
    elif isinstance(value, list):
        for x in value:
            text_fields(x, tool, path, acc)
    return acc


def shipped_allowlist(attributes):
    sys.path.insert(0, str(ROOT / 'hass_mcp_engineering_beta'))
    from ha_mcp_engineering.providers import upstream_device_projection, upstream_platform_reads
    found = set()
    for module in (upstream_device_projection, upstream_platform_reads):
        for attribute in attributes:
            value = getattr(module, attribute, None)
            if isinstance(value, dict) and attribute == '_PLATFORM_KEYS':
                found.update(f'{domain}.{key}' for domain, keys in value.items() for key in keys)
            elif value is not None:
                found.update(value)
    return found


def build():
    rows, bound = load_receipts()
    reads = {}
    for tool, s in sorted(READS.items(), key=lambda item: item[1]['n']):
        mine = [r for r in rows if r[2] == tool]
        observed = {'9.1.2': {}, '9.2.0': {}}
        calls = {}
        baseline_warnings, new_warnings, texts = set(), set(), set()
        for set_name, call_id, _, _, _, content in mine:
            profile, version = set_name.split('/')
            calls.setdefault(set_name, []).append(call_id)
            if content is None:
                continue
            for name in names(content, tool):
                observed[version].setdefault(name, set()).add(profile)
            (baseline_warnings if version == '9.1.2' else new_warnings).update(warnings_of(content, tool, set()))
            texts.update(text_fields(content, tool, '', set()))
        old, new = set(observed['9.1.2']), set(observed['9.2.0'])
        drops = set(s.get('drops', ()))
        kind = s.get('kind', 'closed')
        admitted = {}
        if kind == 'closed':
            for name in sorted(old - drops - set(ENGINEERING_ADDED)):
                admitted[name] = 'receipt 9.1.2 profile ' + '+'.join(sorted(observed['9.1.2'][name]))
            source, named = s['named']
            for name in named:
                if name in admitted or name in s.get('ruled', {}):
                    continue
                cite = E1R_SOURCES.get(tool, {}).get(name)
                if cite is None:
                    raise SystemExit(f'E1-R: {tool}.{name} has neither a receipt nor a 9.2.0 citation')
                admitted[name] = f'{E1R}{cite} (source-review key; shipped {source})'
            for name, ruling in s.get('ruled', {}).items():
                admitted[name] = ruling
        else:
            source, attributes = s['rebuild']
            allow = shipped_allowlist(attributes) if attributes[0].startswith('_') else set(attributes)
            for name in sorted(allow | set(s.get('extra_consumed', ()))):
                admitted[name] = f'{source} (shipped rebuild)'
        # Names are flat per read, so a ruled name already used elsewhere in a
        # 9.1.2 row is listed here too, with the profiles it was seen on.
        new_keys = {}
        for name in sorted((new - old - drops) | set(s.get('pending', ())) | set(s.get('ruled', ()))):
            seen = {'observed_9_1_2_profiles': sorted(observed['9.1.2'].get(name, ())),
                    'observed_9_2_0_profiles': sorted(observed['9.2.0'].get(name, ()))}
            if name in s.get('ruled', {}):
                new_keys[name] = {'status': 'admitted', 'source': s['ruled'][name], **seen}
            elif kind == 'closed' and name in admitted:
                new_keys[name] = {'status': 'admitted', 'source': admitted[name], **seen}
            else:
                new_keys[name] = {'status': 'withheld_pending_review', 'source': 'no recorded review', **seen}
                admitted.pop(name, None)
        reads[tool] = {
            'number': s['n'],
            'upstream_tool': s['upstream'],
            'projection': kind,
            'calls': {k: sorted(v) for k, v in sorted(calls.items())},
            'observed': {v: sorted(observed[v]) for v in ('9.1.2', '9.2.0')},
            'admitted': dict(sorted(admitted.items())),
            'new_in_9_2_0': new_keys,
            'removed_in_9_2_0': sorted(old - new),
            'policy_drops': sorted(drops),
            'open_containers': sorted(spec(tool)[0]),
            'map_containers': dict(sorted(spec(tool)[1].items())),
            'qualified_containers': list(s.get('qualify', ())),
            'value_types': dict(sorted(s.get('typed', {}).items())),
            'recomputed_by_engineering': sorted(s.get('recomputed', ())),
            'consumed_structure': sorted(s.get('consumed', ())),
            'withheld_by_rebuild': sorted((old | new) - set(admitted) - drops
                                          - set(s.get('consumed', ())) - set(s.get('recomputed', ())))
            if kind == 'rebuild' else [],
            'reviewed_warnings': sorted(baseline_warnings),
            'warnings_new_in_9_2_0': sorted(new_warnings - baseline_warnings),
            'upstream_text_fields': sorted(texts),
            'notes': list(s.get('notes', ())),
        }
    contract = {
        'contract_format': 'hamcp371-output-key-contract-v1',
        'upstream': {'name': 'ha-mcp', 'version': '9.2.0', 'baseline_version': '9.1.2'},
        'generator': GENERATOR,
        'receipt_sets': {k: f'{S6}/{run}/out/{v}' for k, (run, v) in SETS.items()},
        'rules': {
            'unknown_keys': 'withheld by default and counted as distinct names per call (ruling 2, item 10)',
            'count_cap': COUNT_CAP,
            'engineering_added': list(ENGINEERING_ADDED),
            'replaced_warning_code': REPLACED_WARNING_CODE,
            'open_containers_ruled': list(OPEN_RULED),
            'default_withhold_scope': '9.2.0 only; the 9.1 modules are not edited (item 4)',
        },
        'proposals_pending_ruling': list(PROPOSALS),
        'reads': reads,
    }
    provenance = {
        'provenance_model': 'hamcp371-fixture-provenance-v1',
        'consumer': 'tests/test_hamcp371_real_core_replay.py',
        'fixture_file': 'tests/fixtures/ha_mcp_9_2_0/output-key-contract.json',
        'upstream': {'name': 'ha-mcp', 'version': '9.2.0', 'baseline_version': '9.1.2'},
        'generation': f'{GENERATOR} over the committed S6 receipts; receipts are never edited',
        'source_files': {path: sha(ROOT / path) for path in sorted({C, E, H, PL, D, f'{P}/upstream_blueprint.py', GENERATOR})},
        'fixtures': {
            f'receipt/{set_name}/{call_id}': {
                'class': 'C', 'receipt': path, 'sha256': digest, 'pointers': [POINTER],
                'use': 'S7 offline replay through the real gateway and the output-key contract',
            }
            for set_name, call_id, _, path, digest, _ in sorted(rows, key=lambda r: (r[0], r[3]))
        },
    }
    return contract, provenance, dict(sorted(bound.items()))


def dump(value):
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + '\n'


def outputs():
    contract, provenance, sums = build()
    return {
        'output-key-contract.json': dump(contract),
        'provenance.json': dump(provenance),
        'SHA256SUMS.json': dump(sums),
    }


def main(argv):
    files = outputs()
    if '--check' in argv:
        stale = [n for n, text in files.items() if (OUT / n).read_text(encoding='utf-8') != text]
        if stale:
            raise SystemExit(f'stale: {stale}')
        return
    OUT.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (OUT / name).write_text(text, encoding='utf-8', newline='\n')


if __name__ == '__main__':
    main(sys.argv[1:])
