"""HAMCP-319: every upstream tool Engineering uses is admitted only exactly.

Josh decision relayed by the architect, 2026-11-07: on ha-mcp 8.5.0 and 9.1.0
each upstream tool Engineering dispatches must match its reviewed release
descriptor field by field (name, title, description, inputSchema,
outputSchema, annotations and _meta). A missing, changed or duplicated used
tool fails closed (not admitted, not exposed, no dispatch). Extra unused
upstream tools are tolerated and never exposed.

The comparison is the release's runtime model,
``ha-mcp-operational-tool-descriptor-v2``: the whole descriptor is hashed
verbatim except that the four dynamic ``_meta.ha_mcp.policy`` values
(deployment, enabled, live, rules) are projected to a validity bit, because
they vary with the deployment and the operator's live policy state. 8.5.0
publishes that policy object; 9.1.0 publishes no ``_meta.ha_mcp`` at all, so a
9.1.0 descriptor that gains one is a change.

Families: the read gateway's read routes (automatic reads and the adapter
reads), the typed power/fan/helper providers, the dashboard provider (getter
read path, best-practices guide and setter) and the optional YAML reader.
Descriptors are the committed captures under
docs/evidence/upstream-read-compatibility and docs/evidence/hamcp162; nothing
is contacted.
"""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'hass_mcp_engineering_beta'), str(ROOT / 'tests')]

from ha_mcp_engineering.clients.upstream_read import McpReadCatalog
from ha_mcp_engineering.errors import DashboardProviderError
from ha_mcp_engineering.providers.upstream_fan import REQUIRED_TOOLS
from ha_mcp_engineering.providers.upstream_yaml_reader import TOOL_NAME as YAML_TOOL
from ha_mcp_engineering.upstream_tool_policy import (
    EXACT_USED_TOOL_RELEASES, load_reviewed_upstream_release_registry,
)
import test_hamcp162_yaml_admission as adm
from test_hamcp319_dashboard import (
    GETTER, GUIDE, SETTER, DashboardPeer, Harness as DashboardHarness,
)
from test_hamcp319_typed_writes import FAMILIES, catalog_tools
from test_hamcp319_yaml import wire_catalog

VERSIONS = ('8.5.0', '9.1.0')
REGISTRY = load_reviewed_upstream_release_registry()
EXTRA = 'ha_synthetic_unreviewed_extra'
# The blueprint adapter exposes its upstream tool under a reviewed name.
EXPOSED_AS = {'ha_manage_blueprints': 'ha_get_blueprint'}


def used_reads(version):
    """Every read route the gateway may dispatch for this release."""
    policy = REGISTRY.by_version[version].policy
    return sorted(entry.upstream_name for entry in policy.tools if entry.is_read_route)


def _set_title(tool):
    tool['title'] = str(tool.get('title', '')) + ' (synthetic change)'


def _set_description(tool):
    tool['description'] = tool['description'] + ' Synthetic change.'


def _set_input(tool):
    tool['inputSchema'].setdefault('properties', {})['synthetic_extra'] = {'type': 'string'}


def _set_output(tool):
    if 'outputSchema' in tool:
        tool['outputSchema'] = dict(tool['outputSchema'], description='Synthetic change.')
    else:
        tool['outputSchema'] = {'type': 'object'}


def _set_annotations(tool):
    tool['annotations'] = dict(tool['annotations'], title='Synthetic change')


def _set_static_meta(tool):
    tool['_meta']['fastmcp']['tags'] = list(tool['_meta']['fastmcp']['tags']) + ['Synthetic']


def _set_dynamic_policy(tool):
    # 8.5.0: an invalid dynamic value (outside the projection's domain).
    # 9.1.0: no _meta.ha_mcp was reviewed, so adding one is a change.
    ha_mcp = tool['_meta'].get('ha_mcp')
    if isinstance(ha_mcp, dict):
        ha_mcp['policy'] = dict(ha_mcp['policy'], rules=-1)
    else:
        tool['_meta']['ha_mcp'] = {'policy': {
            'deployment': 'standalone', 'enabled': False, 'live': False, 'rules': 0}}


# One mutation per reviewed field class. ``name`` is covered by the missing
# case: a renamed used tool is a missing used tool plus an extra unused one.
FIELD_CHANGES = {
    'title': _set_title,
    'description': _set_description,
    'inputSchema': _set_input,
    'outputSchema': _set_output,
    'annotations': _set_annotations,
    '_meta_static': _set_static_meta,
    '_meta_ha_mcp_policy': _set_dynamic_policy,
}


def valid_dynamic_policy_change(tool):
    """A different but valid policy state; True when the release has one."""
    ha_mcp = tool['_meta'].get('ha_mcp')
    if not isinstance(ha_mcp, dict):
        return False
    ha_mcp['policy'] = {'deployment': 'addon', 'enabled': True, 'live': True, 'rules': 7}
    return True


def changed(tools, name, change):
    tools = deepcopy(list(tools))
    change(next(tool for tool in tools if tool['name'] == name))
    return tools


def without(tools, name):
    return [deepcopy(tool) for tool in tools if tool['name'] != name]


def duplicated(tools, name):
    tools = deepcopy(list(tools))
    index = next(i for i, tool in enumerate(tools) if tool['name'] == name)
    tools.insert(index + 1, deepcopy(tools[index]))
    return tools


def with_extra(tools, template='ha_get_state'):
    tools = deepcopy(list(tools))
    extra = deepcopy(next(tool for tool in tools if tool['name'] == template))
    extra['name'] = EXTRA
    tools.append(extra)
    return tools


class Scope(unittest.TestCase):
    def test_exact_used_tool_releases(self):
        self.assertEqual(EXACT_USED_TOOL_RELEASES, frozenset(VERSIONS))
        for version in VERSIONS:
            release = REGISTRY.by_version[version]
            self.assertEqual(release.runtime_contract_fingerprint_model,
                             'ha-mcp-operational-tool-descriptor-v2')
            self.assertEqual(len(release.tool_contracts_by_name), 77)

    def test_used_tool_inventory(self):
        """The complete used set per release (listed in the PR body)."""
        expected_reads = {
            'ha_config_get_automation', 'ha_config_get_calendar_events',
            'ha_config_get_category', 'ha_config_get_label', 'ha_config_get_scene',
            'ha_config_get_script', 'ha_config_list_dashboard_resources',
            'ha_config_list_groups', 'ha_config_list_helpers', 'ha_eval_template',
            'ha_get_app', 'ha_get_automation_traces', 'ha_get_device', 'ha_get_entity',
            'ha_get_entity_exposure', 'ha_get_history', 'ha_get_integration',
            'ha_get_overview', 'ha_get_skill_guide', 'ha_get_state',
            'ha_get_system_health', 'ha_get_todo', 'ha_get_zone', 'ha_list_floors_areas',
            'ha_list_services', 'ha_manage_blueprints', 'ha_search',
        }
        self.assertEqual(set(used_reads('9.1.0')), expected_reads)
        self.assertEqual(set(used_reads('8.5.0')), expected_reads | {'ha_get_hacs_info'})
        self.assertEqual(REQUIRED_TOOLS, ('ha_list_services', 'ha_get_state', 'ha_call_service'))
        self.assertEqual((GETTER, GUIDE, SETTER), (
            'ha_config_get_dashboard', 'ha_get_skill_guide', 'ha_config_set_dashboard'))
        self.assertEqual(YAML_TOOL, 'ha_config_get_yaml')


# ---------------------------------------------------------------------------
# Read gateway: automatic reads and adapter reads.
# ---------------------------------------------------------------------------
def gateway_catalog(version):
    return deepcopy(adm.captured(version)) if version != '9.1.0' else deepcopy(wire_catalog())


class ReadGatewayExactAdmission(adm.Harness):
    async def baseline(self, version):
        gateway, transport = await self.gateway(version, gateway_catalog(version))
        exposed = self.exposed(gateway)
        for name in used_reads(version):
            self.assertIn(EXPOSED_AS.get(name, name), exposed)
        self.assertEqual(transport.sent, [])
        return exposed

    async def test_each_field_class_change_fails_closed(self):
        for version in VERSIONS:
            base = await self.baseline(version)
            for field, change in FIELD_CHANGES.items():
                with self.subTest(version=version, field=field):
                    gateway, transport = await self.gateway(
                        version, changed(gateway_catalog(version), 'ha_get_state', change))
                    self.assertEqual(self.exposed(gateway), base - {'ha_get_state'})
                    self.assertEqual(transport.sent, [])

    async def test_title_only_change_fails_closed_for_every_used_read(self):
        # Title is compared only through the whole-descriptor runtime hash.
        for version in VERSIONS:
            base = await self.baseline(version)
            for name in used_reads(version):
                with self.subTest(version=version, tool=name):
                    gateway, transport = await self.gateway(
                        version, changed(gateway_catalog(version), name, _set_title))
                    self.assertEqual(self.exposed(gateway), base - {EXPOSED_AS.get(name, name)})
                    self.assertEqual(transport.sent, [])

    async def test_valid_dynamic_policy_change_is_projected(self):
        base = await self.baseline('8.5.0')
        tools = gateway_catalog('8.5.0')
        self.assertTrue(valid_dynamic_policy_change(
            next(tool for tool in tools if tool['name'] == 'ha_get_state')))
        gateway, _transport = await self.gateway('8.5.0', tools)
        self.assertEqual(self.exposed(gateway), base)

    async def test_missing_used_read_fails_closed(self):
        for version in VERSIONS:
            for name in ('ha_get_state', 'ha_manage_blueprints'):
                with self.subTest(version=version, tool=name):
                    gateway, transport = await self.gateway(
                        version, without(gateway_catalog(version), name))
                    self.assertNotIn(EXPOSED_AS.get(name, name), self.exposed(gateway))
                    self.assertEqual(transport.sent, [])

    async def test_duplicate_used_read_fails_closed(self):
        for version in VERSIONS:
            with self.subTest(version=version):
                gateway, transport = await self.gateway(
                    version, duplicated(gateway_catalog(version), 'ha_get_state'))
                self.assertNotIn('ha_get_state', self.exposed(gateway))
                self.assertEqual(transport.sent, [])

    async def test_extra_unused_tool_is_tolerated_and_not_exposed(self):
        for version in VERSIONS:
            base = await self.baseline(version)
            with self.subTest(version=version):
                gateway, transport = await self.gateway(
                    version, with_extra(gateway_catalog(version)))
                self.assertEqual(self.exposed(gateway), base)
                self.assertNotIn(EXTRA, self.exposed(gateway))
                self.assertEqual(transport.sent, [])

    async def test_changed_unused_reviewed_tool_is_tolerated_and_not_exposed(self):
        for version in VERSIONS:
            base = await self.baseline(version)
            with self.subTest(version=version):
                gateway, _transport = await self.gateway(
                    version, changed(gateway_catalog(version), SETTER, _set_title))
                self.assertEqual(self.exposed(gateway), base)
                self.assertNotIn(SETTER, self.exposed(gateway))


# ---------------------------------------------------------------------------
# Typed control: power, fan and helper providers.
# ---------------------------------------------------------------------------
class _Recorder:
    def __init__(self):
        self.calls = []

    async def execute_read(self, *args, **kwargs):  # pragma: no cover - must not run
        self.calls.append(args)
        raise AssertionError('typed provider reached its transport')


class TypedControlExactAdmission(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def catalog(version, tools):
        return McpReadCatalog('2025-03-26', 'ha-mcp', version, tuple(tools), 0)

    def admit(self, family, version, tools):
        return family.provider(None, lambda *a: 'a').validate_catalog(self.catalog(version, tools))

    def test_exact_catalog_admits(self):
        for kind, family in FAMILIES.items():
            for version in VERSIONS:
                with self.subTest(kind=kind, version=version):
                    self.assertEqual(self.admit(family, version, catalog_tools(version))[0], version)

    def test_each_field_class_change_of_each_used_tool_fails_closed(self):
        for kind, family in FAMILIES.items():
            for version in VERSIONS:
                for name in REQUIRED_TOOLS:
                    for field, change in FIELD_CHANGES.items():
                        with self.subTest(kind=kind, version=version, tool=name, field=field):
                            with self.assertRaises(family.refusal) as caught:
                                self.admit(family, version,
                                           changed(catalog_tools(version), name, change))
                            self.assertIn('contract_mismatch', str(caught.exception.args))

    def test_valid_dynamic_policy_change_is_projected(self):
        for kind, family in FAMILIES.items():
            tools = deepcopy(list(catalog_tools('8.5.0')))
            for tool in tools:
                if tool['name'] in REQUIRED_TOOLS:
                    self.assertTrue(valid_dynamic_policy_change(tool))
            with self.subTest(kind=kind):
                self.assertEqual(self.admit(family, '8.5.0', tools)[0], '8.5.0')

    def test_missing_or_duplicate_used_tool_fails_closed(self):
        for kind, family in FAMILIES.items():
            for version in VERSIONS:
                for name in REQUIRED_TOOLS:
                    for label, tools in (('missing', without(catalog_tools(version), name)),
                                         ('duplicate', duplicated(catalog_tools(version), name))):
                        with self.subTest(kind=kind, version=version, tool=name, case=label):
                            with self.assertRaises(family.refusal):
                                self.admit(family, version, tools)

    def test_extra_or_changed_unused_tool_is_tolerated(self):
        for kind, family in FAMILIES.items():
            for version in VERSIONS:
                for label, tools in (
                        ('extra', with_extra(catalog_tools(version))),
                        ('changed_unused', changed(catalog_tools(version), SETTER, _set_title))):
                    with self.subTest(kind=kind, version=version, case=label):
                        self.assertEqual(self.admit(family, version, tools)[0], version)

    async def test_extra_unused_tool_is_never_dispatched(self):
        for kind, family in FAMILIES.items():
            recorder = _Recorder()

            async def before():
                raise AssertionError('intent recorded for an unused tool')

            with self.subTest(kind=kind):
                with self.assertRaises(family.refusal) as caught:
                    await family.provider(recorder, lambda *a: 'a')._call(EXTRA, {}, before)
                self.assertIn('tool_unreviewed', str(caught.exception.args))
                self.assertEqual(recorder.calls, [])


# ---------------------------------------------------------------------------
# Dashboard provider: getter read path, guide and setter.
# ---------------------------------------------------------------------------
class DashboardExactAdmission(DashboardHarness):
    async def assert_read_refused(self, peer):
        provider = self.provider(peer)
        with self.assertRaises(DashboardProviderError):
            await provider.refresh_capabilities()
        with self.assertRaises(DashboardProviderError):
            await provider.list_dashboards(limit=10, response_limit=60_000)
        with self.assertRaises(DashboardProviderError):
            await provider.get_dashboard_config(
                url_path='operations', force_reload=False, response_limit=60_000)
        self.assertEqual(peer.calls, [])
        self.balanced()

    async def assert_read_admitted(self, peer):
        provider = self.provider(peer)
        await provider.refresh_capabilities()
        result = await provider.list_dashboards(limit=10, response_limit=60_000)
        self.assertIsNotNone(result)
        self.assertEqual(peer.names(), [GETTER])

    async def assert_guide_refused(self, peer):
        provider = self.provider(peer)
        with self.assertRaises(DashboardProviderError):
            await provider.best_practices_acknowledgement_key()
        self.assertNotIn(GUIDE, peer.names())
        self.assertNotIn(SETTER, peer.names())

    async def test_exact_catalog_admits_read_and_guide(self):
        for version in VERSIONS:
            with self.subTest(version=version):
                await self.assert_read_admitted(DashboardPeer(version))
                peer = DashboardPeer(version)
                key = await self.provider(peer).best_practices_acknowledgement_key()
                self.assertEqual(key, peer.key)

    async def test_each_field_class_change_of_the_getter_fails_closed(self):
        for version in VERSIONS:
            for field, change in FIELD_CHANGES.items():
                with self.subTest(version=version, field=field):
                    peer = DashboardPeer(version)
                    peer.tools = changed(peer.tools, GETTER, change)
                    await self.assert_read_refused(peer)

    async def test_each_field_class_change_of_guide_or_setter_refuses_writes(self):
        for version in VERSIONS:
            for name in (GETTER, GUIDE, SETTER):
                for field, change in FIELD_CHANGES.items():
                    with self.subTest(version=version, tool=name, field=field):
                        peer = DashboardPeer(version)
                        peer.tools = changed(peer.tools, name, change)
                        await self.assert_guide_refused(peer)

    async def test_valid_dynamic_policy_change_is_projected(self):
        peer = DashboardPeer('8.5.0')
        for tool in peer.tools:
            if tool['name'] in (GETTER, GUIDE, SETTER):
                self.assertTrue(valid_dynamic_policy_change(tool))
        await self.assert_read_admitted(peer)
        key = await self.provider(peer).best_practices_acknowledgement_key()
        self.assertEqual(key, peer.key)

    async def test_missing_or_duplicate_getter_fails_closed(self):
        for version in VERSIONS:
            for label, edit in (('missing', without), ('duplicate', duplicated)):
                with self.subTest(version=version, case=label):
                    peer = DashboardPeer(version)
                    peer.tools = edit(peer.tools, GETTER)
                    await self.assert_read_refused(peer)

    async def test_missing_or_duplicate_guide_or_setter_refuses_writes(self):
        for version in VERSIONS:
            for name in (GUIDE, SETTER):
                for label, edit in (('missing', without), ('duplicate', duplicated)):
                    with self.subTest(version=version, tool=name, case=label):
                        peer = DashboardPeer(version)
                        peer.tools = edit(peer.tools, name)
                        await self.assert_guide_refused(peer)

    async def test_extra_or_changed_unused_tool_is_tolerated(self):
        for version in VERSIONS:
            for label in ('extra', 'changed_unused'):
                with self.subTest(version=version, case=label):
                    peer = DashboardPeer(version)
                    peer.tools = (with_extra(peer.tools) if label == 'extra'
                                  else changed(peer.tools, 'ha_call_service', _set_title))
                    await self.assert_read_admitted(peer)
                    self.assertNotIn(EXTRA, peer.names())


# ---------------------------------------------------------------------------
# Optional YAML reader (siblings are unused, prohibited and never routed).
# ---------------------------------------------------------------------------
class YamlReaderExactAdmission(adm.Harness):
    async def stock(self, version):
        gateway, _transport = await self.gateway(version, without(gateway_catalog(version), YAML_TOOL))
        return self.exposed(gateway)

    async def test_exact_reader_admitted(self):
        for version in VERSIONS:
            with self.subTest(version=version):
                gateway, transport = await self.gateway(version, gateway_catalog(version))
                self.assertEqual(self.optional(gateway)[YAML_TOOL], 'admitted')
                self.assertIn(YAML_TOOL, self.exposed(gateway))
                self.assertEqual(transport.sent, [])

    async def test_each_field_class_change_fails_closed(self):
        for version in VERSIONS:
            stock = await self.stock(version)
            for field, change in FIELD_CHANGES.items():
                with self.subTest(version=version, field=field):
                    gateway, transport = await self.gateway(
                        version, changed(gateway_catalog(version), YAML_TOOL, change))
                    self.assertTrue(self.optional(gateway)[YAML_TOOL].startswith('quarantined:'),
                                    self.optional(gateway))
                    self.assertEqual(self.exposed(gateway), stock)
                    self.assertEqual(transport.sent, [])

    async def test_valid_dynamic_policy_change_is_projected(self):
        tools = gateway_catalog('8.5.0')
        self.assertTrue(valid_dynamic_policy_change(
            next(tool for tool in tools if tool['name'] == YAML_TOOL)))
        gateway, _transport = await self.gateway('8.5.0', tools)
        self.assertEqual(self.optional(gateway)[YAML_TOOL], 'admitted')

    async def test_missing_or_duplicate_reader_fails_closed(self):
        for version in VERSIONS:
            stock = await self.stock(version)
            for label, edit, state in (
                    ('missing', without, 'optional_absent'),
                    ('duplicate', duplicated, 'quarantined:optional_descriptor_duplicate')):
                with self.subTest(version=version, case=label):
                    gateway, transport = await self.gateway(
                        version, edit(gateway_catalog(version), YAML_TOOL))
                    self.assertEqual(self.optional(gateway)[YAML_TOOL], state)
                    self.assertEqual(self.exposed(gateway), stock)
                    self.assertEqual(transport.sent, [])

    async def test_extra_unused_tool_is_tolerated_and_not_exposed(self):
        for version in VERSIONS:
            with self.subTest(version=version):
                gateway, _transport = await self.gateway(
                    version, with_extra(gateway_catalog(version), template=YAML_TOOL))
                self.assertEqual(self.optional(gateway)[YAML_TOOL], 'admitted')
                self.assertNotIn(EXTRA, self.exposed(gateway))


if __name__ == '__main__':
    unittest.main()
