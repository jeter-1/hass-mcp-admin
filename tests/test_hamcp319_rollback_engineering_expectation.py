"""HAMCP-319: the exact-image oracle is bound to the Engineering image under test.

The side_by_side rollback stage runs ``scripts/exact_image_read_gateway_acceptance.py``
against the previous published release 2.4.2-beta.6 (tag v2.4.2-beta.6, source
af5ec837). That release predates HAMCP-290 and HAMCP-229: it registers 81 static
tools (no ``get_marketplace_inventory`` or ``automation_reliability_batch_analysis``)
and still delegates ``ha_get_hacs_info`` (nothing
retired, ``core_withheld_read_count`` 0). Applying the current source's
expectation to it failed CI run 42547738402 (``sbs_rollback_85_legacy_useful``).

Offline: the Engineering session is a synthetic in-process fake; no Docker,
network, live Home Assistant or credentials. The fake stops at the first
delegated read call, so reaching it proves every catalog-level check passed.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
BETA = ROOT / "hass_mcp_engineering_beta"
if str(BETA) not in sys.path:
    sys.path.insert(0, str(BETA))

from ha_mcp_engineering.ha_core_readmission.routes import (  # noqa: E402
    RETIRED_DELEGATED_READS,
)
from ha_mcp_engineering.tools import (  # noqa: E402
    get_registered_server,
    registered_tools,
)
from ha_mcp_engineering.upstream_tool_policy import (  # noqa: E402
    load_reviewed_upstream_release_registry,
)
from ha_mcp_engineering.version import SERVER_VERSION  # noqa: E402


def _load_script(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"{name}_hamcp319_rollback", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


acceptance = _load_script("exact_image_read_gateway_acceptance")
runners = _load_script("exact_image_admission_91_runners")

ROLLBACK_VERSION = "2.4.2-beta.6"
MARKETPLACE = "get_marketplace_inventory"
RELIABILITY_BATCH = "automation_reliability_batch_analysis"
DEVICE_CONFIGURATION = "control_device_configuration"  # HAMCP-297
CURRENT_STATIC = frozenset(
    tool.name for tool in registered_tools(get_registered_server()).values()
)
# Independently restated here (not read from the script): beta.6 is the current
# static set without the HAMCP-290 marketplace tool and the HAMCP-229
# reliability batch tool, and (HAMCP-297) the device-configuration tool.
BETA6_STATIC = CURRENT_STATIC - {MARKETPLACE, RELIABILITY_BATCH, DEVICE_CONFIGURATION}
RELEASE_850 = load_reviewed_upstream_release_registry().by_version["8.5.0"]
POLICY_850 = RELEASE_850.policy
READS_850 = frozenset(e.exposed_name for e in POLICY_850.tools if e.is_read_route)
RETIRED_850 = frozenset(
    e.exposed_name
    for e in POLICY_850.tools
    if e.is_read_route and e.upstream_name in RETIRED_DELEGATED_READS
)


class _ReachedDelegatedCalls(Exception):
    """Every catalog-level check passed; the first delegated read was attempted."""


def _catalog(static: frozenset[str], reads: frozenset[str]) -> list[dict[str, Any]]:
    by_name = {entry.exposed_name: entry for entry in POLICY_850.tools}
    items = [{"name": name, "annotations": {}} for name in sorted(static)]
    for name in sorted(reads):
        annotations = by_name[name].reviewed_annotations
        items.append(
            {
                "name": name,
                "annotations": {
                    "readOnlyHint": annotations.read_only,
                    "destructiveHint": annotations.destructive,
                    "idempotentHint": annotations.idempotent,
                    "openWorldHint": annotations.open_world,
                },
            }
        )
    return items


def _health(version: Any, *, omit_server: bool = False) -> dict[str, Any]:
    data: dict[str, Any] = {"provider_routing": {"upstream_read_gateway": 0}}
    if not omit_server:
        data["server"] = {"id": "hass-mcp-engineering-beta", "version": version}
    return {"success": True, "data": data}


class _FakeSession:
    def __init__(self, health: dict[str, Any], calls: list[str]) -> None:
        self._health = health
        self._calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def initialize(self):
        return SimpleNamespace(serverInfo=SimpleNamespace(name="ha-engineering-beta"))

    async def call_tool(self, name: str, arguments: dict[str, Any]):
        self._calls.append(name)
        if name == "get_server_health":
            return SimpleNamespace(structuredContent=self._health, content=[])
        raise _ReachedDelegatedCalls(name)


def _inspect(
    module,
    *,
    catalog: list[dict[str, Any]],
    health: dict[str, Any],
    expected_upstream_version: str = "8.5.0",
) -> list[str]:
    calls: list[str] = []

    @contextlib.asynccontextmanager
    async def fake_client(endpoint):
        yield (None, None, None)

    with (
        mock.patch.object(
            module, "engineering_readiness",
            return_value={"http_status": 200, "ready": True},
        ),
        mock.patch.object(module, "streamablehttp_client", fake_client),
        mock.patch.object(
            module, "ClientSession", lambda read, write: _FakeSession(health, calls)
        ),
        mock.patch.object(module, "list_all_tools", mock.AsyncMock(return_value=catalog)),
    ):
        asyncio.run(
            module.inspect_engineering(
                "http://127.0.0.1:9/mcp",
                "http://127.0.0.1:9/__fixture__/stats",
                set(),
                expected_upstream_version=expected_upstream_version,
                policy=POLICY_850,
                release=RELEASE_850,
            )
        )
    return calls


BETA6_CATALOG = _catalog(BETA6_STATIC, READS_850)
CURRENT_CATALOG = _catalog(CURRENT_STATIC, READS_850 - RETIRED_850)


class RollbackImageCatalogOracleTests(unittest.TestCase):
    """End-to-end through inspect_engineering with a synthetic session."""

    def assert_catalog_accepted(self, *, catalog, version) -> None:
        with self.assertRaises(_ReachedDelegatedCalls):
            _inspect(acceptance, catalog=catalog, health=_health(version))

    def assert_refused(self, *, catalog, health, message: str) -> list[str]:
        calls: list[str] = []
        with self.assertRaises(acceptance.AcceptanceFailure) as raised:
            calls = _inspect(acceptance, catalog=catalog, health=health)
        self.assertIn(message, str(raised.exception))
        return calls

    def test_fixture_preconditions(self):
        self.assertEqual(RETIRED_850, {"ha_get_hacs_info"})
        self.assertIn(MARKETPLACE, CURRENT_STATIC)
        self.assertIn(RELIABILITY_BATCH, CURRENT_STATIC)
        self.assertIn(DEVICE_CONFIGURATION, CURRENT_STATIC)  # HAMCP-297
        self.assertEqual(len(BETA6_STATIC), len(CURRENT_STATIC) - 3)
        self.assertNotEqual(SERVER_VERSION, ROLLBACK_VERSION)

    def test_beta6_rollback_image_catalog_is_accepted(self):
        # Failed before the correction: "The first accepted Engineering catalog
        # is incomplete" (current 82-tool base applied to beta.6's 81).
        self.assert_catalog_accepted(catalog=BETA6_CATALOG, version=ROLLBACK_VERSION)

    def test_current_image_keeps_the_full_hamcp290_checks(self):
        self.assert_catalog_accepted(catalog=CURRENT_CATALOG, version=SERVER_VERSION)

    def test_current_image_advertising_the_retired_read_is_refused(self):
        catalog = _catalog(CURRENT_STATIC, READS_850)
        self.assert_refused(
            catalog=catalog, health=_health(SERVER_VERSION),
            message="a retired delegated read is advertised",
        )

    def test_current_image_missing_marketplace_is_refused(self):
        catalog = _catalog(BETA6_STATIC, READS_850 - RETIRED_850)
        self.assert_refused(
            catalog=catalog, health=_health(SERVER_VERSION),
            message="catalog is incomplete",
        )

    def test_mutant_b_current_expectation_on_beta6_image_fails(self):
        # The beta.6 image catalog under the current expectation (the
        # pre-correction behavior) must not pass.
        self.assert_refused(
            catalog=BETA6_CATALOG, health=_health(SERVER_VERSION),
            message="catalog is incomplete",
        )

    def test_mutant_a_beta6_expectation_on_current_image_fails(self):
        # A current-source catalog cannot pass by self-reporting beta.6.
        self.assert_refused(
            catalog=CURRENT_CATALOG, health=_health(ROLLBACK_VERSION),
            message="catalog is incomplete",
        )
        catalog = _catalog(CURRENT_STATIC, READS_850)
        self.assert_refused(
            catalog=catalog, health=_health(ROLLBACK_VERSION),
            message="unexpected tool exposed",
        )

    def test_mutant_c_unknown_or_missing_version_fails_closed(self):
        for health in (
            _health("2.4.2-beta.5"),
            _health("2.4.2-beta.6 "),
            _health("2.4.2-BETA.6"),
            _health(""),
            _health(None),
            _health(7),
            _health(None, omit_server=True),
        ):
            for catalog in (BETA6_CATALOG, CURRENT_CATALOG):
                with self.subTest(health=health["data"].get("server")):
                    with self.assertRaises(acceptance.AcceptanceFailure) as raised:
                        _inspect(acceptance, catalog=catalog, health=health)
                    self.assertIn("unreviewed Engineering version", str(raised.exception))


class VersionBoundExpectationTests(unittest.TestCase):
    def test_pinned_beta6_expectation_matches_published_source(self):
        self.assertEqual(acceptance.BETA6_ENGINEERING_VERSION, ROLLBACK_VERSION)
        self.assertEqual(len(acceptance.BETA6_STATIC_TOOL_NAMES), 81)
        self.assertEqual(acceptance.BETA6_STATIC_TOOL_NAMES, BETA6_STATIC)
        self.assertEqual(acceptance.BETA6_RETIRED_DELEGATED_READS, frozenset())
        self.assertNotIn(MARKETPLACE, acceptance.BETA6_STATIC_TOOL_NAMES)
        self.assertNotIn(RELIABILITY_BATCH, acceptance.BETA6_STATIC_TOOL_NAMES)
        self.assertNotIn(DEVICE_CONFIGURATION, acceptance.BETA6_STATIC_TOOL_NAMES)  # HAMCP-297

    def test_pinned_version_is_the_rollback_identity(self):
        self.assertEqual(runners.ROLLBACK_ENGINEERING_VERSION, ROLLBACK_VERSION)
        workflow = yaml.safe_load(
            (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        )
        env = workflow["jobs"]["exact-image-admission-lanes"]["env"]
        self.assertEqual(env["ROLLBACK_ENGINEERING_VERSION"], ROLLBACK_VERSION)

    def test_provenance_is_recorded(self):
        source = (ROOT / "scripts" / "exact_image_read_gateway_acceptance.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("af5ec837", source)
        self.assertIn("v2.4.2-beta.6", source)

    def test_selector(self):
        current = acceptance.engineering_expectation(
            SERVER_VERSION, current_static_names=CURRENT_STATIC
        )
        self.assertEqual(current.version, SERVER_VERSION)
        self.assertEqual(current.static_names, CURRENT_STATIC)
        self.assertEqual(current.retired_upstream, frozenset(RETIRED_DELEGATED_READS))
        beta6 = acceptance.engineering_expectation(
            ROLLBACK_VERSION, current_static_names=CURRENT_STATIC
        )
        self.assertEqual(beta6.static_names, BETA6_STATIC)
        self.assertEqual(beta6.retired_upstream, frozenset())
        for unknown in ("2.4.2-beta.5", "2.4.2", "", None, 7):
            with self.subTest(unknown=unknown):
                with self.assertRaises(acceptance.AcceptanceFailure):
                    acceptance.engineering_expectation(
                        unknown, current_static_names=CURRENT_STATIC
                    )

    def test_retired_exposed_names_follow_the_expectation(self):
        # Drives both the "not advertised" check and the
        # core_withheld_read_count == len(retired) health check.
        self.assertEqual(
            acceptance.retired_exposed_names(POLICY_850, frozenset(RETIRED_DELEGATED_READS)),
            {"ha_get_hacs_info"},
        )
        self.assertEqual(
            acceptance.retired_exposed_names(
                POLICY_850, acceptance.BETA6_RETIRED_DELEGATED_READS
            ),
            set(),
        )

    def test_mutant_pinned_set_equal_to_current_rejects_beta6(self):
        with mock.patch.object(acceptance, "BETA6_STATIC_TOOL_NAMES", CURRENT_STATIC):
            with self.assertRaises(acceptance.AcceptanceFailure):
                _inspect(acceptance, catalog=BETA6_CATALOG, health=_health(ROLLBACK_VERSION))

    def test_mutant_beta6_retiring_hacs_rejects_beta6(self):
        with mock.patch.object(
            acceptance, "BETA6_RETIRED_DELEGATED_READS", frozenset(RETIRED_DELEGATED_READS)
        ):
            with self.assertRaises(acceptance.AcceptanceFailure):
                _inspect(acceptance, catalog=BETA6_CATALOG, health=_health(ROLLBACK_VERSION))

    def test_beta6_outside_its_reviewed_upstream_version_fails_closed(self):
        with self.assertRaises(acceptance.AcceptanceFailure) as raised:
            _inspect(
                acceptance, catalog=BETA6_CATALOG, health=_health(ROLLBACK_VERSION),
                expected_upstream_version="9.1.0",
            )
        self.assertIn("no reviewed expectation", str(raised.exception))

    def test_mutant_pinned_reads_drift_rejects_beta6(self):
        drifted = {"8.5.0": READS_850 - {"ha_get_hacs_info"}}
        with mock.patch.object(
            acceptance, "BETA6_DELEGATED_READS_BY_UPSTREAM_VERSION", drifted
        ):
            with self.assertRaises(acceptance.AcceptanceFailure) as raised:
                _inspect(acceptance, catalog=BETA6_CATALOG, health=_health(ROLLBACK_VERSION))
        self.assertIn("differs from the Engineering image's own source", str(raised.exception))


BETA6_COMMIT = "af5ec8375401276f910804843c2ddb9180533c77"
BETA6_TAG = "v2.4.2-beta.6"
DRIVER = ROOT / "tests" / "support" / "hamcp319_beta6_expectation_driver.py"


def _git(*args: str, binary: bool = False) -> Any:
    result = subprocess.run(
        ["git", "-C", str(ROOT), *args], capture_output=True, check=False, timeout=300
    )
    if result.returncode != 0:
        return None
    return result.stdout if binary else result.stdout.decode().strip()


class PinnedBeta6ValuesComeFromPublishedSourceTests(unittest.TestCase):
    """Recompute every pinned beta.6 value from the af5ec837 tree itself.

    Source: tag v2.4.2-beta.6 = commit af5ec837 (the published 2.4.2-beta.6
    rollback image). CI unit shards check out full history (fetch-depth 0),
    where this commit is an ancestor; there a missing commit is a failure, not
    a skip, so the pins cannot drift unobserved.
    """

    @classmethod
    def setUpClass(cls) -> None:
        missing = None
        if shutil.which("git") is None:
            missing = "git is unavailable"
        elif _git("rev-parse", "--verify", "--quiet", f"{BETA6_COMMIT}^{{commit}}") != BETA6_COMMIT:
            missing = f"beta.6 commit {BETA6_COMMIT} is not in this clone"
        if missing is not None:
            if os.environ.get("CI") == "true":
                raise AssertionError(missing)
            raise unittest.SkipTest(missing)
        archive = _git("archive", "--format=tar", BETA6_COMMIT, "hass_mcp_engineering_beta", binary=True)
        if archive is None:
            raise AssertionError("git archive of beta.6 failed")
        cls.archive_commit = subprocess.run(
            ["git", "-C", str(ROOT), "get-tar-commit-id"], input=archive,
            capture_output=True, check=True, timeout=120,
        ).stdout.decode().strip()
        temp = Path(tempfile.mkdtemp(prefix="hamcp319-beta6-expectation-"))
        cls.addClassCleanup(shutil.rmtree, temp)
        source = temp / "beta6-source"
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(source, filter="data")
        completed = subprocess.run(
            [sys.executable, "-I", str(DRIVER), str(source / "hass_mcp_engineering_beta")],
            cwd=str(temp), capture_output=True, text=True, timeout=600, check=False,
            env={**os.environ, "TMPDIR": str(temp)},
        )
        if completed.returncode != 0:
            raise AssertionError(f"beta.6 derivation failed:\n{completed.stderr[-4000:]}")
        cls.derived = json.loads(completed.stdout)

    def test_archive_is_the_published_commit(self):
        self.assertEqual(self.archive_commit, BETA6_COMMIT)
        tagged = _git("rev-parse", "--verify", "--quiet", f"{BETA6_TAG}^{{commit}}")
        if tagged is not None:
            self.assertEqual(tagged, BETA6_COMMIT)

    def test_version_pin(self):
        self.assertEqual(self.derived["version"], acceptance.BETA6_ENGINEERING_VERSION)

    def test_static_tool_pin(self):
        self.assertEqual(self.derived["static_count_constant"], 81)
        self.assertEqual(len(self.derived["static_names"]), 81)
        self.assertEqual(
            frozenset(self.derived["static_names"]), acceptance.BETA6_STATIC_TOOL_NAMES
        )

    def test_delegated_read_pin(self):
        derived = self.derived["delegated_reads_by_upstream_version"]
        self.assertEqual(
            set(acceptance.BETA6_DELEGATED_READS_BY_UPSTREAM_VERSION), set(derived)
        )
        for version, names in derived.items():
            self.assertEqual(
                acceptance.BETA6_DELEGATED_READS_BY_UPSTREAM_VERSION[version],
                frozenset(names),
            )
        self.assertIn("ha_get_hacs_info", derived["8.5.0"])
        self.assertEqual(len(derived["8.5.0"]), 28)

    def test_retired_set_and_core_withheld_pin(self):
        self.assertEqual(self.derived["retired_delegated_reads"], [])
        self.assertEqual(acceptance.BETA6_RETIRED_DELEGATED_READS, frozenset())
        # beta.6 has no retirement disposition at all, so nothing is withheld
        # by retirement: core_withheld_read_count == len(retired) == 0.
        for token in ("RETIRED_DELEGATED_READS", "retired_delegated_read"):
            with self.subTest(token=token):
                self.assertIsNone(
                    _git("grep", "-q", token, BETA6_COMMIT, "--", "hass_mcp_engineering_beta")
                )
                self.assertIsNotNone(
                    _git("grep", "-q", token, "HEAD", "--", "hass_mcp_engineering_beta")
                )
        self.assertEqual(
            len(acceptance.retired_exposed_names(POLICY_850, acceptance.BETA6_RETIRED_DELEGATED_READS)),
            0,
        )


if __name__ == "__main__":
    unittest.main()
