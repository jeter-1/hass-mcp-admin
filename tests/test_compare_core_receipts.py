"""HAMCP-443: the field-by-field receipt comparison and its declared exclusions.

Offline: synthetic receipts, plus the committed hamcp366 device receipt and the
committed declaration read from the checkout. Nothing starts a container.
"""
import contextlib
import copy
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import compare_core_receipts as compare  # noqa: E402

DEVICE = "device_configuration_entity"
UPSTREAM = "beta25-real-ha-upstream-ha-2026-11-0-device-configuration"


def declaration(**lane):
    entry = {"harness": "scripts/compare_core_receipts.py", "committed_receipt": "committed.json",
             "committed_receipt_sha256": "", "excluded": [], "expected_changes": []}
    entry.update(lane)
    return {"model": compare.MODEL, "lanes": {"lane": entry}}


def body(committed, observed, **lane):
    _, classes, changes = compare.lane_declaration(declaration(**lane), "lane")
    return compare.compare(committed, observed, classes, changes)


RUN = {"class": "run_identity", "reason": "run", "paths": [["source_commit"], ["ops", "*", "seconds"]]}
RECEIPT = {"source_commit": "a" * 40, "result": "PASS", "ops": [{"seconds": 0.7, "status": "ok", "attempts": 1}],
           "image": "child:8.5.0", "flags": {"changed": True}}


def setting(receipt, path, value):
    node = receipt
    for part in path[:-1]:
        node = node[part]
    node[path[-1]] = value


class PatternTests(unittest.TestCase):
    def test_segments(self):
        self.assertTrue(compare.matches(("a", "*", "b"), ("a", 0, "b")))
        self.assertTrue(compare.matches(("a", "*", "b"), ("a", "x", "b")))
        self.assertFalse(compare.matches(("a", "*", "b"), ("a", "b")))
        self.assertTrue(compare.matches(("**", "b"), ("b",)))
        self.assertTrue(compare.matches(("**", "b"), ("a", 3, "c", "b")))
        self.assertFalse(compare.matches(("**", "b"), ("b", "c")))
        self.assertTrue(compare.matches(("a", ("x", "y")), ("a", "y")))
        self.assertFalse(compare.matches(("a", ("x", "y")), ("a", "z")))

    def test_literal_never_matches_a_list_index(self):
        self.assertFalse(compare.matches(("ops", "0"), ("ops", 0)))

    def test_invalid_segments_refuse(self):
        for path in ([], [""], [3], [[]], [["*"]], [["a", "a"]], "a"):
            with self.subTest(path=path), self.assertRaises(compare.Refusal):
                compare.lane_declaration(declaration(excluded=[{"class": "c", "reason": "r", "paths": [path]}]), "lane")


class CompareTests(unittest.TestCase):
    def observed(self):
        value = copy.deepcopy(RECEIPT)
        value["source_commit"], value["ops"][0]["seconds"] = "b" * 40, 1.3
        return value

    def test_excluded_leaves_may_differ_and_are_reported(self):
        result = body(RECEIPT, self.observed(), excluded=[RUN])
        self.assertTrue(result["equal"])
        self.assertEqual(result["excluded_leaves"], 2)
        self.assertEqual([item["path"] for item in result["excluded_differences"]["run_identity"]],
                         ["/source_commit", "/ops/0/seconds"])

    def test_every_other_leaf_must_be_equal(self):
        for path, value in ((("result",), "FAIL"), (("ops", 0, "status"), "other"), (("ops", 0, "attempts"), 2),
                            (("flags", "changed"), False), (("image",), "child:9.2.0")):
            with self.subTest(path=path):
                observed = self.observed()
                setting(observed, path, value)
                result = body(RECEIPT, observed, excluded=[RUN])
                self.assertFalse(result["equal"])
                self.assertEqual(result["differences"], [{"path": compare.pointer(path), "kind": "value",
                                                          "committed": RECEIPT_VALUE(path), "observed": value}])

    def test_types_are_exact_even_for_excluded_leaves(self):
        for path, value in ((("ops", 0, "seconds"), 1), (("ops", 0, "seconds"), None), (("source_commit",), 7),
                            (("flags", "changed"), 1), (("ops", 0, "attempts"), 1.0)):
            with self.subTest(path=path, value=value):
                observed = self.observed()
                setting(observed, path, value)
                result = body(RECEIPT, observed, excluded=[RUN])
                self.assertEqual([item["kind"] for item in result["differences"]], ["type"])

    def test_key_sets_and_lengths_are_compared_everywhere(self):
        missing = self.observed()
        del missing["ops"][0]["seconds"]
        extra = self.observed()
        extra["ops"][0]["note"] = "x"
        longer = self.observed()
        longer["ops"].append(copy.deepcopy(longer["ops"][0]))
        for observed, kind in ((missing, "missing"), (extra, "unexpected"), (longer, "length")):
            with self.subTest(kind=kind):
                result = body(RECEIPT, observed, excluded=[RUN])
                self.assertEqual([item["kind"] for item in result["differences"]], [kind])

    def test_expected_change_must_be_observed_exactly(self):
        change = {"path": ["image"], "committed": "child:8.5.0", "observed": "child:9.2.0", "reason": "D7"}
        observed = self.observed()
        observed["image"] = "child:9.2.0"
        result = body(RECEIPT, observed, excluded=[RUN], expected_changes=[change])
        self.assertTrue(result["equal"])
        self.assertEqual(result["expected_changes"], [{"path": "/image", "applied": True}])
        for value in ("child:8.5.0", "child:9.3.0"):
            with self.subTest(value=value):
                observed["image"] = value
                result = body(RECEIPT, observed, excluded=[RUN], expected_changes=[change])
                self.assertEqual([item["kind"] for item in result["differences"]], ["expected_change_not_observed"])
                self.assertEqual(result["expected_changes"], [{"path": "/image", "applied": False}])

    def test_expected_change_from_a_stale_baseline_refuses_to_pass(self):
        change = {"path": ["image"], "committed": "child:8.4.0", "observed": "child:9.2.0", "reason": "D7"}
        observed = self.observed()
        observed["image"] = "child:9.2.0"
        self.assertFalse(body(RECEIPT, observed, excluded=[RUN], expected_changes=[change])["equal"])

    def test_expected_change_path_must_exist(self):
        change = {"path": ["absent"], "committed": 1, "observed": 2, "reason": "r"}
        result = body(RECEIPT, self.observed(), excluded=[RUN], expected_changes=[change])
        self.assertEqual([item["kind"] for item in result["differences"]], ["expected_change_path_absent"])

    def test_declaration_errors_refuse(self):
        cases = {
            "unused pattern": dict(excluded=[{"class": "c", "reason": "r", "paths": [["nowhere"]]}]),
            "pattern naming an interior node": dict(excluded=[{"class": "c", "reason": "r", "paths": [["flags"]]}]),
            "change under an exclusion": dict(excluded=[RUN], expected_changes=[
                {"path": ["source_commit"], "committed": "a" * 40, "observed": "c" * 40, "reason": "r"}]),
            "change with equal values": dict(expected_changes=[
                {"path": ["image"], "committed": "x", "observed": "x", "reason": "r"}]),
            "wildcard change path": dict(expected_changes=[
                {"path": ["ops", "*"], "committed": 1, "observed": 2, "reason": "r"}]),
            "duplicate class": dict(excluded=[RUN, RUN]),
            "class without reason": dict(excluded=[dict(RUN, reason="")]),
            "class with extra key": dict(excluded=[dict(RUN, note="x")]),
        }
        for name, lane in cases.items():
            with self.subTest(name), self.assertRaises(compare.Refusal):
                body(RECEIPT, self.observed(), **lane)
        with self.assertRaises(compare.Refusal):
            compare.lane_declaration(dict(declaration(), extra=1), "lane")
        with self.assertRaises(compare.Refusal):
            compare.lane_declaration(declaration(), "other")

    def test_json_inputs_are_strict(self):
        for data in (b'{"a": 1, "a": 2}', b'{"a": NaN}', b'{"a": Infinity}', b"\xff", b"{"):
            with self.subTest(data=data), self.assertRaises(compare.Refusal):
                compare.load_json(data)


def RECEIPT_VALUE(path):
    node = RECEIPT
    for part in path:
        node = node[part]
    return node


class DeviceDeclarationTests(unittest.TestCase):
    """The committed device declaration against the committed hamcp366 receipt."""

    @classmethod
    def setUpClass(cls):
        cls.declaration = json.loads(compare.DECLARATION.read_text())
        cls.entry, cls.classes, cls.changes = compare.lane_declaration(cls.declaration, DEVICE)
        cls.data = (ROOT / cls.entry["committed_receipt"]).read_bytes()
        cls.committed = json.loads(cls.data)

    def applied(self):
        observed = copy.deepcopy(self.committed)
        for path, (_, after) in self.changes.items():
            setting(observed, path, after)
        return observed

    def test_baseline_is_pinned(self):
        self.assertEqual(self.entry["committed_receipt"], "docs/evidence/hamcp366/device_configuration_entity.json")
        self.assertEqual(hashlib.sha256(self.data).hexdigest(), self.entry["committed_receipt_sha256"])
        self.assertEqual(self.entry["harness"], "scripts/core_device_configuration_local.py")

    def test_d7_changes_are_the_only_expected_changes(self):
        self.assertEqual(set(self.changes), {("images", UPSTREAM, "image"), ("images", UPSTREAM, "config_digest")})
        self.assertTrue(self.changes[("images", UPSTREAM, "image")][1].startswith("ghcr.io/homeassistant-ai/ha-mcp:9.2.0@"))

    def test_perturbing_every_excluded_leaf_still_compares_equal(self):
        observed, count = self.applied(), 0
        for path, value in compare.leaves(self.committed):
            if compare.exclusion_class(self.classes, path) and isinstance(value, (str, int, float)) \
                    and not isinstance(value, bool):
                setting(observed, path, value + ("x" if isinstance(value, str) else 1))
                count += 1
        result = compare.compare(self.committed, observed, self.classes, self.changes)
        self.assertTrue(result["equal"], result["differences"][:3])
        self.assertGreater(count, 150)

    def test_the_unchanged_baseline_does_not_pass(self):
        result = compare.compare(self.committed, self.committed, self.classes, self.changes)
        self.assertEqual({item["kind"] for item in result["differences"]}, {"expected_change_not_observed"})

    def test_lane_semantics_stay_compared(self):
        compared = [("result",), ("cleanup", "clean"), ("authority_transition", 0), ("journal_sha256",),
                    ("profile", "contract_fingerprint"), ("service_sends",), ("stub_writes",), ("fallback_count",),
                    ("images", "beta25-real-ha-ha-2026-11-0-device-configuration", "config_digest"),
                    ("operations", "number_forward", "status"), ("operations", "number_forward", "observation_attempts"),
                    ("operations", "number_stored_value_mismatch", "final", "terminal_outcome"),
                    ("operations", "number_stored_value_mismatch", "final", "verification", "manual_review_reason_code"),
                    ("refusals", "extra_field", "code"), ("refusals", "extra_field", "sends"),
                    ("observations", "reregistration_fingerprint", "reload_only", "targets",
                     "number.aqara_door_and_window_sensor_p2_sensitivity", "fields", "context_id", "changed"),
                    ("observations", "reregistration_fingerprint", "reload_only", "targets",
                     "number.aqara_door_and_window_sensor_p2_sensitivity", "changed_fields", 0),
                    ("observations", "reregistration_fingerprint", "reload_only", "targets",
                     "number.aqara_door_and_window_sensor_p2_sensitivity", "fields", "device.identifiers", "after",
                     "sha256"),
                    ("observations", "reregistration_fingerprint", "reload_only", "targets",
                     "number.aqara_door_and_window_sensor_p2_sensitivity", "registry_level", "unique_id", "after"),
                    ("stub_host_entry", "name"), ("network_posture", "host_bind_address")]
        leaf_paths = {path for path, _ in compare.leaves(self.committed)}
        for path in compared:
            with self.subTest(path=path):
                self.assertIn(path, leaf_paths)
                self.assertIsNone(compare.exclusion_class(self.classes, path))

    def test_a_semantic_change_fails_the_comparison(self):
        for path, value in ((("operations", "select_forward", "status"), "changed_unverified"),
                            (("refusals", "extra_field", "code"), "other"),
                            (("operations", "switch_undo", "observation_attempts"), 2)):
            with self.subTest(path=path):
                observed = self.applied()
                setting(observed, path, value)
                result = compare.compare(self.committed, observed, self.classes, self.changes)
                self.assertEqual([item["path"] for item in result["differences"]], [compare.pointer(path)])


class CommandTests(unittest.TestCase):
    def call(self, *argv):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            code = compare.main(list(argv))
        return code, json.loads(stream.getvalue())

    def test_exit_codes_and_report(self):
        committed = json.loads((ROOT / "docs/evidence/hamcp366/device_configuration_entity.json").read_text())
        with tempfile.TemporaryDirectory() as directory:
            same, applied = Path(directory, "same.json"), Path(directory, "applied.json")
            same.write_text(json.dumps(committed))
            value = copy.deepcopy(committed)
            value["images"][UPSTREAM]["image"] = ("ghcr.io/homeassistant-ai/ha-mcp:9.2.0@sha256:"
                                                  "9de5bf6c4216142fdffe862900abe72dac8dfe9621dd0b2b050d132372b676ba")
            value["images"][UPSTREAM]["config_digest"] = (
                "sha256:9de5bf6c4216142fdffe862900abe72dac8dfe9621dd0b2b050d132372b676ba")
            value["started_utc"] = "2026-11-20T00:00:00+00:00"
            applied.write_text(json.dumps(value))
            output = Path(directory, "report.json")
            code, report = self.call("--lane", DEVICE, "--observed", str(applied), "--output", str(output))
            self.assertEqual((code, report["equal"], report["model"]), (0, True, compare.REPORT_MODEL))
            self.assertEqual(json.loads(output.read_text()), report)
            self.assertEqual([item["path"] for item in report["excluded_differences"]["run_identity"]],
                             ["/started_utc"])
            self.assertEqual(self.call("--lane", DEVICE, "--observed", str(same))[0], 1)
            self.assertEqual(self.call("--lane", DEVICE, "--observed", str(applied), "--output", str(output))[0], 2)
            self.assertEqual(self.call("--lane", "unknown", "--observed", str(applied))[0], 2)
            self.assertEqual(self.call("--lane", DEVICE, "--observed", str(Path(directory, "absent.json")))[0], 2)

    def test_a_moved_baseline_refuses(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            (root / "scripts/compare_core_receipts.py").write_text("")
            (root / "committed.json").write_text(json.dumps(RECEIPT))
            path = root / "declaration.json"
            path.write_text(json.dumps(declaration(committed_receipt_sha256="0" * 64)))
            with self.assertRaisesRegex(compare.Refusal, "committed_receipt_sha256_mismatch"):
                compare.run("lane", root / "committed.json", path, root)
            path.write_text(json.dumps(declaration(
                committed_receipt_sha256=hashlib.sha256((root / "committed.json").read_bytes()).hexdigest(),
                harness="scripts/absent.py")))
            with self.assertRaisesRegex(compare.Refusal, "harness_not_found"):
                compare.run("lane", root / "committed.json", path, root)


if __name__ == "__main__":
    unittest.main()
