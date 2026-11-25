"""HAMCP-489 offline contracts: Core 2026.11.3 as a new reviewed main anchor over the signed 2026.11.0.

No Docker, network, Home Assistant, GitHub credential or production key. The anchor and journal come
only from committed reviewed data (D5): the 2026.11.3 provenance fixture, its REVIEWED_CORE_BINDINGS
entry and the four per-lane source-hash records. Every new or changed guard has a refusal here, and
2026.11.0's local and renewal paths are shown unchanged.
"""
from dataclasses import asdict
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT / "scripts")]

import core_created_object_local as created_object  # noqa: E402
import core_device_configuration_local as device  # noqa: E402
import core_evidence_workflow as w  # noqa: E402
import core_helper_value_local as helper  # noqa: E402
import core_lane_bindings  # noqa: E402
import core_lane_host as lane_host  # noqa: E402
import core_lane_proof as proof  # noqa: E402
import core_marketplace_read_local as marketplace  # noqa: E402
import core_release_evidence as e  # noqa: E402
import core_security_metadata_local as security  # noqa: E402
import core_thread_matter_topology_a2_local as a2  # noqa: E402
import core_thread_matter_topology_local as a1  # noqa: E402
import thread_matter_topology_a2_acceptance as a2_acceptance  # noqa: E402
from core_lane_bindings import REVIEWED_CORE_BINDINGS  # noqa: E402

ANCHOR = "2026.11.3"
PREDECESSOR = "2026.11.0"
RUN = {"GITHUB_RUN_ID": "489", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_SHA": "a" * 40}
JOURNAL = ROOT / "upstream-trust/ha-core-release-registry.json"
CORE_SOURCES = ROOT / "docs/evidence/hamcp315/core-sources"
# The exact 2026.11.3 archive, when a local run has it (ignored scratch; never committed).
REAL_ARCHIVE = ROOT / ".artifacts/core-2026.11.3.tar.gz"
# Lanes whose CI source hashes are a per-lane {core_version, source_commit, source_archive_sha256, files} record.
LISTED = (marketplace, security, a1, device)
LANES = (helper, marketplace, security, a1, device, created_object)  # a2's CI context is a1's
# The committed 2026.11.3 records: path, sha256, and the files that differ from the 2026.11.0 sibling.
RECORDS = {
    marketplace: ("docs/evidence/hamcp290/lane/core-marketplace-source-hashes-2026.11.3.json",
                  "42863229650473af9649255ad52ede547d9ead79616fdf1d0d887410f0e4cccf",
                  {"homeassistant/components/marketplace/base.py",
                   "homeassistant/components/marketplace/repositories/base.py", "homeassistant/const.py"}),
    security: ("docs/evidence/hamcp134/lane/core-security-metadata-source-hashes-2026.11.3.json",
               "db8384e78c5418372241770ed3720c16af8ddb93e6a32a4d6f357ab1cfd8ef22", {"homeassistant/core_config.py"}),
    a1: ("docs/evidence/hamcp231/lane/core-thread-matter-source-hashes-2026.11.3.json",
         "fd03a8e356371c7ac9210bd929359cc12ccaffdead62b31f8f1f10f60dbcd2c5", {"homeassistant/const.py"}),
    device: ("docs/evidence/hamcp297/lane/core-device-configuration-source-hashes-2026.11.3.json",
             "a31e3717124369e88c06148e396f8fce2579873ce836f3884c40a3c53778ebca", {"homeassistant/const.py"}),
}
PREFIX = {marketplace: "marketplace", security: "security_metadata", a1: "thread_matter",
          device: "device_configuration", created_object: "created_object"}


class Reached(Exception):
    """Raised by a mock that must not be reached."""


def fixture(version=ANCHOR):
    return e.read_json(ROOT / "tests/fixtures" / core_lane_bindings.provenance_name(version))


def signed(version):
    journal, _ = w.journal(JOURNAL.read_bytes())
    return next(x.to_mapping() for x in journal.accepted.entries if x.version == version)


def main_ctx(**changes):
    """The context prepare builds for 2026.11.3: its own main anchor over the signed 2026.11.0."""
    value = {"model": "core-evidence-context-v1", "run": dict(RUN),
             "manifest_sha256": e.digest(e.canonical(e.manifest())), "anchor": ANCHOR,
             "binding": asdict(REVIEWED_CORE_BINDINGS[ANCHOR]), "provenance": fixture(),
             "predecessor": PREDECESSOR, "mode": "main", "proof": None, "fixture": False}
    value.update(changes)
    return value


def renewal_ctx():
    return main_ctx(anchor=PREDECESSOR, binding=asdict(REVIEWED_CORE_BINDINGS[PREDECESSOR]),
                    provenance=fixture(PREDECESSOR))


def actions(**extra):
    return {**{k: v for k, v in os.environ.items()
               if k not in ("GITHUB_ACTIONS", "CORE_EVIDENCE_CONTEXT", "RUNNER_TEMP")}, **extra}


def git(revision=RUN["GITHUB_SHA"]):
    def command(*args, **_kwargs):
        if args[:2] == ("git", "status"):
            return mock.Mock(returncode=0, stdout=b"")
        if args[:2] == ("git", "rev-parse"):
            return mock.Mock(returncode=0, stdout=revision.encode() + b"\n")
        raise Reached(args)
    return command


def profile(module, group=None):
    capability = created_object.LANES[group][0] if module is created_object else module.CAPABILITY
    return e.read_json(module.MANIFEST)["profiles"][capability]


def scratch(test):
    (ROOT / ".artifacts").mkdir(exist_ok=True)
    directory = tempfile.TemporaryDirectory(dir=ROOT / ".artifacts")
    test.addCleanup(directory.cleanup)
    return Path(directory.name)


class Verify(unittest.TestCase):
    """(a) verify_context: the anchor's renewal, or a new reviewed anchor over a lower stable release."""

    def refused(self, pattern, value):
        with self.assertRaisesRegex(ValueError, pattern):
            e.verify_context(value, RUN)

    def test_new_anchor_over_the_signed_predecessor_is_admitted(self):
        self.assertEqual(e.verify_context(main_ctx(), RUN)["anchor"], ANCHOR)
        self.assertEqual(e.verify_context(renewal_ctx(), RUN)["predecessor"], PREDECESSOR)

    def test_predecessor_above_the_anchor_or_not_stable_refuses(self):
        for predecessor in ("2026.11.4", "2026.12.0", "2026.11.3b1", "2026.11", "", None, 2026):
            with self.subTest(predecessor=predecessor):
                self.refused("main_predecessor_invalid", main_ctx(predecessor=predecessor))

    def test_unreviewed_anchor_refuses(self):
        for anchor in ("2026.11.2", "2026.11.4", "2026.12.0"):
            with self.subTest(anchor=anchor):
                self.refused("unreviewed binding anchor", main_ctx(anchor=anchor))

    def test_main_provenance_and_binding_cannot_be_substituted(self):
        for key, value in (("source_commit", "d" * 40), ("source_tree", "d" * 40),
                           ("source_archive_sha256", "d" * 64), ("entry_id", "ha-core-2026.11.3-reviewed-v1")):
            changed = main_ctx(); changed["provenance"][key] = value
            with self.subTest(key=key):
                self.refused("main binding provenance mismatch", changed)
        changed = main_ctx(); changed["binding"]["child_consumer"] = "8.5.0"
        self.refused("derived binding changes semantics", changed)
        # The 2026.11.0 fixture under the 2026.11.3 anchor: another release's provenance.
        changed = main_ctx(provenance=fixture(PREDECESSOR))
        self.refused("derived binding changes semantics|main binding provenance mismatch", changed)


class Baseline(unittest.TestCase):
    """(b) authenticated_baseline: the newest signed entry below a new anchor; renewal stays exact."""

    def baseline(self, ctx, entry):
        directory = scratch(self)
        (directory / "predecessor.json").write_bytes(JOURNAL.read_bytes())
        w.write(directory / "baseline.json", entry)
        return proof.authenticated_baseline(ctx, directory)

    def test_newest_signed_predecessor_is_the_baseline(self):
        self.assertEqual(self.baseline(main_ctx(), signed(PREDECESSOR)), signed(PREDECESSOR))

    def test_an_older_signed_entry_is_not_the_newest(self):
        # 2026.10.3 is signed and below 2026.11.3, but 2026.11.0 lies between them.
        with self.assertRaisesRegex(proof.ProofRefusal, "baseline_not_newest"):
            self.baseline(main_ctx(predecessor="2026.10.3"), signed("2026.10.3"))

    def test_an_unsigned_or_altered_baseline_refuses(self):
        for key, value in (("entry_id", "ha-core-2026.11.0-reviewed-v2"), ("source_commit", "d" * 40)):
            with self.subTest(key=key), self.assertRaisesRegex(proof.ProofRefusal, "baseline_not_signed"):
                self.baseline(main_ctx(), {**signed(PREDECESSOR), key: value})

    def test_baseline_version_must_be_the_context_predecessor(self):
        with self.assertRaisesRegex(proof.ProofRefusal, "baseline_version_mismatch"):
            self.baseline(main_ctx(predecessor="2026.10.3"), signed(PREDECESSOR))

    def test_renewal_with_drifted_provenance_still_refuses(self):
        self.assertEqual(self.baseline(renewal_ctx(), signed(PREDECESSOR)), signed(PREDECESSOR))
        for key, value in (("source_commit", "d" * 40), ("image_index_digest", "sha256:" + "d" * 64)):
            ctx = renewal_ctx(); ctx["provenance"][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(proof.ProofRefusal, "baseline_provenance_mismatch"):
                self.baseline(ctx, signed(PREDECESSOR))

    def test_ci_entry_is_the_signed_predecessor_under_the_anchor_s_provenance(self):
        directory = scratch(self)
        (directory / "predecessor.json").write_bytes(JOURNAL.read_bytes())
        w.write(directory / "baseline.json", signed(PREDECESSOR))
        for module in LANES:
            with self.subTest(module=module.__name__), \
                    mock.patch.dict(os.environ, {e.CONTEXT_ENV: str(directory / "context.json")}, clear=True), \
                    mock.patch.object(module, "sha256", return_value=fixture()["source_archive_sha256"]):
                entry = module.LANE_HOST.ci_entry(main_ctx(), directory / "core.tar.gz")
            self.assertEqual(entry, {**signed(PREDECESSOR), **fixture()})


class LaneContext(unittest.TestCase):
    """(c) ci_context admits an anchor only with both the lane's reviewed data and a reviewed binding."""

    def ci(self, module, ctx, environ=None, revision=RUN["GITHUB_SHA"]):
        with mock.patch.dict(os.environ, actions(GITHUB_ACTIONS="true") if environ is None else environ, clear=True), \
                mock.patch.object(lane_host.release, "context", return_value=ctx), \
                mock.patch.object(module, "command", git(revision)):
            return module.LANE_HOST.ci_context()

    def refused(self, module, ctx, environ=None, revision=RUN["GITHUB_SHA"]):
        with self.assertRaises(module.Refusal) as caught:
            self.ci(module, ctx, environ, revision)
        return str(caught.exception)

    def test_every_lane_admits_the_2026_11_3_anchor(self):
        for module in LANES:
            with self.subTest(module=module.__name__):
                self.assertEqual(module.REVIEWED_ANCHORS, frozenset({PREDECESSOR, ANCHOR}))
                self.assertEqual(self.ci(module, main_ctx()), (main_ctx(), RUN["GITHUB_SHA"]))

    def test_an_anchor_without_the_lane_s_reviewed_data_refuses(self):
        for module in LANES:
            with self.subTest(module=module.__name__), \
                    mock.patch.object(module, "REVIEWED_ANCHORS", frozenset({PREDECESSOR})):
                self.assertEqual(self.refused(module, main_ctx()), "lane_anchor_unreviewed")

    def test_lane_data_without_a_reviewed_binding_refuses(self):
        for module in LANES:
            with self.subTest(module=module.__name__), \
                    mock.patch.object(module, "REVIEWED_ANCHORS", frozenset({PREDECESSOR, ANCHOR, "2026.12.0"})):
                self.assertEqual(self.refused(module, main_ctx(anchor="2026.12.0")), "lane_anchor_unreviewed")

    def test_off_actions_or_another_commit_still_refuses(self):
        for module in LANES:
            with self.subTest(module=module.__name__):
                self.assertEqual(self.refused(module, main_ctx(), actions()), "ci_mode_requires_actions")
                self.assertEqual(self.refused(module, main_ctx(), revision="9" * 40), "ci_revision_mismatch")

    def test_lane_provenance_is_the_anchor_s_committed_fixture(self):
        self.assertEqual(lane_host.lane_provenance(main_ctx()), fixture())
        # Derived mode: the release inherits the anchor's sources, so its own provenance differs.
        derived = renewal_ctx(); derived.update(mode="derived", provenance={**fixture(PREDECESSOR), "version": "2026.11.1"})
        self.assertEqual(lane_host.lane_provenance(derived), fixture(PREDECESSOR))

    def test_lane_provenance_refuses_drift_or_an_unreviewed_anchor(self):
        for key, value in (("source_commit", "d" * 40), ("source_archive_sha256", "d" * 64),
                           ("version", PREDECESSOR), ("entry_id", "x")):
            ctx = main_ctx(); ctx["provenance"][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "lane_provenance_mismatch"):
                lane_host.lane_provenance(ctx)
        with self.assertRaisesRegex(ValueError, "lane_anchor_unreviewed"):
            lane_host.lane_provenance(main_ctx(anchor="2026.11.2"))

    def test_ci_images_use_the_anchor_s_child(self):
        with mock.patch.object(helper, "command"), mock.patch.object(helper, "cached_image"):
            images = helper.LANE_HOST.ci_images(main_ctx(), upstream=True)
        self.assertEqual(images[0], "ghcr.io/home-assistant/home-assistant:2026.11.3@" + fixture()["image_index_digest"])
        self.assertEqual(images[1:], helper.LANE_HOST.images(upstream=True, anchor=ANCHOR)[1:])


class Inputs(unittest.TestCase):
    """(d) Each harness's CI inputs read the anchor's record against the release archive."""

    def build(self, module, group=None, *, record=None, content=lambda path: path.encode()):
        """A synthetic archive of the profile's paths and the anchor record that hashes it."""
        directory = scratch(self)
        commit, paths = fixture()["source_commit"], profile(module, group)
        archive = directory / "core.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            for path in paths:
                data = content(path)
                info = tarfile.TarInfo("core-" + commit + "/" + path); info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        hashes = {path: hashlib.sha256(path.encode()).hexdigest() for path in paths}
        if module is created_object:
            value = {"version": ANCHOR, "commit": commit, "tree": fixture()["source_tree"],
                     "archive_sha256": fixture()["source_archive_sha256"], "hashes": hashes}
        else:
            value = {"core_version": ANCHOR, "source_commit": commit,
                     "source_archive_sha256": fixture()["source_archive_sha256"],
                     "files": [{"path": path, "sha256": hashes[path]} for path in paths]}
        value.update(record or {})
        w.write(directory / "record.json", value)
        return archive, directory / "record.json"

    def cases(self):
        yield from ((module, None) for module in LISTED)
        yield from ((created_object, group) for group in created_object.LANES)

    def source(self, module, group, archive, record, *, anchor=ANCHOR):
        args = (group,) if module is created_object else ()
        with mock.patch.dict(module.ANCHOR_SOURCE_HASHES, {ANCHOR: record}):
            return module.source_manifest(*args, archive, fixture(), release_commit=fixture()["source_commit"],
                                          anchor=anchor)

    def refused(self, module, group, archive, record, code, *, anchor=ANCHOR):
        with self.assertRaises(module.Refusal) as caught:
            self.source(module, group, archive, record, anchor=anchor)
        self.assertEqual(str(caught.exception), PREFIX[module] + "_" + code)

    def test_the_anchor_record_and_release_archive_pass(self):
        for module, group in self.cases():
            with self.subTest(module=module.__name__, group=group):
                archive, record = self.build(module, group)
                self.assertEqual(self.source(module, group, archive, record)["source_hashes_sha256"],
                                 hashlib.sha256(record.read_bytes()).hexdigest())

    def test_a_record_that_is_not_the_anchor_fixture_s_refuses(self):
        for module, group in self.cases():
            keys = (("version", "commit", "archive_sha256", "tree") if module is created_object
                    else ("core_version", "source_commit", "source_archive_sha256"))
            for key in keys:
                value = {"version": PREDECESSOR, "core_version": PREDECESSOR, "tree": "d" * 40}.get(
                    key, "d" * (64 if "sha256" in key else 40))
                with self.subTest(module=module.__name__, group=group, key=key):
                    archive, record = self.build(module, group, record={key: value})
                    self.refused(module, group, archive, record, "source_provenance_mismatch")

    def test_the_2026_11_0_record_under_the_2026_11_3_anchor_refuses(self):
        for module, group in self.cases():
            with self.subTest(module=module.__name__, group=group):
                archive, _ = self.build(module, group)
                self.refused(module, group, archive, module.SOURCE_HASHES, "source_provenance_mismatch")

    def test_a_release_archive_that_differs_from_the_record_refuses(self):
        for module, group in self.cases():
            with self.subTest(module=module.__name__, group=group):
                archive, record = self.build(module, group, content=lambda path: b"drift:" + path.encode())
                self.refused(module, group, archive, record, "source_hash_mismatch")

    def test_an_anchor_without_a_record_refuses(self):
        for module, group in self.cases():
            with self.subTest(module=module.__name__, group=group):
                archive, record = self.build(module, group)
                self.refused(module, group, archive, record, "manifest_missing", anchor="2026.11.2")
                self.refused(module, group, archive, record.with_name("absent.json"), "manifest_missing")

    def test_local_mode_still_reads_only_the_2026_11_0_record(self):
        for module, group in self.cases():
            args = (group,) if module is created_object else ()
            with self.subTest(module=module.__name__, group=group), \
                    mock.patch.object(module, "SOURCE_HASHES", ROOT / ".artifacts/absent.json"), \
                    self.assertRaises(module.Refusal) as caught:
                module.source_manifest(*args, "unused", fixture(PREDECESSOR))
            self.assertEqual(str(caught.exception), PREFIX[module] + "_manifest_missing")

    def test_ci_inputs_pass_the_anchor_and_its_fixture(self):
        archive, commit = Path("core.tar.gz"), fixture()["source_commit"]
        for module, group in self.cases():
            args = (group,) if module is created_object else ()
            with self.subTest(module=module.__name__, group=group), \
                    mock.patch.object(module.LANE_HOST, "ci_entry", return_value="entry"), \
                    mock.patch.object(module, "source_manifest", return_value="sources") as sources, \
                    mock.patch.object(device, "stub_assets", return_value="stubs"):
                module.ci_inputs(*args, main_ctx(), archive)
            sources.assert_called_once_with(*args, archive, fixture(), release_commit=commit, anchor=ANCHOR)

    def test_ci_inputs_refuse_a_context_whose_provenance_is_not_the_fixture(self):
        ctx = main_ctx(); ctx["provenance"]["source_archive_sha256"] = "d" * 64
        for module, group in self.cases():
            args = (group,) if module is created_object else ()
            with self.subTest(module=module.__name__, group=group), \
                    mock.patch.object(module.LANE_HOST, "ci_entry", return_value="entry"), \
                    mock.patch.object(module, "source_manifest", side_effect=Reached), \
                    mock.patch.object(device, "stub_assets", return_value="stubs"), \
                    self.assertRaisesRegex(ValueError, "lane_provenance_mismatch"):
                module.ci_inputs(*args, ctx, Path("core.tar.gz"))

    def test_device_and_helper_children_come_from_the_anchor(self):
        self.assertEqual(REVIEWED_CORE_BINDINGS[ANCHOR].child_consumer, REVIEWED_CORE_BINDINGS[PREDECESSOR].child_consumer)
        for module in (device, helper):
            with self.subTest(module=module.__name__):
                self.assertEqual(module.REVIEWED_ANCHORS, frozenset({PREDECESSOR, ANCHOR}))
                self.assertIn("REVIEWED_CORE_BINDINGS[anchor].child_consumer", Path(module.__file__).read_text())

    def test_a2_matter_identifier_is_reviewed_for_the_anchor(self):
        devices = [{"id": "dev-1", "identifiers": [["matter", "deviceid_" + "0" * 16 + "-" + "00000000000000A1"
                                                     + "-MatterNodeDevice"]]}]
        self.assertEqual(a2_acceptance.registry_node_devices(devices, anchor=ANCHOR),
                         a2_acceptance.registry_node_devices(devices, anchor=PREDECESSOR))
        self.assertEqual(a2_acceptance.node_identifier(ANCHOR).pattern, a2_acceptance.node_identifier(PREDECESSOR).pattern)
        for anchor in ("2026.11.2", "2026.11.4", None):
            with self.subTest(anchor=anchor), self.assertRaisesRegex(ValueError, "lane_anchor_unreviewed"):
                a2_acceptance.node_identifier(anchor)


class CommittedRecords(unittest.TestCase):
    """(g) The four new records are the anchor fixture's exact archive, on the manifest profile."""

    def test_records_are_bound_to_the_anchor_fixture(self):
        for module, (path, digest, differing) in RECORDS.items():
            with self.subTest(module=module.__name__):
                self.assertEqual(module.ANCHOR_SOURCE_HASHES, {ANCHOR: ROOT / path})
                self.assertEqual(hashlib.sha256((ROOT / path).read_bytes()).hexdigest(), digest)
                record = e.read_json(ROOT / path)
                self.assertEqual((record["core_version"], record["source_commit"], record["source_archive_sha256"]),
                                 (ANCHOR, fixture()["source_commit"], fixture()["source_archive_sha256"]))
                self.assertEqual([row["path"] for row in record["files"]], profile(module))
                old = {row["path"]: row["sha256"] for row in e.read_json(module.SOURCE_HASHES)["files"]}
                self.assertEqual({row["path"] for row in record["files"] if old[row["path"]] != row["sha256"]}, differing)

    def test_created_object_reads_the_o3_source_record(self):
        record = e.read_json(CORE_SOURCES / "2026.11.3.json")
        self.assertEqual(created_object.ANCHOR_SOURCE_HASHES, {ANCHOR: CORE_SOURCES / "2026.11.3.json"})
        self.assertEqual((record["version"], record["commit"], record["tree"], record["archive_sha256"]),
                         (ANCHOR, fixture()["source_commit"], fixture()["source_tree"], fixture()["source_archive_sha256"]))
        for group in created_object.LANES:
            self.assertLessEqual(set(profile(created_object, group)), set(record["hashes"]))

    def test_fixture_is_the_reviewed_binding_s(self):
        self.assertEqual(fixture()["image_index_digest"], REVIEWED_CORE_BINDINGS[ANCHOR].image_index_digest)
        self.assertEqual(fixture()["version"], ANCHOR)

    @unittest.skipUnless(REAL_ARCHIVE.is_file(), "the exact 2026.11.3 archive is local scratch only")
    def test_the_exact_archive_matches_every_record(self):
        self.assertEqual(hashlib.sha256(REAL_ARCHIVE.read_bytes()).hexdigest(), fixture()["source_archive_sha256"])
        commit = fixture()["source_commit"]
        for module in LISTED:
            with self.subTest(module=module.__name__):
                self.assertEqual(module.source_manifest(REAL_ARCHIVE, fixture(), release_commit=commit, anchor=ANCHOR)[
                    "profile_paths"], len(profile(module)))
        for group in created_object.LANES:
            with self.subTest(group=group):
                self.assertEqual(created_object.source_manifest(group, REAL_ARCHIVE, fixture(), release_commit=commit,
                                                                anchor=ANCHOR)["profile_paths"],
                                 len(profile(created_object, group)))


class Prepare(unittest.TestCase):
    """(e) prepare: a reviewed binding is its own main anchor; any disagreement refuses, never derived."""

    def run_prepare(self, version=ANCHOR, *, after=None, image=None):
        directory = scratch(self) / "prepared"
        before = e.read_json(CORE_SOURCES / "2026.11.0.json"); before["tag_commit"] = before["commit"]
        after = e.read_json(CORE_SOURCES / "2026.11.3.json") if after is None else after
        tag = {"tag_name": version, "published_at": "2026-11-20T00:00:00Z"}
        def api(endpoint, **_kwargs):
            if endpoint == "repos/home-assistant/core/releases/tags/" + version:
                return tag
            if "/git/ref/heads/" in endpoint:
                return None
            if "/workflows/" in endpoint:
                return {"workflow_runs": []}
            raise AssertionError(endpoint)
        if image is None:
            image = (fixture()["image_index_digest"], fixture()["architecture_manifests"], {"oci": "receipts"})
        provenance = mock.Mock(side_effect=image) if isinstance(image, BaseException) else mock.Mock(return_value=image)
        env = {**RUN, "GITHUB_REF": "refs/heads/main", "GITHUB_REPOSITORY": w.REPOSITORY,
               "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_OUTPUT": str(directory.parent / "outputs"),
               "GITHUB_STEP_SUMMARY": str(directory.parent / "summary")}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(w, "registry_input", return_value=(JOURNAL.read_bytes(), True)), \
                mock.patch.object(w, "github", side_effect=api), \
                mock.patch.object(w, "source", side_effect=[before, after]) as source, \
                mock.patch.object(w, "image_provenance", provenance):
            self.last = directory
            w.prepare(directory, False, version)
        self.assertEqual([c.args[0] for c in source.call_args_list], [PREDECESSOR, version])
        return directory, provenance

    def outputs(self, directory):
        return dict(line.split("=", 1) for line in (directory.parent / "outputs").read_text().splitlines())

    def test_2026_11_3_is_a_main_context_over_the_signed_2026_11_0(self):
        directory, provenance = self.run_prepare()
        provenance.assert_called_once_with(ANCHOR, fixture()["source_commit"])
        ctx = e.read_json(directory / "release.json")
        self.assertEqual((ctx["mode"], ctx["anchor"], ctx["predecessor"], ctx["fixture"], ctx["proof"]),
                         ("main", ANCHOR, PREDECESSOR, False, None))
        self.assertEqual(ctx["provenance"], fixture())
        self.assertEqual(ctx["binding"], asdict(REVIEWED_CORE_BINDINGS[ANCHOR]))
        self.assertEqual(e.read_json(directory / "baseline.json"), signed(PREDECESSOR))
        self.assertEqual(e.read_json(directory / "state.json"),
                         {"registry_exists": True, "acceptance": False, "withhold": []})
        self.assertEqual(e.read_json(directory / "oci.json"), {"oci": "receipts"})
        # O3 is disclosure only: the sources differ, and the reviewed binding still runs.
        self.assertFalse(e.read_json(directory / "comparison.json")["inherited"])
        out = self.outputs(directory)
        self.assertEqual((out["refused"], out["run"], out["release"], out["registry_exists"]), ("false", "true", ANCHOR, "true"))
        self.assertEqual([row["ha_version"] for row in json.loads(out["matrix"])["include"]], [ANCHOR])
        self.assertEqual(e.verify_context(ctx, RUN), ctx)

    def test_a_reviewed_fixture_that_disagrees_with_the_source_or_image_refuses(self):
        cases = [("commit", {"commit": "d" * 40, "tag_commit": "d" * 40}, None), ("tree", {"tree": "d" * 40}, None),
                 ("archive", {"archive_sha256": "d" * 64}, None)]
        index, platforms = fixture()["image_index_digest"], fixture()["architecture_manifests"]
        cases += [("index", {}, ("sha256:" + "d" * 64, platforms, {})),
                  ("platforms", {}, (index, {**platforms, "linux/arm64": "sha256:" + "d" * 64}, {})),
                  ("platform set", {}, (index, {"linux/amd64": platforms["linux/amd64"]}, {}))]
        for name, change, image in cases:
            after = {**e.read_json(CORE_SOURCES / "2026.11.3.json"), **change}
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, "reviewed_binding_mismatch"):
                    self.run_prepare(after=after, image=image)
                # Refused before any context, baseline, state, receipt or step output exists.
                for written in ("release.json", "baseline.json", "state.json", "oci.json"):
                    self.assertFalse((self.last / written).exists(), written)
                self.assertFalse((self.last.parent / "outputs").exists())

    def test_a_fixture_for_another_version_refuses(self):
        # The O3 record check already pins the fixture file to its version; this is prepare's own copy.
        real, name = w.read_json, core_lane_bindings.provenance_name(ANCHOR)
        def read(path):
            value = real(path)
            return {**value, "version": PREDECESSOR} if Path(path).name == name else value
        with mock.patch.object(w, "read_json", side_effect=read), \
                self.assertRaisesRegex(ValueError, "reviewed_binding_mismatch"):
            self.run_prepare()
        self.assertFalse((self.last / "release.json").exists())

    def test_an_unreviewed_version_still_takes_the_o3_refusal(self):
        after = {**e.read_json(CORE_SOURCES / "2026.11.3.json"), "version": "2026.11.99"}
        directory, provenance = self.run_prepare("2026.11.99", after=after, image=Reached("image_provenance"))
        provenance.assert_not_called()
        self.assertFalse((directory / "release.json").exists())
        out = self.outputs(directory)
        self.assertEqual((out["refused"], out["run"], out["release"]), ("true", "false", "2026.11.99"))


def assembly(test, value, state, baseline=PREDECESSOR, base=None):
    """D8's producer layout for one main context, every group passing."""
    base = scratch(test) if base is None else base
    prepared = base / "prepared"; receipts = base / "receipts"; prepared.mkdir(); receipts.mkdir()
    (prepared / "predecessor.json").write_bytes(JOURNAL.read_bytes())
    w.write(prepared / "baseline.json", signed(baseline))
    w.write(prepared / "state.json", state)
    context_path = base / "context.json"; context_path.write_bytes(e.canonical(value)); sha = e.digest(context_path.read_bytes())
    w.write(receipts / "cleanup.json", {"clean": True, "run": RUN, "context_sha256": sha})
    for name, ids in e.groups().items():
        receipt = {"model": "core-evidence-group-v1", "group": name, "passed": True, "run": RUN,
                   "release": value["provenance"]["version"], "context_sha256": sha, "profiles": ids}
        if name in e.LANE_GROUPS:
            folder = receipts / w.producer_name("release", name); folder.mkdir()
            w.write(folder / (name + ".json"), receipt)
            w.write(folder / ("cleanup-" + name + ".json"), {
                "clean": True, "run": RUN, "context_sha256": sha, "group": name,
                "owned": {"containers": ["hamcp-lane-" + name + "-core"], "networks": ["hamcp-lane-" + name]}})
        else:
            w.write(receipts / (name + ".json"), receipt)
    env = {**RUN, e.CONTEXT_ENV: str(context_path), "CORE_EVIDENCE_CONTEXT_SHA256": sha, "GITHUB_OUTPUT": str(base / "out")}
    with mock.patch.dict(os.environ, env, clear=True):
        w.assemble(prepared, receipts, "release")
    return receipts / "candidate"


class Assemble(unittest.TestCase):
    """(f) The new anchor's entry carries a reviewed name; a renewal keeps its own."""

    PRODUCTION = {"acceptance": False, "registry_exists": True, "withhold": []}

    def test_the_new_anchor_entry_is_ha_core_2026_11_3_reviewed_v1(self):
        candidate = assembly(self, main_ctx(), self.PRODUCTION)
        entry = e.read_json(candidate / "entry.json")
        self.assertEqual(entry["entry_id"], "ha-core-2026.11.3-reviewed-v1")
        self.assertNotEqual(entry["entry_id"], fixture()["entry_id"])
        self.assertEqual({k: entry[k] for k in fixture() if k != "entry_id"},
                         {k: v for k, v in fixture().items() if k != "entry_id"})
        self.assertEqual(entry["capabilities"], signed(PREDECESSOR)["capabilities"])
        # The unchanged add appends it; the signed 2026.11.0 entry is carried byte-identical.
        envelope = e.read_json(candidate / "core-candidate.json")["envelope"]
        target = next(x for x in envelope["entries"] if x["version"] == ANCHOR)
        self.assertEqual(target["entry_id"], "ha-core-2026.11.3-reviewed-v1")
        self.assertEqual(e.canonical(next(x for x in envelope["entries"] if x["version"] == PREDECESSOR)),
                         e.canonical(signed(PREDECESSOR)))

    def test_a_renewal_keeps_its_entry_id(self):
        candidate = assembly(self, renewal_ctx(), {**self.PRODUCTION, "acceptance": True})
        self.assertEqual(e.read_json(candidate / "entry.json")["entry_id"], signed(PREDECESSOR)["entry_id"])
        # Without acceptance a renewal keeps the pre-HAMCP-489 behaviour: the context's own entry_id,
        # which the unchanged add refuses as a changed release identity.
        base = scratch(self)
        with self.assertRaises(subprocess.CalledProcessError):
            assembly(self, renewal_ctx(), self.PRODUCTION, base=base)
        entry = e.read_json(base / "receipts/candidate/entry.json")
        self.assertEqual(entry["entry_id"], fixture(PREDECESSOR)["entry_id"])


class LocalModeUnchanged(unittest.TestCase):
    """Local mode keeps 2026.11.0: a CI marker still refuses, whatever anchor its context names."""

    def test_a_2026_11_3_context_in_local_mode_refuses_before_any_command(self):
        directory = scratch(self)
        context = directory / "context.json"; context.write_bytes(e.canonical(main_ctx()))
        argv = ["--mode", "local", "--pull", "never", "--journal", str(JOURNAL),
                "--source-archive", "archive", "--run-dir", str(directory / "run")]
        for module in (*LANES, a2):
            extra = ["--lane", "scene_create_delete"] if module is created_object else []
            with self.subTest(module=module.__name__), \
                    mock.patch.dict(os.environ, {"CORE_EVIDENCE_CONTEXT": str(context)}, clear=True), \
                    mock.patch.object(module, "command", side_effect=Reached), \
                    mock.patch.object(a1, "command", side_effect=Reached), \
                    mock.patch.object(a1 if module is a2 else module, "inputs", side_effect=Reached), \
                    self.assertRaises(module.Refusal) as caught:
                module._main(argv + extra)
            self.assertEqual(str(caught.exception), "local_only_closed_lane")
            self.assertFalse((directory / "run").exists())

    def test_the_local_journal_pin_still_refuses_another_journal(self):
        directory = scratch(self)
        journal = directory / "journal.json"; journal.write_bytes(JOURNAL.read_bytes() + b"\n")
        for module in LISTED:
            with self.subTest(module=module.__name__), \
                    mock.patch.object(module, "source_manifest", side_effect=Reached), \
                    self.assertRaises(module.Refusal) as caught:
                module.inputs(journal, journal)
            self.assertEqual(str(caught.exception), "input_hash_mismatch")

    def test_local_constants_are_2026_11_0(self):
        for module in LANES:
            with self.subTest(module=module.__name__):
                self.assertEqual(module.VERSION, PREDECESSOR)


if __name__ == "__main__":
    unittest.main()
