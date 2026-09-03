from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from jsonschema import validate


ROOT = Path(__file__).resolve().parents[1]
BETA = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA))


def _load_script(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"{name}_test_module", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


addon_acceptance = _load_script("exact_addon_runtime_acceptance")
gateway_acceptance = _load_script("exact_image_read_gateway_acceptance")
readmission = _load_script("exact_image_readmission_acceptance")
custom_component_shutdown = _load_script(
    "exact_custom_component_shutdown_acceptance"
)
fixture = _load_script("fake_ha_read_gateway_contract_server")
packaging_acceptance = _load_script("verify_ha_mcp_8_1_1_packaging")


class ExactAddonProfileTests(unittest.TestCase):
    def tearDown(self) -> None:
        addon_acceptance._select_exact_addon_profile("8.0.0")
        fixture.ADDON_DETAIL_PROFILE = "compact"
        fixture.INSTALLED_ADDONS[0]["version"] = "7.14.2"

    def test_exact_profiles_retain_8_0_and_bind_all_8_1_identities(self):
        self.assertEqual(
            set(addon_acceptance.EXACT_ADDON_PROFILES),
            {"8.0.0", "8.1.0", "8.1.1", "8.2.0", "8.4.1"},
        )

        addon_acceptance._select_exact_addon_profile("8.1.0")

        self.assertEqual(addon_acceptance.EXPECTED_UPSTREAM_VERSION, "8.1.0")
        self.assertEqual(
            addon_acceptance.EXPECTED_ENTRY_ID, "ha-mcp-v8.1.0-4c07e625"
        )
        self.assertEqual(
            addon_acceptance.EXPECTED_RAW_CATALOG_FINGERPRINT,
            "6b5cd123cc60ff6668c2ff4dd1f9cedbe6a7a21fe43fe00471cd46611d4406d7",
        )
        self.assertEqual(
            addon_acceptance.EXPECTED_NORMALIZED_CATALOG_FINGERPRINT,
            "5ec7b1f4a4c2ffabb2acc14c73a230f08a5f94908b6f27e57cb6739d662f03d7",
        )
        self.assertEqual(
            addon_acceptance.EXPECTED_DASHBOARD_RUNTIME_FINGERPRINT,
            "fb7f3789c8c020d8636a96b85a207635e94eefe9e0944c8814de59aba17e532e",
        )
        self.assertEqual(
            addon_acceptance.EXPECTED_ADDON_DETAIL_PROFILE, "live-8.1.0"
        )
        self.assertEqual(addon_acceptance.EXPECTED_AUTOMATIC_READ_COUNT, 24)
        self.assertEqual(
            addon_acceptance.EXPECTED_HELD_TOOLS,
            {"ha_get_operation_status", "ha_search"},
        )

        addon_acceptance._select_exact_addon_profile("8.1.1")
        self.assertEqual(
            addon_acceptance.EXPECTED_ENTRY_ID,
            "ha-mcp-v8.1.1-e1d76a6e",
        )
        self.assertEqual(
            addon_acceptance.EXPECTED_NORMALIZED_CATALOG_FINGERPRINT,
            "389c33d95537d93ad96d33f2859716611c60fa53313c6d56a598fb3c9034a82b",
        )
        self.assertEqual(addon_acceptance.EXPECTED_AUTOMATIC_READ_COUNT, 25)
        self.assertEqual(
            addon_acceptance.EXPECTED_HELD_TOOLS,
            {"ha_get_operation_status"},
        )

        addon_acceptance._select_exact_addon_profile("8.4.1")
        self.assertEqual(
            addon_acceptance.EXPECTED_ENTRY_ID,
            "ha-mcp-v8.4.1-7823b365",
        )
        self.assertEqual(
            addon_acceptance.EXPECTED_RAW_CATALOG_FINGERPRINT,
            "9adeb184810701b9186adc1d1db7edb29a29f946db3c95ad1a4e906d9fbd708c",
        )
        self.assertEqual(
            addon_acceptance.EXPECTED_NORMALIZED_CATALOG_FINGERPRINT,
            "c5926e759d86557bbe73a46162859b26119b2b76affed0984069019d4d6740c5",
        )
        self.assertEqual(addon_acceptance.EXPECTED_AUTOMATIC_READ_COUNT, 25)
        self.assertEqual(addon_acceptance.EXPECTED_DASHBOARD_STATUS, "quarantined")
        self.assertFalse(
            addon_acceptance.EXPECTED_OPERATIONAL_PLANNING_SUPPORTED
        )

        addon_acceptance._select_exact_addon_profile("8.2.0")
        self.assertEqual(
            addon_acceptance.EXPECTED_ENTRY_ID,
            "ha-mcp-v8.2.0-dbcfc0ee",
        )
        self.assertEqual(
            addon_acceptance.EXPECTED_NORMALIZED_CATALOG_FINGERPRINT,
            "912c68f50271b5b45639453c75931aa80bb42bb5a1e6249defe6777017c7da70",
        )
        self.assertEqual(addon_acceptance.EXPECTED_AUTOMATIC_READ_COUNT, 25)
        self.assertEqual(
            addon_acceptance.EXPECTED_HELD_TOOLS,
            {"ha_get_operation_status"},
        )

    def test_unknown_addon_acceptance_profile_fails_closed(self):
        with self.assertRaises(addon_acceptance.AcceptanceFailure):
            addon_acceptance._select_exact_addon_profile("8.1.2")

    def test_gateway_acceptance_requires_exact_dashboard_disposition(self):
        self.assertEqual(
            gateway_acceptance.expected_dashboard_attestation_status(
                "8.4.1"
            ),
            "quarantined",
        )
        for version in ("8.0.0", "8.1.0", "8.1.1", "8.2.0"):
            with self.subTest(version=version):
                self.assertEqual(
                    gateway_acceptance.expected_dashboard_attestation_status(
                        version
                    ),
                    "reviewed",
                )

    def test_addon_runtime_uses_authoritative_exact_local_accounting(self):
        self.assertEqual(
            addon_acceptance.ENGINEERING_STATIC_TOOL_COUNT, 51
        )
        for version, delegated in (
            ("8.0.0", 24),
            ("8.1.0", 24),
            ("8.1.1", 25),
            ("8.2.0", 25),
            ("8.4.1", 25),
        ):
            with self.subTest(version=version):
                addon_acceptance._select_exact_addon_profile(version)
                snapshot = addon_acceptance._runtime_snapshot(
                    observed_catalog_fingerprint="a" * 64
                )
                self.assertEqual(snapshot["engineering_tool_count"], 51)
                self.assertEqual(
                    snapshot["registered_tool_count"], 51 + delegated
                )

    def test_packaging_probe_has_no_third_party_requirement_parser(self):
        cases = {
            "websockets==17.0": "websockets",
            "HTTPX[socks]==0.28.1": "httpx",
            "python_dotenv>=1; python_version >= '3.13'": "python-dotenv",
            "example.package @ https://example.invalid/package.whl": (
                "example-package"
            ),
        }
        for requirement, expected in cases.items():
            with self.subTest(requirement=requirement):
                self.assertEqual(
                    packaging_acceptance.canonical_dependency_name(requirement),
                    expected,
                )
        with self.assertRaises(packaging_acceptance.PackagingAcceptanceFailure):
            packaging_acceptance.canonical_dependency_name("@ invalid")

    def test_live_profiles_preserve_exact_identity_and_detail_bound(self):
        for version in ("8.0.0", "8.1.0", "8.1.1", "8.2.0", "8.4.1"):
            with self.subTest(version=version):
                fixture.ADDON_DETAIL_PROFILE = f"live-{version}"
                fixture.INSTALLED_ADDONS[0]["version"] = version
                detail = fixture._addon_detail(fixture.INSTALLED_ADDONS[0])
                self.assertEqual(detail["version"], version)
                self.assertEqual(detail["version_latest"], version)
                self.assertEqual(detail["hostname"], "abcdef12-ha-mcp")
                self.assertEqual(
                    fixture._addon_detail_payload_bytes(),
                    fixture.SOURCE_DERIVED_MINIMUM_ADDON_DETAIL_BYTES,
                )

    def test_hacs_fixture_exposes_source_derived_read_inputs_only(self):
        self.assertEqual(fixture._result_for("hacs/info", {}), {"version": "2.0.5"})
        repositories = fixture._result_for("hacs/repositories/list", {})
        self.assertEqual(len(repositories), 1)
        self.assertEqual(repositories[0]["id"], "441028036")
        self.assertTrue(repositories[0]["installed"])
        self.assertEqual(
            fixture._result_for(
                "hacs/repository/info", {"repository_id": "441028036"}
            )["full_name"],
            "piitaya/lovelace-mushroom",
        )
        self.assertIsNone(
            fixture._result_for(
                "hacs/repository/info", {"repository_id": "unreviewed"}
            )
        )

    def test_workflow_pins_all_exact_addon_release_profiles(self):
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        for value in (
            "sha256:693ecd5c68f98e64111fbf58e02547a51b2168a942056684dbe262c550aff9cd",
            "sha256:65856752c37e4c1f9093060fbbc4a1a826cac1cbd6a76e856af5f5672a96c404",
            "sha256:2744a11c90f7a66e61fabe8166d058191d236094393c50d976978407c039d45d",
            "sha256:f415b72351d79414a3133c227622633d9c190a3f4f6b849eed93ac524ac1c2d5",
            "sha256:71bd08ac7ab4272bc226b91d299929949fa24b674e164121566bc1d84666e273",
            "sha256:2dad5c7f8afcfb8c5624d82a7d9c322fc70351d32d9697e07a162ec7015250b0",
            "sha256:f5186360a6cdf66ce9a7f94f1096609ca966d4c159dedcca1b562fd0ccf7e429",
            "sha256:9b051abf89667209dcc3f3d77614e0b914b69b4aa20350637569193eea23e7f2",
            "sha256:013ce6faff9b197634a346d5654854859d40aab1a1b1a9423f5e9e77ca38c176",
            "sha256:8a14c856be38d621ee99807fde76406b7cabf99935fa2869686aaf205fed71fb",
            "sha256:c86b0414a88b9ee404b6f151ed80419fe1bb120f6bb3baf1d31a6b01a5113e36",
            "sha256:8abc94e916b1cc5333e2aee64fcebc749e814b5446980d1c773fa243c56b8c57",
            "sha256:72b5f80bcdb614ae3c1ecc04f1f0f31275c8048c6ff8fd5a0859e61b6848adb0",
            "sha256:ed614264dee86264a8d08d9bb3e9e8dab2cbcae82734ce73d4213983916b0ef6",
            "sha256:2c80b35c599ca3222e1312cb9d4d9227a405d60b3bf1c2e4cf062e12033f397b",
            "sha256:3fbe577a9e50ecdb91291b7c1346fff8a2f46a515e29f4a21cfebd8de7e588c3",
            "sha256:40f6762f85fe7f228c929c9fe6185eaf4bd23ce28887a7e52027bcd068c3dc78",
            "sha256:69a738be869064819788d5db84691045ae78f60e092335e3c194f5dfe7ba5031",
        ):
            self.assertIn(value, workflow)
        self.assertIn(
            "matrix.upstream_version == '8.1.0' || "
            "matrix.upstream_version == '8.1.1' || "
            "matrix.upstream_version == '8.2.0' || "
            "matrix.upstream_version == '8.4.1'",
            workflow,
        )
        self.assertIn("exact_image_readmission_acceptance.py", workflow)
        self.assertIn("exact_image_sidecar_lifecycle_acceptance.py", workflow)
        self.assertIn("docker exec -i", workflow)
        self.assertIn(
            "< scripts/exact_image_sidecar_lifecycle_acceptance.py",
            workflow,
        )
        self.assertIn("--output /dev/stdout", workflow)
        self.assertIn(
            '> "$RUNNER_TEMP/exact-image-sidecar-lifecycle-result.json"',
            workflow,
        )
        self.assertNotIn(
            "exact-read-gateway-upstream:/tmp/exact_image_sidecar_lifecycle_acceptance.py",
            workflow,
        )
        self.assertNotIn(
            "exact-read-gateway-upstream:/tmp/exact-image-sidecar-lifecycle-result.json",
            workflow,
        )
        self.assertIn(
            "exact_custom_component_shutdown_acceptance.py", workflow
        )
        self.assertIn(
            "d1ea2b571e5737c59e459d155163ae750228c2e455796e38f848c543720ba108",
            workflow,
        )
        self.assertIn(
            'EXPECTED_SOURCE_COMMIT: ${{ matrix.source_commit }}',
            workflow,
        )
        self.assertIn(
            'test "$source_commit" = 0683f5ff34e5c71f35bce08d1cedcdee3c0a60b2',
            workflow,
        )
        self.assertIn(
            'test "$EXPECTED_IMAGE_REVISION" = 6213fc8047171a2731af6299f9bcecd73e96fcad',
            workflow,
        )
        self.assertIn(
            'test "$EXPECTED_IMAGE_REVISION" != "$source_commit"',
            workflow,
        )
        self.assertIn(
            "custom_components/ha_mcp_tools/embedded_server.py",
            workflow,
        )
        self.assertIn(
            "exact-image-sidecar-lifecycle-result.json", workflow
        )
        self.assertIn('"rate_limit_per_minute": 600', workflow)
        self.assertIn('"rate_limit_burst": 100', workflow)
        self.assertNotIn(
            "--entrypoint /app/.venv/bin/ha-mcp-web", workflow
        )
        self.assertNotIn(
            '-e "HA_MCP_BUILD_VERSION=$UPSTREAM_VERSION"', workflow
        )
        self.assertIn('["python3","/start.py"]', workflow)
        self.assertIn("default_addon_startup: true", workflow)
        self.assertEqual(
            workflow.count("exact-addon-contract-capture-1.json"), 3
        )
        self.assertIn("exact-addon-contract-capture-2.json", workflow)
        self.assertIn(
            'print(version("ha-mcp"))',
            workflow,
        )
        self.assertIn("docker stop --time 20", workflow)
        self.assertIn(
            "always() && (matrix.upstream_version == '8.1.0' || "
            "matrix.upstream_version == '8.1.1' || "
            "matrix.upstream_version == '8.2.0' || "
            "matrix.upstream_version == '8.4.1')",
            workflow,
        )
        self.assertNotIn("--delete-branch", workflow)

    def test_exact_image_cases_cover_every_automatic_read_for_all_releases(self):
        policy_paths = {
            "7.14.1": "upstream_tool_policy.json",
            "7.14.2": "upstream_tool_policy_7_14_2.json",
            "8.0.0": "upstream_tool_policy_8_0_0.json",
            "8.1.0": "upstream_tool_policy_8_1_0.json",
            "8.1.1": "upstream_tool_policy_8_1_1.json",
            "8.2.0": "upstream_tool_policy_8_2_0.json",
            "8.4.1": "upstream_tool_policy_8_4_1.json",
        }
        expected_counts = {
            "7.14.1": 26,
            "7.14.2": 26,
            "8.0.0": 24,
            "8.1.0": 24,
            "8.1.1": 25,
            "8.2.0": 25,
            "8.4.1": 25,
        }
        for version, filename in policy_paths.items():
            with self.subTest(version=version):
                policy = json.loads(
                    (
                        BETA
                        / "ha_mcp_engineering"
                        / filename
                    ).read_text(encoding="utf-8")
                )
                automatic = {
                    entry["exposed_name"]
                    for entry in policy["tools"]
                    if entry["classification"] == "automatic_read"
                }
                self.assertEqual(len(automatic), expected_counts[version])
                error_arguments = {
                    expected["tool"]: expected["arguments"]
                    for expected in gateway_acceptance.UPSTREAM_ERROR_CALLS.values()
                    if expected["tool"] in automatic
                    and (
                        expected.get("reviewed_versions") is None
                        or version in expected["reviewed_versions"]
                    )
                }
                exercised = (
                    set(gateway_acceptance.DELEGATED_READ_CALLS)
                    | set(error_arguments)
                )
                self.assertEqual(
                    automatic & exercised,
                    automatic,
                )
                catalog = json.loads(
                    (
                        ROOT
                        / "docs"
                        / "evidence"
                        / "upstream-read-compatibility"
                        / f"ha-mcp-{version}.json"
                    ).read_text(encoding="utf-8")
                )
                descriptors = {
                    descriptor["name"]: descriptor
                    for descriptor in catalog["tools"]
                }
                for name in automatic:
                    arguments = gateway_acceptance.DELEGATED_READ_CALLS.get(
                        name, error_arguments.get(name)
                    )
                    self.assertIsNotNone(arguments)
                    validate(
                        instance=arguments,
                        schema=descriptors[name]["inputSchema"],
                    )

    def test_missing_operation_is_an_exact_fail_closed_read_case(self):
        expected = gateway_acceptance.UPSTREAM_ERROR_CALLS["missing_operation"]

        self.assertEqual(expected["tool"], "ha_get_operation_status")
        self.assertEqual(
            expected["reviewed_versions"], ("7.14.1", "7.14.2")
        )
        self.assertEqual(expected["upstream_code"], "RESOURCE_NOT_FOUND")
        self.assertEqual(expected["public_code"], "provider_error")
        self.assertEqual(expected["failure_category"], "upstream_error")
        self.assertTrue(expected["retryable"])
        self.assertNotIn(
            "ha_get_operation_status", gateway_acceptance.DELEGATED_READ_CALLS
        )

    def test_supervisor_core_websocket_route_matches_addon_startup(self):
        source = (
            ROOT / "scripts/fake_ha_read_gateway_contract_server.py"
        ).read_text(encoding="utf-8")

        self.assertIn(
            'application.router.add_get("/core/websocket", websocket)', source
        )
        self.assertNotIn(
            'application.router.add_get("/core/api/websocket", websocket)',
            source,
        )

    def test_exact_image_lifecycle_harness_covers_bounded_runtime_properties(self):
        source = (
            ROOT / "scripts" / "exact_image_sidecar_lifecycle_acceptance.py"
        ).read_text(encoding="utf-8")
        for value in (
            "stable_identity_across_restart",
            "corrupt_state_regenerated_identity",
            "loopback_only_binding",
            "serving_files_removed",
            "live_generation_replaced",
            "single_serving_generation",
            "sidecar.maybe_spawn()",
            "reader_cancelled",
            "watcher_cancelled",
            "cleanup_cancellation_observed",
            "ha-mcp-exact-image-lifecycle-evidence-v1",
        ):
            self.assertIn(value, source)
        self.assertIn("ProxyHandler({})", source)
        self.assertIn('"failure_type": type(exc).__name__[:96]', source)
        self.assertNotIn('"url": url', source)
        self.assertNotIn('"secret_path":', source)
        self.assertNotIn(
            '[sys.executable, "-m", "ha_mcp.stdio_settings_sidecar"]',
            source,
        )

    def test_custom_component_shutdown_harness_executes_reviewed_functions(self):
        source = b'''\
_TEARDOWN_TIMEOUT_SECONDS = 2.0

def _cancel_pending_tasks(loop):
    pending = asyncio.all_tasks(loop)
    if not pending:
        return
    for task in pending:
        task.cancel()
    done, still_pending = loop.run_until_complete(
        asyncio.wait(pending, timeout=_TEARDOWN_TIMEOUT_SECONDS)
    )
    if still_pending:
        _LOGGER.warning("pending")
    for task in done:
        if not task.cancelled() and task.exception() is not None:
            _LOGGER.warning("raised")

def _teardown_worker_loop(loop):
    _cancel_pending_tasks(loop)
    for factory in (
        loop.shutdown_asyncgens,
        partial(
            loop.shutdown_default_executor,
            timeout=_TEARDOWN_TIMEOUT_SECONDS,
        ),
    ):
        loop.run_until_complete(factory())
    loop.close()
'''
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "embedded_server.py"
            path.write_bytes(source)
            teardown, evidence = custom_component_shutdown.load_reviewed_teardown(
                path,
                expected_sha256=hashlib.sha256(source).hexdigest(),
            )
            observed = custom_component_shutdown.exercise_worker_teardown(
                teardown
            )

        self.assertEqual(evidence["teardown_timeout_seconds"], 2.0)
        self.assertEqual(observed["pending_before"], 2)
        self.assertTrue(observed["reader_cancelled"])
        self.assertTrue(observed["watcher_cancelled"])
        self.assertTrue(observed["generator_finalized"])
        self.assertTrue(observed["loop_closed"])
        self.assertEqual(observed["loop_error_count"], 0)

    def test_custom_component_shutdown_harness_rejects_source_drift(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "embedded_server.py"
            path.write_text("_TEARDOWN_TIMEOUT_SECONDS = 2.0\n", encoding="utf-8")
            with self.assertRaises(
                custom_component_shutdown.CustomComponentShutdownFailure
            ):
                custom_component_shutdown.load_reviewed_teardown(
                    path,
                    expected_sha256="0" * 64,
                )


class ExactImageReadmissionTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _args(
        phase: str, *, expected_upstream_version: str = "8.1.0"
    ) -> argparse.Namespace:
        return argparse.Namespace(
            engineering_endpoint="http://127.0.0.1:18100/synthetic/mcp",
            expected_upstream_version=expected_upstream_version,
            phase=phase,
        )

    @staticmethod
    def _exact_observed(*, upstream_version: str = "8.1.0", **changes):
        expected = readmission.EXPECTED_ACCOUNTING_BY_VERSION[upstream_version]
        observed = {
            "success": True,
            "error_code": None,
            "provider": "upstream_read_gateway",
            "upstream_version": upstream_version,
            "fallback": "none",
            "fallback_occurred": False,
            "engineering_tool_count": expected["engineering_total_tool_count"],
            "engineering_local_tool_count": (
                readmission.ENGINEERING_STATIC_TOOL_COUNT
            ),
            "held_tools_absent": True,
            "gateway_health": {
                "admission_status": "admitted_exact",
                "selected_compatibility_entry_id": (
                    readmission.EXPECTED_ENTRY_BY_VERSION[upstream_version]
                ),
                "observed_protocol_version": "2025-03-26",
                "observed_advertised_tool_count": 78,
                "reviewed_accounted_tool_count": 78,
                "reviewed_tool_accounting_valid": True,
                "exact_matched_automatic_read_count": expected[
                    "delegated_read_count"
                ],
                "dynamically_exposed_count": expected[
                    "delegated_read_count"
                ],
                "held_read_count": len(expected["held_tools"]),
                "held_tools": sorted(expected["held_tools"]),
                **{
                    name: 0
                    for name in readmission.ZERO_ADMISSION_COUNTERS
                },
            },
        }
        observed.update(changes)
        return observed

    async def test_disconnect_is_truthful_and_never_falls_back(self):
        observed = {
            "success": False,
            "error_code": "provider_unavailable",
            "provider": "upstream_read_gateway",
            "upstream_version": None,
            "fallback": "none",
            "fallback_occurred": False,
        }
        with patch.object(readmission, "probe", AsyncMock(return_value=observed)):
            result = await readmission.run(self._args("disconnected"))

        self.assertEqual(result["result"], "PASS")
        self.assertEqual(result["probe"]["provider"], "upstream_read_gateway")
        self.assertEqual(result["probe"]["fallback"], "none")
        self.assertFalse(result["probe"]["fallback_occurred"])

    async def test_disconnect_rejects_a_stale_success(self):
        observed = {
            "success": True,
            "error_code": None,
            "provider": "upstream_read_gateway",
            "upstream_version": "8.1.0",
            "fallback": "none",
            "fallback_occurred": False,
        }
        with patch.object(readmission, "probe", AsyncMock(return_value=observed)):
            with self.assertRaises(readmission.ReadmissionFailure):
                await readmission.run(self._args("disconnected"))

    async def test_readmission_requires_exact_version_and_no_fallback(self):
        wrong = self._exact_observed()
        wrong["upstream_version"] = "8.0.0"
        exact = self._exact_observed()
        with patch.object(
            readmission,
            "probe",
            AsyncMock(side_effect=[wrong, exact]),
        ), patch.object(readmission.asyncio, "sleep", AsyncMock()):
            result = await readmission.run(self._args("readmitted"))

        self.assertEqual(result["attempt"], 2)
        self.assertEqual(result["probe"]["upstream_version"], "8.1.0")
        self.assertEqual(result["probe"]["fallback"], "none")
        self.assertEqual(result["probe"]["engineering_tool_count"], 75)
        self.assertTrue(result["probe"]["held_tools_absent"])
        self.assertEqual(
            result["probe"]["gateway_health"][
                "selected_compatibility_entry_id"
            ],
            "ha-mcp-v8.1.0-4c07e625",
        )

    async def test_exact_8_1_1_readmission_uses_promoted_accounting(self):
        exact = self._exact_observed(upstream_version="8.1.1")
        with patch.object(
            readmission,
            "probe",
            AsyncMock(return_value=exact),
        ):
            result = await readmission.run(
                self._args(
                    "readmitted", expected_upstream_version="8.1.1"
                )
            )

        self.assertEqual(result["probe"]["engineering_tool_count"], 76)
        health = result["probe"]["gateway_health"]
        self.assertEqual(health["dynamically_exposed_count"], 25)
        self.assertEqual(health["held_tools"], ["ha_get_operation_status"])

    async def test_exact_8_2_0_readmission_uses_exact_accounting(self):
        exact = self._exact_observed(upstream_version="8.2.0")
        with patch.object(
            readmission,
            "probe",
            AsyncMock(return_value=exact),
        ):
            result = await readmission.run(
                self._args(
                    "readmitted", expected_upstream_version="8.2.0"
                )
            )

        self.assertEqual(result["probe"]["engineering_tool_count"], 76)
        self.assertEqual(
            result["probe"]["engineering_local_tool_count"], 51
        )
        self.assertEqual(
            result["probe"]["gateway_health"][
                "dynamically_exposed_count"
            ],
            25,
        )

    async def test_exact_8_4_1_readmission_uses_exact_accounting(self):
        exact = self._exact_observed(upstream_version="8.4.1")
        with patch.object(
            readmission,
            "probe",
            AsyncMock(return_value=exact),
        ):
            result = await readmission.run(
                self._args(
                    "readmitted", expected_upstream_version="8.4.1"
                )
            )

        self.assertEqual(result["probe"]["engineering_tool_count"], 76)
        self.assertEqual(
            result["probe"]["gateway_health"][
                "selected_compatibility_entry_id"
            ],
            "ha-mcp-v8.4.1-7823b365",
        )

    async def test_readmission_rejects_any_inexact_admission_accounting(self):
        for field, value in (
            ("observed_advertised_tool_count", 77),
            ("dynamically_exposed_count", 23),
            ("held_read_count", 1),
            ("schema_mismatch_count", 1),
            ("runtime_contract_mismatch_count", 1),
            ("missing_reviewed_read_count", 1),
            ("unreviewed_tool_count", 1),
            ("fallback_count", 1),
        ):
            with self.subTest(field=field):
                observed = self._exact_observed()
                observed["gateway_health"][field] = value
                with patch.object(
                    readmission,
                    "probe",
                    AsyncMock(return_value=observed),
                ), patch.object(
                    readmission.asyncio, "sleep", AsyncMock()
                ), patch.object(readmission, "MAX_ATTEMPTS", 1):
                    with self.assertRaises(readmission.ReadmissionFailure):
                        await readmission.run(self._args("readmitted"))

    async def test_readmission_rejects_fallback_even_for_exact_version(self):
        fallback = self._exact_observed(
            fallback="direct_ha_api", fallback_occurred=True
        )
        with patch.object(
            readmission,
            "probe",
            AsyncMock(return_value=fallback),
        ), patch.object(readmission.asyncio, "sleep", AsyncMock()), patch.object(
            readmission, "MAX_ATTEMPTS", 2
        ):
            with self.assertRaises(readmission.ReadmissionFailure):
                await readmission.run(self._args("readmitted"))

    async def test_readmission_rejects_inconsistent_fallback_metadata(self):
        inconsistent = self._exact_observed(fallback_occurred=True)
        with patch.object(
            readmission,
            "probe",
            AsyncMock(return_value=inconsistent),
        ), patch.object(readmission.asyncio, "sleep", AsyncMock()), patch.object(
            readmission, "MAX_ATTEMPTS", 2
        ):
            with self.assertRaises(readmission.ReadmissionFailure):
                await readmission.run(self._args("readmitted"))


if __name__ == "__main__":
    unittest.main()
