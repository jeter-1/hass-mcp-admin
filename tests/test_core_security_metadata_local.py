"""Offline HAMCP-134 security_metadata lane boundaries; no Docker, network or production key."""
import argparse
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
import core_security_metadata_local as lane
import security_metadata_contract_acceptance as acceptance
from core_release_evidence import groups, manifest, passing_subset
from ha_mcp_engineering import security_metadata as sm

GENERATOR = ROOT / "docs/evidence/hamcp134/lane/generate_source_records.py"
ARCHIVES = {v: ROOT / f".artifacts/inputs/core-{v}.tar.gz" for v in ("2026.10.3", "2026.11.0")}
PROVENANCE = json.loads((ROOT / "tests/fixtures/core_2026_11_0_lane_provenance.json").read_text())


class Contracts(unittest.TestCase):
    def test_mapping_never_adds_unsigned_capability_or_lends_another_group(self):
        ref = acceptance.reference()
        self.assertEqual(ref["contract_fingerprint"], lane.CONTRACT_FINGERPRINT)
        self.assertEqual(groups()["security_metadata"], [ref["capability_id"]])
        self.assertEqual(passing_subset([], {"security_metadata": True}), [])
        self.assertEqual(passing_subset([ref], {"general": True}), [])
        self.assertEqual(passing_subset([ref], {"helper_value": True}), [])
        self.assertEqual(passing_subset([ref], {"security_metadata": True}), [ref])
        # Missing receipt (absent result) withholds; it never admits.
        self.assertEqual(passing_subset([ref], {"security_metadata": False}), [])

    def test_groups_file_keeps_what_release_evidence_relies_on(self):
        """HAMCP-358: the invariant ``scripts/core_release_evidence.py`` relies on.

        ``groups()`` needs every reference in exactly one group and the 19
        general references; ``passing_subset()`` looks a group up by name
        only. The position of a group in the file is not relied on.
        """
        value = json.loads((ROOT / "ci/core-evidence-groups.json").read_text())
        flat = [ref for refs in value.values() for ref in refs]
        self.assertEqual(len(flat), len(set(flat)))
        self.assertEqual(len(value["general"]), 19)
        self.assertIn("helper_value", value)
        self.assertEqual(value["security_metadata"], [lane.CAPABILITY])
        self.assertEqual(groups(), value)
        # By name only: the same groups in the opposite order select the same profile.
        ref = acceptance.reference()
        reordered = dict(reversed(list(value.items())))
        self.assertNotEqual(list(reordered), list(value))
        with patch("core_release_evidence.read_json", return_value=reordered):
            self.assertEqual(passing_subset([ref], {"security_metadata": True}), [ref])
            self.assertEqual(passing_subset([ref], {"helper_value": True}), [])
        # A reference in two groups is refused, wherever the groups sit.
        duplicated = {**value, "synthetic_duplicate": [lane.CAPABILITY]}
        with patch("core_release_evidence.read_json", return_value=duplicated):
            with self.assertRaisesRegex(ValueError, "invalid evidence groups"):
                groups()
        # Any other number of general references is refused. Each mapping
        # below is otherwise valid: every reference still in exactly one group.
        extra = "core.synthetic_general_extra"
        self.assertNotIn(extra, flat)
        for general in (value["general"][:-1], [*value["general"], extra]):
            with self.subTest(general_count=len(general)):
                miscounted = {**value, "general": general}
                refs = [ref for group in miscounted.values() for ref in group]
                self.assertEqual(len(refs), len(set(refs)))
                self.assertIn(len(general), (18, 20))
                with patch("core_release_evidence.read_json", return_value=miscounted):
                    with self.assertRaisesRegex(ValueError, "invalid evidence groups"):
                        groups()

    def test_manifest_profile_matches_committed_source_hashes(self):
        proof = json.loads(lane.SOURCE_HASHES.read_text())
        profile = manifest()["profiles"][lane.CAPABILITY]
        self.assertEqual(profile, [row["path"] for row in proof["files"]])
        self.assertEqual(profile, sorted(set(profile)))
        self.assertTrue(set(profile) <= set(manifest()["paths"]))
        self.assertEqual((proof["core_version"], proof["source_commit"], proof["source_archive_sha256"]),
                         (lane.VERSION, PROVENANCE["source_commit"], PROVENANCE["source_archive_sha256"]))
        # One source file per sent command handler plus the frame/auth path.
        for path in ("homeassistant/components/auth/__init__.py",
                     "homeassistant/components/config/auth.py",
                     "homeassistant/components/http/websocket_api.py",
                     "homeassistant/components/http/config.py",
                     "homeassistant/components/websocket_api/commands.py",
                     "homeassistant/components/homeassistant/exposed_entities.py",
                     "homeassistant/components/assist_pipeline/pipeline.py",
                     "homeassistant/components/conversation/http.py",
                     "homeassistant/components/frontend/__init__.py",
                     "homeassistant/components/websocket_api/connection.py",
                     "homeassistant/auth/mfa_modules/__init__.py"):
            self.assertIn(path, profile)
        # The lane record and the O3 source record are the same exact bytes' hashes.
        record = json.loads((ROOT / "docs/evidence/hamcp315/core-sources/2026.11.0.json").read_text())
        for row in proof["files"]:
            self.assertEqual(record["hashes"][row["path"]], row["sha256"])

    @unittest.skipUnless(all(p.is_file() for p in ARCHIVES.values()), "exact Core archives not staged")
    def test_generator_reproduces_committed_records(self):
        args = [arg for v, p in ARCHIVES.items() for arg in ("--archive", f"{v}={p}")]
        result = subprocess.run([sys.executable, "-I", str(GENERATOR), *args, "--check"],
                                capture_output=True, text=True, timeout=300)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_cached_image_requires_exact_repo_digest(self):
        def reply(digests):
            return subprocess.CompletedProcess([], 0, json.dumps([{"Id": "config", "RepoDigests": digests}]).encode())
        with patch.object(lane, "command", return_value=reply(["repo@sha256:other"])):
            with self.assertRaisesRegex(lane.Refusal, "cached_image_digest_mismatch"):
                lane.cached_image("repo:tag@sha256:exact")
        with patch.object(lane, "command", return_value=reply(["repo@sha256:exact"])):
            self.assertEqual(lane.cached_image("repo:tag@sha256:exact"), "config")

    def test_images_are_exact_core_and_fixture_only(self):
        core, fixture = lane.images()
        self.assertTrue(core.startswith("ghcr.io/home-assistant/home-assistant:2026.11.0@sha256:"))
        self.assertTrue(core.endswith(PROVENANCE["image_index_digest"]))
        self.assertEqual(fixture, lane.host.FIXTURE_IMAGE)

    def test_inspection_error_is_not_absence_and_wrong_owner_is_refused(self):
        failed = subprocess.CompletedProcess([], 1, b"")
        with patch.object(lane, "command", side_effect=[failed, lane.Refusal("daemon_failed")]):
            with self.assertRaises(lane.Refusal):
                lane.inspect_owned("container", "owned", "id")
        data = {"Config": {"Labels": {lane.LABEL: "another-run"}}}
        with patch.object(lane, "command", return_value=subprocess.CompletedProcess([], 0, json.dumps([data]).encode())):
            with self.assertRaisesRegex(lane.Refusal, "resource_ownership_mismatch"):
                lane.inspect_owned("container", "owned", "id")


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
            with self.assertRaisesRegex(lane.Refusal, "security_metadata_manifest_missing"):
                lane.source_manifest(self.archive, PROVENANCE)
        self.write(self.profile, {})
        self.hashes.unlink()
        with self.assertRaisesRegex(lane.Refusal, "security_metadata_manifest_missing"):
            self.source()

    def test_hash_mismatch_refuses(self):
        self.write(self.profile, {self.profile[0]: "0" * 64,
                                  self.profile[1]: hashlib.sha256(self.content[self.profile[1]]).hexdigest()})
        with self.assertRaisesRegex(lane.Refusal, "security_metadata_source_hash_mismatch"):
            self.source()

    def test_profile_and_record_must_list_identical_paths(self):
        hashes = {p: hashlib.sha256(d).hexdigest() for p, d in self.content.items()}
        self.write(self.profile[:1], hashes)
        with self.assertRaisesRegex(lane.Refusal, "security_metadata_manifest_mismatch"):
            self.source()

    def test_record_for_another_core_refuses(self):
        hashes = {p: hashlib.sha256(d).hexdigest() for p, d in self.content.items()}
        self.write(self.profile, hashes, core_version="2026.10.3")
        with self.assertRaisesRegex(lane.Refusal, "security_metadata_source_provenance_mismatch"):
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

    def test_wrong_journal_sequence_or_entry_refuses(self):
        import prepare_core_release_registry as prepare
        for sequence, entries, reason in ((16, [], "predecessor_sequence_mismatch"),
                                          (18, [], "predecessor_sequence_mismatch"),
                                          (17, [], "predecessor_entry_missing")):
            parsed = SimpleNamespace(accepted=SimpleNamespace(sequence=sequence, entries=entries))
            with self.subTest(sequence=sequence), patch.object(lane, "sha256", side_effect=self.digests()), \
                    patch.object(lane, "source_manifest", return_value={}), \
                    patch("core_registry_one_tap.load_public_key", return_value=None), \
                    patch.object(prepare, "parse_journal", return_value=parsed):
                with self.assertRaisesRegex(lane.Refusal, reason):
                    lane.inputs(self.journal, self.archive)

    def test_predecessor_already_carrying_security_metadata_refuses(self):
        import prepare_core_release_registry as prepare
        refs = [{"capability_id": "core.x%d" % i} for i in range(31)] + [acceptance.reference()]
        entry = SimpleNamespace(version=lane.VERSION, to_mapping=lambda: {
            **{k: v for k, v in PROVENANCE.items()}, "capabilities": refs})
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
                 admission_error=None, memory_args=(), network_override=None, ports_override=None,
                 exercise_result=None):
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
                return network_override if network_override is not None else {"Driver": "bridge", "Internal": False}
            ports = {"8123/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18123"}]}
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
                    "--source-archive", "archive", "--run-dir", str(out), *memory_args]
            with patch.dict(os.environ, {"REAL_HA_UPSTREAM_IMAGE": "stale"}, clear=True), \
                 patch.object(lane, "ROOT", root), \
                 patch.object(lane, "command", side_effect=command), \
                 patch.object(lane, "inputs", return_value=({}, reference, {"manifest_sha256": "m" * 64,
                              "source_hashes_sha256": "s" * 64, "profile_paths": 33})), \
                 patch.object(lane, "images", return_value=("core@sha256:a", lane.host.FIXTURE_IMAGE)), \
                 patch.object(lane, "cached_image", return_value="config"), \
                 patch.object(lane.resources, "wait_for_admission", side_effect=admission_error,
                              return_value={"other_heavy_jobs": 0}), \
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
                if network_override is not None or ports_override is not None:
                    exercise.assert_not_called()
                result = json.loads((out / lane.RECEIPT).read_text())
                if admission_error:
                    self.assertEqual(code, 1)
                    self.assertFalse(result["execution_started"])
                    self.assertTrue(result["cleanup"]["clean"])
                    self.assertFalse(resources)
                    self.assertFalse(any(c[:2] == ("docker", "run") for c in calls))
                    return
                self.assertNotIn("REAL_HA_UPSTREAM_IMAGE", os.environ)
            self.assertFalse(resources)
            runs = [c for c in calls if c[:2] == ("docker", "run")]
            self.assertLessEqual(len(runs), 1)  # Core only: never an upstream container.
            for call in runs:
                self.assertEqual(call[call.index("--pull") + 1], "never")
                self.assertIn("127.0.0.1:18123:8123", call)
                self.assertEqual(call[call.index("--memory") + 1], "3g" if memory_args else "2g")
            for call in calls:
                if call[:3] == ("docker", "network", "create"):
                    self.assertEqual(call[call.index("--driver") + 1], "bridge")
                    self.assertNotIn("--internal", call)
                self.assertNotIn("pull", call[1:2])
            return code, result

    def test_pass_binds_fingerprint_and_requires_cleanup(self):
        code, result = self.run_lane()
        self.assertEqual(code, 0)
        self.assertEqual(result["result"], "PASS")
        self.assertTrue(result["cleanup"]["clean"])
        self.assertEqual(result["contract_fingerprint"], lane.CONTRACT_FINGERPRINT)
        self.assertEqual(result["profile_reference"], acceptance.reference())
        self.assertEqual((result["model"], result["group"], result["core_version"]),
                         ("core-security-metadata-read-local-evidence-v1", "security_metadata", "2026.11.0"))
        self.assertEqual(result["journal_sha256"], lane.JOURNAL_SHA256)

    def test_receipt_profile_drift_withholds(self):
        drift = {**acceptance.reference(), "contract_fingerprint": "sha256:" + "1" * 64}
        code, result = self.run_lane(exercise_result={"result": "PASS", "profile": drift})
        self.assertEqual(code, 1)
        self.assertEqual(result["result"], "WITHHELD")

    def test_admission_timeout_creates_refusal_receipt_without_containers(self):
        self.run_lane(admission_error=lane.resources.Refusal("host_admission_timeout"))

    def test_explicit_memory_override(self):
        code, result = self.run_lane(memory_args=("--core-memory", "3g", "--fixture-memory", "256m"))
        self.assertEqual(code, 0)
        self.assertEqual(result["container_memory"], {"core": "3g", "fixture": "256m"})

    def test_missing_or_wildcard_mapping_refuses_before_readiness(self):
        for ports in ({}, {"8123/tcp": None}, {"8123/tcp": [{"HostIp": "0.0.0.0", "HostPort": "18123"}]}):
            with self.subTest(ports=ports):
                code, result = self.run_lane(ports_override=ports)
                self.assertEqual(code, 1)
                self.assertEqual((result["failure"]["lane"], result["failure"]["stage"], result["failure"]["check"]),
                                 ("security_metadata", "container_start", "port_mapping"))
                self.assertTrue(result["cleanup"]["clean"])

    def test_wrong_network_posture_refuses(self):
        for network in ({"Driver": "bridge", "Internal": True}, {"Driver": "host", "Internal": False}, {}):
            with self.subTest(network=network):
                code, result = self.run_lane(network_override=network)
                self.assertEqual(code, 1)
                self.assertEqual(result["failure"]["check"], "network_create")

    def test_lane_failure_withholds_redacts_and_still_cleans(self):
        code, result = self.run_lane(exercise_error=AssertionError("synthetic-private-text"))
        self.assertEqual((code, result["result"]), (1, "WITHHELD"))
        self.assertTrue(result["cleanup"]["clean"])
        self.assertNotIn("synthetic-private-text", json.dumps(result))

    def test_cleanup_failure_overrides_pass(self):
        code, result = self.run_lane(cleanup_error=True)
        self.assertEqual((code, result["result"]), (1, "WITHHELD"))
        self.assertFalse(result["cleanup"]["clean"])

    def test_dirty_checkout_never_starts(self):
        self.run_lane(dirty=True)

    def test_prior_run_is_preserved(self):
        self.run_lane(existing=True)

    def test_unsupported_mode_or_pull_policy_refuses(self):
        for argv in (["--mode", "ci"], ["--pull", "always"]):
            with self.subTest(argv=argv), patch("sys.stderr", new_callable=io.StringIO):
                base = {"--mode": "local", "--pull": "never"}
                base[argv[0]] = argv[1]
                with self.assertRaises(SystemExit):
                    lane.main([*sum(base.items(), ()), "--journal", "j", "--source-archive", "a", "--run-dir", "r"])


class RunningIdentity(unittest.IsolatedAsyncioTestCase):
    async def test_wrong_running_core_version_refuses_before_acceptance(self):
        import real_ha_contract_tests as harness
        with tempfile.TemporaryDirectory() as directory, lane.diagnostics.lane("security_metadata"), \
                patch.object(harness, "UPSTREAM_IMAGE", ""), \
                patch.object(harness, "bootstrap_disposable_admin", new=AsyncMock(return_value="synthetic-token")), \
                patch.object(harness, "wait_for_runtime_ready", new=AsyncMock(return_value={"version": "2026.10.3"})), \
                patch.object(acceptance, "run", new=AsyncMock()) as accept:
            with self.assertRaisesRegex(lane.Refusal, "running_core_version_mismatch") as caught:
                await lane.exercise(Path(directory), {}, "core", "run", "2g")
            self.assertEqual(caught.exception._local_lane_check, ("image_verify", "running_identity"))
            accept.assert_not_called()

    async def test_configured_upstream_refuses(self):
        import real_ha_contract_tests as harness
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(harness, "UPSTREAM_IMAGE", "repo@sha256:synthetic"), \
                patch.object(harness, "bootstrap_disposable_admin", new=AsyncMock(return_value="synthetic-token")), \
                patch.object(acceptance, "run", new=AsyncMock()) as accept:
            with self.assertRaisesRegex(lane.Refusal, "upstream_not_expected"):
                await lane.exercise(Path(directory), {}, "core", "run", "2g")
            accept.assert_not_called()




def admin_current_user():
    # The disposable lane connects as a bootstrapped admin over HA_URL/HA_TOKEN.
    return {"id": "u-raw-owner-0001", "name": "SynthOwnerName", "is_owner": True, "is_admin": True,
            "credentials": [{"auth_provider_type": "homeassistant", "auth_provider_id": None}],
            "mfa_modules": [{"id": "totp", "name": "Authenticator app", "enabled": False}]}


class AcceptanceScript(unittest.IsolatedAsyncioTestCase):
    """Exercise the script with synthetic Core/transport; never Core evidence."""

    async def run_acceptance(self, *, predecessor_available=False, extra_command=None, mutate=False,
                             socket=False):
        from copy import deepcopy
        from tests import test_hamcp134_security_metadata as fixtures
        data = fixtures.results(supervisor=False)
        data[sm.CURRENT_USER] = admin_current_user()
        created, sent_frames, raw_calls = [], [], []
        self.socket_events = events = []

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
                return {"available": self.signed or predecessor_available, "generation": 1,
                        "observed_core_version": "2026.11.0"}

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

        async def read(reader, *, core_version, plan, authorize, on_dispatch, on_sent):
            authorize()
            if core_version != "2026.11.0":
                raise sm.SecurityMetadataError("identity_mismatch")
            replies = []
            for kind in [p["type"] for p in plan] + ([extra_command] if extra_command else []):
                authorize()
                on_dispatch(kind)
                sent_frames.append(kind)
                on_sent(kind)
                replies.append(sm.Reply(kind, result=deepcopy(data[kind])))
            return replies

        async def raw(_self, payload):
            kind = payload["type"]
            raw_calls.append(kind)
            if kind == "config/entity_registry/list":
                changed = mutate and raw_calls.count(kind) > 1
                return {"type": "result", "id": 1, "success": True,
                        "result": [{"entity_id": "input_boolean.synthetic"}] if changed else []}
            return {"type": "result", "id": 1, "success": True, "result": deepcopy(data[kind])}

        class Socket:
            def __init__(self):
                self.replies = [{"type": "auth_required"}]

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False

            async def receive_json(self):
                return self.replies.pop(0)

            async def send_json(self, payload):
                events.append(("send", payload))
                if payload["type"] == "auth":
                    self.replies.append({"type": "auth_ok"})
                else:
                    self.replies.append({**await raw(None, payload), "id": payload["id"]})

        class Session:
            def __init__(self, **kwargs):
                events.append(("session", kwargs))

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False

            def ws_connect(self, url, **kwargs):
                events.append(("ws_connect", url, kwargs))
                return Socket()

        # Either the client's one send method is replaced, or the real client
        # runs over a stand-in session that answers from the same synthetic data.
        transport = (patch.object(acceptance.aiohttp, "ClientSession", new=Session) if socket
                     else patch.object(acceptance.RawCore, "command", new=raw))
        registry = SimpleNamespace(refresh=AsyncMock(return_value=True))
        configured = SimpleNamespace(websocket_url=acceptance.RAW_CORE_URL, ha_token=fixtures.HA_TOKEN,
                                     access_secret=fixtures.ACCESS_SECRET)
        refs = [{"capability_id": f"core.synthetic_{i}"} for i in range(32)]
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(acceptance, "CoreRuntime", side_effect=Runtime), \
                patch.object(acceptance, "CoreReleaseRegistry", return_value=registry), \
                patch.object(acceptance, "prepare_candidate", return_value={}), \
                patch.object(acceptance, "sign_candidate", return_value=b"{}"), \
                patch.object(acceptance, "canonical_json", return_value=b"{}"), \
                patch.object(acceptance.SecurityMetadataReader, "read", new=read), \
                transport, \
                patch.object(acceptance, "close_disposable_authority", new=AsyncMock()) as close:
            result = await acceptance.run(configured, {"version": "2026.11.0", "capabilities": refs},
                                          Path(directory))
        self.assertEqual(close.await_count, 2)
        # The harness's own raw traffic is reads only: never a write-shaped type.
        self.assertTrue(set(raw_calls) <= {s["type"] for s in acceptance.SNAPSHOTS} | {
            sm.CURRENT_USER, sm.GET_CONFIG, sm.AGENT_LIST, sm.GET_PANELS})
        return result, sent_frames

    async def test_pass_scenario_ledger_counts_and_refusals(self):
        result, sent = await self.run_acceptance()
        self.assertEqual(result["result"], "PASS")
        self.assertEqual(result["authority_transition"], [32, 33])
        self.assertEqual(result["predecessor_reads"], 0)
        # Exactly the design's ten full-plan reads, then auth/current_user alone for sections=["mfa"].
        self.assertEqual(result["full_ledger"], [p["type"] for p in sm.read_plan(sm.validate_arguments({}))])
        self.assertEqual(result["commands_sent"], [*result["full_ledger"], sm.CURRENT_USER])
        self.assertEqual(sent, result["commands_sent"])
        self.assertEqual(result["item_counts"]["users"], result["raw_counts"]["users"])
        self.assertEqual(result["item_counts"]["panels"], result["raw_counts"]["panels"])
        self.assertEqual(result["connection_identity"]["kind"], "other_connection_user")
        self.assertEqual(result["coverage"]["mfa"]["reason_code"], "connection_identity_not_supervisor")
        self.assertEqual(set(result["refusals"]["wrong_core_version"].values()), {"identity_mismatch"})
        self.assertLessEqual(result["output_bytes"], sm.OUTPUT_BYTES)
        self.assertEqual(result["profile"], acceptance.reference())
        self.assertFalse(result["production_authority"])

    async def test_the_lane_passes_over_its_own_pinned_socket(self):
        # HAMCP-364: the same scenario with the real RawCore. Every raw command
        # the lane sends is on its closed list, and each goes to the pinned URL.
        from tests import test_hamcp134_security_metadata as fixtures
        result, _sent = await self.run_acceptance(socket=True)
        self.assertEqual(result["result"], "PASS")
        events = self.socket_events
        sessions = [event[1] for event in events if event[0] == "session"]
        dialled = [event for event in events if event[0] == "ws_connect"]
        frames = [event[1] for event in events if event[0] == "send"]
        self.assertTrue(dialled)
        self.assertEqual((len(sessions), len(frames)), (len(dialled), 2 * len(dialled)))
        for kwargs in sessions:
            self.assertEqual(set(kwargs), {"timeout", "trust_env", "trace_configs"})
            self.assertIs(kwargs["trust_env"], False)
            self.assertEqual(kwargs["timeout"].total, 60)
        for _kind, url, kwargs in dialled:
            self.assertIs(url, acceptance.RAW_CORE_URL)
            self.assertEqual(kwargs, {})
        self.assertEqual(acceptance.RAW_CORE_URL, f"ws://127.0.0.1:{lane.PORTS[0]}/api/websocket")
        self.assertEqual(frames[0::2], [{"type": "auth", "access_token": fixtures.HA_TOKEN}] * len(dialled))
        commands = frames[1::2]
        for frame in commands:
            self.assertEqual(frame["id"], 1)
            self.assertTrue(acceptance.raw_command_listed({k: v for k, v in frame.items() if k != "id"}), frame)
        # The list holds nothing the lane does not send in this run.
        self.assertEqual({(frame["type"], frame.get("assistant")) for frame in commands},
                         {(kind, None) for kind, fields in acceptance.RAW_COMMANDS.items() if not fields}
                         | {(acceptance.EXPOSE_NEW_GET, name) for name in acceptance.KNOWN_ASSISTANTS})

    async def test_an_unpinned_core_url_stops_the_lane_before_any_session(self):
        from tests import test_hamcp134_security_metadata as fixtures
        events, refs = [], [{"capability_id": f"core.synthetic_{i}"} for i in range(32)]

        class Session:
            def __init__(self, **kwargs):
                events.append(kwargs)

        for url in ("ws://synthetic", "ws://127.0.0.1:8123/api/websocket", "ws://198.51.100.7:18123/api/websocket",
                    "ws://127.0.0.1:18123@core.example.invalid/api/websocket"):
            configured = SimpleNamespace(websocket_url=url, ha_token=fixtures.HA_TOKEN,
                                         access_secret=fixtures.ACCESS_SECRET)
            with self.subTest(url=url), tempfile.TemporaryDirectory() as directory, \
                    patch.object(acceptance.aiohttp, "ClientSession", new=Session), \
                    patch.object(acceptance, "CoreRuntime") as runtime, \
                    patch.object(acceptance.SecurityMetadataReader, "read", new=AsyncMock()) as read:
                with self.assertRaises(ValueError) as caught:
                    await acceptance.run(configured, {"version": "2026.11.0", "capabilities": refs},
                                         Path(directory))
                self.assertEqual(caught.exception.args, ("raw_core_url_not_pinned",))
                self.assertEqual((events, read.await_count, runtime.call_count), ([], 0, 0))

    async def test_predecessor_admission_fails_the_lane(self):
        with self.assertRaises(AssertionError):
            await self.run_acceptance(predecessor_available=True)

    async def test_unplanned_command_fails_the_lane(self):
        # The service refuses an unplanned send; the lane's full-report step then fails.
        with self.assertRaises((AssertionError, sm.SecurityMetadataError)):
            await self.run_acceptance(extra_command=sm.GET_CONFIG)

    async def test_changed_store_between_snapshots_fails_the_lane(self):
        with self.assertRaises(AssertionError):
            await self.run_acceptance(mutate=True)

    async def test_lane_owns_no_write_or_supervisor_command(self):
        source = (ROOT / "scripts/security_metadata_contract_acceptance.py").read_text()
        for forbidden in ("supervisor/", "hassio/", "/create", "/update", "/delete", "/set", "call_service"):
            self.assertNotIn('"' + forbidden, source)
        for payload in acceptance.SNAPSHOTS:
            self.assertNotIn(payload["type"], sm.NEVER_SENT_COMMANDS)
            self.assertFalse(payload["type"].startswith(sm.NEVER_SENT_PREFIXES))


if __name__ == "__main__":
    unittest.main()
