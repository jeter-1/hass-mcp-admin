"""HAMCP-162: optional reviewed-tool admission for ``ha_config_get_yaml``.

Design ``docs/HAMCP_162_YAML_READER_DESIGN.md`` §8.1 groups 10-13: R4
descriptor binding, R5 generation and lease, unchanged stock invariants, and
the Core mapping. Catalogs are the D1 captures (``docs/evidence/hamcp162``).

Both admission modes are exercised:
- Mode A, the production default: the signed release registry is disabled
  and the compiled release registry selects the policy directly;
- Mode B: the signed registry is enabled, so the readmission coordinator,
  its generations and its single-use route leases govern dispatch.

Synthetic peers only; no live Home Assistant and no real credential.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import inspect
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from mcp.server.fastmcp import FastMCP

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT / "tests")]

from ha_mcp_engineering.clients.upstream_read import McpReadResult  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.routes import (  # noqa: E402
    DELEGATED_CORE_REQUIREMENTS,
    delegated_requirements,
)
from ha_mcp_engineering.ha_mcp_readmission.models import (  # noqa: E402
    AdmissionDisposition,
    UpstreamSurface,
    evidence_fingerprint,
)
from ha_mcp_engineering.providers import upstream_yaml_reader as y  # noqa: E402
from ha_mcp_engineering.providers.upstream_fan import FanProvider  # noqa: E402
from ha_mcp_engineering.providers.upstream_power import PowerProvider  # noqa: E402
from ha_mcp_engineering.providers.upstream_read_gateway import (  # noqa: E402
    UpstreamReadGateway,
)
from ha_mcp_engineering.tools.registry import (  # noqa: E402
    ENGINEERING_STATIC_TOOL_COUNT,
)
from ha_mcp_engineering.upstream_tool_policy import (  # noqa: E402
    EXACT_RUNTIME_TOOL_ORDER_FINGERPRINTS,
    RELEASE_REGISTRY_PATH,
    UpstreamToolPolicyError,
    load_reviewed_upstream_release_registry,
    runtime_contract_fingerprint,
    schema_fingerprint,
    validate_reviewed_release_catalog,
)
from signed_registry_fixtures import revocation  # noqa: E402
from test_hamcp129_platform_reads import HEALTH, call, default_response, ok  # noqa: E402
import test_ha_mcp_production_readmission as signed  # noqa: E402
import tests.test_core_update_continuity as core_continuity  # noqa: E402
import tests.test_core_2026_10_device_registry as october  # noqa: E402
from core_registry_fixtures import core_entry, core_revocation  # noqa: E402

EVIDENCE = ROOT / "docs/evidence/hamcp162"
VERSIONS = ("8.4.3", "8.5.0")
TOOL = y.TOOL_NAME
OPTIONAL = (TOOL,) + y.SIBLING_TOOLS
SESSION = "synthetic-ha-mcp-session"
STOCK_UPSTREAM = {"8.4.3": 78, "8.5.0": 77}  # K9 stock reviewed catalogs
STOCK_PUBLIC = 96


def captured(version, label="A"):
    return json.loads((EVIDENCE / f"captures/catalog-{version}-{label}.json").read_text())["tools"]


def stock_catalog(version):
    """The D1 capture with the component off: the stock 78/77 tools."""

    return captured(version, "OFF")


def descriptor(tools):
    return next(item for item in tools if item.get("name") == TOOL)


def with_reader(version, change):
    """The exact catalog with the reader descriptor changed by ``change``."""

    tools = deepcopy(captured(version))
    change(descriptor(tools))
    return tools


def yaml_response(path="recorder"):
    return ok({
        "success": True, "yaml_path": path,
        "matches": [{"file": "configuration.yaml", "yaml_path": path,
                     "parsed": {"purge_keep_days": 10}}],
        "count": 1, "files_searched": 1, "files_unreadable": 0,
    })


class RaceTransport(signed._GatewayTransport):
    """A synthetic transport with hooks around the dispatch linearization.

    ``between`` runs after live revalidation and before ``before_dispatch``
    (the route commit); ``during`` runs after the commit, while the result is
    in flight. Each hook runs once.
    """

    def __init__(self, tools, version, *, session_id=SESSION):
        super().__init__(tools, version=version, session_id=session_id)
        self.sent = []
        self.between = None
        self.during = None

    def set_tools(self, tools, *, session_id=None):
        self.catalog = replace(
            self.catalog, tools=tuple(deepcopy(tools)),
            session_id=session_id or self.catalog.session_id,
        )

    async def execute_read(self, tool_name, arguments, *, timeout_seconds,
                           catalog_validator, before_dispatch=None):
        catalog_validator(self.catalog)
        hook, self.between = self.between, None
        if hook is not None:
            await hook()
        if before_dispatch is not None:
            result = before_dispatch()
            if inspect.isawaitable(result):
                await result
        self.sent.append((tool_name, deepcopy(arguments)))
        hook, self.during = self.during, None
        if hook is not None:
            await hook()
        response = (
            yaml_response(arguments.get("yaml_path", "recorder"))
            if tool_name == TOOL else default_response(tool_name, arguments)
        )
        return McpReadResult(
            protocol_version=self.catalog.protocol_version,
            server_name=self.catalog.server_name,
            server_version=self.catalog.server_version,
            call_result=response, connection_latency_ms=1.0, tool_call_latency_ms=1.0,
        )


class Harness(unittest.IsolatedAsyncioTestCase):
    """Builds gateways in Mode A (registry disabled) or Mode B (signed)."""

    def setUp(self):
        self.compiled = load_reviewed_upstream_release_registry()
        self.ha_signer = signed.RegistrySigner(key_id=signed.TRUST_ANCHOR_KEY_ID)
        self.now = datetime(2026, 8, 27, 12, tzinfo=timezone.utc)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)

    def signed_registry(self, revoked=()):
        revocations = []
        for version in revoked:
            release = self.compiled.by_version[version]
            item = revocation(entry_id=release.entry_id, version=version,
                              image_index_digest=release.image_index_digest)
            item["revoked_at"] = (self.now - timedelta(minutes=2)).isoformat().replace("+00:00", "Z")
            revocations.append(item)
        raw = self.ha_signer.journal_raw(
            sequence=1, generated_at=self.now - timedelta(minutes=1),
            expires_at=self.now + timedelta(days=1), entries=[], revocations=revocations,
        )

        async def fetcher(url, maximum):
            return raw

        return signed.SignedReleaseRegistry(
            enabled=True, public_key=self.ha_signer.public_key_base64,
            cache_path=Path(self.temporary.name) / f"cache-{len(revoked)}-{id(raw)}.json",
            fetcher=fetcher, now=lambda: self.now,
        )

    async def gateway(self, version="8.5.0", tools=None, *, mode="A", revoked=(), core_runtime=None):
        transport = RaceTransport(captured(version) if tools is None else tools, version)
        gateway = UpstreamReadGateway()
        kwargs = {"core_runtime": core_runtime} if core_runtime is not None else {}
        if mode == "A":
            configured = replace(
                signed._settings("synthetic-no-key"), ha_mcp_release_registry_enabled=False,
                upstream_dashboard_mcp_url="http://synthetic-provider:9583/synthetic-path-credential/mcp",
            )
            gateway.configure(configured, transport=transport, **kwargs)
        else:
            gateway.configure(
                signed._settings(self.ha_signer.public_key_base64), transport=transport,
                release_registry=self.compiled, signed_release_registry=self.signed_registry(revoked),
                **kwargs,
            )
        server = FastMCP(f"synthetic-hamcp162-admission-{version}-{mode}")
        await gateway.initialize(server)
        gateway.synthetic_server = server
        return gateway, transport

    @staticmethod
    def exposed(gateway):
        return set(gateway._registered_tool_registry.snapshot())

    def optional(self, gateway):
        return gateway.health_snapshot()["optional_tools"]

    def assert_reader_only_refused(self, gateway, transport, stock, state):
        names = self.exposed(gateway)
        self.assertEqual(self.optional(gateway)[TOOL], state, gateway.health_snapshot())
        self.assertNotIn(TOOL, names)
        self.assertFalse(set(y.SIBLING_TOOLS) & names)
        self.assertEqual(names, stock)
        self.assertEqual(transport.sent, [])


# ---------------------------------------------------------------------------
# Group 10: admission and descriptor binding (R4).


def _set(path, value):
    def change(tool):
        current = tool
        for name in path[:-1]:
            current = current.setdefault(name, {})
        current[path[-1]] = value
    return change


def _drop(name):
    return lambda tool: tool.pop(name)


DRIFT = (
    ("title_changed", _set(("title",), "Read YAML Config Fragment (synthetic)"), "optional_title_changed"),
    ("title_removed", _drop("title"), "optional_descriptor_fields_changed"),
    ("description_changed", lambda t: t.update(description=t["description"] + " synthetic"),
     "optional_description_changed"),
    ("description_removed", _drop("description"), "optional_descriptor_fields_changed"),
    ("input_property_added", _set(("inputSchema", "properties", "synthetic"), {"type": "string"}),
     "optional_input_schema_changed"),
    ("input_removed", _drop("inputSchema"), "optional_descriptor_fields_changed"),
    ("output_changed", _set(("outputSchema", "synthetic"), True), "optional_output_schema_changed"),
    ("output_removed", _drop("outputSchema"), "optional_descriptor_fields_changed"),
    ("read_only_hint_false", _set(("annotations", "readOnlyHint"), False), "optional_annotations_changed"),
    ("destructive_hint_added", _set(("annotations", "destructiveHint"), True), "optional_annotations_changed"),
    ("annotation_title_changed", _set(("annotations", "title"), "Synthetic"), "optional_annotations_changed"),
    ("annotations_removed", _drop("annotations"), "optional_descriptor_fields_changed"),
    ("meta_removed", _drop("_meta"), "optional_descriptor_fields_changed"),
    ("meta_fastmcp_tags", _set(("_meta", "fastmcp", "tags"), ["System"]), "optional_meta_changed"),
    ("meta_fastmcp_added", _set(("_meta", "fastmcp", "synthetic"), 1), "optional_meta_changed"),
    ("meta_ha_mcp_pinned", _set(("_meta", "ha_mcp", "pinned"), True), "optional_meta_changed"),
    ("meta_ha_mcp_exposed", _set(("_meta", "ha_mcp", "llm_api_exposed"), True), "optional_meta_changed"),
    ("meta_ha_mcp_added", _set(("_meta", "ha_mcp", "synthetic"), "x"), "optional_meta_changed"),
    ("meta_top_added", _set(("_meta", "synthetic"), {}), "optional_meta_changed"),
    ("execution_added", _set(("execution",), {"taskSupport": "forbidden"}),
     "optional_descriptor_fields_changed"),
    ("execution_task_optional", _set(("execution",), {"taskSupport": "optional"}),
     "optional_descriptor_fields_changed"),
    ("execution_task_required", _set(("execution",), {"taskSupport": "required"}),
     "optional_descriptor_fields_changed"),
    ("icons_added", _set(("icons",), [{"src": "data:,"}]), "optional_descriptor_fields_changed"),
    ("unknown_field", _set(("x-synthetic-future",), 1), "optional_descriptor_fields_changed"),
    ("policy_out_of_shape_rules", _set(("_meta", "ha_mcp", "policy", "rules"), -1), "optional_meta_changed"),
    ("policy_out_of_shape_bool", _set(("_meta", "ha_mcp", "policy", "rules"), True), "optional_meta_changed"),
    ("policy_out_of_shape_deployment", _set(("_meta", "ha_mcp", "policy", "deployment"), "synthetic"),
     "optional_meta_changed"),
    ("policy_out_of_shape_key", _set(("_meta", "ha_mcp", "policy", "synthetic"), 1), "optional_meta_changed"),
)

POLICY_IN_SHAPE = (
    ("addon", _set(("_meta", "ha_mcp", "policy", "deployment"), "addon")),
    ("enabled", _set(("_meta", "ha_mcp", "policy", "enabled"), True)),
    ("live", _set(("_meta", "ha_mcp", "policy", "live"), True)),
    ("rules", _set(("_meta", "ha_mcp", "policy", "rules"), 3)),
)


class AdmissionTests(Harness):
    async def stock_names(self, version, mode="A"):
        gateway, _ = await self.gateway(version, stock_catalog(version), mode=mode)
        return self.exposed(gateway)

    async def test_stock_catalog_is_optional_absent_with_87_public_tools(self):
        self.assertEqual(ENGINEERING_STATIC_TOOL_COUNT, 68)
        for version in VERSIONS:
            for mode in ("A", "B"):
                with self.subTest(version=version, mode=mode):
                    gateway, transport = await self.gateway(version, stock_catalog(version), mode=mode)
                    health = gateway.health_snapshot()
                    self.assertEqual(health["optional_tools"],
                                     {name: "optional_absent" for name in OPTIONAL})
                    self.assertEqual(health["quarantine_reason_counts"], {})
                    self.assertEqual(health["optional_sibling_drift"], [])
                    self.assertEqual(health["upstream_advertised_tool_count"], STOCK_UPSTREAM[version])
                    self.assertEqual(health["reviewed_policy_entry_count"], STOCK_UPSTREAM[version])
                    self.assertTrue(health["reviewed_tool_accounting_valid"])
                    self.assertEqual(ENGINEERING_STATIC_TOOL_COUNT + len(self.exposed(gateway)), STOCK_PUBLIC)
                    self.assertNotIn(TOOL, self.exposed(gateway))
                    self.assertEqual(transport.sent, [])

    async def test_exact_catalog_admits_the_reader_for_88_public_tools(self):
        for version in VERSIONS:
            for mode in ("A", "B"):
                with self.subTest(version=version, mode=mode):
                    stock = await self.stock_names(version, mode)
                    gateway, transport = await self.gateway(version, mode=mode)
                    health = gateway.health_snapshot()
                    self.assertEqual(health["optional_tools"], {
                        TOOL: "admitted", "ha_list_files": "prohibited_withheld",
                        "ha_read_file": "prohibited_withheld",
                    })
                    self.assertTrue(health["optional_reviewed_accounting_valid"])
                    # K9: 78 -> 81 (8.4.3) and 77 -> 80 (8.5.0). The stock
                    # field keeps its meaning (design §5.2, D3): the three
                    # optional names are outside the policy, so it reports
                    # false exactly as it did before HAMCP-162; the optional
                    # field above accounts for them.
                    self.assertEqual(health["upstream_advertised_tool_count"], STOCK_UPSTREAM[version] + 3)
                    self.assertEqual(health["reviewed_policy_entry_count"], STOCK_UPSTREAM[version])
                    self.assertFalse(health["reviewed_tool_accounting_valid"])
                    self.assertEqual(health["optional_sibling_drift"], [])
                    self.assertEqual(self.exposed(gateway), stock | {TOOL})
                    self.assertEqual(ENGINEERING_STATIC_TOOL_COUNT + len(self.exposed(gateway)), STOCK_PUBLIC + 1)
                    self.assertEqual(transport.sent, [])
                    result = await call(gateway, TOOL, {"yaml_path": "recorder"})
                    self.assertTrue(result["success"], result)
                    self.assertEqual(result["data"]["value"], {"purge_keep_days": 10})
                    self.assertEqual(transport.sent, [(TOOL, y.upstream_arguments("recorder"))])
                    self.assertEqual(transport.sent[0][1]["file"], "configuration.yaml")

    async def test_repeated_captures_reproduce_the_binding(self):
        # A2, B, S and S2 are later captures of the same versions (D1).
        for version in VERSIONS:
            binding = y.OPTIONAL_TOOL_BINDINGS[(version, TOOL)]
            for label in ("A", "A2", "B", "S", "S2"):
                with self.subTest(version=version, capture=label):
                    tool = descriptor(captured(version, label))
                    self.assertEqual(
                        runtime_contract_fingerprint(tool, model=binding.runtime_contract_fingerprint_model),
                        binding.runtime_contract_fingerprint)
                    self.assertEqual(tuple(sorted(tool)), binding.top_level_fields)
                    gateway, _ = await self.gateway(version, captured(version, label))
                    self.assertEqual(self.optional(gateway)[TOOL], "admitted")

    async def test_every_descriptor_mutation_quarantines_only_the_reader(self):
        for version in VERSIONS:
            stock = await self.stock_names(version)
            for name, change, reason in DRIFT:
                with self.subTest(version=version, mutation=name):
                    self.assertEqual(y.match_descriptor(with_reader(version, change),
                                                        y.OPTIONAL_TOOL_BINDINGS[(version, TOOL)]).reason, reason)
                    gateway, transport = await self.gateway(version, with_reader(version, change))
                    self.assert_reader_only_refused(gateway, transport, stock, f"quarantined:{reason}")
                    result = await call(gateway, HEALTH, {"sections": ["repairs"]})
                    self.assertTrue(result["success"], result)
                    self.assertEqual([tool for tool, _ in transport.sent], [HEALTH])

    async def test_descriptor_mutations_quarantine_in_signed_mode(self):
        stock = await self.stock_names("8.5.0", "B")
        for name, change, reason in DRIFT[::3]:
            with self.subTest(mutation=name):
                gateway, transport = await self.gateway("8.5.0", with_reader("8.5.0", change), mode="B")
                self.assert_reader_only_refused(gateway, transport, stock, f"quarantined:{reason}")

    async def test_renamed_reader_is_absent_and_never_dispatched(self):
        stock = await self.stock_names("8.5.0")
        gateway, transport = await self.gateway(
            "8.5.0", with_reader("8.5.0", _set(("name",), "ha_config_get_yaml_v2")))
        self.assert_reader_only_refused(gateway, transport, stock, "optional_absent")
        self.assertNotIn("ha_config_get_yaml_v2", self.exposed(gateway))

    async def test_policy_state_within_the_reviewed_shape_does_not_quarantine(self):
        for version in VERSIONS:
            for name, change in POLICY_IN_SHAPE:
                with self.subTest(version=version, state=name):
                    gateway, _ = await self.gateway(version, with_reader(version, change))
                    self.assertEqual(self.optional(gateway)[TOOL], "admitted")
                    self.assertIn(TOOL, self.exposed(gateway))

    async def test_duplicate_quarantines_only_the_reader(self):
        for version in VERSIONS:
            stock = await self.stock_names(version)
            tools = captured(version)
            tools.append(deepcopy(descriptor(tools)))
            with self.subTest(version=version):
                gateway, transport = await self.gateway(version, tools)
                self.assert_reader_only_refused(gateway, transport, stock,
                                                "quarantined:optional_descriptor_duplicate")
                self.assertFalse(gateway.health_snapshot()["optional_reviewed_accounting_valid"])

    async def test_source_commit_mismatch_withholds_only_the_reader(self):
        stock = await self.stock_names("8.5.0")
        binding = y.OPTIONAL_TOOL_BINDINGS[("8.5.0", TOOL)]
        with patch.dict(y.OPTIONAL_TOOL_BINDINGS,
                        {("8.5.0", TOOL): replace(binding, source_commit="0" * 40)}):
            gateway, transport = await self.gateway("8.5.0")
        self.assert_reader_only_refused(gateway, transport, stock,
                                        "withheld:optional_source_commit_mismatch")

    async def test_held_disposition_refuses(self):
        for version in VERSIONS:
            stock = await self.stock_names(version)
            with self.subTest(version=version), patch.dict(y.OPTIONAL_TOOL_DISPOSITIONS, {version: "held"}):
                gateway, transport = await self.gateway(version)
                self.assert_reader_only_refused(gateway, transport, stock, "held")
                selection = await self.selection(version)
                self.assertIsNone(selection.optional_extension)
                self.assertEqual(selection.optional_withheld_reason, "optional_disposition_held")

    async def selection(self, version):
        gateway, _ = await self.gateway(version, mode="B")
        return gateway._readmission_selector.select(
            server_name="ha-mcp", version=version, protocol_version="2025-03-26")

    def test_read_gateway_held_and_every_unbound_version_withhold(self):
        for version in VERSIONS:
            release = self.compiled.by_version[version]
            held = replace(release, provider_dispositions=tuple(
                (surface, "held" if surface == "read_gateway" else value)
                for surface, value in release.provider_dispositions))
            self.assertEqual(y.withheld_reason(release, signed_selection=False), None)
            self.assertEqual(y.withheld_reason(held, signed_selection=False), "optional_disposition_held")
            self.assertEqual(y.evaluate_optional(captured(version), held).status, "held")
            self.assertEqual(y.withheld_reason(release, signed_selection=True), "optional_signed_selection")
        self.assertEqual(y.withheld_reason(None, signed_selection=False), "optional_binding_absent")
        for release in self.compiled.releases:
            if release.version in VERSIONS:
                continue
            with self.subTest(version=release.version):
                self.assertEqual(y.OPTIONAL_TOOL_DISPOSITIONS[release.version], "held")
                self.assertEqual(y.withheld_reason(release, signed_selection=False),
                                 "optional_disposition_held")
        with patch.dict(y.OPTIONAL_TOOL_BINDINGS):
            del y.OPTIONAL_TOOL_BINDINGS[("8.5.0", TOOL)]
            self.assertEqual(y.withheld_reason(self.compiled.by_version["8.5.0"], signed_selection=False),
                             "optional_binding_absent")

    async def test_read_gateway_held_withholds_through_the_gateway(self):
        release = self.compiled.by_version["8.5.0"]
        held = replace(release, provider_dispositions=tuple(
            (surface, "held" if surface == "read_gateway" else value)
            for surface, value in release.provider_dispositions))
        registry = replace(self.compiled, releases=tuple(
            held if item.version == "8.5.0" else item for item in self.compiled.releases))
        with patch("ha_mcp_engineering.providers.upstream_read_gateway.load_reviewed_upstream_release_registry",
                   return_value=registry):
            gateway, transport = await self.gateway("8.5.0")
        self.assertNotIn(TOOL, self.exposed(gateway))
        self.assertEqual(transport.sent, [])

    async def test_revoked_release_refuses_with_zero_calls(self):
        for version in VERSIONS:
            with self.subTest(version=version):
                gateway, transport = await self.gateway(version, mode="B", revoked=(version,))
                health = gateway.health_snapshot()
                self.assertEqual(health["readmission_authority_source"], "signed_registry")
                self.assertEqual(health["optional_tools"][TOOL], "withheld:optional_signed_selection")
                self.assertEqual(self.exposed(gateway), set())
                self.assertEqual(transport.sent, [])

    def test_load_assertions_fail_closed(self):
        registry = self.compiled
        y.validate_bindings_against_registry(registry)
        binding = y.OPTIONAL_TOOL_BINDINGS[("8.5.0", TOOL)]
        cases = (
            ("optional_disposition_versions_mismatch",
             {"dispositions": {k: v for k, v in y.OPTIONAL_TOOL_DISPOSITIONS.items() if k != "8.6.0"}}),
            ("optional_disposition_versions_mismatch",
             {"dispositions": {**y.OPTIONAL_TOOL_DISPOSITIONS, "9.9.9": "held"}}),
            ("optional_disposition_invalid",
             {"dispositions": {**y.OPTIONAL_TOOL_DISPOSITIONS, "8.6.0": "default"}}),
            ("optional_binding_version_unknown",
             {"bindings": {("9.9.9", TOOL): replace(binding, version="9.9.9")}}),
            ("optional_binding_version_unknown",
             {"bindings": {("8.4.3", TOOL): binding}}),
            ("optional_binding_model_mismatch",
             {"bindings": {("8.5.0", TOOL): replace(
                 binding, runtime_contract_fingerprint_model="ha-mcp-operational-tool-descriptor-v1")}}),
            ("optional_binding_model_invalid",
             {"bindings": {("8.5.0", TOOL): replace(binding, binding_model="synthetic-model")}}),
            ("optional_admitted_binding_absent",
             {"dispositions": {**y.OPTIONAL_TOOL_DISPOSITIONS, "8.4.1": "admitted"}}),
        )
        for code, change in cases:
            with self.subTest(code=code, change=sorted(change)):
                with self.patched_tables(**change):
                    with self.assertRaisesRegex(y.OptionalBindingError, f"^{code}$"):
                        y.validate_bindings_against_registry(registry)
                    # The shipped registry load is the assertion point.
                    with self.assertRaisesRegex(UpstreamToolPolicyError, f"^{code}$"):
                        load_reviewed_upstream_release_registry(RELEASE_REGISTRY_PATH)

    @contextmanager
    def patched_tables(self, dispositions=None, bindings=None):
        with patch.dict(y.OPTIONAL_TOOL_DISPOSITIONS, clear=dispositions is not None), \
                patch.dict(y.OPTIONAL_TOOL_BINDINGS):
            if dispositions is not None:
                y.OPTIONAL_TOOL_DISPOSITIONS.update(dispositions)
            if bindings is not None:
                y.OPTIONAL_TOOL_BINDINGS.update(bindings)
            yield

    def test_an_unlisted_version_is_never_default_admitted(self):
        # HAMCP-184 rule: a version missing from the disposition table is
        # held, even with an otherwise matching binding for it.
        release = replace(self.compiled.by_version["8.5.0"], version="9.9.9")
        binding = y.OPTIONAL_TOOL_BINDINGS[("8.5.0", TOOL)]
        with self.patched_tables(bindings={("9.9.9", TOOL): binding}):
            self.assertNotIn("9.9.9", y.OPTIONAL_TOOL_DISPOSITIONS)
            self.assertEqual(y.withheld_reason(release, signed_selection=False),
                             "optional_disposition_held")
            self.assertEqual(y.evaluate_optional(captured("8.5.0"), release).status, "held")

    def test_every_registry_version_has_an_explicit_disposition(self):
        self.assertEqual(set(y.OPTIONAL_TOOL_DISPOSITIONS), {r.version for r in self.compiled.releases})
        self.assertEqual({v for v, d in y.OPTIONAL_TOOL_DISPOSITIONS.items() if d == "admitted"},
                         set(VERSIONS))
        self.assertEqual(y.OPTIONAL_TOOL_DISPOSITIONS["8.6.0"], "held")

    def test_captures_are_pinned_with_provenance(self):
        for version in VERSIONS:
            for tool in OPTIONAL:
                binding = y.OPTIONAL_TOOL_BINDINGS[(version, tool)]
                with self.subTest(version=version, tool=tool):
                    raw = (ROOT / binding.capture_resource).read_bytes()
                    self.assertEqual(sha256(raw).hexdigest(), binding.capture_sha256)
                    self.assertEqual(binding.source_commit, self.compiled.by_version[version].source_commit)
                    self.assertEqual(json.loads(raw)["initialize_result"]["serverInfo"]["version"], version)
                    observed = next(t for t in json.loads(raw)["tools"] if t["name"] == tool)
                    self.assertEqual(runtime_contract_fingerprint(observed, model=binding.runtime_contract_fingerprint_model),
                                     binding.runtime_contract_fingerprint)
                    self.assertEqual(binding.binding_model, y.BINDING_MODEL)
                    self.assertEqual(binding.adapter_token is not None, tool == TOOL)
                    self.assertEqual(binding.classification,
                                     "mixed_or_requires_wrapper" if tool == TOOL else "prohibited")
        manifest = (EVIDENCE / "MANIFEST.sha256").read_text()
        for version in VERSIONS:
            digest = y.OPTIONAL_TOOL_BINDINGS[(version, TOOL)].capture_sha256
            self.assertIn(f"{digest}  ./captures/catalog-{version}-A.json", manifest)

    async def test_siblings_are_never_exposed_or_dispatched(self):
        drifted_siblings = captured("8.5.0")
        for item in drifted_siblings:
            if item["name"] in y.SIBLING_TOOLS:
                item["description"] += " synthetic"
        states = (
            ("admitted", "A", captured("8.5.0"), ()),
            ("quarantined", "A", with_reader("8.5.0", _drop("title")), ()),
            ("sibling_drift", "A", drifted_siblings, ()),
            ("signed", "B", captured("8.5.0"), ("8.5.0",)),
            ("compiled", "B", captured("8.5.0"), ()),
        )
        for name, mode, tools, revoked in states:
            with self.subTest(state=name):
                gateway, transport = await self.gateway("8.5.0", tools, mode=mode, revoked=revoked)
                self.assertFalse(set(y.SIBLING_TOOLS) & self.exposed(gateway))
                for sibling in y.SIBLING_TOOLS:
                    self.assertEqual(self.optional(gateway)[sibling], "prohibited_withheld")
                self.assertEqual(transport.sent, [])
        gateway, _ = await self.gateway("8.5.0", drifted_siblings)
        self.assertEqual(self.optional(gateway)[TOOL], "admitted")
        self.assertEqual(sorted(tool for tool, _ in gateway.health_snapshot()["optional_sibling_drift"])
                         if gateway.health_snapshot()["optional_sibling_drift"]
                         and isinstance(gateway.health_snapshot()["optional_sibling_drift"][0], (list, tuple))
                         else sorted(item["tool"] for item in gateway.health_snapshot()["optional_sibling_drift"]),
                         sorted(y.SIBLING_TOOLS))
        for sibling in y.SIBLING_TOOLS:
            with self.assertRaises(y.OptionalBindingError):
                y.synthesized_policy_entry(y.OPTIONAL_TOOL_BINDINGS[("8.5.0", sibling)])


# ---------------------------------------------------------------------------
# Group 11: generation and lease (R5).


class GenerationLeaseTests(Harness):
    async def test_compiled_selections_read_through_a_committed_lease(self):
        for version in VERSIONS:
            with self.subTest(version=version):
                gateway, transport = await self.gateway(version, mode="B")
                coordinator = gateway._readmission_coordinator
                selection = gateway._readmission_selector.select(
                    server_name="ha-mcp", version=version, protocol_version="2025-03-26")
                self.assertEqual(selection.authority_source.value, "compiled_exact")
                self.assertIsNotNone(selection.optional_extension)
                self.assertIsNone(selection.optional_withheld_reason)
                generation = coordinator.generation_for(UpstreamSurface.HA_MCP)
                decision = generation.decision_for(TOOL)
                self.assertTrue(decision.disposition.admitted)
                self.assertEqual(decision.profile_id, selection.optional_extension.profile_id)
                result = await call(gateway, TOOL, {"yaml_path": "recorder"})
                self.assertTrue(result["success"], result)
                self.assertEqual(len(transport.sent), 1)
                health = coordinator.health_projection()
                self.assertEqual(health["active_commit_count"] if "active_commit_count" in health
                                 else sum(s["active_commit_count"] for s in health["surfaces"]), 0)

    async def test_extension_changes_the_authority_token_and_not_the_stock_profile(self):
        for version in VERSIONS:
            with self.subTest(version=version):
                admitted = await AdmissionTests.selection(self, version)
                with patch.dict(y.OPTIONAL_TOOL_DISPOSITIONS, {version: "held"}):
                    held = await AdmissionTests.selection(self, version)
                self.assertEqual(admitted.profile.to_mapping(), held.profile.to_mapping())
                self.assertEqual(admitted.profile.profile_id, held.profile.profile_id)
                self.assertNotEqual(admitted.authority_token, held.authority_token)
                self.assertEqual(admitted.authority.fingerprint != held.authority.fingerprint, True)
                self.assertEqual(held.profiles, (held.profile,))
                self.assertEqual(admitted.profiles, (admitted.profile, admitted.optional_extension))
                self.assertTrue(admitted.optional_extension.profile_id.startswith(f"ha-mcp-optional-yaml-{version}-"))

    async def test_authority_token_binds_the_extension_itself(self):
        admitted = await AdmissionTests.selection(self, "8.5.0")
        extension = admitted.optional_extension
        stock_only = replace(admitted, optional_extension=None)
        # Without an extension the token is the stock material, byte-identical.
        self.assertEqual(stock_only.authority_token, evidence_fingerprint({
            "profile": admitted.profile.to_mapping(),
            "authority": admitted.authority.material_mapping(),
        }))
        self.assertNotEqual(admitted.authority_token, stock_only.authority_token)
        # Changing only the extension, with the same authority, changes it.
        changed = replace(admitted, optional_extension=replace(
            extension, profile_version=extension.profile_version + 1))
        self.assertEqual(changed.authority.fingerprint, admitted.authority.fingerprint)
        self.assertNotEqual(changed.authority_token, admitted.authority_token)

    async def test_withheld_selections_report_the_reason(self):
        for change, reason in (
            ({"dispositions": {"8.5.0": "held"}}, "optional_disposition_held"),
            ({"commit": "0" * 40}, "optional_source_commit_mismatch"),
        ):
            with self.subTest(reason=reason):
                binding = y.OPTIONAL_TOOL_BINDINGS[("8.5.0", TOOL)]
                with patch.dict(y.OPTIONAL_TOOL_DISPOSITIONS, change.get("dispositions", {})), \
                        patch.dict(y.OPTIONAL_TOOL_BINDINGS, {("8.5.0", TOOL): replace(
                            binding, source_commit=change.get("commit", binding.source_commit))}):
                    selection = await AdmissionTests.selection(self, "8.5.0")
                    gateway, transport = await self.gateway("8.5.0", mode="B")
                self.assertIsNone(selection.optional_extension)
                self.assertEqual(selection.optional_withheld_reason, reason)
                self.assertNotIn(TOOL, self.exposed(gateway))
                # An observed reader outside every installed profile gets the
                # coordinator's existing non-admitted decision; no route lease.
                decision = gateway._readmission_coordinator.generation_for(
                    UpstreamSurface.HA_MCP).decision_for(TOOL)
                self.assertFalse(decision.disposition.admitted)
                self.assertIsNone(decision.profile_id)
                self.assertIsNone(gateway._readmission_coordinator.acquire_route(TOOL, session_id=SESSION))
                self.assertEqual(transport.sent, [])
        # An unknown version is refused as a whole, before any optional state.
        gateway, transport = await self.gateway("8.5.0", captured("8.5.0"), mode="B")
        transport.catalog = replace(transport.catalog, server_version="8.5.9")
        await gateway.initialize(gateway.synthetic_server)
        self.assertNotIn(TOOL, self.exposed(gateway))
        self.assertEqual(transport.sent, [])

    async def race(self, mode, hook, *, tool=TOOL, arguments=None, version="8.5.0"):
        gateway, transport = await self.gateway(version, mode=mode)
        transport.between = lambda: hook(gateway, transport)
        result = await call(gateway, tool, arguments or {"yaml_path": "recorder"})
        return gateway, transport, result

    @staticmethod
    async def reinitialize(gateway, transport, tools=None, *, session_id=None):
        if tools is not None or session_id is not None:
            transport.set_tools(tools if tools is not None else transport.catalog.tools,
                                session_id=session_id)
        await gateway.initialize(gateway.synthetic_server)

    async def test_zero_dispatch_after_changes_between_admission_and_dispatch(self):
        binding = y.OPTIONAL_TOOL_BINDINGS[("8.5.0", TOOL)]

        async def session_change(g, t):
            await self.reinitialize(g, t, session_id="synthetic-ha-mcp-session-2")

        async def catalog_refresh_reader_removed(g, t):
            await self.reinitialize(g, t, stock_catalog("8.5.0"))

        async def catalog_refresh_stock_drift(g, t):
            tools = captured("8.5.0")
            next(item for item in tools if item["name"] == HEALTH)["description"] += " synthetic"
            await self.reinitialize(g, t, tools)

        async def optional_drift(g, t):
            await self.reinitialize(g, t, with_reader("8.5.0", _drop("title")))

        async def disposition_change(g, t):
            with patch.dict(y.OPTIONAL_TOOL_DISPOSITIONS, {"8.5.0": "held"}):
                await self.reinitialize(g, t)

        async def extension_change(g, t):
            with patch.dict(y.OPTIONAL_TOOL_BINDINGS,
                            {("8.5.0", TOOL): replace(binding, source_commit="0" * 40)}):
                await self.reinitialize(g, t)

        async def authority_retirement(g, t):
            self.assertIsNotNone(g._readmission_coordinator.retire_surface_authority(UpstreamSurface.HA_MCP))

        # Mode A has no coordinator: its linearization point is route
        # identity, so only changes that replace the reader's route apply.
        # Mode B adds the session-bound lease and generation retirement.
        cases = {
            "A": (catalog_refresh_reader_removed, optional_drift, disposition_change, extension_change),
            "B": (session_change, catalog_refresh_reader_removed, catalog_refresh_stock_drift,
                  optional_drift, disposition_change, extension_change, authority_retirement),
        }
        for mode, hooks in cases.items():
            for hook in hooks:
                with self.subTest(mode=mode, change=hook.__name__):
                    gateway, transport, result = await self.race(mode, hook)
                    self.assertFalse(result.get("success"), result)
                    self.assertNotIn("data", result)
                    self.assertEqual(transport.sent, [], result)
                    self.assertFalse(result["metadata"]["fallback_occurred"])

    async def test_route_preserving_refreshes_treat_the_reader_like_a_stock_read(self):
        # Existing semantics: a refresh that leaves the route unchanged (an
        # identical catalog, or in Mode A a new session) keeps the in-flight
        # route. The reader gets exactly the stock behaviour, no more.
        async def identical_refresh(g, t):
            await self.reinitialize(g, t, captured("8.5.0", "A2"))

        async def session_change(g, t):
            await self.reinitialize(g, t, session_id="synthetic-ha-mcp-session-2")

        for mode, hook in (("A", identical_refresh), ("B", identical_refresh), ("A", session_change)):
            outcomes = []
            for tool, arguments in ((TOOL, {"yaml_path": "recorder"}), (HEALTH, {"sections": ["repairs"]})):
                with self.subTest(mode=mode, change=hook.__name__, tool=tool):
                    _, transport, result = await self.race(mode, hook, tool=tool, arguments=arguments)
                    outcomes.append((bool(result.get("success")), len(transport.sent)))
            self.assertEqual(outcomes[0], outcomes[1], (mode, hook.__name__))
            self.assertEqual(outcomes[0], (True, 1))

    async def test_leases_are_single_use_session_bound_and_retired(self):
        gateway, transport = await self.gateway("8.5.0", mode="B")
        coordinator = gateway._readmission_coordinator
        self.assertIsNone(coordinator.acquire_route(TOOL, session_id="synthetic-foreign-session"))
        lease = coordinator.acquire_route(TOOL, session_id=SESSION)
        self.assertIsNotNone(lease)
        self.assertIsNone(coordinator.commit_route(lease, session_id="synthetic-foreign-session"))
        lease = coordinator.acquire_route(TOOL, session_id=SESSION)
        commit = coordinator.commit_route(lease, session_id=SESSION)
        self.assertIsNotNone(commit)
        self.assertIsNone(coordinator.commit_route(lease, session_id=SESSION))  # replayed
        self.assertTrue(coordinator.finish_committed(commit))
        retired = coordinator.acquire_route(TOOL, session_id=SESSION)
        await self.reinitialize(gateway, transport, with_reader("8.5.0", _drop("title")))
        self.assertIsNone(coordinator.commit_route(retired, session_id=SESSION))
        self.assertIsNone(coordinator.acquire_route(TOOL, session_id=SESSION))
        self.assertEqual(transport.sent, [])

    async def test_result_after_retirement_does_not_readmit_or_reexpose(self):
        for mode in ("A", "B"):
            with self.subTest(mode=mode):
                gateway, transport = await self.gateway("8.5.0", mode=mode)

                async def drift():
                    await self.reinitialize(gateway, transport, with_reader("8.5.0", _drop("title")))

                transport.during = drift
                await call(gateway, TOOL, {"yaml_path": "recorder"})
                self.assertEqual(len(transport.sent), 1)
                self.assertNotIn(TOOL, self.exposed(gateway))
                self.assertEqual(self.optional(gateway)[TOOL], "quarantined:optional_descriptor_fields_changed")
                if mode == "B":
                    decision = gateway._readmission_coordinator.generation_for(
                        UpstreamSurface.HA_MCP).decision_for(TOOL)
                    self.assertFalse(decision.disposition.admitted)
                    self.assertIsNone(gateway._readmission_coordinator.acquire_route(TOOL, session_id=SESSION))

    async def test_stock_aggregate_stays_admitted_exact_while_the_reader_is_quarantined(self):
        for version in VERSIONS:
            with self.subTest(version=version):
                stock, _ = await self.gateway(version, stock_catalog(version), mode="B")
                admitted, _ = await self.gateway(version, mode="B")
                drifted, _ = await self.gateway(version, with_reader(version, _drop("title")), mode="B")
                for gateway in (stock, admitted, drifted):
                    generation = gateway._readmission_coordinator.generation_for(UpstreamSurface.HA_MCP)
                    self.assertIs(generation.disposition, AdmissionDisposition.ADMITTED_EXACT)
                    surface, = (item for item in gateway._readmission_coordinator.health_projection()["surfaces"]
                                if item["surface"] == UpstreamSurface.HA_MCP.value)
                    self.assertEqual(surface["disposition"], "admitted_exact")
                decision = drifted._readmission_coordinator.generation_for(
                    UpstreamSurface.HA_MCP).decision_for(TOOL)
                self.assertFalse(decision.disposition.admitted)
                self.assertEqual(self.exposed(drifted), self.exposed(stock))

    async def test_inflight_stock_lease_across_an_optional_drift_generation(self):
        async def drift(g, t):
            await self.reinitialize(g, t, with_reader("8.5.0", _drop("title")))

        gateway, transport, result = await self.race("B", drift, tool=HEALTH,
                                                    arguments={"sections": ["repairs"]})
        self.assertFalse(result.get("success"), result)
        self.assertEqual(transport.sent, [])
        result = await call(gateway, HEALTH, {"sections": ["repairs"]})
        self.assertTrue(result["success"], result)
        self.assertEqual([tool for tool, _ in transport.sent], [HEALTH])


# ---------------------------------------------------------------------------
# Group 12: invariants unchanged.


class InvariantTests(Harness):
    def test_order_fingerprints_cover_only_stock_names(self):
        for version in VERSIONS:
            with self.subTest(version=version):
                stock_order = [t["name"] for t in stock_catalog(version)]
                exact_order = [t["name"] for t in captured(version) if t["name"] not in OPTIONAL]
                self.assertEqual(exact_order, stock_order)
                self.assertEqual(schema_fingerprint(stock_order), EXACT_RUNTIME_TOOL_ORDER_FINGERPRINTS[version])
                self.assertNotEqual(schema_fingerprint([t["name"] for t in captured(version)]),
                                    EXACT_RUNTIME_TOOL_ORDER_FINGERPRINTS[version])

    def test_policy_and_registry_documents_hold_no_optional_name(self):
        registry_text = RELEASE_REGISTRY_PATH.read_text()
        for name in OPTIONAL:
            self.assertNotIn(name, registry_text)
        for release in self.compiled.releases:
            with self.subTest(version=release.version):
                raw = (ROOT / release.policy_resource).read_bytes() if (ROOT / release.policy_resource).exists() \
                    else (RELEASE_REGISTRY_PATH.parent / release.policy_resource).read_bytes()
                self.assertEqual("sha256:" + sha256(raw).hexdigest(), release.policy_sha256)
                self.assertFalse(set(OPTIONAL) & set(release.policy.by_name))
                for name in OPTIONAL:
                    self.assertNotIn(name.encode(), raw)

    def test_whole_catalog_validator_still_refuses_the_exact_catalog(self):
        for version in VERSIONS:
            release = self.compiled.by_version[version]
            for tools, valid, extra in ((stock_catalog(version), True, 0), (captured(version), False, 3)):
                with self.subTest(version=version, count=len(tools)):
                    result = validate_reviewed_release_catalog(
                        release, observed_server_name="ha-mcp", observed_upstream_version=version,
                        observed_protocol_version="2025-03-26", tools=tools)
                    self.assertEqual(result.valid, valid)
                    self.assertEqual(result.additional_tool_count, extra)

    async def test_stock_reads_and_profiles_are_identical_with_or_without_the_reader(self):
        for version in VERSIONS:
            for mode in ("A", "B"):
                with self.subTest(version=version, mode=mode):
                    stock, _ = await self.gateway(version, stock_catalog(version), mode=mode)
                    admitted, _ = await self.gateway(version, mode=mode)
                    drifted, _ = await self.gateway(version, with_reader(version, _drop("title")), mode=mode)
                    self.assertEqual(self.exposed(admitted) - {TOOL}, self.exposed(stock))
                    self.assertEqual(self.exposed(drifted), self.exposed(stock))
                    for gateway in (admitted, drifted):
                        for name in self.exposed(stock):
                            self.assertEqual(gateway._exposed[name].entry, stock._exposed[name].entry)
                            self.assertEqual(gateway._exposed[name].contract_fingerprint,
                                             stock._exposed[name].contract_fingerprint)
                    if mode == "B":
                        profiles = {
                            g._readmission_selector.select(
                                server_name="ha-mcp", version=version,
                                protocol_version="2025-03-26").profile.to_mapping()
                            ["profile_id"]
                            for g in (stock, admitted, drifted)
                        }
                        self.assertEqual(len(profiles), 1)

    async def test_power_fan_and_dashboard_admission_are_unchanged(self):
        for version in VERSIONS:
            catalogs = {
                "stock": stock_catalog(version),
                "admitted": captured(version),
                "quarantined": with_reader(version, _drop("title")),
            }
            outcomes = {}
            for name, tools in catalogs.items():
                gateway, _ = await self.gateway(version, tools)
                health = gateway.health_snapshot()
                c = replace(gateway._transport.catalog) if hasattr(gateway, "_transport") else None
                provider_results = []
                for provider_type in (PowerProvider, FanProvider):
                    try:
                        provider_results.append(provider_type(None, lambda *a: "a").validate_catalog(
                            signed._GatewayTransport(tools, version=version).catalog))
                    except Exception as error:  # noqa: BLE001 - outcome comparison
                        provider_results.append(type(error).__name__)
                outcomes[name] = (
                    tuple(provider_results),
                    health["dashboard_attestation_status"],
                    gateway.power_provider_authority_token(version),
                )
                del c
            with self.subTest(version=version):
                self.assertEqual(outcomes["admitted"], outcomes["quarantined"])
                self.assertEqual(outcomes["admitted"][1:], outcomes["stock"][1:])


# ---------------------------------------------------------------------------
# Group 13: Core.


def synthetic_core_entry(version):
    return core_entry(version, typed_operations=True)


class CoreTests(Harness):
    asyncSetUp = core_continuity.CoreContinuityTests.asyncSetUp
    runtime = core_continuity.CoreContinuityTests.runtime
    next_journal = core_continuity.CoreContinuityTests.next_journal

    def test_the_reader_has_an_explicit_core_mapping(self):
        self.assertEqual(delegated_requirements(TOOL), y.CORE_REQUIREMENTS)
        self.assertEqual(DELEGATED_CORE_REQUIREMENTS[TOOL], ("core.basic_rest_read", "core.basic_websocket_read"))
        self.assertEqual(len(DELEGATED_CORE_REQUIREMENTS), 31)
        for sibling in y.SIBLING_TOOLS:
            self.assertNotIn(sibling, DELEGATED_CORE_REQUIREMENTS)

    async def october_runtime(self, version):
        """A signed October Core (the 2026.10 device-record probe profile)."""

        signer = october.CoreSigner()
        entry = synthetic_core_entry(version)
        entry.update(
            probe_profile_id=october.OCTOBER.profile_id,
            probe_profile_sha256=october.OCTOBER.contract_fingerprint,
            capabilities=[{k: p.to_mapping()[k] for k in (
                "capability_id", "profile_id", "profile_version", "adapter_id", "contract_fingerprint")}
                for p in october.CORE_RUNTIME_CAPABILITY_PROFILES],
        )
        raw = signer.journal_raw(entries=[entry], generated_at=october.NOW)

        async def fetch(*_args):
            return raw

        registry = october.CoreReleaseRegistry(
            enabled=True, public_key=signer.public_key_base64,
            cache_path=Path(self.temporary.name) / f"core-{version}.json", fetcher=fetch,
            now=lambda: october.NOW)
        self.assertTrue(await registry.refresh())

        class Source:
            async def capture_core_snapshot(self):
                evidence = october.capability_evidence_for_probes(**october.probe_inputs(
                    registry.probe_profile_for(version), version=version))
                return {"identity": october.CORE_IDENTITY, "connected": True, "authenticated": True,
                        "session_id": "synthetic-october-core", "rest_config": {"version": version},
                        "websocket_auth_ok": {"type": "auth_ok", "ha_version": version},
                        "websocket_get_config": {"version": version}, "capability_evidence": evidence}

        core = october.CoreRuntime()
        core.configure(object(), source=Source(), release_registry=registry)
        await core.reconcile_once("startup")
        return core

    async def test_both_supported_cores_admit_and_lease_each_read(self):
        for core_version in ("2026.9.2", "2026.10.0", "2026.10.1"):
            self.raw = self.signer.journal_raw(entries=[core_entry(core_version)])
            for mode in ("A", "B"):
                with self.subTest(core=core_version, mode=mode):
                    core = (await self.runtime(core_version))[0] if core_version == "2026.9.2" \
                        else await self.october_runtime(core_version)
                    gateway, transport = await self.gateway("8.5.0", mode=mode, core_runtime=core)
                    self.assertIn(TOOL, self.exposed(gateway))
                    result = await call(gateway, TOOL, {"yaml_path": "recorder"})
                    self.assertTrue(result["success"], result)
                    self.assertEqual(len(transport.sent), 1)
                    health = core.health_snapshot()
                    self.assertEqual((health["active_commit_count"], health["issued_lease_count"]), (0, 0))

    async def test_unknown_core_withholds_the_reader_with_zero_calls(self):
        core, _ = await self.runtime("2026.9.3")
        for mode in ("A", "B"):
            with self.subTest(mode=mode):
                gateway, transport = await self.gateway("8.5.0", mode=mode, core_runtime=core)
                self.assertNotIn(TOOL, self.exposed(gateway))
                health = gateway.health_snapshot()
                self.assertIn(TOOL, {item["tool"] for item in health["core_withheld_tools"]})
                self.assertEqual(self.optional(gateway)[TOOL], "withheld:core_unavailable")
                self.assertEqual(transport.sent, [])

    async def test_unmapped_reader_is_withheld_not_ungated(self):
        core, _ = await self.runtime()
        with patch.dict(DELEGATED_CORE_REQUIREMENTS):
            del DELEGATED_CORE_REQUIREMENTS[TOOL]
            gateway, transport = await self.gateway("8.5.0", core_runtime=core)
        self.assertNotIn(TOOL, self.exposed(gateway))
        self.assertEqual(self.optional(gateway)[TOOL], "withheld:core_requirement_unmapped")
        self.assertEqual(transport.sent, [])

    async def test_core_retirement_before_dispatch_refuses_with_zero_calls(self):
        core, _ = await self.runtime()
        gateway, transport = await self.gateway("8.5.0", core_runtime=core)
        self.assertIn(TOOL, self.exposed(gateway))

        async def retire():
            self.raw = self.next_journal(entries=[], revocations=[core_revocation()])
            self.assertTrue(await self.registry.refresh())

        transport.between = retire
        result = await call(gateway, TOOL, {"yaml_path": "recorder"})
        self.assertFalse(result.get("success"), result)
        self.assertEqual(transport.sent, [])


if __name__ == "__main__":
    unittest.main()
