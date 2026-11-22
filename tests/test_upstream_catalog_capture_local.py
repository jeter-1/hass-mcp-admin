"""Offline tests of the S4 and S8 catalog capture lane (HAMCP-371).

Docker is replaced by a scripted stand-in; no container, network, registry or
Home Assistant is used.  Every value is synthetic.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import upstream_catalog_capture_local as lane  # noqa: E402
from support import frozen_capture_baseline as baseline  # noqa: E402

RUN_ID = "0" * 32
HELPER = {"image": lane.HELPER_IMAGE, "config_digest": "sha256:" + "a" * 64}
UPSTREAM = {"image": lane.UPSTREAM_REPOSITORY + "@sha256:" + "b" * 64,
            "config_digest": "sha256:" + "b" * 64}
HOLDER_ID = "c" * 64
PROBE = {"interfaces": ["lo"], "ipv4_routes": 0, "egress_errno": lane.ENETUNREACH}


def container(image, memory, mode, networks, **changes):
    data = {"Id": HOLDER_ID, "Image": image["config_digest"],
            "Config": {"Image": image["image"], "Labels": {lane.LANE_LABEL: RUN_ID}},
            "HostConfig": {"NetworkMode": mode, "Memory": lane.host.memory_bytes(memory),
                           "ReadonlyRootfs": True, "PortBindings": {}, "Privileged": False},
            "NetworkSettings": {"Networks": networks, "Ports": {}},
            "State": {"Running": True, "Status": "running"}, "Mounts": []}
    for path, value in changes.items():
        target = data
        *parents, leaf = path.split("__")
        for parent in parents:
            target = target[parent]
        target[leaf] = value
    return data


def holder(**changes):
    return container(HELPER, lane.HOLDER_MEMORY, "none", {"none": {}}, **changes)


def peer(**changes):
    return container(UPSTREAM, lane.PEER_MEMORY, "container:" + HOLDER_ID, {}, **changes)


class ArgvTests(unittest.TestCase):
    def test_holder_has_no_network_and_never_pulls(self) -> None:
        argv = lane.holder_argv("h", RUN_ID, ROOT / ".artifacts/x/out", "9.1.2")
        self.assertEqual(argv[argv.index("--network") + 1], "none")
        self.assertEqual(argv[argv.index("--pull") + 1], "never")
        self.assertIn("--read-only", argv)
        self.assertEqual(argv[argv.index("--memory") + 1], lane.HOLDER_MEMORY)
        for forbidden in ("--publish", "-p", "--publish-all", "-P", "--privileged", "host"):
            self.assertNotIn(forbidden, argv)
        mounts = [argv[i + 1] for i, item in enumerate(argv) if item == "--mount"]
        self.assertEqual(len(mounts), 3)
        self.assertEqual(sum(item.endswith(",readonly") for item in mounts), 2)
        # Only committed source directories and the run's own output are mounted.
        self.assertTrue(all(f"source={ROOT}/" in item for item in mounts))
        self.assertIn(f"{lane.LANE_LABEL}={RUN_ID}", argv)

    def test_peer_joins_the_holder_namespace_with_the_reviewed_environment(self) -> None:
        argv = lane.peer_argv("p", RUN_ID, "h", UPSTREAM["image"])
        self.assertEqual(argv[argv.index("--network") + 1], "container:h")
        self.assertEqual(argv[argv.index("--pull") + 1], "never")
        self.assertEqual(argv[-2:], [UPSTREAM["image"], "ha-mcp-web"])
        self.assertNotIn("--mount", argv)
        self.assertNotIn("--volume", argv)
        for forbidden in ("--publish", "-p", "--publish-all", "-P", "--privileged", "host"):
            self.assertNotIn(forbidden, argv)
        environment = [argv[i + 1] for i, item in enumerate(argv) if item == "--env"]
        self.assertEqual(environment, [
            "HOMEASSISTANT_URL=http://127.0.0.1:18123",
            "HOMEASSISTANT_TOKEN=synthetic-release-registry-token",
            "MCP_HOST=127.0.0.1", "MCP_PORT=18086", "MCP_SECRET_PATH=/mcp",
            "HA_MCP_DISABLE_SETTINGS_UI=true"])

    def test_a_run_without_an_explicit_memory_limit_is_refused(self) -> None:
        with self.assertRaises(lane.host.Refusal):
            lane.command("docker", "run", "--detach", "image")


class IsolationTests(unittest.TestCase):
    def assert_refused(self, reason, function, *args) -> None:
        with self.assertRaises(lane.Refusal) as caught:
            function(*args)
        self.assertEqual(str(caught.exception), reason)

    def test_verified_posture_is_recorded(self) -> None:
        posture = lane.verify_holder(holder(), PROBE, HELPER)
        self.assertEqual(posture["interfaces"], ["lo"])
        self.assertEqual(posture["network_mode"], "none")
        self.assertEqual(posture["docker_networks_created"], [])
        self.assertEqual(lane.verify_peer(peer(), HOLDER_ID, UPSTREAM)["docker_networks"], [])

    def test_holder_with_any_network_is_refused(self) -> None:
        self.assert_refused("holder_network_not_none", lane.verify_holder,
                            holder(HostConfig__NetworkMode="bridge"), PROBE, HELPER)
        self.assert_refused("holder_network_not_none", lane.verify_holder,
                            holder(NetworkSettings__Networks={"none": {}, "bridge": {}}),
                            PROBE, HELPER)
        self.assert_refused("holder_network_not_none", lane.verify_holder,
                            holder(HostConfig__NetworkMode="host",
                                   NetworkSettings__Networks={"host": {}}), PROBE, HELPER)

    def test_namespace_with_another_interface_or_a_route_is_refused(self) -> None:
        for probe in ({**PROBE, "interfaces": ["eth0", "lo"]}, {**PROBE, "ipv4_routes": 1},
                      {**PROBE, "interfaces": []}):
            self.assert_refused("namespace_not_loopback_only", lane.verify_holder,
                                holder(), probe, HELPER)
        for code in (0, None, 111, 110):
            self.assert_refused("namespace_egress_not_refused", lane.verify_holder,
                                holder(), {**PROBE, "egress_errno": code}, HELPER)

    def test_published_ports_and_privilege_are_refused(self) -> None:
        binding = [{"HostIp": "127.0.0.1", "HostPort": "18086"}]
        for changed in (holder(HostConfig__PortBindings={"18123/tcp": binding}),
                        holder(NetworkSettings__Ports={"18123/tcp": binding}),
                        holder(HostConfig__Privileged=True)):
            self.assert_refused("container_publishes_ports", lane.verify_holder,
                                changed, PROBE, HELPER)
        self.assert_refused("container_publishes_ports", lane.verify_peer,
                            peer(NetworkSettings__Ports={"18086/tcp": binding}),
                            HOLDER_ID, UPSTREAM)

    def test_peer_outside_the_holder_namespace_is_refused(self) -> None:
        for changed in (peer(HostConfig__NetworkMode="bridge"),
                        peer(HostConfig__NetworkMode="host"),
                        peer(HostConfig__NetworkMode="container:" + "d" * 64),
                        peer(NetworkSettings__Networks={"bridge": {}})):
            self.assert_refused("peer_network_not_holder_namespace", lane.verify_peer,
                                changed, HOLDER_ID, UPSTREAM)
        self.assert_refused("peer_has_bind_mount", lane.verify_peer,
                            peer(Mounts=[{"Type": "bind", "Destination": "/x"}]),
                            HOLDER_ID, UPSTREAM)

    def test_wrong_image_limits_or_state_are_refused(self) -> None:
        self.assert_refused("running_image_mismatch", lane.verify_peer,
                            peer(Image="sha256:" + "e" * 64), HOLDER_ID, UPSTREAM)
        self.assert_refused("running_image_mismatch", lane.verify_peer,
                            peer(Config__Image="ghcr.io/homeassistant-ai/ha-mcp:latest"),
                            HOLDER_ID, UPSTREAM)
        self.assert_refused("running_container_limits_mismatch", lane.verify_peer,
                            peer(HostConfig__Memory=0), HOLDER_ID, UPSTREAM)
        self.assert_refused("running_container_limits_mismatch", lane.verify_peer,
                            peer(HostConfig__ReadonlyRootfs=False), HOLDER_ID, UPSTREAM)
        self.assert_refused("container_not_running", lane.verify_peer,
                            peer(State__Running=False), HOLDER_ID, UPSTREAM)


class BindingTests(unittest.TestCase):
    RECORD = {
        "schema": "hamcp.upstream_image_provenance.v1", "version": "9.2.0",
        "image_repository": lane.UPSTREAM_REPOSITORY,
        "source_commit": lane.CANDIDATE_SOURCE_COMMIT, "slsa_provenance": "present_per_platform",
        "platform_digests": {"linux/amd64": "sha256:" + "1" * 64,
                             "linux/arm64": "sha256:" + "2" * 64},
        "image_index_digest": "sha256:" + "3" * 64, "workflow_sha": "4" * 40}

    def candidate(self, **changes):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "provenance.json"
            path.write_text(json.dumps({**self.RECORD, **changes}))
            return lane.binding("9.2.0", provenance=path)

    def test_baseline_comes_from_the_reviewed_registry_row(self) -> None:
        # Historical capture-lane coverage; shipped default refuses (HAMCP-437).
        bound = baseline.binding("9.1.2")
        row, = [item for item in json.loads(baseline.REGISTRY.read_text())["releases"]
                if item["version"] == "9.1.2"]
        self.assertEqual(bound["image"], lane.UPSTREAM_REPOSITORY + "@" + row["image_index_digest"])
        self.assertEqual(bound["labels"]["org.opencontainers.image.revision"],
                         row["image_revision"])
        self.assertEqual(bound["committed_capture_sha256"], row["capture_sha256"])

    def test_the_shipped_registry_refuses_the_9_1_2_baseline(self) -> None:
        # HAMCP-437: the shipped registry holds only 9.2.0, so the lane's default
        # binding of the historical baseline refuses fail-closed.
        self.assertIs(baseline.s4, lane)
        self.assertEqual([item["version"] for item in
                          json.loads(lane.REGISTRY.read_text())["releases"]], ["9.2.0"])
        with self.assertRaises(lane.Refusal) as caught:
            lane.binding("9.1.2")
        self.assertEqual(str(caught.exception), "baseline_registry_row_missing")

    def test_the_frozen_row_binds_the_committed_capture(self) -> None:
        row, = [item for item in json.loads(baseline.REGISTRY.read_text())["releases"]
                if item["version"] == "9.1.2"]
        capture = (ROOT / row["capture_resource"]).read_bytes()
        self.assertEqual(baseline.binding("9.1.2")["committed_capture_sha256"],
                         lane.sha256_bytes(capture))

    def test_candidate_comes_from_the_provenance_record(self) -> None:
        bound = self.candidate()
        self.assertEqual(bound["image"], lane.UPSTREAM_REPOSITORY + "@sha256:" + "3" * 64)
        self.assertEqual(bound["labels"], {
            "org.opencontainers.image.revision": "4" * 40,
            "org.opencontainers.image.version": "9.2.0",
            "org.opencontainers.image.source": lane.UPSTREAM_SOURCE})
        self.assertNotIn("committed_capture_sha256", bound)

    def test_a_record_for_another_source_or_without_provenance_is_refused(self) -> None:
        for changes in ({"source_commit": "5" * 40}, {"slsa_provenance": "absent"},
                        {"version": "9.2.1"}, {"image_repository": "ghcr.io/other/ha-mcp"},
                        {"schema": "other"}, {"platform_digests": {"linux/amd64": "sha256:x"}}):
            with self.subTest(changes=changes), self.assertRaises(lane.Refusal) as caught:
                self.candidate(**changes)
            self.assertEqual(str(caught.exception), "candidate_provenance_record_mismatch")
        with self.assertRaises(lane.Refusal) as caught:
            self.candidate(image_index_digest="sha256:short")
        self.assertEqual(str(caught.exception), "binding_malformed")

    def test_unbound_versions_and_a_missing_record_are_refused(self) -> None:
        for version in ("9.1.0", "9.2.1", "latest"):
            with self.assertRaises(lane.Refusal) as caught:
                lane.binding(version)
            self.assertEqual(str(caught.exception), "version_not_bound")
        with self.assertRaises(lane.Refusal) as caught:
            lane.binding("9.2.0", provenance=ROOT / "tests" / "absent-provenance.json")
        self.assertEqual(str(caught.exception), "candidate_provenance_record_missing")


def export(entries) -> io.BytesIO:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for name, payload in entries:
            info = tarfile.TarInfo(name)
            if isinstance(payload, bytes):
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
            else:
                info.type, info.linkname = tarfile.SYMTYPE, payload
                archive.addfile(info)
    stream.seek(0)
    return stream


class ResourceInventoryTests(unittest.TestCase):
    ENTRIES = (
        ("app/src/ha_mcp/resources/skills-vendor/skills/b/SKILL.md", b"synthetic b\n"),
        ("app/src/ha_mcp/resources/skills-vendor/skills/a/SKILL.md", b"synthetic a\n"),
        ("app/src/ha_mcp/resources/link", "skills-vendor"),
        ("app/src/ha_mcp/server.py", b"not a resource\n"),
        ("app/.venv/lib/python3.14/site-packages/synthetic_pkg-1.2.3.dist-info/METADATA", b"x"),
        ("etc/passwd", b"ignored\n"),
    )

    def test_resource_files_are_hashed_from_the_stream_only(self) -> None:
        with mock.patch.object(tarfile.TarFile, "extractall",
                               side_effect=AssertionError("nothing is extracted to disk")):
            inventory = lane.resource_inventory(export(self.ENTRIES))
        self.assertEqual([item["path"] for item in inventory["files"]], [
            "/app/src/ha_mcp/resources/skills-vendor/skills/a/SKILL.md",
            "/app/src/ha_mcp/resources/skills-vendor/skills/b/SKILL.md"])
        import hashlib
        self.assertEqual(inventory["files"][0]["sha256"],
                         hashlib.sha256(b"synthetic a\n").hexdigest())
        self.assertEqual(inventory["file_count"], 2)
        self.assertEqual(inventory["total_bytes"], 24)
        self.assertEqual(inventory["links"],
                         [{"path": "/app/src/ha_mcp/resources/link", "target": "skills-vendor"}])
        self.assertEqual(inventory["installed_distributions"], ["synthetic_pkg-1.2.3"])
        # The tree digest is independent of archive order.
        again = lane.resource_inventory(export(tuple(reversed(self.ENTRIES))))
        self.assertEqual(again["tree_sha256"], inventory["tree_sha256"])
        changed = lane.resource_inventory(export(
            (("app/src/ha_mcp/resources/skills-vendor/skills/a/SKILL.md", b"changed\n"),)))
        self.assertNotEqual(changed["tree_sha256"], inventory["tree_sha256"])

    def test_bounds_are_enforced(self) -> None:
        with mock.patch.object(lane, "MAX_RESOURCE_FILES", 1), \
                self.assertRaises(lane.Refusal) as caught:
            lane.resource_inventory(export(self.ENTRIES))
        self.assertEqual(str(caught.exception), "image_resources_exceed_bound")
        with mock.patch.object(lane, "MAX_RESOURCE_BYTES", 5), self.assertRaises(lane.Refusal):
            lane.resource_inventory(export(self.ENTRIES))


class FixtureStatsTests(unittest.TestCase):
    QUIET = {"rest_reads": {"/api/": 3}, "http_mutations": {}, "websocket_mutations": {},
             "operational_backup_creates": [], "approval_notification_calls": []}

    def test_the_fixture_shape_with_no_mutation_is_accepted(self) -> None:
        self.assertTrue(lane.recorded_nothing(self.QUIET))
        self.assertEqual(
            set(lane.MUTATION_COUNTERS),
            {"http_mutations", "websocket_mutations", "operational_backup_creates",
             "approval_notification_calls"})
        # The fixture source still reports each counter the lane reads.
        source = (ROOT / "scripts/fake_ha_read_gateway_contract_server.py").read_text()
        for key in lane.MUTATION_COUNTERS:
            self.assertIn(f'"{key}"', source)

    def test_any_recorded_mutation_or_missing_counter_is_refused(self) -> None:
        for key, value in (("http_mutations", {"POST /api/synthetic": 1}),
                           ("websocket_mutations", {"synthetic/write": 2}),
                           ("operational_backup_creates", [{"synthetic": True}]),
                           ("approval_notification_calls", 1),
                           ("approval_notification_calls", None),
                           ("approval_notification_calls", False),
                           ("http_mutations", "")):
            with self.subTest(key=key, value=value):
                self.assertFalse(lane.recorded_nothing({**self.QUIET, key: value}))
        for key in lane.MUTATION_COUNTERS:
            reduced = {name: value for name, value in self.QUIET.items() if name != key}
            self.assertFalse(lane.recorded_nothing(reduced))
        self.assertFalse(lane.recorded_nothing([]))


class Completed:
    def __init__(self, returncode=0, stdout=b"", stderr=b"") -> None:
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


class FakeHost:
    """Scripted git and Docker; records every argv and what containers exist."""

    def __init__(self, test, **options) -> None:
        self.test, self.options = test, options
        self.calls: list[list[str]] = []
        self.containers: dict[str, dict] = {}
        self.out: Path | None = None

    def image(self, reference):
        labels = dict(lane.HELPER_LABELS) if reference == lane.HELPER_IMAGE else dict(
            lane.binding("9.1.2")["labels"])
        if self.options.get("wrong_label") and reference != lane.HELPER_IMAGE:
            labels["org.opencontainers.image.revision"] = "f" * 40
        identity = "sha256:" + ("a" if reference == lane.HELPER_IMAGE else "b") * 64
        return {"Id": identity, "RepoDigests": [reference], "Size": 1, "Architecture": "amd64",
                "Config": {"Labels": labels, "Cmd": ["x"], "Env": ["PATH=/x"]}}

    def run(self, argv, **_kwargs):
        self.calls.append(list(argv))
        if argv[:2] == ["git", "status"]:
            return Completed(stdout=b" M x\n" if self.options.get("dirty") else b"")
        if argv[:2] == ["git", "rev-parse"]:
            return Completed(stdout=b"1" * 40 + b"\n")
        if argv[:3] == ["docker", "image", "inspect"]:
            if self.options.get("uncached") == argv[3]:
                return Completed(returncode=1)
            return Completed(stdout=json.dumps([self.image(argv[3])]).encode())
        if argv[:3] == ["docker", "container", "ls"]:
            return Completed(stdout="\n".join(self.containers).encode())
        if argv[:3] == ["docker", "container", "inspect"]:
            if argv[3] not in self.containers:
                return Completed(returncode=1)
            return Completed(stdout=json.dumps([self.containers[argv[3]]]).encode())
        if argv[:2] == ["docker", "create"]:
            name = argv[argv.index("--name") + 1]
            self.containers[name] = {"Config": {"Labels": self.labels(argv)},
                                     "State": {"Status": "created"}}
            return Completed()
        if argv[:2] == ["docker", "run"]:
            return self.start(argv)
        if argv[:2] == ["docker", "rm"]:
            self.containers.pop(argv[-1], None)
            return Completed()
        if argv[:2] == ["docker", "logs"]:
            return Completed(stdout=b"synthetic log\n")
        if argv[:2] == ["docker", "exec"]:
            return self.execute(argv)
        self.test.fail(f"unexpected command {argv[:4]}")

    @staticmethod
    def labels(argv):
        return dict(argv[i + 1].split("=", 1) for i, item in enumerate(argv) if item == "--label")

    def start(self, argv):
        name = argv[argv.index("--name") + 1]
        network = argv[argv.index("--network") + 1]
        is_holder = network == "none"
        image = HELPER if lane.HELPER_IMAGE in argv else {
            "image": lane.binding("9.1.2")["image"], "config_digest": "sha256:" + "b" * 64}
        if is_holder:
            mount = [argv[i + 1] for i, item in enumerate(argv) if item == "--mount"][-1]
            self.out = Path(mount.split("source=", 1)[1].split(",", 1)[0])
            mode, networks = self.options.get("holder_mode", "none"), {"none": {}}
        else:
            mode, networks = "container:" + HOLDER_ID, {}
        data = container(image, argv[argv.index("--memory") + 1], mode, networks)
        data["Config"]["Labels"] = self.labels(argv)
        self.containers[name] = data
        return Completed()

    def execute(self, argv):
        text = " ".join(argv)
        if lane.NET_PROBE in argv:
            return Completed(stdout=json.dumps(PROBE).encode())
        if lane.RUNTIME in argv:
            pins = dict(line.strip().split("==", 1)
                        for line in lane.REQUIREMENTS.read_text().splitlines() if "==" in line)
            return Completed(stdout=json.dumps(
                {"python": "3.12.0", "mcp": pins["mcp"], "aiohttp": pins["aiohttp"]}).encode())
        if lane.FETCH in argv:
            # The shape the fixture really reports: per-path maps and lists.
            stats = {"rest_reads": {"/api/": 1}, "http_mutations": {},
                     "websocket_mutations": {}, "operational_backup_creates": [],
                     "approval_notification_calls": []}
            if self.options.get("mutations"):
                stats["http_mutations"] = {"POST /api/synthetic": 1}
            return Completed(stdout=json.dumps(stats).encode())
        if "capture --endpoint" in text:
            index = int(argv[-1].rsplit("-", 1)[1].split(".")[0])
            body = {"capture_format_version": 1, "server_name": "ha-mcp",
                    "server_version": self.options.get("server_version", "9.1.2"),
                    "protocol_version": "2025-03-26", "tool_count": 2,
                    "catalog_fingerprint": "f" * 64, "tools": [], "error_shapes": {}}
            if index == 2 and self.options.get("unstable"):
                body["tool_count"] = 3
            (self.out / f"capture-{index}.json").write_text(json.dumps(body))
        elif "mcp_raw_observation.py --endpoint" in text:
            (self.out / "observation.json").write_text(json.dumps(
                {"tool_count": 2, "negotiated_protocol_version": "2025-03-26",
                 "requested_protocol_version": "2025-03-26"}))
        return Completed()


class FakeExport:
    def __init__(self, *_args, **_kwargs) -> None:
        self.stdout = export(ResourceInventoryTests.ENTRIES)

    def wait(self, timeout=None) -> int:
        return 0


class LaneTests(unittest.TestCase):
    def setUp(self) -> None:
        # Historical capture-lane coverage; shipped default refuses (HAMCP-437).
        patcher = baseline.patch_binding()
        patcher.start()
        self.addCleanup(patcher.stop)

    def launch(self, *argv, **options):
        fake = FakeHost(self, **options)
        scratch = tempfile.TemporaryDirectory(dir=ROOT / ".artifacts")
        self.addCleanup(scratch.cleanup)
        run_dir = Path(scratch.name) / "run"
        log = Path(scratch.name) / "launch-log.jsonl"
        with mock.patch.object(lane.subprocess, "run", fake.run), \
                mock.patch.object(lane.subprocess, "Popen", FakeExport), \
                mock.patch.object(lane, "host_figures", lambda: options.get("figures", {
                    "memory_available_gb": 12.0, "load_1m": 1.0, "root_free_gb": 50.0,
                    "root_total_gb": 140.0})), \
                mock.patch.dict(lane.os.environ, options.get("environ", {}), clear=False), \
                mock.patch("builtins.print"):
            for name in ("GITHUB_ACTIONS", "CI"):
                if name not in options.get("environ", {}):
                    lane.os.environ.pop(name, None)
            code = lane.main([*argv, "--run-dir", str(run_dir), "--launch-log", str(log),
                              "--note", "synthetic note"])
        entry = json.loads(log.read_text().splitlines()[-1])
        receipt = (json.loads((run_dir / "receipt.json").read_text())
                   if (run_dir / "receipt.json").is_file() else None)
        return code, entry, receipt, fake, run_dir

    def started(self, fake):
        return [call for call in fake.calls if call[:2] in (["docker", "run"], ["docker", "create"])]

    def test_run_passes_and_leaves_nothing_behind(self) -> None:
        code, entry, receipt, fake, run_dir = self.launch("run", "--version", "9.1.2")
        self.assertEqual((code, entry["result"], receipt["result"]), (0, "PASS", "PASS"))
        self.assertEqual(fake.containers, {})
        self.assertEqual(receipt["cleanup"], {"clean": True, "containers_absent": True,
                                              "docker_networks_created": 0})
        self.assertEqual(receipt["network_posture"]["interfaces"], ["lo"])
        self.assertEqual(receipt["redaction"]["setting"], "image defaults")
        self.assertEqual(receipt["notes"], ["synthetic note"])
        self.assertIn("no Home Assistant Core was started", receipt["home_assistant"])
        self.assertTrue(receipt["captures_byte_identical"])
        self.assertFalse(receipt["matches_committed_capture"])
        self.assertEqual(receipt["image_resources"]["file_count"], 2)
        self.assertTrue((run_dir / "image-resources.json").is_file())
        self.assertTrue((run_dir / "peer.log").is_file())
        # No network is created, nothing is pulled and no port is published.
        flat = [" ".join(call) for call in fake.calls]
        self.assertFalse([text for text in flat if text.startswith(
            ("docker network", "docker pull", "docker buildx", "docker login"))])
        for call in self.started(fake):
            self.assertEqual(call[call.index("--pull") + 1], "never")
            self.assertNotIn("--publish", call)
            self.assertIn(call[call.index("--network") + 1],
                          {"none", "container:" + lane.names("9.1.2")["holder"]})
        # The inspection container is created and exported, never started.
        self.assertNotIn(["docker", "start"], [call[:2] for call in fake.calls])

    def test_selftest_uses_only_the_helper_image(self) -> None:
        code, entry, receipt, fake, _ = self.launch("selftest")
        self.assertEqual((code, receipt["result"]), (0, "PASS"))
        self.assertNotIn("upstream_image", receipt)
        self.assertFalse([call for call in fake.calls
                          if any(lane.UPSTREAM_REPOSITORY in item for item in call)])
        self.assertEqual(fake.containers, {})

    def test_preflight_refusals_start_nothing_and_create_no_run_directory(self) -> None:
        cases = (
            ({"dirty": True}, "clean_committed_checkout_required"),
            ({"environ": {"GITHUB_ACTIONS": "true"}}, "local_only_lane"),
            ({"uncached": lane.HELPER_IMAGE}, "image_not_cached_by_digest"),
            ({"uncached": lane.binding("9.1.2")["image"]}, "image_not_cached_by_digest"),
            ({"wrong_label": True}, "cached_image_label_mismatch"),
            ({"figures": {"memory_available_gb": 3.0, "load_1m": 1.0, "root_free_gb": 50.0,
                          "root_total_gb": 140.0}}, "host_resource_floor_not_met"),
            ({"figures": {"memory_available_gb": 12.0, "load_1m": 14.0, "root_free_gb": 50.0,
                          "root_total_gb": 140.0}}, "host_resource_floor_not_met"),
        )
        for options, reason in cases:
            with self.subTest(reason=reason, options=sorted(options)):
                code, entry, receipt, fake, run_dir = self.launch(
                    "run", "--version", "9.1.2", **options)
                self.assertEqual((code, entry["result"], entry["failure"]), (2, "REFUSED", reason))
                self.assertFalse(entry["execution_started"])
                self.assertIsNone(receipt)
                self.assertFalse(run_dir.exists())
                self.assertEqual(self.started(fake), [])

    def test_unbound_version_and_reused_directory_are_preflight_refusals(self) -> None:
        code, entry, _, fake, _ = self.launch("run", "--version", "9.3.0")
        self.assertEqual((code, entry["failure"]), (2, "version_not_bound"))
        self.assertEqual(self.started(fake), [])
        with self.assertRaises(lane.Refusal) as caught:
            lane.new_run_directory(ROOT / "tests")
        self.assertEqual(str(caught.exception), "new_task_artifact_directory_required")
        with self.assertRaises(lane.Refusal) as caught:
            lane.new_run_directory(ROOT / ".artifacts")
        self.assertEqual(str(caught.exception), "new_task_artifact_directory_required")

    def test_failures_after_start_are_withheld_and_cleaned_up(self) -> None:
        cases = (
            ({"holder_mode": "bridge"}, "holder_network_not_none"),
            ({"unstable": True}, "capture_not_deterministic"),
            ({"mutations": 1}, "fixture_recorded_a_mutation_surface"),
            ({"server_version": "9.1.0"}, "peer_version_mismatch"),
        )
        for options, reason in cases:
            with self.subTest(reason=reason):
                code, entry, receipt, fake, _ = self.launch("run", "--version", "9.1.2", **options)
                self.assertEqual((code, entry["result"], receipt["result"]),
                                 (1, "WITHHELD", "WITHHELD"))
                self.assertEqual(receipt["failure"], reason)
                self.assertTrue(entry["execution_started"])
                self.assertTrue(receipt["cleanup"]["clean"])
                self.assertEqual(fake.containers, {})
        # The peer is never started when the namespace check fails.
        _, _, _, fake, _ = self.launch("run", "--version", "9.1.2", holder_mode="bridge")
        self.assertFalse([call for call in self.started(fake) if "ha-mcp-web" in call])

    def test_pull_is_by_bound_digest_anonymous_and_refused_for_the_baseline(self) -> None:
        code, entry, _, fake, _ = self.launch("pull", "--version", "9.1.2")
        self.assertEqual((code, entry["failure"]),
                         (2, "pull_requires_committed_provenance_record"))
        self.assertFalse([call for call in fake.calls if call[:2] == ["docker", "pull"]])


def fake_tools(version, filesystem):
    tools = [{"name": "ha_get_state"}, {"name": "ha_search"}]
    if filesystem:
        tools.append({"name": "ha_read_file"})
    if version == "9.1.2":
        tools.append({"name": "ha_synthetic_older"})
    return tools


def fake_capture(version, filesystem, extra=False):
    tools = fake_tools(version, filesystem) + ([{"name": "ha_extra"}] if extra else [])
    return json.dumps({"capture_format_version": 1, "server_name": "ha-mcp",
                       "server_version": version, "protocol_version": "2025-03-26",
                       "tool_count": len(tools), "catalog_fingerprint": "f" * 64,
                       "tools": tools, "error_shapes": {}}, sort_keys=True).encode()


def fake_observation(version, filesystem, extra=False):
    tools = fake_tools(version, filesystem) + ([{"name": "ha_extra"}] if extra else [])
    return json.dumps({"tool_count": len(tools), "negotiated_protocol_version": "2025-03-26",
                       "requested_protocol_version": "2025-03-26",
                       "tools_list_pages": [{"messages": [
                           {"jsonrpc": "2.0", "id": 2, "result": {"tools": tools}}]}]}).encode()


def write_evidence(base, files, prefix=""):
    """Synthetic committed evidence: the files and a ``SHA256SUMS`` that lists them."""
    lines = []
    for relative, raw in files.items():
        (base / relative).parent.mkdir(parents=True, exist_ok=True)
        (base / relative).write_bytes(raw)
        lines.append(f"{lane.hashlib.sha256(raw).hexdigest()}  {prefix}{relative}")
    (base / "SHA256SUMS").write_text("\n".join(lines) + "\n")


class ProfileHost(FakeHost):
    """The scripted host for ``profiles`` and ``pull --image app``."""

    STANDALONE = {lane.binding("9.2.0")["image"]: "9.2.0",
                  baseline.binding("9.1.2")["image"]: "9.1.2"}
    APP = lane.APP_REPOSITORY + "@" + lane.APP_MANIFEST_DIGEST

    def __init__(self, test, **options) -> None:
        super().__init__(test, **options)
        self.peers: list[list[str]] = []
        self.current = None
        self.environments: list[dict] = []

    def image(self, reference):
        if reference == lane.HELPER_IMAGE:
            return super().image(reference)
        if reference == self.APP:
            config = {"Labels": dict(lane.APP_LABELS), "Cmd": ["python3", "/start.py"],
                      "Env": ["PATH=/x"]}
            if "app_user" in self.options:
                config["User"] = self.options["app_user"]
            identity = self.options.get("app_id", lane.APP_MANIFEST_DIGEST)
            return {"Id": identity, "RepoDigests": [reference], "Size": 1,
                    "Architecture": "amd64", "Config": config}
        version = self.STANDALONE[reference]
        return {"Id": "sha256:" + lane.hashlib.sha256(reference.encode()).hexdigest(),
                "RepoDigests": [reference], "Size": 1, "Architecture": "amd64",
                "Config": {"Labels": dict(lane.binding(version)["labels"]), "User": "mcpuser",
                           "Cmd": ["ha-mcp"], "Env": ["PATH=/x"]}}

    def run(self, argv, **kwargs):
        if argv[:1] == [sys.executable] and argv[-1].endswith("provenance_gate.py"):
            self.calls.append(list(argv))
            verdict = b"FAIL" if self.options.get("gate_fails") else b"PASS"
            return Completed(returncode=int(bool(self.options.get("gate_fails"))),
                             stdout=b"synthetic\nQ7-b provenance gate " + verdict + b"\n")
        if argv[:2] == ["docker", "pull"]:
            self.calls.append(list(argv))
            self.environments.append(dict(kwargs.get("env") or {}))
            return Completed()
        if argv[:3] == ["docker", "image", "inspect"] and argv[3] in (
                self.options.get("uncached"),):
            self.calls.append(list(argv))
            return Completed(returncode=1)
        return super().run(argv, **kwargs)

    def start(self, argv):
        if argv[argv.index("--network") + 1] == "none":
            return super().start(argv)
        # One peer at a time: no other peer exists when one starts.
        self.test.assertFalse([name for name in self.containers if name.endswith("-peer")])
        reference = argv[-2]
        self.peers.append(list(argv))
        environment = dict(argv[i + 1].split("=", 1) for i, item in enumerate(argv)
                           if item == "--env")
        kind = "app" if reference == self.APP else "standalone"
        version = lane.APP_VERSION if kind == "app" else self.STANDALONE[reference]
        # As upstream does: the filesystem flag is a beta sub-flag, applied only
        # with ENABLE_BETA_FEATURES on (src/ha_mcp/config_registry.py).
        self.current = (kind, version,
                        environment.get("ENABLE_BETA_FEATURES") == "true"
                        and environment.get("HAMCP_ENABLE_FILESYSTEM_TOOLS") == "true")
        image = {"image": reference, "config_digest": self.image(reference)["Id"]}
        data = container(image, argv[argv.index("--memory") + 1], "container:" + HOLDER_ID, {})
        data["Config"]["Labels"] = self.labels(argv)
        self.containers[argv[argv.index("--name") + 1]] = data
        return Completed()

    def execute(self, argv):
        text = " ".join(argv)
        if "capture --endpoint" in text or "mcp_raw_observation.py --endpoint" in text:
            kind, version, filesystem = self.current
            extra = ((kind, filesystem) == ("app", True) and self.options.get("app_differs")
                     or version == "9.1.2" and self.options.get("control_differs") == filesystem)
            target = self.out / argv[-1].removeprefix("/out/")
            if "capture --endpoint" in text:
                index = int(target.name.split("-")[1].split(".")[0])
                unstable = index == 2 and self.options.get("unstable") == (kind, filesystem)
                target.write_bytes(fake_capture(version, filesystem, extra or unstable))
            else:
                target.write_bytes(fake_observation(version, filesystem, extra))
            return Completed()
        return super().execute(argv)


class ProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        # Historical capture-lane coverage; shipped default refuses (HAMCP-437).
        patcher = baseline.patch_binding()
        patcher.start()
        self.addCleanup(patcher.stop)

    def launch(self, *argv, references=None, **options):
        fake = ProfileHost(self, **options)
        scratch = tempfile.TemporaryDirectory(dir=ROOT / ".artifacts")
        self.addCleanup(scratch.cleanup)
        base = Path(scratch.name)
        s4, hamcp351 = base / "s4", base / "hamcp351"
        write_evidence(s4, {"ha-mcp-9.2.0/capture.json": fake_capture("9.2.0", False),
                            "ha-mcp-9.2.0/observation.json": fake_observation("9.2.0", False)})
        write_evidence(hamcp351, {
            f"catalogs/standalone-fs-{state}-tools-response.json": json.dumps(
                {"jsonrpc": "2.0", "id": 1, "result": {"tools": fake_tools("9.1.2", on)}},
                indent=1).encode()
            for state, on in (("off", False), ("on", True))}, prefix="./")
        if options.get("tampered_reference"):
            (s4 / "ha-mcp-9.2.0/observation.json").write_bytes(b"{}")
        table = references or {
            "standalone-9.2.0-fs-off-observation": (s4, "ha-mcp-9.2.0/observation.json"),
            "hamcp351-9.1.2-fs-off-tools": (hamcp351, "catalogs/standalone-fs-off-tools-response.json"),
            "hamcp351-9.1.2-fs-on-tools": (hamcp351, "catalogs/standalone-fs-on-tools-response.json")}
        run_dir, log = base / "run", base / "launch-log.jsonl"
        with mock.patch.object(lane.subprocess, "run", fake.run), \
                mock.patch.object(lane.subprocess, "Popen", FakeExport), \
                mock.patch.object(lane, "REFERENCES", table), \
                mock.patch.object(lane, "host_figures", lambda: {
                    "memory_available_gb": 12.0, "load_1m": 1.0, "root_free_gb": 50.0,
                    "root_total_gb": 140.0}), \
                mock.patch("builtins.print"):
            for name in ("GITHUB_ACTIONS", "CI"):
                lane.os.environ.pop(name, None)
            code = lane.main([*argv, "--run-dir", str(run_dir), "--launch-log", str(log)])
        entry = json.loads(log.read_text().splitlines()[-1])
        receipt = (json.loads((run_dir / "receipt.json").read_text())
                   if (run_dir / "receipt.json").is_file() else None)
        return code, entry, receipt, fake, run_dir

    def test_profiles_pass_one_peer_at_a_time_with_the_hamcp351_definitions(self) -> None:
        code, entry, receipt, fake, run_dir = self.launch("profiles")
        self.assertEqual((code, entry["result"], receipt["result"]), (0, "PASS", "PASS"))
        self.assertEqual(fake.containers, {})
        self.assertTrue(receipt["cleanup"]["clean"])
        self.assertEqual(sorted(receipt["profiles"]), sorted(item[0] for item in lane.PROFILES))
        self.assertTrue(all(receipt["comparisons"].values()))
        self.assertEqual(receipt["tool_differences"],
                         {"standalone_9.2.0_fs_on_only": ["ha_read_file"],
                          "standalone_9.2.0_fs_off_only": []})
        self.assertEqual(len(fake.peers), 4)
        for argv, (profile, kind, version, filesystem) in zip(fake.peers, lane.PROFILES):
            with self.subTest(profile=profile):
                environment = [argv[i + 1] for i, item in enumerate(argv) if item == "--env"]
                expected = [f"{key}={value}" for key, value in lane.PEER_ENVIRONMENT]
                if filesystem:
                    expected += ["ENABLE_BETA_FEATURES=true", "HAMCP_ENABLE_FILESYSTEM_TOOLS=true"]
                self.assertEqual(environment, expected)
                self.assertEqual(argv[-1], "ha-mcp-web")
                tmpfs = [argv[i + 1] for i, item in enumerate(argv) if item == "--tmpfs"]
                self.assertEqual(tmpfs, ["/tmp", "/root/.ha-mcp" if kind == "app"
                                         else "/home/mcpuser/.ha-mcp"])
                if kind == "app":
                    self.assertEqual(argv[-2], ProfileHost.APP)
                self.assertEqual(argv[argv.index("--pull") + 1], "never")
                self.assertTrue((run_dir / "out" / profile / "capture-2.json").is_file())
        # Nothing is pulled; the App image is named by manifest digest, never by tag.
        flat = [" ".join(call) for call in fake.calls]
        self.assertFalse([text for text in flat if text.startswith(("docker pull", "docker network"))])
        self.assertFalse([text for text in flat if "ha-mcp-addon-amd64:" in text])
        self.assertIn([sys.executable, "-I", "-B", str(lane.APP_READS / "provenance_gate.py")],
                      fake.calls)
        self.assertEqual(receipt["bindings"]["app-9.2.0"]["manifest_digest"],
                         lane.APP_MANIFEST_DIGEST)
        # Only the filesystem-off 9.1.2 catalogue is the registry's committed capture.
        self.assertNotIn("matches_committed_capture",
                         receipt["profiles"]["control-standalone-9.1.2-fs-on"])
        self.assertIn("matches_committed_capture",
                      receipt["profiles"]["control-standalone-9.1.2-fs-off"])
        # The controls are judged on the review script's fingerprints and the full result.
        self.assertEqual(sorted(receipt["controls"]), sorted(lane.CONTROLS))
        for profile, control in receipt["controls"].items():
            with self.subTest(control=profile):
                self.assertEqual(control["comparison_used"], "review_fingerprints_and_result")
                self.assertEqual(control["expected"], control["found"])
                self.assertTrue(control["result_equal"] and control["fingerprints_equal"])
        self.assertEqual(
            [receipt["controls"][name]["found"]["tool_count"] for name in lane.CONTROLS], [3, 4])

    def test_every_filesystem_on_profile_passes_the_beta_and_filesystem_flags(self) -> None:
        """Ruling Q9: the filesystem flag is a beta sub-flag; both reach every on-peer."""
        self.assertEqual({filesystem for *_, filesystem in lane.PROFILES}, {True, False})
        code, _, receipt, fake, _ = self.launch("profiles")
        self.assertEqual(code, 0)
        for argv, (profile, *_, filesystem) in zip(fake.peers, lane.PROFILES):
            with self.subTest(profile=profile):
                environment = dict(argv[i + 1].split("=", 1) for i, item in enumerate(argv)
                                   if item == "--env")
                recorded = dict(next(item["peer_environment"] for item in
                                     receipt["profile_definitions"] if item["profile"] == profile))
                for passed in (environment, recorded):
                    if filesystem:
                        self.assertEqual(passed.get("ENABLE_BETA_FEATURES"), "true")
                        self.assertEqual(passed.get("HAMCP_ENABLE_FILESYSTEM_TOOLS"), "true")
                    else:
                        self.assertNotIn("ENABLE_BETA_FEATURES", passed)
                        self.assertNotIn("HAMCP_ENABLE_FILESYSTEM_TOOLS", passed)

    def test_a_filesystem_flag_without_beta_fails_the_control(self) -> None:
        """The profiles-1 defect: without the beta flag the control is withheld."""
        def without_beta(filesystem):
            return lane.PEER_ENVIRONMENT + ((lane.FILESYSTEM_FLAG,) if filesystem else ())
        with mock.patch.object(lane, "profile_environment", without_beta):
            code, entry, receipt, fake, _ = self.launch("profiles")
        self.assertEqual((code, entry["result"], receipt["failure"]),
                         (1, "WITHHELD", "control_differs_from_hamcp351"))
        self.assertFalse(receipt["comparisons"]["control-standalone-9.1.2-fs-on_reproduces_hamcp351"])
        self.assertTrue(receipt["comparisons"]["control-standalone-9.1.2-fs-off_reproduces_hamcp351"])
        self.assertEqual(fake.containers, {})

    def test_catalog_differences_and_unstable_captures_are_withheld(self) -> None:
        cases = (({"app_differs": True}, "app_catalog_differs_from_standalone"),
                 ({"control_differs": True}, "control_differs_from_hamcp351"),
                 ({"control_differs": False}, "control_differs_from_hamcp351"),
                 ({"unstable": ("app", True)}, "capture_not_deterministic"),
                 ({"unstable": ("standalone", False)}, "capture_not_deterministic"))
        for options, reason in cases:
            with self.subTest(reason=reason, options=sorted(options)):
                code, entry, receipt, fake, _ = self.launch("profiles", **options)
                self.assertEqual((code, entry["result"], receipt["result"], receipt["failure"]),
                                 (1, "WITHHELD", "WITHHELD", reason))
                self.assertTrue(receipt["cleanup"]["clean"])
                self.assertEqual(fake.containers, {})
                if reason != "capture_not_deterministic":
                    self.assertFalse(all(receipt["comparisons"].values()))

    def test_profile_preflight_refusals_start_nothing(self) -> None:
        cases = (({"gate_fails": True}, "app_provenance_gate_failed"),
                 ({"app_user": "mcpuser"}, "image_user_unexpected"),
                 ({"app_id": "sha256:" + "9" * 64}, "cached_image_digest_mismatch"),
                 ({"uncached": ProfileHost.APP}, "image_not_cached_by_digest"),
                 ({"tampered_reference": True}, "committed_evidence_mismatch"),
                 ({"dirty": True}, "clean_committed_checkout_required"))
        for options, reason in cases:
            with self.subTest(reason=reason):
                code, entry, receipt, fake, run_dir = self.launch("profiles", **options)
                self.assertEqual((code, entry["result"], entry["failure"]), (2, "REFUSED", reason))
                self.assertIsNone(receipt)
                self.assertFalse(run_dir.exists())
                self.assertFalse([call for call in fake.calls
                                  if call[:2] in (["docker", "run"], ["docker", "create"])])

    def test_app_pull_is_by_manifest_digest_and_anonymous(self) -> None:
        code, entry, receipt, fake, run_dir = self.launch(
            "pull", "--version", "9.2.0", "--image", "app")
        self.assertEqual((code, entry["result"], receipt["result"]), (0, "PASS", "PASS"))
        pulls = [call for call in fake.calls if call[:2] == ["docker", "pull"]]
        self.assertEqual(pulls, [["docker", "pull", "--quiet", "--platform", "linux/amd64",
                                  ProfileHost.APP]])
        config = Path(fake.environments[0]["DOCKER_CONFIG"])
        self.assertEqual((config, list(config.iterdir())), (run_dir / "docker-config-empty", []))
        self.assertEqual(receipt["image_kind"], "app")
        self.assertEqual(receipt["upstream_image"]["config_digest"], lane.APP_MANIFEST_DIGEST)
        for argv, reason in ((("--version", "9.1.2"), "version_not_bound"),
                             (("--version", "9.2.0"), "app_provenance_gate_failed")):
            with self.subTest(reason=reason):
                code, entry, _, fake, _ = self.launch(
                    "pull", *argv, "--image", "app", gate_fails=reason != "version_not_bound")
                self.assertEqual((code, entry["failure"]), (2, reason))
                self.assertFalse([call for call in fake.calls if call[:2] == ["docker", "pull"]])


class AppBindingTests(unittest.TestCase):
    def copy(self):
        scratch = tempfile.TemporaryDirectory(dir=ROOT / ".artifacts")
        self.addCleanup(scratch.cleanup)
        target = Path(scratch.name) / "app-reads"
        lane.shutil.copytree(lane.APP_READS, target)
        return target

    def refused(self, reads):
        with self.assertRaises(lane.Refusal) as caught:
            lane.app_binding(reads)
        return str(caught.exception)

    def test_the_committed_reads_bind_the_manifest_digest(self) -> None:
        bound = lane.app_binding()
        self.assertEqual(bound["image"], lane.APP_REPOSITORY + "@" + lane.APP_MANIFEST_DIGEST)
        self.assertEqual(bound["provenance_gate"], "PASS")
        self.assertEqual(bound["labels"], lane.APP_LABELS)

    def test_changed_reads_or_a_failing_gate_are_refused(self) -> None:
        reads = self.copy()
        (reads / "raw/manifest-amd64.raw.json").write_bytes(b"{}")
        self.assertEqual(self.refused(reads), "committed_evidence_mismatch")
        reads = self.copy()
        write_evidence(reads / "raw", {"manifest-amd64.raw.json": b"{}"})
        # The rewritten sum is listed last, so it is the one that applies.
        (reads / "SHA256SUMS").write_bytes((lane.APP_READS / "SHA256SUMS").read_bytes()
                                           + (reads / "raw/SHA256SUMS").read_bytes())
        self.assertEqual(self.refused(reads), "app_reads_digest_mismatch")
        for script in ('print("Q7-b provenance gate FAIL")\nraise SystemExit(1)\n',
                       'print("Q7-b provenance gate PASS")\nraise SystemExit(1)\n',
                       'print("nothing")\n'):
            with self.subTest(script=script):
                reads = self.copy()
                (reads / "provenance_gate.py").write_text(script)
                self.assertEqual(self.refused(reads), "app_provenance_gate_failed")

    def test_profile_argv_and_observation_shape(self) -> None:
        self.assertEqual(lane.profile_environment(False), lane.PEER_ENVIRONMENT)
        argv = lane.peer_argv("p", RUN_ID, "h", ProfileHost.APP,
                              environment=lane.profile_environment(True),
                              data_directory=lane.DATA_DIRECTORIES[""])
        self.assertIn("HAMCP_ENABLE_FILESYSTEM_TOOLS=true", argv)
        self.assertIn("ENABLE_BETA_FEATURES=true", argv)
        self.assertEqual([argv[i + 1] for i, item in enumerate(argv) if item == "--tmpfs"],
                         ["/tmp", "/root/.ha-mcp"])
        good = json.loads(fake_observation("9.2.0", False))
        self.assertEqual(lane.tool_names(lane.tools_result(good)), ["ha_get_state", "ha_search"])
        for bad in ({}, {"tools_list_pages": []},
                    {"tools_list_pages": [good["tools_list_pages"][0]] * 2},
                    {"tools_list_pages": [{"messages": []}]},
                    {"tools_list_pages": [{"messages": [{"error": {}}]}]}):
            with self.subTest(bad=bad), self.assertRaises(lane.Refusal) as caught:
                lane.tools_result(bad)
            self.assertEqual(str(caught.exception), "observation_shape_unexpected")


class SourceTests(unittest.TestCase):
    def test_the_lane_is_standard_library_only_and_never_names_a_host_network(self) -> None:
        source = Path(lane.__file__).read_text(encoding="utf-8")
        self.assertNotIn('"--network", "host"', source)
        self.assertNotIn("network create", source)
        self.assertEqual(subprocess.run(
            [sys.executable, "-I", "-B", lane.__file__, "--help"],
            capture_output=True, timeout=60).returncode, 0)


if __name__ == "__main__":
    unittest.main()
