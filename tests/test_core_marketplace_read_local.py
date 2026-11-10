"""Offline HAMCP-290 marketplace_read lane boundaries; no Docker, network or production key."""
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
import core_marketplace_read_local as lane
import marketplace_read_contract_acceptance as acceptance
from core_release_evidence import groups, manifest, passing_subset
from ha_mcp_engineering.marketplace_inventory import (
    HACS_LIST_COMMAND, MARKETPLACE_LIST_COMMAND, MarketplaceError)

GENERATOR = ROOT / "docs/evidence/hamcp290/lane/generate_source_records.py"
ARCHIVES = {v: ROOT / f".artifacts/inputs/core-{v}.tar.gz" for v in ("2026.10.3", "2026.11.0")}
PROVENANCE = json.loads((ROOT / "tests/fixtures/core_2026_11_0_lane_provenance.json").read_text())


class Contracts(unittest.TestCase):
    def test_mapping_never_adds_unsigned_capability_or_lends_another_group(self):
        ref = acceptance.reference()
        self.assertEqual(ref["contract_fingerprint"], lane.CONTRACT_FINGERPRINT)
        self.assertEqual(groups()["marketplace_read"], [ref["capability_id"]])
        self.assertEqual(passing_subset([], {"marketplace_read": True}), [])
        self.assertEqual(passing_subset([ref], {"general": True}), [])
        self.assertEqual(passing_subset([ref], {"helper_value": True}), [])
        self.assertEqual(passing_subset([ref], {"marketplace_read": True}), [ref])
        # Missing receipt (absent result) withholds; it never admits.
        self.assertEqual(passing_subset([ref], {"marketplace_read": False}), [])

    def test_groups_file_places_marketplace_before_security_metadata(self):
        # Release convention: security_metadata stays last; new groups go before it.
        names = list(json.loads((ROOT / "ci/core-evidence-groups.json").read_text()))
        self.assertLess(names.index("helper_value"), names.index("marketplace_read"))
        self.assertLess(names.index("marketplace_read"), names.index("security_metadata"))

    def test_manifest_profile_matches_committed_source_hashes(self):
        proof = json.loads(lane.SOURCE_HASHES.read_text())
        profile = manifest()["profiles"][lane.CAPABILITY]
        self.assertEqual(profile, [row["path"] for row in proof["files"]])
        self.assertEqual(profile, sorted(set(profile)))
        self.assertTrue(set(profile) <= set(manifest()["paths"]))
        self.assertEqual((proof["core_version"], proof["source_commit"], proof["source_archive_sha256"]),
                         (lane.VERSION, PROVENANCE["source_commit"], PROVENANCE["source_archive_sha256"]))
        for path in ("homeassistant/components/marketplace/__init__.py",
                     "homeassistant/components/marketplace/websocket/repositories.py",
                     "homeassistant/components/marketplace/websocket/decorators.py",
                     "homeassistant/components/websocket_api/connection.py",
                     "homeassistant/bootstrap.py"):
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
            with self.assertRaisesRegex(lane.Refusal, "marketplace_manifest_missing"):
                lane.source_manifest(self.archive, PROVENANCE)
        self.write(self.profile, {})
        self.hashes.unlink()
        with self.assertRaisesRegex(lane.Refusal, "marketplace_manifest_missing"):
            self.source()

    def test_hash_mismatch_refuses(self):
        self.write(self.profile, {self.profile[0]: "0" * 64,
                                  self.profile[1]: hashlib.sha256(self.content[self.profile[1]]).hexdigest()})
        with self.assertRaisesRegex(lane.Refusal, "marketplace_source_hash_mismatch"):
            self.source()

    def test_profile_and_record_must_list_identical_paths(self):
        hashes = {p: hashlib.sha256(d).hexdigest() for p, d in self.content.items()}
        self.write(self.profile[:1], hashes)
        with self.assertRaisesRegex(lane.Refusal, "marketplace_manifest_mismatch"):
            self.source()

    def test_record_for_another_core_refuses(self):
        hashes = {p: hashlib.sha256(d).hexdigest() for p, d in self.content.items()}
        self.write(self.profile, hashes, core_version="2026.10.3")
        with self.assertRaisesRegex(lane.Refusal, "marketplace_source_provenance_mismatch"):
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

    def test_predecessor_already_carrying_marketplace_refuses(self):
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
                              "source_hashes_sha256": "s" * 64, "profile_paths": 75})), \
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
                         ("core-marketplace-read-local-evidence-v1", "marketplace_read", "2026.11.0"))
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
                                 ("marketplace_read", "container_start", "port_mapping"))
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

    def test_actions_or_evidence_context_refuses_before_any_command(self):
        # Cursor M1 (#421): the lane is local-only. Either CI marker refuses before
        # git, Docker, the inputs, the run directory or the acceptance are touched.
        for variable, value in (("GITHUB_ACTIONS", "true"), ("CORE_EVIDENCE_CONTEXT", "pull_request")):
            with self.subTest(variable=variable), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / ".artifacts").mkdir()
                out = root / ".artifacts" / "run"
                argv = ["--mode", "local", "--pull", "never", "--journal", "journal",
                        "--source-archive", "archive", "--run-dir", str(out)]
                reached = AssertionError("the lane went past the CI-context refusal")
                with patch.dict(os.environ, {variable: value}, clear=True), \
                        patch.object(lane, "ROOT", root), \
                        patch.object(lane, "command", side_effect=reached) as command, \
                        patch.object(lane.subprocess, "run", side_effect=reached) as process, \
                        patch.object(lane, "inputs", side_effect=reached) as inputs, \
                        patch.object(lane, "images", side_effect=reached) as images, \
                        patch.object(lane, "require_free_ports", side_effect=reached), \
                        patch.object(lane.resources, "wait_for_admission", side_effect=reached), \
                        patch.object(lane.diagnostics, "_raw_log", return_value=False), \
                        patch.object(lane, "exercise", new=AsyncMock(side_effect=reached)) as exercise:
                    with self.assertRaises(lane.Refusal) as caught:
                        lane.main(argv)
                    self.assertEqual(str(caught.exception), "local_only_closed_lane")
                    for mock in (command, process, inputs, images, exercise):
                        mock.assert_not_called()
                    self.assertFalse(out.exists())

    def test_missing_install_candidate_withholds_the_receipt(self):
        # Josh ruling 2026-11-10: with neither named candidate in the store the
        # lane withholds its receipt; the code-owned reason is the only detail.
        code, result = self.run_lane(exercise_error=acceptance.FixtureError("install_candidate_missing"))
        self.assertEqual((code, result["result"]), (1, "WITHHELD"))
        self.assertEqual(result["failure"]["exception_chain"][0]["message"], "install_candidate_missing")
        self.assertNotIn("install_fixture", result)
        self.assertTrue(result["cleanup"]["clean"])


class RunningIdentity(unittest.IsolatedAsyncioTestCase):
    async def test_wrong_running_core_version_refuses_before_acceptance(self):
        import real_ha_contract_tests as harness
        with tempfile.TemporaryDirectory() as directory, lane.diagnostics.lane("marketplace_read"), \
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


def store(installed=(), total=3, theme=acceptance.INSTALL_CANDIDATES[0]):
    """Synthetic raw Core list: two integrations and one theme (the first named candidate)."""
    rows = [{"id": str(100 + i), "full_name": f"owner/repo{i}", "name": f"Repo {i}", "category": "integration",
             "domain": f"repo{i}", "available_version": "1.0", "description": "synthetic integration"}
            for i in range(total - 1)]
    rows.append({"id": "200", "full_name": theme, "name": "Google Light Theme",
                 "category": "theme", "domain": None, "available_version": "v2.0",
                 "description": "A light theme for the disposable lane"})
    for row in rows:
        on = row["id"] in installed
        row.update(installed=on, installed_version=row["available_version"] if on else "",
                   pending_upgrade=False, custom=False, can_install=True, local_path="/config/x",
                   authors=["someone"], stars=3)
    return rows


def page_for(records, partial=()):
    return {"results": records, "partial_reasons": list(partial)}


def projected(row, **changes):
    value = {key: row[key] for key in acceptance.PROJECTED}
    value["domain"] = value["domain"] or None
    value.update(changes)
    return value


class AcceptanceScript(unittest.IsolatedAsyncioTestCase):
    """Exercise the script with synthetic Core/transport; never Core evidence."""

    async def run_acceptance(self, *, predecessor_available=False, reply_version="2026.11.0", fallback=False,
                             github_connected=False, install_success=True, installs=True, project=None,
                             inject=None, health_fallback_count=0, theme=acceptance.INSTALL_CANDIDATES[0]):
        # inject=(n, written): the n-th reader call also admits (and, if written,
        # writes) the HACS list command while the backend stays core_marketplace.
        # Calls: 1 routed read, 2 redaction canary, 3 wrong identity, 4 disabled entry.
        created, sent_frames, raw_sent, read_calls = [], [], [], []
        state = {"disabled": False, "installed": ()}
        self.raw_sent = raw_sent

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
                return {"fallback_count": health_fallback_count}

        async def read(reader, *, core_version, authorize, on_dispatch, on_sent):
            read_calls.append(core_version)
            authorize()
            if core_version != reply_version or core_version == "2026.10.3":
                raise MarketplaceError("identity_mismatch")
            commands = [MARKETPLACE_LIST_COMMAND] + ([HACS_LIST_COMMAND] if fallback else [])
            for command in commands:
                authorize()
                on_dispatch(command)
                sent_frames.append(command)
                on_sent(command)
            if inject is not None and inject[0] == len(read_calls):
                authorize()
                on_dispatch(HACS_LIST_COMMAND)
                if inject[1]:
                    sent_frames.append(HACS_LIST_COMMAND)
                    on_sent(HACS_LIST_COMMAND)
            if state["disabled"]:
                raise MarketplaceError("marketplace_not_loaded")
            authorize()
            value = store(state["installed"], theme=theme)
            return ("hacs" if fallback else "core_marketplace"), (project(value) if project else value)

        async def raw(_self, payload, seconds=60):
            kind = payload["type"]
            raw_sent.append(kind)
            if kind == "marketplace/info":
                return {"success": True, "result": {"stage": "running", "github_connected": github_connected}}
            if kind == "marketplace/warning/accept":
                return {"success": True, "result": None}
            if kind == "marketplace/repository/install":
                self.assertEqual((payload["repository"], seconds), ("200", acceptance.INSTALL_SECONDS))
                if not install_success:
                    return {"success": False, "error": {"code": "error", "message": "upstream text"}}
                if installs:
                    state["installed"] = ("200",)
                return {"success": True, "result": None}
            if kind == MARKETPLACE_LIST_COMMAND:
                if state["disabled"]:
                    return {"success": False, "error": {"code": "not_loaded"}}
                return {"success": True, "result": store(state["installed"], theme=theme)}
            if kind == "config_entries/get":
                return {"success": True, "result": [{"entry_id": "synthetic-entry"}]}
            if kind == "config_entries/disable":
                assert payload == {"type": kind, "entry_id": "synthetic-entry", "disabled_by": "user"}
                state["disabled"] = True
                return {"success": True, "result": {"require_restart": False}}
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
                patch.object(acceptance.MarketplaceReader, "read", new=read), \
                patch.object(acceptance.RawCore, "command", new=raw), \
                patch.object(acceptance.asyncio, "sleep", new=AsyncMock()), \
                patch.object(acceptance, "close_disposable_authority", new=AsyncMock()) as close:
            result = await acceptance.run(configured, {"version": "2026.11.0", "capabilities": refs},
                                          Path(directory))
        self.assertEqual(close.await_count, 2)
        return result, sent_frames

    async def test_pass_scenario_counts_and_refusals(self):
        result, sent = await self.run_acceptance()
        self.assertEqual(result["result"], "PASS")
        self.assertEqual(result["authority_transition"], [32, 33])
        self.assertEqual(result["inventory"]["installed_total"], result["raw_installed"])
        self.assertEqual(result["inventory"]["backend"], "core_marketplace")
        self.assertEqual(result["commands_sent"], [MARKETPLACE_LIST_COMMAND, MARKETPLACE_LIST_COMMAND])
        # Main reader twice plus the redaction-canary reader once; never HACS.
        self.assertEqual(sent, [MARKETPLACE_LIST_COMMAND] * 3)
        self.assertEqual(result["profile"], acceptance.reference())
        self.assertFalse(result["production_authority"])
        # Exactly one disposable install through the harness, then a real installed record.
        self.assertEqual(self.raw_sent.count("marketplace/repository/install"), 1)
        self.assertEqual(self.raw_sent.count("marketplace/warning/accept"), 1)
        self.assertEqual(result["raw_installed"], 1)
        self.assertEqual(result["inventory"]["installed_total"], 1)
        fixture = result["install_fixture"]
        # Josh ruling 2026-11-10: the receipt records the installed repository and version.
        self.assertEqual((fixture["repository"], fixture["id"], fixture["installed_version"]),
                         (acceptance.INSTALL_CANDIDATES[0], "200", "v2.0"))
        self.assertEqual(fixture["candidates"], list(acceptance.INSTALL_CANDIDATES))
        self.assertNotIn("selection", fixture)  # only named candidates exist: nothing to select between
        self.assertEqual(result["fallback_count"], 0)
        self.assertFalse(fixture["github_connected"])
        self.assertFalse(fixture["network_change"])
        record = result["installed_record"]
        self.assertEqual(record["fields"], sorted(acceptance.PROJECTED))
        self.assertEqual((record["category"], record["installed_version"]), ("theme", "v2.0"))
        self.assertEqual(set(record["text"].values()), {"equal"})
        canary = result["redaction_canary"]
        self.assertEqual((canary["field"], canary["completeness"], canary["leaked"]),
                         ("description", "partial", False))
        self.assertIn("sanitized_text", canary["partial_reasons"])

    async def test_connected_github_account_refuses_before_install(self):
        with self.assertRaises(acceptance.FixtureError) as caught:
            await self.run_acceptance(github_connected=True)
        self.assertEqual(caught.exception.reason, "github_account_connected")
        self.assertNotIn("marketplace/repository/install", self.raw_sent)

    async def test_failed_install_fails_without_upstream_text(self):
        with self.assertRaises(acceptance.FixtureError) as caught:
            await self.run_acceptance(install_success=False)
        self.assertEqual(str(caught.exception), "install_failed")

    async def test_install_record_missing_fails_the_lane(self):
        # Core says the install succeeded but the record never reads back installed.
        with self.assertRaises(acceptance.FixtureError) as caught:
            await self.run_acceptance(installs=False)
        self.assertEqual(caught.exception.reason, "installed_record_missing")
        self.assertEqual(self.raw_sent.count("marketplace/repository/install"), 1)

    async def test_record_missing_from_routed_page_fails_the_lane(self):
        # The service read omits the installed record: the lane must not pass.
        def drop(rows):
            return [{**row, "installed": False} if row["id"] == "200" else row for row in rows]
        with self.assertRaises(AssertionError):
            await self.run_acceptance(project=drop)

    async def test_predecessor_admission_fails_the_lane(self):
        with self.assertRaises(AssertionError):
            await self.run_acceptance(predecessor_available=True)

    async def test_hacs_fallback_fails_the_lane(self):
        with self.assertRaises(AssertionError):
            await self.run_acceptance(fallback=True)

    async def test_injected_fallback_command_fails_the_lane(self):
        # Cursor M2 (#421): the backend still says core_marketplace, so only the
        # commands the lane observed can show the fallback. Admitted counts even
        # when the frame was never written.
        for call in (1, 2, 4):
            for written in (False, True):
                with self.subTest(reader_call=call, written=written):
                    with self.assertRaises(AssertionError):
                        await self.run_acceptance(inject=(call, written))

    async def test_health_snapshot_fallback_fails_the_lane(self):
        # Cursor M2 (#421): Marketplace-only commands and backend core_marketplace,
        # but Engineering's own health snapshot counts a fallback.
        with self.assertRaises(AssertionError):
            await self.run_acceptance(health_fallback_count=1)

    def test_fallback_count_is_derived_from_observed_commands(self):
        def reader(admitted, sent):
            return SimpleNamespace(admitted=admitted, sent=sent)
        market, hacs = MARKETPLACE_LIST_COMMAND, HACS_LIST_COMMAND
        count = acceptance.observed_fallbacks
        self.assertEqual(count(reader([market], [market]), reader([], [])), 0)
        self.assertEqual(count(reader([market, hacs], [market])), 1)          # admitted, never written
        self.assertEqual(count(reader([market, hacs], [market, hacs])), 1)    # one command, counted once
        self.assertEqual(count(reader([market], [market, "hacs/info"])), 1)   # any other command counts
        self.assertEqual(count(reader([market, hacs], [market, hacs]), reader([hacs], [])), 2)

    async def test_receipt_fallback_count_is_the_derived_value(self):
        # False == 0, so the script's own zero check passes and the receipt shows
        # where its value came from: the derivation over all four lane readers.
        with patch.object(acceptance, "observed_fallbacks", return_value=False) as derived:
            result, _ = await self.run_acceptance()
        self.assertIs(result["fallback_count"], False)
        derived.assert_called_once()
        readers = derived.call_args.args
        self.assertEqual(len(readers), 4)
        self.assertEqual(len({id(reader) for reader in readers}), 4)
        self.assertTrue(all(isinstance(reader, acceptance.ObservedReader) for reader in readers))

    async def test_store_without_a_named_candidate_fails_before_any_install(self):
        # Josh ruling 2026-11-10: another installable default-catalog theme is not a fallback.
        with self.assertRaises(acceptance.FixtureError) as caught:
            await self.run_acceptance(theme="Alpha/theme")
        self.assertEqual(caught.exception.reason, "install_candidate_missing")
        self.assertFalse(set(acceptance.INSTALL_COMMANDS) & set(self.raw_sent))


class RawSocket(unittest.IsolatedAsyncioTestCase):
    async def test_disposable_core_socket_never_trusts_the_proxy_environment(self):
        # Cursor M3 (#421): the session that carries the disposable token is built
        # with trust_env=False before ws_connect, whatever the environment says.
        events = []

        class Socket:
            def __init__(self):
                self.replies = iter(({"type": "auth_required"}, {"type": "auth_ok"},
                                     {"type": "result", "id": 1, "success": True, "result": None}))

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False

            async def receive_json(self):
                return next(self.replies)

            async def send_json(self, payload):
                events.append(("send", payload))

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

        configured = SimpleNamespace(websocket_url="ws://127.0.0.1:18123/api/websocket", ha_token="synthetic-token")
        proxies = {name: "http://proxy.invalid:3128" for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                                                                  "WS_PROXY", "WSS_PROXY")}
        with patch.dict(os.environ, proxies), patch.object(acceptance.aiohttp, "ClientSession", new=Session):
            reply = await acceptance.RawCore(configured).command({"type": "marketplace/info"})
        self.assertEqual(reply, {"type": "result", "id": 1, "success": True, "result": None})
        self.assertEqual([event[0] for event in events], ["session", "ws_connect", "send", "send"])
        self.assertIs(events[0][1].get("trust_env"), False)
        self.assertEqual(events[1][1:], (configured.websocket_url, {"max_msg_size": acceptance.HARNESS_FRAME_BYTES}))
        self.assertEqual(events[2][1], {"type": "auth", "access_token": "synthetic-token"})
        self.assertEqual(events[3][1], {"id": 1, "type": "marketplace/info"})


class InstallFixture(unittest.IsolatedAsyncioTestCase):
    """Offline checks of the install fixture and installed-record projection."""

    def setUp(self):
        self.raw_row = next(row for row in store(("200",)) if row["id"] == "200")
        self.secrets = ("synthetic-secret", "synthetic-token")

    def test_projected_record_passes(self):
        summary = acceptance.check_installed_record(page_for([projected(self.raw_row)]), self.raw_row, self.secrets)
        self.assertEqual(summary["id"], "200")
        self.assertEqual(summary["fields"], sorted(acceptance.PROJECTED))

    def check_fails(self, records, reason, partial=()):
        with self.assertRaises(acceptance.FixtureError) as caught:
            acceptance.check_installed_record(page_for(records, partial), self.raw_row, self.secrets)
        self.assertEqual(caught.exception.reason, reason)

    def test_missing_record_fails(self):
        self.check_fails([], "installed_record_missing")

    def test_duplicated_record_fails(self):
        self.check_fails([projected(self.raw_row)] * 2, "installed_record_duplicated")

    def test_unprojected_raw_record_fails(self):
        # A raw Core summary (local_path, authors, stars...) is not the projection.
        self.check_fails([dict(self.raw_row)], "installed_record_unprojected")
        partial = projected(self.raw_row)
        del partial["description"]
        self.check_fails([partial], "installed_record_unprojected")

    def test_changed_identity_flags_or_version_fail(self):
        self.check_fails([projected(self.raw_row, full_name="other/repo")], "installed_record_identity_changed")
        self.check_fails([projected(self.raw_row, custom=True)], "installed_record_flags_changed")
        self.check_fails([projected(self.raw_row, installed_version="")], "installed_version_missing")

    def test_text_must_be_bounded_and_disclosed(self):
        long_row = {**self.raw_row, "description": "d" * 400}
        truncated = projected(long_row, description="d" * 300 + acceptance.TRUNCATED)
        with self.assertRaises(acceptance.FixtureError) as caught:
            acceptance.check_installed_record(page_for([truncated]), long_row, self.secrets)
        self.assertEqual(caught.exception.reason, "installed_record_text_changed")
        summary = acceptance.check_installed_record(page_for([truncated], ["sanitized_text"]), long_row, self.secrets)
        self.assertEqual(summary["text"]["description"], "screened")
        unbounded = projected(long_row)
        with self.assertRaises(acceptance.FixtureError) as caught:
            acceptance.check_installed_record(page_for([unbounded], ["sanitized_text"]), long_row, self.secrets)
        self.assertEqual(caught.exception.reason, "installed_record_unbounded")
        self.check_fails([projected(self.raw_row, description="silently changed")], "installed_record_text_changed")

    def test_secret_in_page_fails(self):
        leaked = projected(self.raw_row)
        row = {**self.raw_row, "description": "has synthetic-token inside"}
        leaked["description"] = row["description"]
        with self.assertRaises(acceptance.FixtureError) as caught:
            acceptance.check_installed_record(page_for([leaked]), row, self.secrets)
        self.assertEqual(caught.exception.reason, "secret_in_page")

    def test_named_candidates_are_chosen_in_declared_order(self):
        first, second = acceptance.INSTALL_CANDIDATES
        rows = store()
        self.assertEqual(acceptance.choose_candidate(rows)["full_name"], first)
        only_second = store(theme=second)
        self.assertEqual(acceptance.choose_candidate(only_second)["full_name"], second)
        # Declared order, not store order.
        self.assertEqual(acceptance.choose_candidate([dict(only_second[-1], id="201")] + rows)["id"], "200")

    def test_unnamed_installable_theme_is_never_chosen(self):
        # Josh ruling 2026-11-10: no "first installable default-catalog theme" fallback.
        named = store()[-1]
        unnamed = [dict(named, full_name=name, id=str(i)) for i, name in enumerate(("zeta/theme", "Alpha/theme"))]
        cases = {"other themes": unnamed + store()[:-1], "no theme": store()[:-1], "empty store": [],
                 "custom": [dict(named, custom=True)], "already installed": [dict(named, installed=True)],
                 "wrong category": [dict(named, category="integration")],
                 "name differs by case": [dict(named, full_name=named["full_name"].upper())]}
        for label, rows in cases.items():
            with self.subTest(label):
                with self.assertRaises(acceptance.FixtureError) as caught:
                    acceptance.choose_candidate(rows)
                self.assertEqual(caught.exception.reason, "install_candidate_missing")

    async def test_non_fresh_core_and_second_installed_record_refuse(self):
        class Raw:
            def __init__(self, listed):
                self.listed, self.sent = listed, []

            async def command(self, payload, seconds=60):
                self.sent.append(payload["type"])
                if payload["type"] == "marketplace/info":
                    return {"success": True, "result": {"github_connected": False}}
                if payload["type"] == MARKETPLACE_LIST_COMMAND:
                    return {"success": True, "result": self.listed}
                return {"success": True, "result": None}
        raw = Raw(store(("200",)))
        with self.assertRaises(acceptance.FixtureError) as caught:
            await acceptance.install_fixture(raw, store(("100",)), sleep=AsyncMock())
        self.assertEqual(caught.exception.reason, "disposable_core_not_fresh")
        self.assertEqual(raw.sent, ["marketplace/info"])
        raw = Raw(store(("200", "100")))
        with self.assertRaises(acceptance.FixtureError) as caught:
            await acceptance.install_fixture(raw, store(), sleep=AsyncMock())
        self.assertEqual(caught.exception.reason, "more_than_one_installed")
        # No named candidate: refused after the read-only info command, before
        # the warning accept and the install.
        raw = Raw(store(("200",), theme="Alpha/theme"))
        with self.assertRaises(acceptance.FixtureError) as caught:
            await acceptance.install_fixture(raw, store(theme="Alpha/theme"), sleep=AsyncMock())
        self.assertEqual(caught.exception.reason, "install_candidate_missing")
        self.assertEqual(raw.sent, ["marketplace/info"])

    def test_canary_and_reason_vocabulary(self):
        self.assertEqual(acceptance.redaction_canary(self.raw_row)[0], "description")
        self.assertEqual(acceptance.redaction_canary({**self.raw_row, "description": ""})[0], "name")
        with self.assertRaises(acceptance.FixtureError):
            acceptance.redaction_canary({"description": "", "name": None})
        with self.assertRaises(ValueError):
            acceptance.FixtureError("free text from upstream")
        self.assertTrue(set(acceptance.FIXTURE_REASONS) <= lane.diagnostics.SAFE_MESSAGES)
        self.assertTrue({"install_fixture", "redaction_canary"} <= lane.diagnostics.CHECKS)


if __name__ == "__main__":
    unittest.main()
