"""HAMCP-319 #408: the governed lane's trust root and ingress peer stay lane-only.

The 9.1 governed lane signs a Core release registry envelope with a key that
exists only in the runner process, and reproduces the Supervisor ingress peer
address on a disposable Docker network. These offline tests show that:

* the shipped add-on configuration cannot admit the lane-signed envelope, and an
  image configured with any other Core registry key refuses it, including a
  cache that the lane key seeded;
* no environment variable or Dockerfile input carries a Core registry key;
* the lane supplies its key only through lane-written options, and never
  enables the other registries;
* the lane reproduces, and cannot relax, the approval page's peer and ingress
  checks.
"""

from __future__ import annotations

import asyncio
import base64
import inspect
import ipaddress
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest import mock

import yaml
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tests.support import governed91_evidence as support

governed = support.governed
ROOT = support.ROOT
ADDON = ROOT / "hass_mcp_engineering_beta"
RUNTIME = ADDON / "ha_mcp_engineering"

from ha_mcp_engineering import application, approval_web, configuration  # noqa: E402
from ha_mcp_engineering.errors import ConfigurationError  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.registry import CoreReleaseRegistry  # noqa: E402

CORE_KEYS = ("ha_core_release_registry_enabled", "ha_core_release_registry_public_key")


def lane_core_version() -> str:
    """A reviewed disposable Core whose typed operations the lane signs."""

    import core_registry_contract_lane as lane
    for fixture in sorted((ROOT / "tests" / "fixtures").glob("core_*_lane_provenance.json")):
        version = fixture.name[len("core_"):-len("_lane_provenance.json")].replace("_", ".")
        try:
            if lane._lane_binding(version).typed_operations is True:
                return version
        except ValueError:
            continue
    raise AssertionError("no reviewed disposable Core with typed operations")


def other_key() -> str:
    """Stands in for any non-lane Core registry key a production image could hold."""

    return base64.b64encode(Ed25519PrivateKey.generate().public_key().public_bytes_raw()).decode()


def shipped_options() -> dict:
    return yaml.safe_load((ADDON / "config.yaml").read_text(encoding="utf-8"))["options"]


def settings_from(options: dict, env: dict | None = None):
    with mock.patch.object(configuration, "_read_options", return_value=dict(options)), \
            mock.patch.dict(os.environ, env or {}, clear=False):
        return configuration.load_settings()


class LaneEnvelope:
    _cache: tuple[bytes, str] | None = None

    @classmethod
    def get(cls) -> tuple[bytes, str]:
        if cls._cache is None:
            cls._cache = governed.signed_core_authority(lane_core_version())
        return cls._cache


class ProductionImageRefusesLaneKeyTests(unittest.TestCase):
    def setUp(self):
        self.envelope, self.lane_key = LaneEnvelope.get()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fetches = 0

    async def fetch(self, _url, _maximum):
        self.fetches += 1
        return self.envelope

    def registry(self, *, enabled: bool, public_key: str, name: str = "cache.json") -> CoreReleaseRegistry:
        return CoreReleaseRegistry(enabled=enabled, public_key=public_key,
                                   cache_path=Path(self.tmp.name) / name, fetcher=self.fetch)

    def assert_no_authority(self, registry: CoreReleaseRegistry, version: str):
        authority = registry.authority()
        self.assertFalse(authority.positive_authority_current)
        self.assertIsNone(authority.entry_for("home-assistant/core", version))

    def seed_with_lane_key(self) -> None:
        seeded = self.registry(enabled=True, public_key=self.lane_key)
        self.assertTrue(asyncio.run(seeded.refresh()))
        self.assertTrue(seeded.authority().positive_authority_current)
        # Positive control: a restart under the lane key reloads the seeded cache.
        self.assertTrue(self.registry(enabled=True, public_key=self.lane_key).authority().positive_authority_current)

    def test_lane_envelope_is_valid_under_the_lane_key_only(self):
        # Positive control: the refusals below are caused by the key, not a bad envelope.
        registry = self.registry(enabled=True, public_key=self.lane_key)
        self.assertTrue(asyncio.run(registry.refresh()))
        self.assertTrue(registry.authority().positive_authority_current)
        self.assertTrue((Path(self.tmp.name) / "cache.json").exists())

    def test_each_run_signs_with_a_new_key(self):
        _envelope, first = governed.signed_core_authority(lane_core_version())
        _envelope, second = governed.signed_core_authority(lane_core_version())
        self.assertNotEqual(first, second)
        self.assertEqual(len(base64.b64decode(first, validate=True)), 32)

    def test_shipped_configuration_admits_nothing_from_the_lane(self):
        settings = settings_from(shipped_options())
        self.assertFalse(settings.ha_core_release_registry_enabled)
        self.assertEqual(settings.ha_core_release_registry_public_key, "")
        registry = self.registry(enabled=settings.ha_core_release_registry_enabled,
                                 public_key=settings.ha_core_release_registry_public_key)
        self.assertFalse(asyncio.run(registry.refresh()))
        self.assertFalse(asyncio.run(registry.refresh_if_due(force=True)))
        self.assertEqual(self.fetches, 0)
        self.assert_no_authority(registry, lane_core_version())
        self.assertFalse((Path(self.tmp.name) / "cache.json").exists())

    def test_shipped_configuration_ignores_a_lane_seeded_cache(self):
        self.seed_with_lane_key()
        settings = settings_from(shipped_options())
        registry = self.registry(enabled=settings.ha_core_release_registry_enabled,
                                 public_key=settings.ha_core_release_registry_public_key)
        self.assert_no_authority(registry, lane_core_version())

    def test_a_differently_keyed_image_refuses_the_lane_envelope(self):
        registry = self.registry(enabled=True, public_key=other_key())
        self.assertFalse(asyncio.run(registry.refresh()))
        self.assertEqual(self.fetches, 1)
        self.assert_no_authority(registry, lane_core_version())

    def test_a_differently_keyed_image_refuses_a_lane_seeded_cache(self):
        self.seed_with_lane_key()
        registry = self.registry(enabled=True, public_key=other_key())
        self.assert_no_authority(registry, lane_core_version())
        self.assertFalse(asyncio.run(registry.refresh()))
        self.assert_no_authority(registry, lane_core_version())

    def test_enabled_registry_without_a_valid_key_fails_configuration(self):
        for key in ("", "not-base64", base64.b64encode(b"short").decode()):
            with self.subTest(key=key):
                settings = settings_from({**shipped_options(), "ha_core_release_registry_enabled": True,
                                          "ha_core_release_registry_public_key": key})
                with self.assertRaises(ConfigurationError) as raised:
                    application.validate_settings(settings)
                self.assertIn("ha_core_release_registry_public_key must be a base64 Ed25519 public key "
                              "when the registry is enabled", raised.exception.details["issues"])


class NoRuntimeInputCarriesTheLaneKeyTests(unittest.TestCase):
    def test_environment_cannot_supply_or_enable_the_core_registry(self):
        _envelope, lane_key = LaneEnvelope.get()
        names = {"HA_CORE_RELEASE_REGISTRY_PUBLIC_KEY", "HA_CORE_RELEASE_REGISTRY_ENABLED",
                 "CORE_RELEASE_REGISTRY_PUBLIC_KEY", "HAMCP_CORE_RELEASE_REGISTRY_PUBLIC_KEY"}
        settings = settings_from(shipped_options(), {name: lane_key if "KEY" in name else "true" for name in names})
        self.assertFalse(settings.ha_core_release_registry_enabled)
        self.assertEqual(settings.ha_core_release_registry_public_key, "")

    def test_runtime_reads_the_core_key_from_options_only(self):
        source = (RUNTIME / "configuration.py").read_text(encoding="utf-8")
        block = source[source.index("ha_core_release_registry_public_key=str("):]
        block = block[:block.index(".strip()")]
        self.assertNotIn("environ", block)
        for path in RUNTIME.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for match in re.finditer(r"environ(?:\.get)?\(\s*[\"']([A-Z0-9_]+)", text):
                with self.subTest(path=str(path.relative_to(ROOT)), name=match.group(1)):
                    self.assertNotIn("CORE_RELEASE_REGISTRY", match.group(1))

    def test_shipped_addon_defaults_disable_the_core_registry(self):
        options = shipped_options()
        self.assertIs(options["ha_core_release_registry_enabled"], False)
        self.assertEqual(options["ha_core_release_registry_public_key"], "")
        self.assertIs(options["upstream_trust_registry_enabled"], False)
        self.assertIs(options["ha_mcp_release_registry_enabled"], False)

    def test_dockerfile_has_no_registry_or_ingress_input(self):
        text = (ADDON / "Dockerfile").read_text(encoding="utf-8")
        inputs = re.findall(r"^\s*(?:ARG|ENV)\s+(.+)$", text, flags=re.MULTILINE)
        self.assertTrue(inputs)
        for line in inputs:
            self.assertNotRegex(line.upper(), r"REGISTRY|PUBLIC_KEY|TRUST|INGRESS|PEER")

    def test_lane_supplies_the_key_only_through_lane_options(self):
        _envelope, lane_key = LaneEnvelope.get()
        options = governed.engineering_options(lane_key)
        self.assertEqual(options["ha_core_release_registry_public_key"], lane_key)
        self.assertIs(options["ha_core_release_registry_enabled"], True)
        self.assertIs(options["upstream_trust_registry_enabled"], False)
        self.assertNotIn("upstream_trust_registry_public_key", options)
        self.assertNotIn("ha_mcp_release_registry_public_key", options)
        self.assertNotIn("ha_mcp_release_registry_enabled", options)
        # The Engineering container command never takes the key; only options.json carries it.
        self.assertNotIn("public_key", inspect.signature(governed.engineering_command).parameters)
        source = inspect.getsource(governed.run_stage)
        env_block = source[source.index('f"HA_URL='):]
        env_block = env_block[:env_block.index(') + "\\n")')]
        self.assertEqual(re.findall(r"([A-Z_]+)=", env_block),
                         ["HA_URL", "HA_TOKEN", "SUPERVISOR_TOKEN", "MCP_PORT", "APPROVAL_INGRESS_PORT",
                          "UPSTREAM_DASHBOARD_MCP_URL"])
        self.assertNotIn("public_key", env_block)
        self.assertNotIn("REGISTRY", env_block)

    def test_lane_key_is_never_written_to_the_repository(self):
        _envelope, lane_key = LaneEnvelope.get()
        for path in (ADDON / "config.yaml", ADDON / "Dockerfile", ROOT / "repository.yaml"):
            self.assertNotIn(lane_key, path.read_text(encoding="utf-8"))


class LaneReproducesIngressChecksTests(unittest.TestCase):
    def test_lane_peer_is_the_runtime_supervisor_peer(self):
        self.assertEqual(governed.INGRESS_PEER, approval_web.SUPERVISOR_INGRESS_PEER)
        subnet = ipaddress.ip_network(governed.INGRESS_SUBNET)
        self.assertIn(ipaddress.ip_address(governed.INGRESS_PEER), subnet)
        self.assertIn(ipaddress.ip_address(governed.INGRESS_GATEWAY), subnet)

    def test_runtime_ingress_defaults_are_not_configurable(self):
        for factory in (approval_web.create_approval_application, approval_web.IngressApprovalApplication):
            parameters = inspect.signature(factory).parameters
            self.assertEqual(parameters["allowed_peers"].default, (approval_web.SUPERVISOR_INGRESS_PEER,))
            self.assertIs(parameters["require_ingress_header"].default, True)
        source = inspect.getsource(application.create_approval_application)
        self.assertNotIn("allowed_peers", source)
        self.assertNotIn("require_ingress_header", source)
        self.assertIn("AdministratorVerifier", source)
        self.assertNotIn("SUPERVISOR_INGRESS_PEER", (RUNTIME / "configuration.py").read_text(encoding="utf-8"))

    def test_lane_never_configures_the_approval_checks(self):
        source = (ROOT / "scripts" / "exact_image_admission_91_governed.py").read_text(encoding="utf-8")
        for name in ("allowed_peers", "require_ingress_header", "administrator_verifier",
                     "SUPERVISOR_INGRESS_PEER", "AdministratorVerifier"):
            self.assertNotIn(name, source)
        # The lane's ingress header satisfies the runtime pattern rather than replacing it.
        self.assertRegex(governed.INGRESS_PATH, approval_web._INGRESS_PATH)
        self.assertIn('"X-Ingress-Path": INGRESS_PATH', source)

if __name__ == "__main__":
    unittest.main()
