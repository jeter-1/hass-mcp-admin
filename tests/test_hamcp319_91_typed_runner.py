"""HAMCP-319 section 5.1 ``91_typed_controls_verified_readback`` runner (offline).

Exercises ``scripts/exact_image_admission_91_typed.py`` with injected runners
only: no Docker, no network, no live system. The peer row is the compiled
reviewed 9.1.2 registry entry (no synthetic image is ever accepted as a pass),
and the Core image comes from the committed support window. Required
negatives: a control not executed, not verified or raising, a peer or Core
start failure, a non-strict endpoint, an image not taken from the registry row
and a non-2025-03-26 protocol each produce a ``failed`` file and nonzero exit.
"""

from __future__ import annotations

import asyncio
import copy
import importlib.util
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "scripts", ROOT / "hass_mcp_engineering_beta"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

SPEC = importlib.util.spec_from_file_location(
    "exact_image_admission_91_typed_under_test", ROOT / "scripts/exact_image_admission_91_typed.py")
typed = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = typed
SPEC.loader.exec_module(typed)

import exact_image_admission_lanes as lanes  # noqa: E402

PROTOCOL = "2025-03-26"
CASE_FILE = "91_typed_controls_verified_readback.json"


def reviewed_row() -> dict:
    release = typed.reviewed_release("9.1.2")
    return {"upstream_version": "9.1.2",
            "upstream_image": "ghcr.io/homeassistant-ai/ha-mcp@" + release.image_index_digest,
            "image_index_digest": release.image_index_digest,
            "architecture_image_digests": dict(release.architecture_image_digests_by_platform),
            "image_revision": release.image_revision, "source_commit": release.source_commit}


def verified(name: str) -> dict:
    return {"executed": True, "verified": True, "operations": [{"verified": True}]}


def runner_with(controls: dict, protocols: list, *, raise_exc: BaseException | None = None, seen=None):
    async def runner(plan):
        if seen is not None:
            seen.append(plan)
        plan.evidence["controls"].update(copy.deepcopy(controls))
        plan.evidence["protocols"].extend(protocols)
        if raise_exc is not None:
            raise raise_exc
    return runner


class RunnerHarness(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="typed91-test-")
        self.cases = Path(self.temp.name) / "cases"
        self.row = reviewed_row()
        self.environ = {"RUNNER_TEMP": self.temp.name}

    def tearDown(self):
        self.temp.cleanup()

    def run_main(self, runner, *, peers=None, architecture="amd64", extra=(), environ=None, **kwargs):
        argv = ["--peers", json.dumps({"candidate": self.row} if peers is None else peers),
                "--architecture", architecture, "--cases-dir", str(self.cases), *extra]
        with redirect_stdout(io.StringIO()):
            code = typed.main(argv, runner=runner, environ=self.environ if environ is None else environ, **kwargs)
        files = sorted(p.name for p in self.cases.glob("*"))
        self.assertEqual(files, [CASE_FILE], "exactly one case file is written")
        result = json.loads((self.cases / CASE_FILE).read_text(encoding="utf-8"))
        self.assertIn(result["status"], ("passed", "failed"), "never skipped")
        self.assertEqual(result["endpoint_form"], "strict_full")
        self.assertEqual(result["case"], "91_typed_controls_verified_readback")
        self.assertEqual(code == 0, result["status"] == "passed")
        return code, result

    def assert_failed(self, code, result, reason_fragment=""):
        self.assertNotEqual(code, 0)
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["reason"], "a failure carries a bounded fixed reason")
        self.assertLessEqual(len(result["reason"]), lanes.MAX_REASON)
        if reason_fragment:
            self.assertIn(reason_fragment, result["reason"])


class PassAndFailTests(RunnerHarness):
    def test_passes_only_when_all_three_controls_executed_and_verified(self):
        seen = []
        controls = {name: verified(name) for name in typed.CONTROLS}
        code, result = self.run_main(runner_with(controls, [PROTOCOL] * 9, seen=seen))
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["reason"], "")
        self.assertEqual(result["observed"]["negotiated_protocol"], PROTOCOL)
        self.assertEqual(result["observed"]["exchange_count"], 9)
        for name in typed.CONTROLS:
            self.assertTrue(result["observed"]["controls"][name]["verified"])
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].endpoint,
                         "http://127.0.0.1:18486/synthetic-upstream-secret/mcp/catalog/full")

    def test_any_control_missing_not_executed_or_not_verified_fails(self):
        for name in typed.CONTROLS:
            for broken in (None, {"executed": False, "verified": False},
                           {"executed": True, "verified": False},
                           {"executed": False, "verified": True},
                           {"executed": "yes", "verified": True}):
                with self.subTest(control=name, broken=broken):
                    controls = {other: verified(other) for other in typed.CONTROLS}
                    if broken is None:
                        del controls[name]
                    else:
                        controls[name] = broken
                    code, result = self.run_main(runner_with(controls, [PROTOCOL] * 3))
                    self.assert_failed(code, result, name)
                    if broken is None:
                        self.assertEqual(result["observed"]["controls"][name],
                                         {"executed": False, "verified": False})

    def test_raising_control_or_lane_failure_fails_with_bounded_category(self):
        controls = {name: verified(name) for name in typed.CONTROLS}
        for exc, fragment in ((lanes.LaneFailure("peer_start_timeout"), "lane refused: peer_start_timeout"),
                              (lanes.LaneFailure("docker_run_failed"), "lane refused: docker_run_failed"),
                              (lanes.LaneFailure("core_version_mismatch"), "lane refused: core_version_mismatch"),
                              (RuntimeError("synthetic secret-bearing text"), "lane error: RuntimeError"),
                              (asyncio.TimeoutError(), "lane error: TimeoutError")):
            with self.subTest(exc=type(exc).__name__, fragment=fragment):
                code, result = self.run_main(runner_with(controls, [PROTOCOL], raise_exc=exc))
                self.assert_failed(code, result, fragment)
                self.assertNotIn("secret", result["reason"])

    def test_guarded_control_records_failure_and_does_not_hide_the_others(self):
        plan = typed.Plan("amd64", {}, {}, "", 18423, 18486, Path(self.temp.name), "typed91-x")

        async def explode():
            plan.evidence["controls"]["fan"] = {"executed": True, "verified": False}
            raise lanes.LaneFailure("fan_turn_on_not_verified")

        async def crash():
            raise KeyError("x")

        async def scenario():
            await typed.guarded("fan", plan, explode())
            await typed.guarded("helper", plan, crash())
            plan.evidence["controls"]["power"] = verified("power")

        asyncio.run(scenario())
        self.assertEqual(plan.evidence["controls"]["fan"],
                         {"executed": True, "verified": False, "failure": "fan_turn_on_not_verified"})
        self.assertEqual(plan.evidence["controls"]["helper"],
                         {"executed": False, "verified": False, "failure": "KeyError"})
        self.assertEqual(typed.evaluate(plan.evidence["controls"], [PROTOCOL])[0], "failed")

    def test_peer_or_core_start_failure_in_real_runner_shape_fails(self):
        # The runner fails before any control: nothing executed, file is failed.
        for category in ("docker_pull_failed", "peer_start_timeout", "core_fixture_entities_unavailable",
                         "read_gateway_not_admitted_exact", "peer_package_version_mismatch"):
            with self.subTest(category=category):
                code, result = self.run_main(runner_with({}, [], raise_exc=lanes.LaneFailure(category)))
                self.assert_failed(code, result, category)
                self.assertIsNone(result["observed"]["negotiated_protocol"])
                self.assertFalse(any(c["executed"] for c in result["observed"]["controls"].values()))


    def test_sigterm_runs_runner_cleanup_and_writes_failed(self):
        import os
        import signal
        cleaned = []

        async def runner(plan):
            plan.evidence["controls"]["power"] = verified("power")
            try:
                os.kill(os.getpid(), signal.SIGTERM)
                await asyncio.sleep(5)
            finally:
                cleaned.append(True)

        before = signal.getsignal(signal.SIGTERM)
        code, result = self.run_main(runner)
        self.assert_failed(code, result, "lane refused: lane_terminated")
        self.assertEqual(cleaned, [True])
        self.assertIs(signal.getsignal(signal.SIGTERM), before)

    def test_docker_disk_preflight_refuses_before_any_pull(self):
        from unittest import mock
        calls = []

        def fake_docker(*args, **kwargs):
            calls.append(args)
            return mock.Mock(stdout="/var/lib/docker\n", returncode=0)

        with mock.patch.object(typed, "docker", fake_docker):
            with self.assertRaises(lanes.LaneFailure) as caught:
                typed.docker_disk_preflight(lambda path: typed.MIN_DOCKER_FREE_BYTES - 1)
            self.assertEqual(str(caught.exception), "docker_disk_insufficient")
            self.assertEqual(typed.docker_disk_preflight(lambda path: typed.MIN_DOCKER_FREE_BYTES),
                             typed.MIN_DOCKER_FREE_BYTES)
        self.assertEqual(calls, [("info", "--format", "{{.DockerRootDir}}")] * 2)

class ProtocolTests(RunnerHarness):
    def test_negotiated_protocol_is_the_observed_value(self):
        controls = {name: verified(name) for name in typed.CONTROLS}
        code, result = self.run_main(runner_with(controls, ["2025-06-18"] * 4))
        self.assert_failed(code, result, "2025-03-26")
        self.assertEqual(result["observed"]["negotiated_protocol"], "2025-06-18")

    def test_mixed_or_absent_protocol_is_not_reported_as_negotiated(self):
        controls = {name: verified(name) for name in typed.CONTROLS}
        for protocols in ([], [PROTOCOL, "2025-06-18"], [PROTOCOL, None]):
            with self.subTest(protocols=protocols):
                code, result = self.run_main(runner_with(controls, protocols))
                self.assert_failed(code, result)
                self.assertIsNone(result["observed"]["negotiated_protocol"])

    def test_case_file_feeds_the_lane_manifest_protocol_rule(self):
        controls = {name: verified(name) for name in typed.CONTROLS}
        self.run_main(runner_with(controls, [PROTOCOL] * 2))
        loaded = lanes.load_case_results(self.cases)
        self.assertEqual(lanes.find_values(loaded, "negotiated_protocol"), [PROTOCOL])
        self.assertIn("91_typed_controls_verified_readback", lanes.case_names("91_only"))
        self.assertEqual(lanes.case_form("91_only", "91_typed_controls_verified_readback"), "strict_full")


class EndpointTests(RunnerHarness):
    def test_strict_endpoint_form_only(self):
        url = typed.strict_endpoint(18486)
        self.assertEqual(url, "http://127.0.0.1:18486/synthetic-upstream-secret/mcp/catalog/full")
        self.assertEqual(lanes.classify_form(url), "strict_full")

    def test_short_and_other_forms_are_refused(self):
        refused = {
            "http://127.0.0.1:18486/synthetic-upstream-secret/catalog/full": "endpoint_short_form_refused",
            "http://127.0.0.1:18486/synthetic-upstream-secret/mcp": "endpoint_not_strict_secret_path",
            "http://127.0.0.1:18486/synthetic-upstream-secret/mcp/catalog/full/": "endpoint_not_strict_secret_path",
            "http://127.0.0.1:18486/synthetic-upstream-secret/mcp/catalog/full?x=1": "endpoint_not_loopback_strict_form",
            "http://127.0.0.1:18486/synthetic-upstream-secret/mcp/catalog/search": "endpoint_not_strict_secret_path",
            "http://10.0.0.5:18486/synthetic-upstream-secret/mcp/catalog/full": "endpoint_not_loopback_strict_form",
            "https://127.0.0.1:18486/synthetic-upstream-secret/mcp/catalog/full": "endpoint_not_loopback_strict_form",
            "http://127.0.0.1:18086/synthetic-upstream-secret/mcp/catalog/full": "endpoint_port_out_of_range",
            "http://127.0.0.1:18486/synthetic-upstream-secret/mcp/%63atalog/full": "endpoint_not_loopback_strict_form",
        }
        for url, category in refused.items():
            with self.subTest(url=url):
                with self.assertRaises(lanes.LaneFailure) as caught:
                    typed.validate_endpoint(url)
                self.assertEqual(str(caught.exception), category)

    def test_ports_must_be_distinct_and_inside_18400_18499(self):
        for extra in (["--peer-port", "18100"], ["--core-port", "18123"], ["--core-port", "18486"],
                      ["--peer-port", "18500"]):
            with self.subTest(extra=extra):
                called = []
                code, result = self.run_main(runner_with({}, [], seen=called), extra=extra)
                self.assert_failed(code, result, "ports_outside_18400_18499_or_equal")
                self.assertEqual(called, [], "the lane never starts on refused ports")

    def test_configured_ports_reach_the_plan(self):
        seen = []
        self.run_main(runner_with({}, [], seen=seen), extra=["--core-port", "18401", "--peer-port", "18402"])
        self.assertEqual((seen[0].core_port, seen[0].peer_port), (18401, 18402))
        self.assertEqual(seen[0].endpoint, "http://127.0.0.1:18402/synthetic-upstream-secret/mcp/catalog/full")


class PeerImageTests(RunnerHarness):
    def test_peer_image_comes_only_from_the_registry_row(self):
        seen = []
        self.run_main(runner_with({}, [], seen=seen), architecture="arm64")
        peer = seen[0].peer
        self.assertEqual(peer["image"], self.row["upstream_image"])
        self.assertEqual(peer["platform"], "linux/arm64")
        self.assertEqual(peer["platform_manifest_digest"], self.row["architecture_image_digests"]["linux/arm64"])
        self.assertRegex(peer["image"], r"^ghcr\.io/homeassistant-ai/ha-mcp@sha256:[0-9a-f]{64}$")

    def test_unpinned_mismatched_or_unreviewed_peer_rows_are_refused(self):
        def mutate(**changes):
            row = copy.deepcopy(self.row)
            row.update(changes)
            return {"candidate": row}
        other = "sha256:" + "9" * 64
        cases = {
            "tag": (mutate(upstream_image="ghcr.io/homeassistant-ai/ha-mcp:9.1.2"), "peer_image_not_digest_pinned"),
            "tag_and_digest": (mutate(upstream_image="ghcr.io/homeassistant-ai/ha-mcp:9.1.2@" + self.row["image_index_digest"]),
                               "peer_image_not_digest_pinned"),
            "other_repo": (mutate(upstream_image="ghcr.io/example/ha-mcp@" + self.row["image_index_digest"]),
                           "peer_image_not_digest_pinned"),
            "index_mismatch": (mutate(image_index_digest=other), "peer_image_index_mismatch"),
            "synthetic_digest": (mutate(upstream_image="ghcr.io/homeassistant-ai/ha-mcp@" + other, image_index_digest=other),
                                 "peer_row_differs_from_reviewed_registry"),
            "revision": (mutate(image_revision="9" * 40), "peer_row_differs_from_reviewed_registry"),
            "platform": (mutate(architecture_image_digests={**self.row["architecture_image_digests"],
                                                            "linux/arm64": other}),
                         "peer_row_differs_from_reviewed_registry"),
            "version": (mutate(upstream_version="8.5.0"), "peer_version_not_9_1_2"),
            "baseline_role_only": ({"baseline": self.row}, "peer_row_absent"),
            "not_object": ([self.row], "peer_row_absent"),
        }
        for label, (peers, category) in cases.items():
            with self.subTest(label=label):
                called = []
                code, result = self.run_main(runner_with({}, [], seen=called), peers=peers)
                self.assert_failed(code, result, category)
                self.assertEqual(called, [], "no container starts for a refused row")

    def test_revoked_or_absent_registry_release_is_refused(self):
        class Revoked:
            revoked = True
        for release_for in (lambda version: None, lambda version: Revoked()):
            with self.subTest(release_for=release_for):
                called = []
                code, result = self.run_main(runner_with({}, [], seen=called), release_for=release_for)
                self.assert_failed(code, result, "peer_release_unreviewed")
                self.assertEqual(called, [])

    def test_peers_not_json_is_refused(self):
        argv = ["--peers", "{not json", "--architecture", "amd64", "--cases-dir", str(self.cases)]
        with redirect_stdout(io.StringIO()):
            code = typed.main(argv, runner=runner_with({}, []), environ=self.environ)
        result = json.loads((self.cases / CASE_FILE).read_text(encoding="utf-8"))
        self.assert_failed(code, result, "peers_not_json")


class CoreImageAndGateTests(RunnerHarness):
    def test_core_image_is_the_reviewed_digest_pinned_support_window_slot(self):
        seen = []
        self.run_main(runner_with({}, [], seen=seen))
        core = seen[0].core
        import support_window
        current = support_window.load()["core"]["current"]
        self.assertEqual(core["image"], "ghcr.io/home-assistant/home-assistant:"
                         f"{current['version']}@{current['image_index_digest']}")
        self.assertRegex(core["image_index_digest"], r"^sha256:[0-9a-f]{64}$")

    def test_core_slot_without_typed_binding_or_digest_is_refused(self):
        import support_window
        window = support_window.load()
        cases = {
            "core_binding_unreviewed": {"version": window["core"]["current"]["version"],
                                        "image_index_digest": "sha256:" + "0" * 64},
            "core_image_not_digest_pinned": {"version": window["core"]["current"]["version"],
                                             "image_index_digest": "latest"},
        }
        for category, current in cases.items():
            with self.subTest(category=category):
                changed = copy.deepcopy(window)
                changed["core"]["current"] = current
                called = []
                code, result = self.run_main(runner_with({}, [], seen=called), window_loader=lambda: changed)
                self.assert_failed(code, result, category)
                self.assertEqual(called, [])

    def test_admission_candidate_must_be_9_1_2(self):
        import support_window
        changed = copy.deepcopy(support_window.load())
        changed["admission"] = {**changed.get("admission", {}), "candidate_version": "8.5.0"}
        code, result = self.run_main(runner_with({}, []), window_loader=lambda: changed)
        self.assert_failed(code, result, "admission_candidate_not_9_1_2")

    def test_ci_job_gate(self):
        controls = {name: verified(name) for name in typed.CONTROLS}
        called = []
        code, result = self.run_main(runner_with(controls, [PROTOCOL], seen=called),
                                     environ={"GITHUB_ACTIONS": "true", "GITHUB_JOB": "other-job"})
        self.assert_failed(code, result, "not_exact_image_admission_lanes_job")
        self.assertEqual(called, [])
        code, result = self.run_main(runner_with(controls, [PROTOCOL]),
                                     environ={"GITHUB_ACTIONS": "true",
                                              "GITHUB_JOB": "exact-image-admission-lanes",
                                              "RUNNER_TEMP": self.temp.name})
        self.assertEqual((code, result["status"]), (0, "passed"))


class ObserverAndControlTests(unittest.TestCase):
    def test_observer_counts_only_completed_dispatch_and_records_protocol(self):
        class Result:
            protocol_version, server_name, server_version = PROTOCOL, "ha-mcp", "9.1.2"

        class Transport:
            async def execute_read(self, tool, args, *, timeout_seconds, catalog_validator, before_dispatch):
                if args.get("refuse"):
                    raise RuntimeError("refused before send")
                await before_dispatch()
                return Result()

        class Provider:
            transport = Transport()

        evidence = {"controls": {}, "protocols": []}
        observer = typed.Observer(evidence)
        provider = Provider()
        observer.wrap(provider, "fan")

        async def before():
            return None

        async def scenario():
            await provider.transport.execute_read("ha_get_state", {}, timeout_seconds=1,
                                                  catalog_validator=None, before_dispatch=before)
            await provider.transport.execute_read("ha_call_service", {}, timeout_seconds=1,
                                                  catalog_validator=None, before_dispatch=before)
            with self.assertRaises(RuntimeError):
                await provider.transport.execute_read("ha_call_service", {"refuse": True}, timeout_seconds=1,
                                                      catalog_validator=None, before_dispatch=before)

        asyncio.run(scenario())
        self.assertEqual(observer.writes("fan"), 1)
        self.assertEqual(observer.writes("power"), 0)
        self.assertEqual(evidence["protocols"], [PROTOCOL, PROTOCOL])
        self.assertEqual(evidence["upstream_server"], {"name": "ha-mcp", "version": "9.1.2"})
        self.assertFalse(observer.exchanges[-1]["complete"])

    def fan_scenario(self, *, writes_per_call=1, duplicate_writes=0, readback_state="on", state="succeeded_verified",
                     contract=None):
        from ha_mcp_engineering.fan.contracts import FAN_SEMANTIC_CONTRACTS
        contract = contract or FAN_SEMANTIC_CONTRACTS["9.1.2"]
        evidence = {"controls": {}, "protocols": []}
        observer = typed.Observer(evidence)
        plan = typed.Plan("amd64", {}, {"version": "2026.10.1"}, "", 18423, 18486, Path("."), "typed91-x",
                          evidence)
        seen = {}

        class Locks:
            def records(self):
                return []

        class Service:
            locks = Locks()

            def health(self):
                return {"nonterminal_tasks": 0, "fallback_count": 0}

            async def control(self, request):
                key = request.operation_id
                first = key not in seen
                for _ in range(writes_per_call if first else duplicate_writes):
                    observer.exchanges.append({"provider": "fan", "tool": "ha_call_service", "sent": True,
                                               "complete": True})
                seen.setdefault(key, {"state": state, "terminal": True, "provider_attempt_count": 1,
                                      "dispatch_intent_recorded": True, "provider": "upstream_typed_fan",
                                      "provider_contract": contract, "core_binding": {"version": "2026.10.1"},
                                      "action": request.action})
                return dict(seen[key])

        class Rest:
            def __init__(self):
                self.current = {"state": "off", "attributes": {"percentage": 0}}

            async def request(self, method, path):
                return self.current

        rest = Rest()
        service = Service()
        original = service.control

        async def control(request):
            receipt = await original(request)
            on = request.action != "turn_off"
            rest.current = {"state": (readback_state if on else "off"),
                            "attributes": {"percentage": request.percentage if on else 0}}
            return receipt
        service.control = control

        class Telemetry:
            pass

        asyncio.run(typed.guarded("fan", plan, typed.fan_control(plan, service, rest, Telemetry(), observer)))
        return evidence["controls"]["fan"]

    def test_fan_control_verifies_single_dispatch_and_independent_readback(self):
        record = self.fan_scenario()
        self.assertEqual((record["executed"], record["verified"]), (True, True))
        self.assertEqual([op["action"] for op in record["operations"]], ["turn_on", "set_percentage", "turn_off"])
        self.assertTrue(all(op["upstream_dispatches"] == 1 and op["duplicate_dispatches"] == 0
                            for op in record["operations"]))

    def test_fan_control_refuses_duplicate_dispatch_missing_write_drift_and_bad_readback(self):
        for label, kwargs in (("duplicate", {"duplicate_writes": 1}), ("no_write", {"writes_per_call": 0}),
                              ("double_write", {"writes_per_call": 2}), ("readback", {"readback_state": "off"}),
                              ("unverified", {"state": "changed_unverified"}),
                              ("contract_drift", {"contract": "ha-mcp-8.5.0-single-fan-v1"})):
            with self.subTest(label=label):
                record = self.fan_scenario(**kwargs)
                self.assertTrue(record["executed"])
                self.assertFalse(record["verified"])
                self.assertEqual(record["failure"], "fan_turn_on_not_verified")


if __name__ == "__main__":
    unittest.main()
