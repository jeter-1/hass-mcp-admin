"""Offline HAMCP-231 thread_matter_topology lane boundaries; no Docker, network or production key."""
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import core_thread_matter_topology_local as lane
import thread_matter_topology_contract_acceptance as acceptance
from core_release_evidence import groups, manifest, passing_subset
from ha_mcp_engineering.thread_matter_topology import (
    DATASETS, LIST_DATASETS_COMMAND, NETWORK_TOPOLOGY_COMMAND, PAYLOADS, TOPOLOGY, SectionOutcome,
    TopologyError)

GENERATOR = ROOT / "docs/evidence/hamcp231/lane/generate_source_records.py"
ARCHIVES = {v: ROOT / f".artifacts/inputs/core-{v}.tar.gz" for v in ("2026.10.3", "2026.11.0")}
PROVENANCE = json.loads((ROOT / "tests/fixtures/core_2026_11_0_lane_provenance.json").read_text())


def decode(encoded):
    data, items, index = bytes.fromhex(encoded), {}, 0
    while index < len(data):
        tag, length = data[index], data[index + 1]
        items[tag] = data[index + 2:index + 2 + length]
        index += 2 + length
    return items


class Contracts(unittest.TestCase):
    def test_mapping_never_adds_unsigned_capability_or_lends_another_group(self):
        ref = acceptance.reference()
        self.assertEqual(ref["contract_fingerprint"], lane.CONTRACT_FINGERPRINT)
        self.assertEqual(ref["capability_id"], lane.CAPABILITY)
        self.assertEqual(groups()["thread_matter_topology"], [ref["capability_id"]])
        self.assertEqual(passing_subset([], {"thread_matter_topology": True}), [])
        for other in ("general", "helper_value", "marketplace_read"):
            self.assertEqual(passing_subset([ref], {other: True}), [])
        self.assertEqual(passing_subset([ref], {"thread_matter_topology": True}), [ref])
        self.assertEqual(passing_subset([ref], {"thread_matter_topology": False}), [])

    def test_groups_file_is_append_only_with_thread_matter_last(self):
        value = json.loads((ROOT / "ci/core-evidence-groups.json").read_text())
        self.assertEqual(list(value)[-3:], ["helper_value", "marketplace_read", "thread_matter_topology"])

    def test_manifest_profile_matches_committed_source_hashes(self):
        proof = json.loads(lane.SOURCE_HASHES.read_text())
        profile = manifest()["profiles"][lane.CAPABILITY]
        self.assertEqual(profile, [row["path"] for row in proof["files"]])
        self.assertEqual(profile, sorted(set(profile)))
        self.assertTrue(set(profile) <= set(manifest()["paths"]))
        self.assertEqual((proof["core_version"], proof["source_commit"], proof["source_archive_sha256"]),
                         (lane.VERSION, PROVENANCE["source_commit"], PROVENANCE["source_archive_sha256"]))
        for path in ("homeassistant/components/thread/websocket_api.py",
                     "homeassistant/components/thread/dataset_store.py",
                     "homeassistant/components/matter/api.py",
                     "homeassistant/components/websocket_api/connection.py",
                     "homeassistant/bootstrap.py"):
            self.assertIn(path, profile)
        record = json.loads((ROOT / "docs/evidence/hamcp315/core-sources/2026.11.0.json").read_text())
        for row in proof["files"]:
            self.assertEqual(record["hashes"][row["path"]], row["sha256"])

    @unittest.skipUnless(all(p.is_file() for p in ARCHIVES.values()), "exact Core archives not staged")
    def test_generator_reproduces_committed_records(self):
        args = [arg for v, p in ARCHIVES.items() for arg in ("--archive", f"{v}={p}")]
        result = subprocess.run([sys.executable, "-I", str(GENERATOR), *args, "--check"],
                                capture_output=True, text=True, timeout=300)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_configuration_loads_thread_and_never_matter(self):
        lines = lane.CONFIG.splitlines()
        self.assertIn("thread:", lines)
        top = {line.split(":")[0] for line in lines if line and not line.startswith(" ")}
        self.assertIn("thread", top)
        self.assertNotIn("matter", top)  # Matter stays not set up: no config entry, no YAML key.
        self.assertIn("default_config:", lines)

    def test_lane_names_and_port_are_distinct_from_marketplace(self):
        import core_marketplace_read_local as marketplace
        for name in ("LANE", "CORE", "NETWORK", "LABEL", "RECEIPT"):
            self.assertNotEqual(getattr(lane, name), getattr(marketplace, name), name)
        self.assertEqual(lane.PORTS, (18124,))
        self.assertFalse(set(lane.PORTS) & set(marketplace.PORTS))
        self.assertIn("thread_matter_topology", lane.diagnostics.LANES)

    def test_images_are_exact_core_and_fixture_only(self):
        core, fixture = lane.images()
        self.assertTrue(core.startswith("ghcr.io/home-assistant/home-assistant:2026.11.0@sha256:"))
        self.assertTrue(core.endswith(PROVENANCE["image_index_digest"]))
        self.assertEqual(fixture, lane.host.FIXTURE_IMAGE)

    def test_cached_image_requires_exact_repo_digest(self):
        def reply(digests):
            return subprocess.CompletedProcess([], 0, json.dumps([{"Id": "config", "RepoDigests": digests}]).encode())
        with patch.object(lane, "command", return_value=reply(["repo@sha256:other"])):
            with self.assertRaisesRegex(lane.Refusal, "cached_image_digest_mismatch"):
                lane.cached_image("repo:tag@sha256:exact")
        with patch.object(lane, "command", return_value=reply(["repo@sha256:exact"])):
            self.assertEqual(lane.cached_image("repo:tag@sha256:exact"), "config")


class SyntheticDatasets(unittest.TestCase):
    def test_tlv_is_complete_and_credentials_are_fresh_per_run(self):
        first = acceptance.synthetic_dataset("HAMCP-231-A", 15)
        second = acceptance.synthetic_dataset("HAMCP-231-A", 15)
        items = decode(first["tlv"])
        self.assertEqual(set(items), {0, 1, 2, 3, 4, 5, 7, 14})
        self.assertEqual(int.from_bytes(items[0], "big"), 15)
        self.assertEqual(items[1].hex(), first["pan_id"])
        self.assertNotEqual(first["pan_id"], "ffff")
        self.assertEqual(items[2].hex(), first["extended_pan_id"])
        self.assertEqual(items[3].decode(), "HAMCP-231-A")
        self.assertEqual((len(items[4]), len(items[5])), (16, 16))
        self.assertEqual(first["canaries"], (items[5].hex(), items[4].hex(), first["tlv"]))
        self.assertEqual(items[7][:1], b"\xfd")
        self.assertEqual(int.from_bytes(items[14], "big") >> 16, 1)
        self.assertNotEqual(first["canaries"][:2], second["canaries"][:2])
        self.assertNotEqual(first["extended_pan_id"], second["extended_pan_id"])

    def test_leak_check_is_case_insensitive(self):
        canary = "ab" * 16
        self.assertEqual(acceptance.leaks({"x": canary.upper()}, (canary,)), [0])
        self.assertEqual(acceptance.leaks({"x": "clean"}, (canary, "")), [])

    def test_payloads_and_invalid_argument_matrix(self):
        self.assertEqual(dict(PAYLOADS[TOPOLOGY]), {"type": NETWORK_TOPOLOGY_COMMAND, "refresh": False})
        self.assertIn({"refresh": False}, acceptance.INVALID_ARGUMENTS)
        self.assertIn({"refresh": True}, acceptance.INVALID_ARGUMENTS)
        for arguments in acceptance.INVALID_ARGUMENTS:
            with self.subTest(arguments=arguments), self.assertRaises(TopologyError) as caught:
                acceptance.validate_arguments(arguments)
            self.assertEqual(caught.exception.reason, "invalid_arguments")


class Inputs(unittest.TestCase):
    """Refusals before any container: hashes, fingerprint, manifest, sequence."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.profile = ["homeassistant/a.py", "homeassistant/b.py"]
        self.content = {p: ("synthetic " + p).encode() for p in self.profile}
        self.archive = self.root / "archive.tar.gz"
        prefix = "core-" + PROVENANCE["source_commit"]
        with tarfile.open(self.archive, "w:gz") as tar:
            for path, data in self.content.items():
                info = tarfile.TarInfo(prefix + "/" + path)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        self.manifest = self.root / "manifest.json"
        self.hashes = self.root / "hashes.json"
        self.write(self.profile, {p: hashlib.sha256(d).hexdigest() for p, d in self.content.items()})

    def tearDown(self):
        self.directory.cleanup()

    def write(self, profile, hashes, **overrides):
        self.manifest.write_text(json.dumps({"paths": self.profile, "profiles": {lane.CAPABILITY: profile}}))
        record = {"core_version": lane.VERSION, "source_commit": PROVENANCE["source_commit"],
                  "source_archive_sha256": PROVENANCE["source_archive_sha256"],
                  "files": [{"path": p, "sha256": h} for p, h in hashes.items()], **overrides}
        self.hashes.write_text(json.dumps(record))

    def source(self):
        with patch.object(lane, "MANIFEST", self.manifest), patch.object(lane, "SOURCE_HASHES", self.hashes):
            return lane.source_manifest(self.archive, PROVENANCE)

    def test_exact_source_hashes_pass(self):
        self.assertEqual(self.source()["profile_paths"], 2)

    def test_missing_manifest_profile_or_record_refuses(self):
        self.manifest.write_text(json.dumps({"paths": self.profile, "profiles": {}}))
        with patch.object(lane, "MANIFEST", self.manifest), patch.object(lane, "SOURCE_HASHES", self.hashes):
            with self.assertRaisesRegex(lane.Refusal, "thread_matter_manifest_missing"):
                lane.source_manifest(self.archive, PROVENANCE)
        self.write(self.profile, {})
        self.hashes.unlink()
        with self.assertRaisesRegex(lane.Refusal, "thread_matter_manifest_missing"):
            self.source()

    def test_hash_mismatch_refuses(self):
        self.write(self.profile, {self.profile[0]: "0" * 64,
                                  self.profile[1]: hashlib.sha256(self.content[self.profile[1]]).hexdigest()})
        with self.assertRaisesRegex(lane.Refusal, "thread_matter_source_hash_mismatch"):
            self.source()

    def test_profile_and_record_must_list_identical_paths(self):
        hashes = {p: hashlib.sha256(d).hexdigest() for p, d in self.content.items()}
        self.write(self.profile[:1], hashes)
        with self.assertRaisesRegex(lane.Refusal, "thread_matter_manifest_mismatch"):
            self.source()

    def test_record_for_another_core_refuses(self):
        hashes = {p: hashlib.sha256(d).hexdigest() for p, d in self.content.items()}
        self.write(self.profile, hashes, core_version="2026.10.3")
        with self.assertRaisesRegex(lane.Refusal, "thread_matter_source_provenance_mismatch"):
            self.source()

    def digests(self, journal=lane.JOURNAL_SHA256):
        self.journal = self.root / "journal"
        self.journal.write_bytes(b"synthetic journal")
        return lambda path: journal if Path(path) == self.journal else PROVENANCE["source_archive_sha256"]

    def test_input_hash_mismatch_refuses(self):
        with patch.object(lane, "sha256", side_effect=self.digests("0" * 64)):
            with self.assertRaisesRegex(lane.Refusal, "input_hash_mismatch"):
                lane.inputs(self.journal, self.archive)

    def test_wrong_fingerprint_refuses_before_journal(self):
        reference = {**acceptance.reference(), "contract_fingerprint": "sha256:" + "0" * 64}
        with patch.object(lane, "sha256", side_effect=self.digests()), \
                patch.object(lane, "compiled_reference", return_value=reference), \
                patch.object(lane, "source_manifest") as sources:
            with self.assertRaisesRegex(lane.Refusal, "profile_fingerprint_mismatch"):
                lane.inputs(self.journal, self.archive)
            sources.assert_not_called()

    def test_predecessor_already_carrying_topology_refuses(self):
        import prepare_core_release_registry as prepare
        refs = [{"capability_id": "core.x%d" % i} for i in range(31)] + [acceptance.reference()]
        entry = SimpleNamespace(version=lane.VERSION, to_mapping=lambda: {**PROVENANCE, "capabilities": refs})
        parsed = SimpleNamespace(accepted=SimpleNamespace(sequence=17, entries=[entry]))
        with patch.object(lane, "sha256", side_effect=self.digests()), \
                patch.object(lane, "source_manifest", return_value={}), \
                patch("core_registry_one_tap.load_public_key", return_value=None), \
                patch.object(prepare, "require_known_contracts"), \
                patch.object(prepare, "parse_journal", return_value=parsed):
            with self.assertRaisesRegex(lane.Refusal, "predecessor_capabilities_mismatch"):
                lane.inputs(self.journal, self.archive)


class Orchestration(unittest.TestCase):
    def run_lane(self, *, exercise_error=None, cleanup_error=False, dirty=False, existing=False,
                 ports_override=None, exercise_result=None):
        calls, resources = [], {}

        def command(*args, **kwargs):
            calls.append(args)
            stdout, code = b"", 0
            if args[:2] == ("git", "status"):
                stdout = b" M source.py" if dirty else b""
            elif args[:2] == ("git", "rev-parse"):
                stdout = b"a" * 40
            elif "inspect" in args:
                code = 0 if existing else 1
            elif args[:3] == ("docker", "network", "create"):
                resources[lane.NETWORK] = True
            elif args[:2] == ("docker", "run"):
                resources[lane.CORE] = True
            elif args[:2] == ("docker", "rm") or args[:3] == ("docker", "network", "rm"):
                resources.pop(args[-1], None)
            return subprocess.CompletedProcess(args, code, stdout)

        def inspect(kind, name, run_id):
            if name not in resources:
                return None
            if kind == "network":
                return {"Driver": "bridge", "Internal": False}
            ports = {"8123/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18124"}]}
            return {"NetworkSettings": {"Networks": {lane.NETWORK: {}},
                    "Ports": ports if ports_override is None else ports_override}}

        def remove(args, *_):
            if cleanup_error:
                raise lane.Refusal("cleanup_failed")
            shutil.rmtree(args.directory)
        reference = acceptance.reference()
        passed = exercise_result or {"result": "PASS", "profile": reference}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".artifacts").mkdir()
            out = root / ".artifacts" / "run"
            argv = ["--mode", "local", "--pull", "never", "--journal", "journal",
                    "--source-archive", "archive", "--run-dir", str(out)]
            with patch.dict(os.environ, {}, clear=True), \
                 patch.object(lane, "ROOT", root), \
                 patch.object(lane, "command", side_effect=command), \
                 patch.object(lane, "inputs", return_value=({}, reference, {"manifest_sha256": "m" * 64,
                              "source_hashes_sha256": "s" * 64, "profile_paths": 38})), \
                 patch.object(lane, "images", return_value=("core@sha256:a", lane.host.FIXTURE_IMAGE)), \
                 patch.object(lane, "cached_image", return_value="config"), \
                 patch.object(lane.resources, "wait_for_admission", return_value={"other_heavy_jobs": 0}), \
                 patch.object(lane, "inspect_owned", side_effect=inspect), \
                 patch.object(lane.host, "local_remove", side_effect=remove), \
                 patch.object(lane, "require_free_ports"), patch.object(lane.signal, "signal"), \
                 patch.object(lane.diagnostics, "_raw_log", return_value=False), \
                 patch.object(lane, "exercise", new=AsyncMock(side_effect=exercise_error,
                              return_value=passed)) as exercise, \
                 patch("sys.stdout", new_callable=io.StringIO):
                if dirty or existing:
                    with self.assertRaises(lane.Refusal):
                        lane.main(argv)
                    self.assertFalse(out.exists())
                    self.assertFalse(any(c[:2] in {("docker", "run"), ("docker", "rm")} for c in calls))
                    return
                code = lane.main(argv)
                if ports_override is not None:
                    exercise.assert_not_called()
                result = json.loads((out / lane.RECEIPT).read_text())
                self.assertEqual(os.environ["REAL_HA_URL"], "http://127.0.0.1:18124")
            self.assertFalse(resources)
            runs = [c for c in calls if c[:2] == ("docker", "run")]
            self.assertLessEqual(len(runs), 1)  # Core only: no ha-mcp and no Matter Server container.
            for call in runs:
                self.assertEqual(call[call.index("--pull") + 1], "never")
                self.assertIn("127.0.0.1:18124:8123", call)
                self.assertEqual(call[call.index("--memory") + 1], "2g")
            return code, result

    def test_pass_binds_fingerprint_and_requires_cleanup(self):
        code, result = self.run_lane()
        self.assertEqual(code, 0)
        self.assertEqual(result["result"], "PASS")
        self.assertTrue(result["cleanup"]["clean"])
        self.assertEqual(result["contract_fingerprint"], lane.CONTRACT_FINGERPRINT)
        self.assertEqual(result["profile_reference"], acceptance.reference())
        self.assertEqual((result["model"], result["group"], result["core_version"]),
                         ("core-thread-matter-topology-local-evidence-v1", "thread_matter_topology", "2026.11.0"))
        self.assertFalse(result["network_posture"]["egress_required_by_capability"])

    def test_receipt_profile_drift_withholds(self):
        drift = {**acceptance.reference(), "contract_fingerprint": "sha256:" + "1" * 64}
        code, result = self.run_lane(exercise_result={"result": "PASS", "profile": drift})
        self.assertEqual((code, result["result"]), (1, "WITHHELD"))

    def test_wildcard_mapping_refuses_before_readiness(self):
        code, result = self.run_lane(ports_override={"8123/tcp": [{"HostIp": "0.0.0.0", "HostPort": "18124"}]})
        self.assertEqual(code, 1)
        self.assertEqual((result["failure"]["lane"], result["failure"]["stage"], result["failure"]["check"]),
                         ("thread_matter_topology", "container_start", "port_mapping"))
        self.assertTrue(result["cleanup"]["clean"])

    def test_lane_failure_withholds_redacts_and_still_cleans(self):
        code, result = self.run_lane(exercise_error=AssertionError("synthetic-private-text"))
        self.assertEqual((code, result["result"]), (1, "WITHHELD"))
        self.assertTrue(result["cleanup"]["clean"])
        self.assertNotIn("synthetic-private-text", json.dumps(result))

    def test_cleanup_failure_overrides_pass(self):
        code, result = self.run_lane(cleanup_error=True)
        self.assertEqual((code, result["result"]), (1, "WITHHELD"))
        self.assertFalse(result["cleanup"]["clean"])

    def test_dirty_checkout_or_prior_run_never_starts(self):
        self.run_lane(dirty=True)
        self.run_lane(existing=True)


class RunningIdentity(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, observed):
        import real_ha_contract_tests as harness
        with tempfile.TemporaryDirectory() as directory, lane.diagnostics.lane("thread_matter_topology"), \
                patch.object(harness, "UPSTREAM_IMAGE", ""), \
                patch.object(harness, "bootstrap_disposable_admin", new=AsyncMock(return_value="synthetic-token")), \
                patch.object(harness, "wait_for_runtime_ready", new=AsyncMock(return_value=observed)), \
                patch.object(acceptance, "run", new=AsyncMock()) as accept:
            with self.assertRaises(lane.Refusal) as caught:
                await lane.exercise(Path(directory), {}, "core", "run", "2g")
            accept.assert_not_called()
            return caught.exception

    async def test_wrong_running_core_version_refuses_before_acceptance(self):
        exc = await self.exercise({"version": "2026.10.3", "components": ["thread"]})
        self.assertEqual(str(exc), "running_core_version_mismatch")
        self.assertEqual(exc._local_lane_check, ("image_verify", "running_identity"))

    async def test_thread_missing_or_matter_loaded_refuses_before_acceptance(self):
        for components in ([], ["thread", "matter"], ["matter"]):
            with self.subTest(components=components):
                exc = await self.exercise({"version": "2026.11.0", "components": components})
                self.assertEqual(str(exc), "running_integration_set_mismatch")


class AcceptanceScript(unittest.IsolatedAsyncioTestCase):
    """Exercise the script with a synthetic Core/transport; never Core evidence."""

    async def run_acceptance(self, *, predecessor_available=False, matter_loaded=False, store_extra=None,
                             echo_source=False):
        created, sent_frames, store = [], [], []

        class Runtime:
            def __init__(self):
                self.signed = bool(created)
                self.current_observation = SimpleNamespace(version="2026.11.0")
                created.append(self)

            def configure(self, *a, **kw):
                pass

            async def reconcile_once(self, _reason):
                pass

            def route_status(self, _caps):
                return {"available": self.signed or predecessor_available, "generation": 1}

            def acquire(self, caps, target=None):
                assert caps == (acceptance.CAPABILITY,) and target is None
                return "lease" if self.signed or predecessor_available else None

            def consume(self, lease):
                return "commit"

            def revalidate(self, lease, commits):
                return True

            def finish(self, commits):
                return True

            def release(self, lease):
                pass

            def health_snapshot(self):
                return {"fallback_count": 0}

        def listed():
            records = [dict(record, **(store_extra or {})) for record in store]
            if echo_source:
                for record in records:
                    record["network_name"] = record["source"]
            return {"datasets": records}

        async def read(reader, *, sections, core_version, authorize, on_dispatch, on_sent):
            authorize()
            if core_version != "2026.11.0":
                raise TopologyError("identity_mismatch")
            outcomes = {}
            for section in sections:
                command = PAYLOADS[section]["type"]
                authorize()
                on_dispatch(command)
                sent_frames.append(command)
                on_sent(command)
                outcome = outcomes[section] = SectionOutcome(section, dispatched=True)
                if section == DATASETS:
                    outcome.result = listed()
                else:
                    outcome.reason = "matter_not_loaded"
            authorize()
            return outcomes

        async def raw(_self, payload):
            kind = payload["type"]
            if kind == LIST_DATASETS_COMMAND:
                return {"success": True, "result": listed() if store else {"datasets": []}}
            if kind == acceptance.ADD_DATASET_COMMAND:
                items = decode(payload["tlv"])
                store.append({
                    "channel": int.from_bytes(items[0], "big"), "created": "2026-11-09T12:00:00.000001+00:00",
                    "dataset_id": "01JSYNTHETIC%014d" % len(store), "extended_pan_id": items[2].hex(),
                    "network_name": items[3].decode(), "pan_id": items[1].hex(), "preferred": False,
                    "preferred_border_agent_id": None, "preferred_extended_address": None,
                    "source": payload["source"]})
                return {"success": True, "result": {"result": "stored"}}
            if kind == NETWORK_TOPOLOGY_COMMAND:
                assert payload == {"type": kind, "refresh": False}
                if matter_loaded:
                    return {"success": True, "result": {"nodes": [], "connections": []}}
                return {"success": False, "error": {"code": "unknown_command"}}
            raise AssertionError(kind)

        registry = SimpleNamespace(refresh=AsyncMock(return_value=True))
        configured = SimpleNamespace(websocket_url="ws://synthetic", ha_token="synthetic-token",
                                     access_secret="synthetic-secret")
        refs = [{"capability_id": f"core.synthetic_{i}"} for i in range(32)]
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(acceptance, "CoreRuntime", side_effect=Runtime), \
                patch.object(acceptance, "CoreReleaseRegistry", return_value=registry), \
                patch.object(acceptance, "prepare_candidate", return_value={}), \
                patch.object(acceptance, "sign_candidate", return_value=b"{}"), \
                patch.object(acceptance, "canonical_json", return_value=b"{}"), \
                patch.object(acceptance.TopologyReader, "read", new=read), \
                patch.object(acceptance.RawCore, "command", new=raw), \
                patch.object(acceptance, "close_disposable_authority", new=AsyncMock()) as close:
            result = await acceptance.run(configured, {"version": "2026.11.0", "capabilities": refs},
                                          Path(directory))
        self.assertEqual(close.await_count, 2)
        return result, sent_frames

    async def test_pass_scenario_counts_and_refusals(self):
        result, sent = await self.run_acceptance()
        self.assertEqual(result["result"], "PASS")
        self.assertEqual(result["authority_transition"], [32, 33])
        self.assertEqual(result["predecessor_reads"], 0)
        self.assertEqual(result["seeded_datasets"], 2)
        self.assertEqual(result["topology"]["datasets_status"], "ok")
        self.assertEqual(result["topology"]["topology_status"], "matter_not_loaded")
        self.assertEqual(result["topology"]["partial_reasons"], ["section_unavailable", "sanitized_text"])
        self.assertEqual(result["topology"]["withheld_identifier_field_count"], 1)
        self.assertEqual(result["commands_sent"], [LIST_DATASETS_COMMAND, NETWORK_TOPOLOGY_COMMAND,
                                                   LIST_DATASETS_COMMAND, NETWORK_TOPOLOGY_COMMAND])
        self.assertEqual(sent, result["commands_sent"])
        self.assertNotIn(acceptance.ADD_DATASET_COMMAND, sent)
        self.assertEqual(result["scope"]["a2_success_lane"], "not_run_pending_matter_server_stub")
        self.assertEqual((result["canary_leaks"], result["topology_refresh"]), (0, False))
        self.assertEqual(result["profile"], acceptance.reference())
        self.assertFalse(result["production_authority"])

    async def test_predecessor_admission_fails_the_lane(self):
        with self.assertRaises(AssertionError):
            await self.run_acceptance(predecessor_available=True)

    async def test_matter_set_up_fails_the_lane(self):
        # This lane proves only the not-set-up case; a loaded Matter is out of scope.
        with self.assertRaises(AssertionError):
            await self.run_acceptance(matter_loaded=True)

    async def test_credential_bearing_store_fails_the_lane(self):
        # A section carrying a TLV key is refused whole, so the A1 success lane fails.
        with self.assertRaises(AssertionError):
            await self.run_acceptance(store_extra={"tlv": "00"})

    async def test_secret_text_in_a_kept_field_is_withheld_not_leaked(self):
        # Echoing the canary source into network_name changes the withheld
        # count; the lane refuses rather than accepting a different projection.
        with self.assertRaises((AssertionError, KeyError)):
            await self.run_acceptance(echo_source=True)


if __name__ == "__main__":
    unittest.main()
