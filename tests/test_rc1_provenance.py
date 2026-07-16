import json
import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
BETA_ROOT = ROOT / "hass_mcp_engineering_beta"
VALID_SHA = "0123456789abcdef0123456789abcdef01234567"
VALID_TIME = "2026-07-14T20:54:10Z"


def metadata_from_fresh_process(**environment):
    env = os.environ.copy()
    env.pop("HAMCP_BUILD_SHA", None)
    env.pop("HAMCP_BUILD_TIME", None)
    env.update(environment)
    env["PYTHONPATH"] = str(BETA_ROOT)
    program = (
        "import json; "
        "from ha_mcp_engineering.capabilities import build_server_metadata; "
        "print(json.dumps(build_server_metadata(ha_url='http://supervisor/core', "
        "runtime_mode='home_assistant_addon', "
        "ha_connection={'checked': False, 'status': 'not_checked'})))"
    )
    result = subprocess.run(
        [sys.executable, "-c", program],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


class ProvenanceValueTests(unittest.TestCase):
    def test_supplied_provenance_appears_in_existing_server_info_fields(self):
        metadata = metadata_from_fresh_process(
            HAMCP_BUILD_SHA=VALID_SHA,
            HAMCP_BUILD_TIME=VALID_TIME,
        )
        self.assertEqual(metadata["server"]["build_sha"], VALID_SHA)
        self.assertEqual(metadata["server"]["build_time"], VALID_TIME)
        self.assertEqual(metadata["server"]["schema_version"], "1")
        self.assertEqual(set(metadata["server"]), {
            "id", "name", "version", "schema_version", "build_sha", "build_time"
        })

    def test_missing_local_provenance_uses_established_safe_fallback(self):
        metadata = metadata_from_fresh_process()
        self.assertEqual(metadata["server"]["build_sha"], "unknown")
        self.assertEqual(metadata["server"]["build_time"], "unknown")

    def test_invalid_or_unbounded_provenance_fails_closed(self):
        invalid_values = (
            ("abc", VALID_TIME),
            ("a" * 39, VALID_TIME),
            ("a" * 65, VALID_TIME),
            ("https://credential@example.invalid/repository", VALID_TIME),
            (VALID_SHA, "2026-07-14T20:54:10-05:00"),
            (VALID_SHA, "2026-02-30T20:54:10Z"),
            (VALID_SHA, "x" * 4096),
        )
        for build_sha, build_time in invalid_values:
            with self.subTest(build_sha=build_sha[:24], build_time=build_time[:24]):
                metadata = metadata_from_fresh_process(
                    HAMCP_BUILD_SHA=build_sha,
                    HAMCP_BUILD_TIME=build_time,
                )
                if build_sha != VALID_SHA:
                    self.assertEqual(metadata["server"]["build_sha"], "unknown")
                if build_time != VALID_TIME:
                    self.assertEqual(metadata["server"]["build_time"], "unknown")

    def test_provenance_keys_are_not_secret_redaction_paths(self):
        sys.path.insert(0, str(BETA_ROOT))
        try:
            from ha_mcp_engineering.logging_config import redact_data
        finally:
            sys.path.pop(0)
        value = {"build_sha": VALID_SHA, "build_time": VALID_TIME}
        self.assertEqual(redact_data(value, secret="different-secret-value"), value)

    def test_capability_and_health_outputs_do_not_gain_provenance_fields(self):
        sys.path.insert(0, str(BETA_ROOT))
        try:
            from ha_mcp_engineering.capabilities import build_capability_catalog
            from ha_mcp_engineering.health import HealthRegistry
        finally:
            sys.path.pop(0)
        catalog = build_capability_catalog()
        health = HealthRegistry().snapshot({"checked": False, "status": "not_checked"})
        self.assertEqual(catalog["registered_count"], 40)
        self.assertEqual(catalog["count"], 25)
        self.assertEqual(catalog["planned"], [])
        self.assertEqual(health["registered_tool_count"], 40)
        self.assertNotIn("build_sha", json.dumps(catalog, sort_keys=True))
        self.assertNotIn("build_time", json.dumps(catalog, sort_keys=True))
        self.assertNotIn("build_sha", json.dumps(health, sort_keys=True))
        self.assertNotIn("build_time", json.dumps(health, sort_keys=True))


class ProvenancePipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dockerfile = (BETA_ROOT / "Dockerfile").read_text(encoding="utf-8")
        cls.workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        cls.deployment = (ROOT / "scripts" / "deploy-beta.ps1").read_text(encoding="utf-8")

    def test_beta_rc_image_accepts_env_and_oci_provenance(self):
        for value in (
            "ARG HAMCP_BUILD_SHA=unknown",
            "ARG HAMCP_BUILD_TIME=unknown",
            "HAMCP_BUILD_SHA=${HAMCP_BUILD_SHA}",
            "HAMCP_BUILD_TIME=${HAMCP_BUILD_TIME}",
            'org.opencontainers.image.revision="${HAMCP_BUILD_SHA}"',
            'org.opencontainers.image.created="${HAMCP_BUILD_TIME}"',
        ):
            self.assertIn(value, self.dockerfile)

    def test_ci_and_deployment_builds_supply_exact_commit_and_utc_time(self):
        self.assertIn('build_sha="$(git rev-parse HEAD)"', self.workflow)
        self.assertIn("date -u +'%Y-%m-%dT%H:%M:%SZ'", self.workflow)
        self.assertIn('"HAMCP_BUILD_SHA=$build_sha"', self.workflow)
        self.assertIn('"HAMCP_BUILD_TIME=$build_time"', self.workflow)
        self.assertIn("git -C $RepoRoot rev-parse HEAD", self.deployment)
        self.assertIn("yyyy-MM-ddTHH:mm:ssZ", self.deployment)
        self.assertIn('"HAMCP_BUILD_SHA=$($build.Sha)"', self.deployment)
        self.assertIn('"HAMCP_BUILD_TIME=$($build.Time)"', self.deployment)

    def test_production_image_build_remains_unmodified(self):
        self.assertIn(
            "docker build -t hass-mcp-admin:test ./hass_mcp_admin",
            self.workflow,
        )


if __name__ == "__main__":
    unittest.main()
