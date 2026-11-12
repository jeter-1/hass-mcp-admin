"""Offline tests of the S4 catalog capture lane (HAMCP-371).

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
        bound = lane.binding("9.1.2")
        row, = [item for item in json.loads(lane.REGISTRY.read_text())["releases"]
                if item["version"] == "9.1.2"]
        self.assertEqual(bound["image"], lane.UPSTREAM_REPOSITORY + "@" + row["image_index_digest"])
        self.assertEqual(bound["labels"]["org.opencontainers.image.revision"],
                         row["image_revision"])
        self.assertEqual(bound["committed_capture_sha256"], row["capture_sha256"])

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
