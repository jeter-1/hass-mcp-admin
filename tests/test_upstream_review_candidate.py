import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "review_upstream_read_release.py"
CAPTURE = (
    ROOT
    / "docs"
    / "evidence"
    / "upstream-read-compatibility"
    / "ha-mcp-8.0.0.json"
)
BASE_POLICY = (
    ROOT
    / "hass_mcp_engineering_beta"
    / "ha_mcp_engineering"
    / "upstream_tool_policy_8_0_0.json"
)
SOURCE_COMMIT = "9dd3ac620e3149cd34ec3c990b6ee81e778191f2"
RUNTIME_MODEL = "ha-mcp-operational-tool-descriptor-v2"
STRICT_MODEL = "ha-mcp-strict-full-contract-v1"


def _canonical_json(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


class UpstreamReviewCandidateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.capture = json.loads(CAPTURE.read_text(encoding="utf-8"))
        policy = json.loads(BASE_POLICY.read_text(encoding="utf-8"))
        self.decisions = [
            {
                "tool_name": item["upstream_name"],
                "policy_classification": item["classification"],
                "reason": (
                    "Explicit exact-release policy decision for "
                    f"{item['upstream_name']}."
                ),
            }
            for item in policy["tools"]
        ]
        self.decisions_path = self.directory / "decisions.json"
        self.strict_response_path = self.directory / "tools-response.json"
        self.output_policy = (
            self.directory / "upstream_tool_policy_8_0_0.json"
        )
        self.output_entry = self.directory / "entry.json"
        self._write_inputs()

    def _write_inputs(self):
        self.decisions_path.write_text(
            json.dumps(self.decisions),
            encoding="utf-8",
        )
        self.strict_response_path.write_text(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "result": {"tools": self.capture["tools"]},
                }
            ),
            encoding="utf-8",
        )

    def _command(self, *, arm_v7=False):
        command = [
            sys.executable,
            str(SCRIPT),
            "candidate",
            "--capture",
            str(CAPTURE),
            "--base-policy",
            str(BASE_POLICY),
            "--version",
            "8.0.0",
            "--source-commit",
            SOURCE_COMMIT,
            "--image-index-digest",
            "sha256:" + "1" * 64,
            "--amd64-digest",
            "sha256:" + "2" * 64,
            "--arm64-digest",
            "sha256:" + "3" * 64,
            "--addon-amd64-index-digest",
            "sha256:" + "4" * 64,
            "--addon-amd64-manifest-digest",
            "sha256:" + "5" * 64,
            "--addon-arm64-index-digest",
            "sha256:" + "6" * 64,
            "--addon-arm64-manifest-digest",
            "sha256:" + "7" * 64,
            "--image-revision",
            SOURCE_COMMIT,
            "--review-date",
            "2026-08-05",
            "--review-decisions",
            str(self.decisions_path),
            "--runtime-contract-fingerprint-model",
            RUNTIME_MODEL,
            "--strict-tools-response",
            str(self.strict_response_path),
            "--strict-full-contract-fingerprint-model",
            STRICT_MODEL,
            "--held-tool",
            "ha_search",
            "--held-tool",
            "ha_get_operation_status",
            "--dashboard-status",
            "quarantined",
            "--output-policy",
            str(self.output_policy),
            "--output-entry",
            str(self.output_entry),
        ]
        if arm_v7:
            command.extend(["--arm-v7-digest", "sha256:" + "8" * 64])
        return command

    def _run(self, command=None):
        return subprocess.run(
            command or self._command(),
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

    def test_candidate_requires_explicit_decisions_and_emits_exact_models(self):
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        entry = json.loads(self.output_entry.read_text(encoding="utf-8"))
        generated_policy = json.loads(
            self.output_policy.read_text(encoding="utf-8")
        )
        self.assertEqual(
            entry["runtime_contract_fingerprint_model"], RUNTIME_MODEL
        )
        self.assertEqual(
            entry["strict_full_contract_fingerprint_model"], STRICT_MODEL
        )
        self.assertEqual(
            entry["strict_full_contract_fingerprint"],
            hashlib.sha256(
                _canonical_json({"tools": self.capture["tools"]})
            ).hexdigest(),
        )
        self.assertEqual(
            entry["architecture_image_digests"],
            {
                "linux/amd64": "sha256:" + "2" * 64,
                "linux/arm64": "sha256:" + "3" * 64,
            },
        )
        self.assertEqual(
            entry["addon_artifact_digests"],
            {
                "linux/amd64": {
                    "index_digest": "sha256:" + "4" * 64,
                    "image_manifest_digest": "sha256:" + "5" * 64,
                },
                "linux/arm64": {
                    "index_digest": "sha256:" + "6" * 64,
                    "image_manifest_digest": "sha256:" + "7" * 64,
                },
            },
        )
        self.assertTrue(
            all("ha-mcp v8.0.0" in item for item in entry["review_provenance"])
        )
        expected = {
            item["tool_name"]: item for item in self.decisions
        }
        actual = {
            item["upstream_name"]: item for item in generated_policy["tools"]
        }
        self.assertEqual(set(actual), set(expected))
        for name, decision in expected.items():
            self.assertEqual(
                actual[name]["classification"],
                decision["policy_classification"],
            )
            self.assertEqual(actual[name]["reason"], decision["reason"])

    def test_candidate_adds_standalone_arm_v7_only_when_supplied(self):
        result = self._run(self._command(arm_v7=True))
        self.assertEqual(result.returncode, 0, result.stderr)
        entry = json.loads(self.output_entry.read_text(encoding="utf-8"))
        self.assertEqual(
            entry["architecture_image_digests"]["linux/arm/v7"],
            "sha256:" + "8" * 64,
        )

    def test_candidate_rejects_incomplete_or_unsupported_decisions(self):
        cases = {
            "missing": self.decisions[:-1],
            "duplicate": [*self.decisions, self.decisions[0]],
            "unsupported": [
                {
                    **item,
                    "policy_classification": (
                        "inherited_trust"
                        if index == 0
                        else item["policy_classification"]
                    ),
                }
                for index, item in enumerate(self.decisions)
            ],
            "missing_reason": [
                ({key: value for key, value in item.items() if key != "reason"}
                 if index == 0 else item)
                for index, item in enumerate(self.decisions)
            ],
        }
        for name, decisions in cases.items():
            with self.subTest(case=name):
                self.decisions = decisions
                self._write_inputs()
                result = self._run()
                self.assertNotEqual(result.returncode, 0)

    def test_candidate_rejects_unsupported_models_and_artifact_digests(self):
        cases = {
            "runtime_model": (
                "--runtime-contract-fingerprint-model",
                "nearest-release-model",
            ),
            "strict_model": (
                "--strict-full-contract-fingerprint-model",
                "unreviewed-strict-model",
            ),
            "addon_digest": (
                "--addon-amd64-index-digest",
                "sha256:not-an-exact-digest",
            ),
        }
        for name, (flag, replacement) in cases.items():
            with self.subTest(case=name):
                command = self._command()
                command[command.index(flag) + 1] = replacement
                result = self._run(command)
                self.assertNotEqual(result.returncode, 0)

    def test_candidate_rejects_strict_response_drift_and_held_mismatch(self):
        changed = json.loads(
            self.strict_response_path.read_text(encoding="utf-8")
        )
        changed["result"]["tools"][0]["description"] += " drift"
        self.strict_response_path.write_text(
            json.dumps(changed),
            encoding="utf-8",
        )
        result = self._run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("differs from the normalized capture", result.stderr)

        self._write_inputs()
        command = self._command()
        held_index = command.index("--held-tool")
        del command[held_index : held_index + 2]
        result = self._run(command)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must exactly match explicit review decisions", result.stderr)


if __name__ == "__main__":
    unittest.main()
