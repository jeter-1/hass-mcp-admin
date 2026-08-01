import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.governance.service import (  # noqa: E402
    ChangeGovernanceService,
)
from ha_mcp_engineering.governance.storage import (  # noqa: E402
    ChangePlanRepository,
)
from tests.test_beta25_external_approval import (  # noqa: E402
    Clock,
    FakeGateway,
)


FIXTURE_ROOT = ROOT / "tests" / "fixtures"
PROVENANCE_PATH = (
    FIXTURE_ROOT
    / "beta6_prohibited_superseded_contract_v2_provenance.json"
)
FIXTURE_PATHS = (
    FIXTURE_ROOT / "beta6_prohibited_superseded_contract_v2_a.json",
    FIXTURE_ROOT / "beta6_prohibited_superseded_contract_v2_b.json",
)
GENERATOR_PATH = (
    ROOT / "scripts" / "generate_beta6_prohibited_compatibility_fixtures.py"
)
BETA6_SOURCE_COMMIT = "5c7eebf962837f85f2309b1b5099401fb075cd6e"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class RealBeta6FixtureProvenanceTests(unittest.TestCase):
    def test_fixture_provenance_binds_exact_beta6_writer_and_bytes(self):
        provenance = json.loads(PROVENANCE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(
            provenance["historical_source_commit"], BETA6_SOURCE_COMMIT
        )
        self.assertEqual(
            provenance["generator_sha256"], _sha256(GENERATOR_PATH)
        )
        by_path = {item["path"]: item for item in provenance["fixtures"]}
        for path in FIXTURE_PATHS:
            relative = path.relative_to(ROOT).as_posix()
            self.assertEqual(by_path[relative]["sha256"], _sha256(path))
            value = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(value["contract_version"], 2)
            self.assertTrue(value["operations"])
            self.assertEqual(
                {operation["execution_status"] for operation in value["operations"]},
                {"pending"},
            )
            for operation in value["operations"]:
                self.assertIsNone(operation["execution_receipt"])
                self.assertIsNone(operation["post_apply_fingerprint"])
                self.assertIsNone(operation["failure_information"])
                self.assertEqual(operation["verification"]["status"], "not_run")


class RealBeta6PreFixDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "plans"
        self.repository = ChangePlanRepository(self.root)
        self.service = ChangeGovernanceService(
            self.repository,
            FakeGateway(),
            now=Clock(),
        )

    def tearDown(self):
        self.temporary.cleanup()

    def _write(self, fixture_path: Path) -> tuple[str, Path]:
        raw = fixture_path.read_bytes()
        plan_id = json.loads(raw)["plan_id"]
        path = self.root / f"{plan_id}.json"
        path.write_bytes(raw)
        return plan_id, path

    def test_real_beta6_contract_v2_shape_explains_beta8_failure(self):
        for fixture_path in FIXTURE_PATHS:
            with self.subTest(fixture=fixture_path.name):
                plan_id, persisted_path = self._write(fixture_path)
                before = persisted_path.read_bytes()
                plan = self.repository.get(plan_id)
                self.assertIsNotNone(plan)
                assert plan is not None
                self.assertEqual(
                    self.service._effective_prohibited_plan_failures(plan),
                    ("historical_contract_version_not_supported",),
                )
                with self.assertRaises(GovernanceError) as raised:
                    self.service.get_plan(plan_id)
                self.assertEqual(
                    raised.exception.code,
                    ErrorCode.APPROVAL_SEQUENCE_FAILURE,
                )
                self.assertEqual(persisted_path.read_bytes(), before)
                persisted_path.unlink()


if __name__ == "__main__":
    unittest.main()
