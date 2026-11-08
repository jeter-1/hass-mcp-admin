"""HAMCP-319 design section 5.1 exact-image admission lanes (offline).

Covers the support-window ``admission`` derivation, the lane manifest writer and
gate in ``scripts/exact_image_admission_lanes.py``, and the ``ci.yml`` wiring.
Required negatives: a missing lane, a listed-but-unexecuted case and a dormant
lane reported as a pass each fail. Nothing here runs Docker, reaches a network
or reads a live system; image identity uses an injected command runner.
"""

from __future__ import annotations

import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unittest
import unittest.mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.append(str(ROOT / "tests"))
from support.support_window_matrix import registry_matrix, support_window  # noqa: E402

WORKFLOW = ROOT / ".github/workflows/ci.yml"
WINDOW = ROOT / "ci/support-window.yaml"
LANES_JOB = "exact-image-admission-lanes"
GATE_JOB = "exact-image-admission-manifest-gate"
COMMIT = "c" * 40
SYNTHETIC_91 = {
    # Synthetic, unmistakably fake pins: never real 9.1 evidence.
    "upstream_version": "9.1.0",
    "upstream_image": "ghcr.io/homeassistant-ai/ha-mcp@sha256:" + "9" * 64,
    "image_index_digest": "sha256:" + "9" * 64,
    "image_revision": "9" * 40,
    "source_commit": "8" * 40,
    "architecture_image_digests": {"linux/amd64": "sha256:" + "a" * 64, "linux/arm64": "sha256:" + "b" * 64},
}


def lanes_module():
    spec = importlib.util.spec_from_file_location(
        "exact_image_admission_lanes_under_test", ROOT / "scripts/exact_image_admission_lanes.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve the module by name
    spec.loader.exec_module(module)
    return module


LANES = lanes_module()


def registry_with_91():
    registry = copy.deepcopy(registry_matrix())
    registry["include"].append(copy.deepcopy(SYNTHETIC_91))
    return registry


def current_matrix():
    sw = support_window()
    return sw.admission_matrix(sw.load(ROOT), registry_matrix())


def passing_results(row):
    """Every listed case of an active row, passed, with matching identity."""

    results = {}
    for case in LANES.CASES[row["configuration"]]:
        observed = {}
        if case.name.startswith("image_identity_"):
            role = case.name.removeprefix("image_identity_")
            peer = row["peers"][role]
            observed = {"role": role, "version": peer["upstream_version"],
                        "image_digest": peer["image_index_digest"],
                        "manifest_digest": peer["architecture_image_digests"]["linux/" + row["architecture"]],
                        "config_digest": "sha256:" + "d" * 64}
        elif case.name.endswith(("gateway_acceptance", "exact_admission_health", "_useful")):
            observed = {"negotiated_protocol": LANES.EXPECTED_PROTOCOL}
        results[case.name] = LANES.case_result(case.name, "passed", form=case.endpoint_form, observed=observed)
    return results


def manifests_for(matrix, *, results=None):
    out = []
    for row in matrix["include"]:
        lane_results = {} if row["state"] == "dormant" else (results or passing_results)(row)
        out.append(LANES.build_manifest(row, COMMIT, lane_results))
    return out


class SupportWindowAdmissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sw = support_window()
        cls.window = cls.sw.load(ROOT)

    def test_committed_window_declares_85_baseline_and_91_candidate(self):
        self.assertEqual(self.window["admission"], {"baseline_version": "8.5.0", "candidate_version": "9.1.0"})
        self.assertEqual(self.window["admission"]["baseline_version"], self.window["ha_mcp"]["current"]["version"])

    def test_today_85_lanes_active_and_91_lanes_dormant_on_both_architectures(self):
        rows = current_matrix()["include"]
        self.assertEqual({(r["configuration"], r["architecture"]) for r in rows}, set(LANES.REQUIRED_LANES))
        self.assertEqual(len(rows), 6)
        for row in rows:
            with self.subTest(lane=(row["configuration"], row["architecture"])):
                self.assertEqual(row["runner"], self.sw.RUNNERS[row["architecture"]])
                if row["configuration"] == "85_only":
                    self.assertEqual(row["state"], "active")
                    self.assertEqual(set(row["peers"]), {"baseline"})
                    self.assertEqual(row["peers"]["baseline"]["image_index_digest"],
                                     self.window["ha_mcp"]["current"]["image_index_digest"])
                    self.assertEqual(row["dormant_reason"], "")
                else:
                    self.assertEqual(row["state"], "dormant")
                    self.assertEqual(row["peers"], {})
                    self.assertTrue(row["dormant_reason"].startswith("not executed:"))

    def test_reviewed_registry_91_row_activates_every_lane_without_changing_gateway(self):
        before = {tier: self.sw.gateway_matrix(self.window, tier, registry_matrix()) for tier in self.sw.TIERS}
        registry = registry_with_91()
        rows = self.sw.admission_matrix(self.window, registry)["include"]
        self.assertTrue(all(row["state"] == "active" for row in rows))
        by_lane = {(r["configuration"], r["architecture"]): r for r in rows}
        self.assertEqual(set(by_lane["91_only", "arm64"]["peers"]), {"candidate"})
        self.assertEqual(set(by_lane["side_by_side", "amd64"]["peers"]), {"baseline", "candidate"})
        self.assertEqual(by_lane["91_only", "amd64"]["peers"]["candidate"], SYNTHETIC_91)
        # The legacy-URL gateway lane never picks up the 9.1 candidate.
        for tier in self.sw.TIERS:
            self.assertEqual(self.sw.gateway_matrix(self.window, tier, registry), before[tier])

    def test_malformed_candidate_registry_row_refuses_instead_of_staying_dormant(self):
        for field in ("image_index_digest", "source_commit", "architecture_image_digests"):
            with self.subTest(field=field):
                registry = registry_with_91()
                registry["include"][-1].pop(field)
                with self.assertRaises(self.sw.Refusal) as caught:
                    self.sw.admission_matrix(self.window, registry)
                self.assertEqual(str(caught.exception), "admission_candidate_registry_malformed")
        registry = registry_with_91()
        del registry["include"][-1]["architecture_image_digests"]["linux/arm64"]
        with self.assertRaises(self.sw.Refusal):
            self.sw.admission_matrix(self.window, registry)

    def test_baseline_registry_mismatch_and_duplicates_refuse(self):
        registry = copy.deepcopy(registry_matrix())
        row = next(r for r in registry["include"] if r["upstream_version"] == "8.5.0")
        row["image_index_digest"] = "sha256:" + "0" * 64
        with self.assertRaises(self.sw.Refusal) as caught:
            self.sw.admission_matrix(self.window, registry)
        self.assertEqual(str(caught.exception), "admission_baseline_registry_mismatch")
        registry = registry_with_91()
        registry["include"].append(copy.deepcopy(SYNTHETIC_91))
        with self.assertRaises(self.sw.Refusal) as caught:
            self.sw.admission_matrix(self.window, registry)
        self.assertEqual(str(caught.exception), "registry_matrix_duplicate")
        registry = {"include": [r for r in registry_matrix()["include"] if r["upstream_version"] != "8.5.0"]}
        with self.assertRaises(self.sw.Refusal) as caught:
            self.sw.admission_matrix(self.window, registry)
        self.assertEqual(str(caught.exception), "admission_baseline_not_in_reviewed_registry")

    def test_window_without_admission_emits_no_lane(self):
        window = copy.deepcopy(self.window)
        del window["admission"]
        self.assertEqual(self.sw.admission_matrix(window, registry_matrix()), {"include": []})


class SupportWindowAdmissionLoadTests(unittest.TestCase):
    def setUp(self):
        self.sw = support_window()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "ci").mkdir()
        (self.root / "tests" / "fixtures").mkdir(parents=True)
        for fixture in (ROOT / "tests" / "fixtures").glob("core_*_lane_provenance.json"):
            shutil.copy2(fixture, self.root / "tests" / "fixtures" / fixture.name)
        self.base = yaml.safe_load(WINDOW.read_text(encoding="utf-8"))

    def tearDown(self):
        self._tmp.cleanup()

    def refuses(self, change, category):
        window = copy.deepcopy(self.base)
        change(window)
        (self.root / "ci" / "support-window.yaml").write_text(yaml.safe_dump(window, sort_keys=False),
                                                               encoding="utf-8")
        with self.assertRaises(self.sw.Refusal) as caught:
            self.sw.load(self.root)
        self.assertEqual(str(caught.exception), category)

    def test_admission_key_refusals(self):
        self.refuses(lambda w: w["admission"].update(extra="1.0.0"), "admission_keys")
        self.refuses(lambda w: w["admission"].pop("candidate_version"), "admission_keys")
        self.refuses(lambda w: w["admission"].update(candidate_version="9.1"), "admission_keys")
        self.refuses(lambda w: w.update(admission=[]), "admission_keys")
        self.refuses(lambda w: w["admission"].update(baseline_version="8.4.1"), "admission_baseline_not_current")
        self.refuses(lambda w: w["admission"].update(candidate_version="8.5.0"), "admission_candidate_in_slot")
        self.refuses(lambda w: w.update(unexpected={}), "window_schema")


class ClassifyFormTests(unittest.TestCase):
    def test_strict_full_and_legacy_labels(self):
        strict = ("http://abcdef12-ha-mcp:18086/synthetic-upstream-secret/catalog/full",
                  "http://h:1/a/b/catalog/full")
        legacy = ("http://abcdef12-ha-mcp:18086/synthetic-upstream-secret/mcp",
                  "http://h:1/catalog/full", "http://h:1/a/catalog/full/", "http://h:1/a/catalog/full?x=1",
                  "http://h:1/a%2Fb/catalog/full", "http://h:1/a/../catalog/full", "http://h:1/a//catalog/full",
                  "http://h:1/readonly/catalog/full", "http://h:1/a/search/catalog/full", "http://h:1/a/catalog/fulls",
                  "http://h:1/a/catalog/full#f", "not a url")
        for url in strict:
            self.assertEqual(LANES.classify_form(url), "strict_full", url)
        for url in legacy:
            self.assertEqual(LANES.classify_form(url), "legacy", url)

    def test_every_case_form_is_a_known_form(self):
        for configuration, cases in LANES.CASES.items():
            self.assertEqual(len({c.name for c in cases}), len(cases), configuration)
            for case in cases:
                self.assertIn(case.endpoint_form, (None, *LANES.FORMS))
                self.assertRegex(case.name, LANES.CASE_NAME)
            self.assertEqual({c.name.removeprefix("image_identity_") for c in cases
                              if c.name.startswith("image_identity_")}, set(LANES.ROLES[configuration]))


class ManifestWriterTests(unittest.TestCase):
    def setUp(self):
        self.rows = {(r["configuration"], r["architecture"]): r for r in current_matrix()["include"]}

    def test_dormant_lane_is_not_executed_with_reason_on_every_case(self):
        row = self.rows["91_only", "arm64"]
        manifest = LANES.build_manifest(row, COMMIT, {})
        self.assertEqual(set(manifest), LANES.MANIFEST_KEYS)
        self.assertEqual(manifest["result"], "NOT_EXECUTED")
        self.assertEqual(manifest["executed_count"], 0)
        self.assertEqual(manifest["case_count"], len(LANES.CASES["91_only"]))
        self.assertEqual(manifest["images"], [])
        self.assertIsNone(manifest["negotiated_protocol"])
        self.assertEqual(manifest["dormant_reason"], row["dormant_reason"])
        for case in manifest["cases"]:
            self.assertEqual(case["status"], "not_executed")
            self.assertEqual(case["reason"], row["dormant_reason"])

    def test_dormant_lane_with_any_case_result_refuses(self):
        row = self.rows["side_by_side", "amd64"]
        result = {"image_identity_baseline": LANES.case_result("image_identity_baseline", "passed", form=None)}
        with self.assertRaises(LANES.LaneFailure) as caught:
            LANES.build_manifest(row, COMMIT, result)
        self.assertEqual(str(caught.exception), "manifest_dormant_lane_has_case_results")
        with self.assertRaises(LANES.LaneFailure):
            LANES.build_manifest({**row, "dormant_reason": ""}, COMMIT, {})

    def test_active_lane_with_every_case_passed_is_pass(self):
        row = self.rows["85_only", "amd64"]
        manifest = LANES.build_manifest(row, COMMIT, passing_results(row))
        self.assertEqual(manifest["result"], "PASS")
        self.assertEqual(manifest["executed_count"], manifest["case_count"])
        self.assertEqual(manifest["negotiated_protocol"], "2025-03-26")
        self.assertEqual(manifest["endpoint_forms"], ["legacy", "strict_full"])
        self.assertEqual(manifest["skips"], [])
        image = manifest["images"][0]
        self.assertEqual(set(image), LANES.IMAGE_KEYS)
        self.assertEqual(image["manifest_digest"], row["peers"]["baseline"]["architecture_image_digests"]["linux/amd64"])

    def test_active_lane_missing_case_is_recorded_not_executed_and_fails(self):
        row = self.rows["85_only", "arm64"]
        results = passing_results(row)
        del results["85_strict_full_engineering_fails_closed"]
        manifest = LANES.build_manifest(row, COMMIT, results)
        self.assertEqual(manifest["result"], "FAIL")
        self.assertEqual(manifest["executed_count"], manifest["case_count"] - 1)
        missing = next(c for c in manifest["cases"] if c["name"] == "85_strict_full_engineering_fails_closed")
        self.assertEqual(missing["status"], "not_executed")
        self.assertTrue(missing["reason"])

    def test_skip_needs_reason_and_is_listed_and_fails(self):
        row = self.rows["85_only", "amd64"]
        results = passing_results(row)
        results["85_strict_full_direct_initialize_refused"] = LANES.case_result(
            "85_strict_full_direct_initialize_refused", "skipped", form="strict_full", reason="synthetic skip")
        manifest = LANES.build_manifest(row, COMMIT, results)
        self.assertEqual(manifest["result"], "FAIL")
        self.assertEqual(manifest["skips"], [{"case": "85_strict_full_direct_initialize_refused",
                                               "reason": "synthetic skip"}])
        results["85_strict_full_direct_initialize_refused"]["reason"] = ""
        with self.assertRaises(LANES.LaneFailure):
            LANES.build_manifest(row, COMMIT, results)

    def test_unlisted_result_form_mismatch_and_bad_commit_refuse(self):
        row = self.rows["85_only", "amd64"]
        results = passing_results(row)
        results["91_typed_controls_verified_readback"] = LANES.case_result(
            "91_typed_controls_verified_readback", "passed", form="strict_full")
        with self.assertRaises(LANES.LaneFailure):
            LANES.build_manifest(row, COMMIT, results)
        results = passing_results(row)
        results["85_legacy_gateway_acceptance"]["endpoint_form"] = "strict_full"
        with self.assertRaises(LANES.LaneFailure):
            LANES.build_manifest(row, COMMIT, results)
        with self.assertRaises(LANES.LaneFailure):
            LANES.build_manifest(row, "not-a-commit", passing_results(row))

    def test_write_manifest_command_exit_codes(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            dormant = self.rows["91_only", "amd64"]
            out = base / "dormant.json"
            self.assertEqual(LANES.main(["write-manifest", "--row", json.dumps(dormant), "--engineering-commit",
                                         COMMIT, "--cases-dir", str(base / "absent"), "--output", str(out)]), 0)
            self.assertEqual(json.loads(out.read_text())["result"], "NOT_EXECUTED")
            active = self.rows["85_only", "amd64"]
            cases = base / "cases"
            cases.mkdir()
            self.assertEqual(LANES.main(["write-manifest", "--row", json.dumps(active), "--engineering-commit",
                                         COMMIT, "--cases-dir", str(cases), "--output", str(out)]), 1)
            self.assertEqual(json.loads(out.read_text())["executed_count"], 0)
            for name, value in passing_results(active).items():
                (cases / f"{name}.json").write_text(json.dumps(value))
            self.assertEqual(LANES.main(["write-manifest", "--row", json.dumps(active), "--engineering-commit",
                                         COMMIT, "--cases-dir", str(cases), "--output", str(out)]), 0)
            self.assertEqual(json.loads(out.read_text())["result"], "PASS")


class GateTests(unittest.TestCase):
    def setUp(self):
        self.matrix = current_matrix()
        self.manifests = manifests_for(self.matrix)

    def verify(self, manifests=None, matrix=None, commit=COMMIT):
        return LANES.verify_manifests(self.manifests if manifests is None else manifests,
                                      self.matrix if matrix is None else matrix, commit)

    def lane(self, manifests, configuration, architecture):
        return next(m for m in manifests if (m["configuration"], m["architecture"]) == (configuration, architecture))

    def test_two_active_pass_and_four_dormant_lanes_pass_the_gate(self):
        acceptance, errors = self.verify()
        self.assertEqual(errors, [])
        self.assertEqual(acceptance["85_only"], "ACCEPTED")
        self.assertEqual(acceptance["91_only"], "NOT ACCEPTED (dormant: not executed)")
        self.assertEqual(acceptance["side_by_side"], "NOT ACCEPTED (dormant: not executed)")

    def test_missing_lane_fails(self):
        for configuration, architecture in sorted(LANES.REQUIRED_LANES):
            with self.subTest(lane=(configuration, architecture)):
                manifests = [m for m in self.manifests
                             if (m["configuration"], m["architecture"]) != (configuration, architecture)]
                acceptance, errors = self.verify(manifests)
                self.assertTrue(any("missing lane" in e for e in errors), errors)
                self.assertTrue(acceptance[configuration].startswith("NOT ACCEPTED"))
                self.assertNotEqual(acceptance[configuration], "NOT ACCEPTED (dormant: not executed)")

    def test_lane_missing_from_derived_matrix_fails(self):
        matrix = {"include": [r for r in self.matrix["include"]
                              if (r["configuration"], r["architecture"]) != ("85_only", "arm64")]}
        _acceptance, errors = self.verify(matrix=matrix)
        self.assertTrue(any("missing from the derived matrix" in e for e in errors), errors)

    def test_listed_but_unexecuted_case_fails(self):
        def results(row):
            value = passing_results(row)
            value.pop("85_legacy_direct_initialize_no_redirect")
            return value

        acceptance, errors = self.verify(manifests_for(self.matrix, results=results))
        self.assertTrue(any("listed but not executed: 85_legacy_direct_initialize_no_redirect" in e
                            for e in errors), errors)
        self.assertEqual(acceptance["85_only"], "NOT ACCEPTED")

    def test_edited_manifest_claiming_pass_with_unexecuted_case_fails(self):
        manifests = copy.deepcopy(self.manifests)
        lane = self.lane(manifests, "85_only", "amd64")
        lane["cases"][1]["status"] = "not_executed"
        _acceptance, errors = self.verify(manifests)
        self.assertTrue(any("listed but not executed" in e for e in errors), errors)
        self.assertTrue(any("executed count" in e for e in errors), errors)

    def test_dormant_lane_reported_as_pass_fails(self):
        for configuration in ("91_only", "side_by_side"):
            with self.subTest(configuration=configuration):
                manifests = copy.deepcopy(self.manifests)
                lane = self.lane(manifests, configuration, "arm64")
                lane["result"] = "PASS"
                acceptance, errors = self.verify(manifests)
                self.assertTrue(any("dormant lane reported 'PASS'" in e for e in errors), errors)
                self.assertEqual(acceptance[configuration], "NOT ACCEPTED")

    def test_dormant_lane_claiming_executed_cases_fails(self):
        manifests = copy.deepcopy(self.manifests)
        lane = self.lane(manifests, "91_only", "amd64")
        lane["cases"][0]["status"] = "passed"
        lane["executed_count"] = 1
        _acceptance, errors = self.verify(manifests)
        self.assertTrue(any("dormant lane reports executed cases" in e for e in errors), errors)

    def test_state_differing_from_derived_matrix_fails(self):
        manifests = copy.deepcopy(self.manifests)
        self.lane(manifests, "85_only", "amd64")["state"] = "dormant"
        _acceptance, errors = self.verify(manifests)
        self.assertTrue(any("differs from the derived state" in e for e in errors), errors)
        # An active derived lane cannot be satisfied by a dormant manifest.
        row = next(r for r in self.matrix["include"] if r["configuration"] == "85_only")
        dormant = LANES.build_manifest({**row, "state": "dormant", "dormant_reason": "x"}, COMMIT, {})
        manifests = [m for m in self.manifests
                     if (m["configuration"], m["architecture"]) != ("85_only", row["architecture"])] + [dormant]
        _acceptance, errors = self.verify(manifests)
        self.assertTrue(errors)

    def test_image_identity_and_commit_and_protocol_mismatches_fail(self):
        manifests = copy.deepcopy(self.manifests)
        self.lane(manifests, "85_only", "arm64")["images"][0]["manifest_digest"] = (
            self.matrix["include"][0]["peers"]["baseline"]["architecture_image_digests"]["linux/amd64"])
        _acceptance, errors = self.verify(manifests)
        self.assertTrue(any("differs from the reviewed registry row" in e for e in errors), errors)
        _acceptance, errors = self.verify(commit="e" * 40)
        self.assertTrue(any("Engineering commit" in e for e in errors), errors)
        manifests = copy.deepcopy(self.manifests)
        self.lane(manifests, "85_only", "amd64")["negotiated_protocol"] = "2025-06-18"
        _acceptance, errors = self.verify(manifests)
        self.assertTrue(any("negotiated protocol" in e for e in errors), errors)

    def test_duplicate_and_unknown_manifests_fail(self):
        _acceptance, errors = self.verify(self.manifests + [copy.deepcopy(self.manifests[0])])
        self.assertTrue(any("more than one manifest" in e for e in errors), errors)
        stray = copy.deepcopy(self.manifests[0])
        stray["configuration"] = "86_only"
        _acceptance, errors = self.verify(self.manifests + [stray])
        self.assertTrue(any("not a section 5.1 lane" in e for e in errors), errors)

    def test_activated_91_lanes_need_their_own_executed_cases(self):
        sw = support_window()
        matrix = sw.admission_matrix(sw.load(ROOT), registry_with_91())
        # Only identity can run until the 9.1 case runners exist: never accepted.
        def identity_only(row):
            return {k: v for k, v in passing_results(row).items() if k.startswith("image_identity_")}

        manifests = [LANES.build_manifest(row, COMMIT, passing_results(row) if row["configuration"] == "85_only"
                                          else identity_only(row)) for row in matrix["include"]]
        acceptance, errors = LANES.verify_manifests(manifests, matrix, COMMIT)
        self.assertEqual(acceptance["91_only"], "NOT ACCEPTED")
        self.assertEqual(acceptance["side_by_side"], "NOT ACCEPTED")
        self.assertTrue(any("91_only" in e and "listed but not executed" in e for e in errors), errors)
        # With every listed case passed and identities matching, they are accepted.
        acceptance, errors = LANES.verify_manifests(manifests_for(matrix), matrix, COMMIT)
        self.assertEqual(errors, [])
        self.assertEqual(set(acceptance.values()), {"ACCEPTED"})

    def test_gate_command_reads_manifest_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "m").mkdir()
            for manifest in self.manifests:
                LANES.write_json(base / "m" / f"{manifest['configuration']}-{manifest['architecture']}.json", manifest)
            args = ["gate", "--manifests-dir", str(base / "m"), "--admission-matrix", json.dumps(self.matrix),
                    "--engineering-commit", COMMIT, "--output", str(base / "gate.json")]
            self.assertEqual(LANES.main(args), 0)
            self.assertEqual(json.loads((base / "gate.json").read_text())["result"], "PASS")
            (base / "m" / "85_only-arm64.json").unlink()
            self.assertEqual(LANES.main(args), 1)
            self.assertEqual(json.loads((base / "gate.json").read_text())["result"], "FAIL")


class CaseRunnerTests(unittest.TestCase):
    def test_initialize_redirect_always_fails(self):
        for status in (301, 302, 307, 308):
            for expect in ("served", "refused"):
                with self.assertRaises(LANES.LaneFailure) as caught:
                    LANES.evaluate_initialize(status, "", "", expect=expect, expected_version=None)
                self.assertEqual(str(caught.exception), "initialize_redirect_observed")

    def test_initialize_served_json_and_sse(self):
        message = {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-03-26",
                                                         "serverInfo": {"name": "ha-mcp", "version": "8.5.0"}}}
        observed = LANES.evaluate_initialize(200, "application/json", json.dumps(message),
                                             expect="served", expected_version="8.5.0")
        self.assertEqual(observed["upstream_protocol"], "2025-03-26")
        self.assertEqual(observed["negotiated_protocol"], "2025-03-26")
        sse = "event: message\ndata: " + json.dumps(message) + "\n\n"
        LANES.evaluate_initialize(200, "text/event-stream", sse, expect="served", expected_version="8.5.0")
        with self.assertRaises(LANES.LaneFailure):
            LANES.evaluate_initialize(200, "application/json", json.dumps(message), expect="served",
                                      expected_version="9.1.0")
        other = copy.deepcopy(message)
        other["result"]["protocolVersion"] = "2025-06-18"
        with self.assertRaises(LANES.LaneFailure):
            LANES.evaluate_initialize(200, "application/json", json.dumps(other), expect="served",
                                      expected_version=None)
        with self.assertRaises(LANES.LaneFailure):
            LANES.evaluate_initialize(404, "", "", expect="served", expected_version=None)

    def test_initialize_refused_needs_client_error(self):
        self.assertEqual(LANES.evaluate_initialize(404, "text/plain", "Not Found", expect="refused",
                                                   expected_version=None), {"http_status": 404})
        for status in (200, 500, 503):
            with self.assertRaises(LANES.LaneFailure):
                LANES.evaluate_initialize(status, "", "", expect="refused", expected_version=None)

    def test_initialize_command_checks_declared_form_and_writes_bounded_result(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "case.json"
            args = LANES.argparse.Namespace(case="85_strict_full_direct_initialize_refused",
                                            url="http://127.0.0.1:18086/synthetic-upstream-secret/catalog/full",
                                            form="strict_full", expect="refused", expected_version=None,
                                            output=str(out))
            self.assertEqual(LANES.command_initialize(args, post=lambda url: (404, "text/plain", "nf")), 0)
            value = json.loads(out.read_text())
            self.assertEqual(value["status"], "passed")
            self.assertNotIn("synthetic-upstream-secret", out.read_text())
            args.form = "legacy"
            self.assertEqual(LANES.command_initialize(args, post=lambda url: (404, "text/plain", "nf")), 1)
            self.assertEqual(json.loads(out.read_text())["reason"], "endpoint_form_mismatch")

    def test_fail_closed_paths(self):
        observed = LANES.evaluate_fail_closed([(503, "{}")] * 3, (503, "{}"), None)
        self.assertEqual(observed["path"], "never_ready")
        with self.assertRaises(LANES.LaneFailure):
            LANES.evaluate_fail_closed([(503, "{}")], (200, "{}"), None)
        with self.assertRaises(LANES.LaneFailure):
            LANES.evaluate_fail_closed([(503, '{"code":"upstream_redirect_refused"}')], (503, ""), None)
        with self.assertRaises(LANES.LaneFailure):
            LANES.evaluate_fail_closed([], (503, ""), None)
        # Not listening yet (0) then answering 503 is the startup race: accepted.
        observed = LANES.evaluate_fail_closed([(0, ""), (0, ""), (503, "{}")], (503, "{}"), None)
        self.assertEqual(observed["ready_statuses"], [0, 503])
        # Engineering never answered at all: refused, whatever the session probe says.
        with self.assertRaises(LANES.LaneFailure) as caught:
            LANES.evaluate_fail_closed([(0, "")] * 5, (503, "{}"), None)
        self.assertEqual(str(caught.exception), "fail_closed_engineering_never_answered")
        # The ready path needs a catalog; its full contract (#404 F1) is in
        # RollbackReadyPathTests below.
        with self.assertRaises(LANES.LaneFailure) as caught:
            LANES.evaluate_fail_closed([(503, "{}"), (200, "{}")], None, None)
        self.assertEqual(str(caught.exception), "fail_closed_ready_without_catalog")

    def test_record_wraps_existing_acceptance_result(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "r.json"
            missing = LANES.record_source("85_legacy_gateway_acceptance", "legacy", source)
            self.assertEqual((missing["status"], missing["reason"]), ("failed", "acceptance_result_missing"))
            source.write_text(json.dumps({"result": "FAIL", "failure_type": "AssertionError"}))
            self.assertEqual(LANES.record_source("x", "legacy", source)["status"], "failed")
            source.write_text(json.dumps({"result": "PASS", "probe": {"gateway_health": {
                "observed_protocol_version": "2025-03-26"}}}))
            passed = LANES.record_source("x", "legacy", source)
            self.assertEqual(passed["status"], "passed")
            self.assertEqual(passed["observed"], {"negotiated_protocol": "2025-03-26"})
            source.write_text(json.dumps({"result": "PASS", "a": {"observed_protocol_version": "2025-03-26"},
                                          "b": {"observed_protocol_version": "2024-11-05"}}))
            self.assertEqual(LANES.record_source("x", "legacy", source)["reason"], "acceptance_protocol_inconsistent")
        # A case not listed for the configuration is refused before any output.
        self.assertEqual(LANES.main(["record", "--configuration", "85_only", "--case",
                                     "91_typed_controls_verified_readback", "--source", "/nonexistent",
                                     "--output", "/nonexistent/out.json"]), 1)
        self.assertFalse(Path("/nonexistent/out.json").exists())

    def test_image_identity_with_injected_runner(self):
        row = current_matrix()["include"][1]["peers"]["baseline"]
        config = "sha256:" + "f" * 64

        def runner(overrides=None):
            overrides = overrides or {}
            index = {"manifests": [
                {"digest": d, "platform": {"os": p.split("/")[0], "architecture": p.split("/")[1]}}
                for p, d in row["architecture_image_digests"].items()] + [
                {"digest": "sha256:" + "1" * 64, "platform": {"os": "unknown", "architecture": "unknown"}}]}

            def run(argv):
                if argv[:4] == ["docker", "buildx", "imagetools", "inspect"]:
                    if argv[-1] != "--raw":
                        return "Name: x\nDigest: " + overrides.get("index", row["image_index_digest"]) + "\n"
                    if argv[4] == row["upstream_image"]:
                        return json.dumps(index)
                    return json.dumps({"config": {"digest": config}})
                if argv[:2] == ["docker", "pull"]:
                    return ""
                if "{{json .Config.Labels}}" in argv:
                    return json.dumps({"org.opencontainers.image.version": row["upstream_version"],
                                       "org.opencontainers.image.revision": row["image_revision"]})
                if "{{.Architecture}}" in argv:
                    return overrides.get("arch", "arm64")
                raise AssertionError(argv)
            return run

        identity = LANES.image_identity(row, "arm64", runner())
        self.assertEqual(identity, {"version": "8.5.0", "image_digest": row["image_index_digest"],
                                    "manifest_digest": row["architecture_image_digests"]["linux/arm64"],
                                    "config_digest": config})
        with self.assertRaises(LANES.LaneFailure):
            LANES.image_identity(row, "arm64", runner({"index": "sha256:" + "2" * 64}))
        with self.assertRaises(LANES.LaneFailure):
            LANES.image_identity(row, "arm64", runner({"arch": "amd64"}))
        with tempfile.TemporaryDirectory() as directory:
            args = LANES.argparse.Namespace(peers=json.dumps({}), configuration="85_only", architecture="arm64",
                                            cases_dir=directory)
            self.assertEqual(LANES.command_identity(args, runner()), 1)
            value = json.loads((Path(directory) / "image_identity_baseline.json").read_text())
            self.assertEqual((value["status"], value["reason"]), ("failed", "identity_peer_absent"))
            args.peers = json.dumps({"baseline": row})
            self.assertEqual(LANES.command_identity(args, runner()), 0)


ROLLBACK_CASE = "85_strict_full_engineering_fails_closed"
STRICT_URL = "http://127.0.0.1:18086/synthetic-upstream-secret/catalog/full"
READY = [(503, "{}"), (200, "{}")]


def gateway_health(*, failure=None, mutate=None, dynamic=0):
    """A real ``get_server_health`` envelope around the real gateway projection.

    ``failure`` drives the gateway's own disable-delegation path; ``mutate``
    edits the decoded ``data`` to build each bad observation.
    """

    from ha_mcp_engineering.models.responses import SuccessResponse
    from ha_mcp_engineering.providers.upstream_read_gateway import UpstreamReadGateway

    gateway = UpstreamReadGateway()
    if failure:
        gateway._record_failure(failure, disable_delegation=True)
    data = {"upstream_read_gateway": gateway.health_projection(), "dynamic_upstream_tool_count": dynamic}
    if mutate:
        mutate(data)
    return SuccessResponse(operation="get_server_health", summary="Returned safe beta operational health.",
                           data=data).to_json()


def set_gateway(**fields):
    return lambda data: data["upstream_read_gateway"].update(fields)


class FakeSession:
    """``tools/list`` pages behind cursors, for the bounded catalog reader."""

    def __init__(self, pages, cursors):
        self.pages, self.cursors, self.requested = pages, cursors, []

    async def list_tools(self, cursor=None, *, params=None):
        assert cursor is None, "the deprecated positional cursor is never used"
        requested = params.cursor if params is not None else None
        self.requested.append(requested)
        index = 0 if requested is None else int(requested.removeprefix("p"))
        tools = [LANES.argparse.Namespace(name=name) for name in self.pages[index % len(self.pages)]]
        return LANES.argparse.Namespace(tools=tools, nextCursor=self.cursors(index))


class RollbackReadyPathTests(unittest.TestCase):
    """#404 F1: the strict-full rollback probe needs positive fail-closed evidence.

    Each bad observation fails the probe, the case writer, the manifest writer
    (``FAIL``) and the aggregate gate (``85_only`` NOT ACCEPTED). The two
    supported success paths stay accepted.
    """

    @classmethod
    def setUpClass(cls):
        cls.static = LANES.engineering_static_tool_names()
        cls.matrix = current_matrix()
        cls.rows = {(r["configuration"], r["architecture"]): r for r in cls.matrix["include"]}
        policy = json.loads((ROOT / "hass_mcp_engineering_beta/ha_mcp_engineering/"
                             "upstream_tool_policy_8_5_0.json").read_text(encoding="utf-8"))
        cls.policy_85_names = sorted({tool["exposed_name"] for tool in policy["tools"]})

    def catalog(self, *, pages=None, complete=True, is_error=False, health=None):
        return {"pages": [sorted(self.static)] if pages is None else pages, "catalog_complete": complete,
                "health_is_error": is_error, "health_text": gateway_health() if health is None else health}

    def refused(self, catalog, category, static=None):
        with self.assertRaises(LANES.LaneFailure) as caught:
            LANES.evaluate_fail_closed(READY, None, catalog, self.static if static is None else static)
        self.assertEqual(str(caught.exception), category)

    # -- probe level ---------------------------------------------------------

    def test_valid_unadmitted_health_and_exact_empty_delegated_set_passes(self):
        for failure in (None, "invalid_response", "http_error"):
            observed = LANES.evaluate_fail_closed(READY, None, self.catalog(health=gateway_health(failure=failure)),
                                                  self.static)
            self.assertEqual(observed["path"], "ready_without_delegated_reads")
            self.assertEqual(observed["delegated_read_count"], 0)
            self.assertIn(observed["admission_status"], LANES.UNADMITTED_STATUSES)
        # Static names spread over several complete pages are one catalog.
        names = sorted(self.static)
        observed = LANES.evaluate_fail_closed(READY, None, self.catalog(pages=[names[:40], names[40:]]), self.static)
        self.assertEqual(observed["catalog_pages"], 2)

    def test_never_ready_with_authenticated_refusal_still_passes(self):
        observed = LANES.evaluate_fail_closed([(503, "{}")] * 3, (503, "{}"), None)
        self.assertEqual(observed["path"], "never_ready")

    def test_expected_delegated_set_is_empty_and_static_catalog_is_source_frozen(self):
        self.assertEqual(LANES.ROLLBACK_EXPECTED_DELEGATED_READS, frozenset())
        self.assertIn("get_server_health", self.static)
        # Every 8.5.0 upstream name except the Engineering-native-owned
        # ha_get_hacs_info (HAMCP-290) is outside the frozen static catalog, so
        # exposing any of them is a delegated read.
        self.assertTrue(self.policy_85_names)
        self.assertEqual(set(self.policy_85_names) & self.static, {"ha_get_hacs_info"})

    def test_extra_delegated_read_other_than_ha_get_state_fails(self):
        self.refused(self.catalog(pages=[sorted(self.static) + ["ha_get_history"]]),
                     "fail_closed_delegated_read_exposed")
        for name in sorted(set(self.policy_85_names) - self.static):
            with self.subTest(name=name):
                self.refused(self.catalog(pages=[sorted(self.static) + [name]]), "fail_closed_delegated_read_exposed")
        # HAMCP-290: a second ha_get_hacs_info beside the native one is a
        # duplicate name and still fails closed.
        self.refused(self.catalog(pages=[sorted(self.static) + ["ha_get_hacs_info"]]),
                     "fail_closed_catalog_duplicate_name")
        # Any non-static name is unexpected, whatever it is called.
        self.refused(self.catalog(pages=[sorted(self.static) + ["synthetic_unknown_tool"]]),
                     "fail_closed_delegated_read_exposed")

    def test_missing_expected_delegated_read_fails(self):
        # The rollback expected set is empty; the comparison is still exact in
        # both directions, so a non-empty expectation that is absent fails.
        with unittest.mock.patch.object(LANES, "ROLLBACK_EXPECTED_DELEGATED_READS", frozenset({"ha_get_state"})):
            self.refused(self.catalog(), "fail_closed_expected_delegated_read_missing")
            observed = LANES.evaluate_fail_closed(
                READY, None, self.catalog(pages=[sorted(self.static) + ["ha_get_state"]]), self.static)
            self.assertEqual(observed["delegated_read_count"], 1)

    def test_delegated_read_on_later_catalog_page_fails(self):
        self.refused(self.catalog(pages=[sorted(self.static), ["ha_get_history"]]),
                     "fail_closed_delegated_read_exposed")
        session = FakeSession([sorted(self.static), ["ha_get_history"]], lambda i: "p1" if i == 0 else None)
        collected = LANES.asyncio.run(LANES.collect_catalog(session))
        self.assertEqual(session.requested, [None, "p1"])
        self.assertTrue(collected["catalog_complete"])
        self.refused({**self.catalog(), **collected}, "fail_closed_delegated_read_exposed")

    def test_incomplete_or_malformed_catalog_fails(self):
        self.refused(self.catalog(complete=False), "fail_closed_catalog_incomplete")
        self.refused({k: v for k, v in self.catalog().items() if k != "catalog_complete"},
                     "fail_closed_catalog_incomplete")
        # A cursor that repeats, or pages past the bound, never completes.
        looping = FakeSession([sorted(self.static)], lambda i: "p0")
        collected = LANES.asyncio.run(LANES.collect_catalog(looping))
        self.assertFalse(collected["catalog_complete"])
        self.refused({**self.catalog(), **collected}, "fail_closed_catalog_incomplete")
        endless = FakeSession([sorted(self.static), []], lambda i: f"p{i + 1}")
        collected = LANES.asyncio.run(LANES.collect_catalog(endless))
        self.assertEqual(len(collected["pages"]), LANES.MAX_CATALOG_PAGES)
        self.assertFalse(collected["catalog_complete"])
        self.refused({**self.catalog(), **collected}, "fail_closed_catalog_incomplete")
        self.refused(self.catalog(pages=[["x"]] * (LANES.MAX_CATALOG_PAGES + 1)), "fail_closed_catalog_incomplete")
        self.refused(self.catalog(pages=[sorted(self.static) + [f"t{i}" for i in range(LANES.MAX_CATALOG_TOOLS)]]),
                     "fail_closed_catalog_incomplete")
        self.refused(self.catalog(pages=[]), "fail_closed_catalog_malformed")
        self.refused(self.catalog(pages=[sorted(self.static), None]), "fail_closed_catalog_malformed")
        self.refused(self.catalog(pages=[sorted(self.static) + [7]]), "fail_closed_catalog_malformed")
        self.refused(self.catalog(pages=[sorted(self.static), ["get_server_health"]]),
                     "fail_closed_catalog_duplicate_name")
        for static in (None, frozenset(), set(self.static)):
            with self.assertRaises(LANES.LaneFailure) as caught:
                LANES.evaluate_fail_closed(READY, None, self.catalog(), static)
            self.assertEqual(str(caught.exception), "fail_closed_static_catalog_unknown")

    def test_missing_health_fails(self):
        self.refused(self.catalog(health=""), "fail_closed_health_missing")
        self.refused(self.catalog(health="  \n"), "fail_closed_health_missing")
        self.refused({k: v for k, v in self.catalog().items() if k != "health_text"}, "fail_closed_health_missing")

    def test_errored_health_fails(self):
        from ha_mcp_engineering.models.responses import FailureResponse

        self.refused(self.catalog(is_error=True), "fail_closed_health_error")
        self.refused(self.catalog(is_error=True, health="Error executing tool get_server_health: boom"),
                     "fail_closed_health_error")
        self.refused({k: v for k, v in self.catalog().items() if k != "health_is_error"}, "fail_closed_health_error")
        failure = FailureResponse(operation="get_server_health", error="RuntimeError", error_code="internal_error",
                                  message="synthetic failure").to_json()
        self.refused(self.catalog(health=failure), "fail_closed_health_error")
        envelope = json.loads(gateway_health())
        envelope["error_code"] = "internal_error"
        self.refused(self.catalog(health=json.dumps(envelope)), "fail_closed_health_error")

    def test_malformed_health_fails(self):
        envelope = json.loads(gateway_health())
        for text in ("not json", "[]", '"admission unavailable"',
                     json.dumps({**envelope, "operation": "list_capabilities"}),
                     json.dumps({k: v for k, v in envelope.items() if k != "data"}),
                     gateway_health(mutate=lambda d: d.pop("upstream_read_gateway")),
                     gateway_health(mutate=lambda d: d.update(upstream_read_gateway="unavailable")),
                     gateway_health(mutate=set_gateway(dynamically_exposed_count="0")),
                     gateway_health(mutate=set_gateway(dynamically_exposed_count=False)),
                     gateway_health(mutate=lambda d: d.pop("dynamic_upstream_tool_count"))):
            with self.subTest(text=text[:60]):
                self.refused(self.catalog(health=text), "fail_closed_health_malformed")

    def test_admitted_health_fails(self):
        for status in ("admitted_exact", "partially_admitted"):
            self.refused(self.catalog(health=gateway_health(mutate=set_gateway(admission_status=status))),
                         "fail_closed_gateway_admitted")

    def test_unadmission_must_be_positively_established(self):
        # No admitted marker is not evidence: a missing, unknown or
        # unconfigured status, or anything exposed, fails.
        unconfigured = {"configured": False, "initialized": False, "generic_delegation_available": False,
                        "dynamically_exposed_count": 0, "writes_allowed": False, "direct_ha_fallback_allowed": False}
        for health, category in (
                (gateway_health(mutate=lambda d: d["upstream_read_gateway"].pop("admission_status")),
                 "fail_closed_gateway_unadmission_unproven"),
                (gateway_health(mutate=set_gateway(admission_status="not_evaluated")),
                 "fail_closed_gateway_unadmission_unproven"),
                (gateway_health(mutate=lambda d: d.update(upstream_read_gateway=unconfigured)),
                 "fail_closed_gateway_unadmission_unproven"),
                (gateway_health(mutate=set_gateway(dynamically_exposed_count=1)), "fail_closed_gateway_exposes_reads"),
                (gateway_health(dynamic=1), "fail_closed_gateway_exposes_reads"),
                (gateway_health(mutate=set_gateway(generic_delegation_available=True)),
                 "fail_closed_gateway_exposes_reads"),
                (gateway_health(mutate=lambda d: d["upstream_read_gateway"].pop("generic_delegation_available")),
                 "fail_closed_gateway_exposes_reads")):
            with self.subTest(category=category):
                self.refused(self.catalog(health=health), category)

    # -- case writer, manifest writer and aggregate gate ---------------------

    def run_case(self, directory, *, ready_status=200, catalog=None, session=(503, "{}")):
        cases = directory / "cases"
        cases.mkdir(exist_ok=True)
        args = LANES.argparse.Namespace(case=ROLLBACK_CASE, configured_url=STRICT_URL, form="strict_full",
                                        ready_url="http://127.0.0.1:18200/ready",
                                        engineering_endpoint="http://127.0.0.1:18200/synthetic-access/mcp",
                                        observe_seconds=0.0, output=str(cases / f"{ROLLBACK_CASE}.json"))

        async def fake_catalog(url):
            return copy.deepcopy(catalog)

        patch = unittest.mock.patch.object
        with patch(LANES, "http_get", lambda url: (ready_status, "{}")), \
                patch(LANES, "http_post_initialize", lambda url: session), \
                patch(LANES, "engineering_catalog", fake_catalog), \
                patch(LANES, "engineering_static_tool_names", lambda: self.static):
            code = LANES.command_fail_closed(args)
        return code, json.loads((cases / f"{ROLLBACK_CASE}.json").read_text())

    def propagate(self, directory):
        """Write the amd64 manifest around the case result, then gate all six lanes."""

        row = self.rows["85_only", "amd64"]
        for name, value in passing_results(row).items():
            if name != ROLLBACK_CASE:
                LANES.write_json(directory / "cases" / f"{name}.json", value)
        manifests = directory / "manifests"
        writer = LANES.main(["write-manifest", "--row", json.dumps(row), "--engineering-commit", COMMIT,
                             "--cases-dir", str(directory / "cases"),
                             "--output", str(manifests / "85_only-amd64.json")])
        for other in manifests_for(self.matrix):
            if (other["configuration"], other["architecture"]) != ("85_only", "amd64"):
                LANES.write_json(manifests / f"{other['configuration']}-{other['architecture']}.json", other)
        gate = LANES.main(["gate", "--manifests-dir", str(manifests), "--admission-matrix", json.dumps(self.matrix),
                           "--engineering-commit", COMMIT, "--output", str(directory / "gate.json")])
        manifest = json.loads((manifests / "85_only-amd64.json").read_text())
        return writer, manifest, gate, json.loads((directory / "gate.json").read_text())

    def assert_propagates_failure(self, category, **observation):
        with tempfile.TemporaryDirectory() as raw, contextlib.redirect_stdout(io.StringIO()):
            directory = Path(raw)
            code, case = self.run_case(directory, **observation)
            self.assertEqual((code, case["status"], case["reason"]), (1, "failed", category))
            writer, manifest, gate, summary = self.propagate(directory)
        self.assertEqual((writer, manifest["result"]), (1, "FAIL"))
        self.assertEqual(gate, 1)
        self.assertEqual(summary["result"], "FAIL")
        self.assertEqual(summary["acceptance"]["85_only"], "NOT ACCEPTED")
        self.assertTrue(any("85_only amd64" in error and ROLLBACK_CASE in error for error in summary["errors"]))

    def test_success_paths_pass_writer_and_gate(self):
        for observation in ({"catalog": self.catalog()},
                            {"catalog": self.catalog(health=gateway_health(failure="invalid_response"))},
                            {"ready_status": 503, "session": (503, "{}")}):
            with self.subTest(observation=sorted(observation)), tempfile.TemporaryDirectory() as raw, \
                    contextlib.redirect_stdout(io.StringIO()):
                directory = Path(raw)
                code, case = self.run_case(directory, **observation)
                self.assertEqual((code, case["status"]), (0, "passed"), case)
                writer, manifest, gate, summary = self.propagate(directory)
                self.assertEqual((writer, manifest["result"], gate, summary["errors"]), (0, "PASS", 0, []))
                self.assertEqual(summary["acceptance"]["85_only"], "ACCEPTED")

    def test_extra_delegated_read_fails_writer_and_gate(self):
        self.assert_propagates_failure("fail_closed_delegated_read_exposed",
                                       catalog=self.catalog(pages=[sorted(self.static) + ["ha_get_history"]]))

    def test_later_page_delegated_read_fails_writer_and_gate(self):
        self.assert_propagates_failure("fail_closed_delegated_read_exposed",
                                       catalog=self.catalog(pages=[sorted(self.static), ["ha_get_history"]]))

    def test_incomplete_catalog_fails_writer_and_gate(self):
        self.assert_propagates_failure("fail_closed_catalog_incomplete", catalog=self.catalog(complete=False))

    def test_missing_expected_read_fails_writer_and_gate(self):
        with unittest.mock.patch.object(LANES, "ROLLBACK_EXPECTED_DELEGATED_READS", frozenset({"ha_get_state"})):
            self.assert_propagates_failure("fail_closed_expected_delegated_read_missing", catalog=self.catalog())

    def test_missing_health_fails_writer_and_gate(self):
        self.assert_propagates_failure("fail_closed_health_missing", catalog=self.catalog(health=""))

    def test_errored_health_fails_writer_and_gate(self):
        self.assert_propagates_failure("fail_closed_health_error",
                                       catalog=self.catalog(is_error=True, health="Error executing tool"))

    def test_malformed_health_fails_writer_and_gate(self):
        self.assert_propagates_failure("fail_closed_health_malformed", catalog=self.catalog(health="not json"))

    def test_admitted_health_fails_writer_and_gate(self):
        self.assert_propagates_failure(
            "fail_closed_gateway_admitted",
            catalog=self.catalog(health=gateway_health(mutate=set_gateway(admission_status="admitted_exact"))))

    def test_codex_reproduction_fails_writer_and_gate(self):
        # #404 F1 reproduction: readiness 200, ha_get_history exposed, health
        # reporting unavailable. It no longer certifies fail-closed.
        self.assert_propagates_failure(
            "fail_closed_delegated_read_exposed",
            catalog=self.catalog(pages=[sorted(self.static) + ["ha_get_history"]]))


class WorkflowWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.workflow = yaml.safe_load(cls.text)
        cls.jobs = cls.workflow["jobs"]
        cls.lanes = cls.jobs[LANES_JOB]
        cls.gate = cls.jobs[GATE_JOB]
        cls.steps = {step.get("name"): step for step in cls.lanes["steps"]}

    def test_workflow_permissions_stay_read_only_and_jobs_add_none(self):
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})
        for job in (self.lanes, self.gate):
            self.assertNotIn("permissions", job)
            self.assertNotIn("secrets", json.dumps(job))
            self.assertNotIn("environment", job)

    def test_both_jobs_feed_validate(self):
        needs = self.jobs["validate"]["needs"]
        self.assertIn(LANES_JOB, needs)
        self.assertIn(GATE_JOB, needs)
        self.assertEqual(self.gate["needs"], ["prepare_exact_image_matrix", LANES_JOB])
        self.assertNotIn("if", self.lanes)
        self.assertNotIn("if", self.gate)
        self.assertEqual(self.jobs["prepare_exact_image_matrix"]["outputs"]["admission"],
                         "${{ steps.support-window.outputs.admission }}")

    def test_lane_matrix_and_native_runner(self):
        self.assertEqual(self.lanes["strategy"]["matrix"],
                         "${{ fromJSON(needs.prepare_exact_image_matrix.outputs.admission) }}")
        self.assertIs(self.lanes["strategy"]["fail-fast"], False)
        self.assertEqual(self.lanes["runs-on"], "${{ matrix.runner || 'ubuntu-24.04' }}")
        self.assertIn("${{ matrix.state }}", self.lanes["name"])
        self.assertIn("${{ matrix.architecture }}", self.lanes["name"])

    def test_every_action_reference_is_pinned_by_commit(self):
        for job in (self.lanes, self.gate):
            for step in job["steps"]:
                if "uses" in step:
                    self.assertRegex(step["uses"], r"^actions/[a-z-]+@[0-9a-f]{40}$")

    def test_dormant_lane_runs_no_case_and_announces_its_reason(self):
        dormant = self.steps["Dormant lane, not executed"]
        self.assertEqual(dormant["if"], "matrix.state != 'active'")
        self.assertIn("::notice", dormant["run"])
        self.assertIn("LANE_DORMANT_REASON", dormant["run"])
        for name, step in self.steps.items():
            if name is None or "run" not in step or name in (
                    "Dormant lane, not executed", "Write section 5.1 acceptance manifest"):
                continue
            with self.subTest(step=name):
                self.assertIn("matrix.state == 'active'", step.get("if", ""))
        for name, step in self.steps.items():
            if name and ("Case 8.5" in name or "8.5 peer" in name or "Engineering on" in name
                         or "Home Assistant fixture" in name):
                with self.subTest(step=name):
                    self.assertEqual(step["if"], "matrix.state == 'active' && matrix.configuration == '85_only'")

    def test_manifest_written_and_uploaded_always(self):
        writer = self.steps["Write section 5.1 acceptance manifest"]
        self.assertEqual(writer["if"], "always()")
        self.assertIn("write-manifest", writer["run"])
        upload = self.steps["Upload section 5.1 acceptance manifest"]
        self.assertEqual(upload["if"], "always()")
        self.assertEqual(upload["with"]["name"],
                         "exact-image-admission-manifest-${{ matrix.configuration }}-${{ matrix.architecture }}")
        self.assertEqual(upload["with"]["if-no-files-found"], "error")
        download = next(s for s in self.gate["steps"] if s.get("name") == "Download every lane manifest")
        self.assertEqual(download["with"]["pattern"], "exact-image-admission-manifest-*")
        self.assertIs(download["with"]["merge-multiple"], True)
        cleanup = self.steps["Remove disposable exact-image admission environment"]
        self.assertIn("always()", cleanup["if"])

    def test_every_active_85_case_has_a_producer_in_the_lane(self):
        lane_text = yaml.safe_dump(self.lanes)
        for case in LANES.CASES["85_only"]:
            if case.name.startswith("image_identity_"):
                self.assertIn("identity", lane_text)
                continue
            with self.subTest(case=case.name):
                self.assertIn(case.name, lane_text)
        # The strict-full value under test is the section 3.2 strict form; the
        # legacy value is the gateway lane's existing form.
        env = self.lanes["env"]
        self.assertEqual(LANES.classify_form(env["STRICT_FULL_UPSTREAM_URL"]), "strict_full")
        self.assertEqual(LANES.classify_form(env["LEGACY_UPSTREAM_URL"]), "legacy")
        for keyword in LANES.SELECTOR_KEYWORDS:
            self.assertNotIn(keyword, "synthetic-upstream-secret")

    def test_lane_adds_no_publication_signing_or_push(self):
        lane_text = yaml.safe_dump({LANES_JOB: self.lanes, GATE_JOB: self.gate})
        for forbidden in ("docker push", "cosign", "attest", "gh release", "git tag", "git push", "--push",
                          "id-token", "packages: write", "secrets."):
            self.assertNotIn(forbidden, lane_text)
        self.assertIsNone(re.search(r"\$\{\{\s*secrets\.", lane_text))


if __name__ == "__main__":
    unittest.main()
