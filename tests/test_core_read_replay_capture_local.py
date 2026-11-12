"""Offline tests of the disposable-Core read capture lane (HAMCP-371).

No container, network, registry, image or Home Assistant is used.  Every
value is synthetic.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import core_read_replay_capture_local as lane  # noqa: E402


def export_stream(members):
    """A synthetic ``docker export`` stream: path -> bytes, or None for a directory."""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as archive:
        for path, body in members.items():
            info = tarfile.TarInfo(path)
            if body is None:
                info.type = tarfile.DIRTYPE
                archive.addfile(info)
            else:
                info.size = len(body)
                archive.addfile(info, io.BytesIO(body))
    raw.seek(0)
    return raw


SITE = "usr/local/lib/python3.14/site-packages/"


class RequirementTests(unittest.TestCase):
    def test_names_compare_normalized(self):
        self.assertEqual(lane.normalized("ruamel.yaml"), "ruamel-yaml")
        self.assertEqual(lane.normalized("Voluptuous_OpenAPI"), "voluptuous-openapi")

    def test_minimum_and_exact_requirements(self):
        self.assertTrue(lane.satisfied("mcp>=1.24.0", "2.3.0"))
        self.assertTrue(lane.satisfied("mcp>=1.24.0", "1.24"))
        self.assertFalse(lane.satisfied("mcp>=1.24.0", "1.23.9"))
        self.assertFalse(lane.satisfied("mcp>=1.24.0", None))
        self.assertTrue(lane.satisfied("ha-mcp==9.2.0", "9.2.0"))
        self.assertFalse(lane.satisfied("ha-mcp==9.2.0", "9.2.1"))

    def test_an_unreviewed_requirement_form_is_refused(self):
        for text in ("mcp", "mcp<2", "mcp>=1.24.0,<2", "mcp @ https://synthetic.invalid/x"):
            with self.subTest(text=text), self.assertRaises(lane.Refusal) as raised:
                lane.parse_requirement(text)
            self.assertEqual(str(raised.exception), "component_requirement_form_not_reviewed")

    def test_a_core_without_two_requirements_makes_profile_b_infeasible(self):
        report = lane.requirement_report("9.2.0", {"mcp": "2.3.0"})
        self.assertEqual(report["missing"],
                         ["ruamel.yaml>=0.18.0", "voluptuous-openapi>=0.4.1"])
        self.assertIs(report["profile_b_feasible_without_a_route"], False)
        sources = {row["requirement"]: row["source"] for row in report["requirements"]}
        self.assertEqual(sources["ha-mcp==9.2.0"], "pinned ha-mcp image")
        self.assertEqual(sources["mcp>=1.24.0"], "core image")
        with self.assertRaises(lane.Refusal) as raised:
            lane.require_profile_b_feasible(report)
        self.assertEqual(str(raised.exception), "profile_b_core_requirement_missing")

    def test_a_core_with_every_other_requirement_is_feasible(self):
        report = lane.requirement_report(
            "9.1.2", {"mcp": "2.3.0", "ruamel-yaml": "0.19.1", "voluptuous-openapi": "0.4.1"})
        self.assertEqual(report["missing"], [])
        lane.require_profile_b_feasible(report)
        self.assertEqual(report["requirements"][-1]["requirement"], "ha-mcp==9.1.2")

    def test_the_package_pin_is_never_taken_from_core(self):
        # A Core that happened to carry the package does not change its source.
        report = lane.requirement_report("9.2.0", {"ha-mcp": "9.2.0"})
        self.assertEqual(report["requirements"][-1]["source"], "pinned ha-mcp image")


class ExportScanTests(unittest.TestCase):
    def test_names_versions_and_the_interpreter_are_read(self):
        found = lane.scan_export(export_stream({
            SITE: None,
            SITE + "mcp-2.3.0.dist-info/METADATA": b"synthetic",
            SITE + "ruamel_yaml-0.19.1.dist-info/METADATA": b"synthetic",
            SITE + "ruamel_yaml-0.19.1.dist-info/RECORD": b"synthetic",
            SITE + "not_a_distribution/METADATA": b"synthetic",
            "app/src/ha_mcp/__init__.py": b"12345",
            "app/src/ha_mcp/tools/x.py": b"123",
            "app/src/other/x.py": b"1",
        }))
        self.assertEqual(found["python"], ["3.14"])
        self.assertEqual(found["distributions"], {"mcp": "2.3.0", "ruamel-yaml": "0.19.1"})
        self.assertEqual(found["package_source"],
                         {"path": "/app/src/ha_mcp/", "files": 2, "bytes": 8})
        self.assertEqual(found["component_manifests_in_image"], [])

    def test_a_component_tree_inside_an_image_is_reported(self):
        found = lane.scan_export(export_stream({
            "config/custom_components/ha_mcp_tools/manifest.json": b"{}"}))
        self.assertEqual(found["component_manifests_in_image"],
                         ["/config/custom_components/ha_mcp_tools/manifest.json"])

    def test_a_hard_link_member_counts_and_is_reported_as_one(self):
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode="w") as archive:
            first = tarfile.TarInfo("opt/synthetic/METADATA")
            first.size = 9
            archive.addfile(first, io.BytesIO(b"synthetic"))
            link = tarfile.TarInfo(SITE + "fastmcp-4.1.6.dist-info/METADATA")
            link.type, link.linkname = tarfile.LNKTYPE, "opt/synthetic/METADATA"
            archive.addfile(link)
        raw.seek(0)
        found = lane.scan_export(raw)
        self.assertEqual(found["distributions"], {"fastmcp": "4.1.6"})
        self.assertEqual(found["metadata_members"], {"regular": 0, "hard_link": 1})
        self.assertEqual(found["dist_info_without_metadata_file"], [])

    def test_a_dist_info_directory_without_a_metadata_file_is_listed_not_counted(self):
        found = lane.scan_export(export_stream({
            SITE + "ruamel_yaml-0.19.1.dist-info/": None,
            SITE + "ruamel_yaml-0.19.1.dist-info/RECORD": b"synthetic",
            SITE + "mcp-2.3.0.dist-info/METADATA": b"synthetic"}))
        self.assertEqual(found["distributions"], {"mcp": "2.3.0"})
        self.assertEqual(found["dist_info_without_metadata_file"], ["ruamel-yaml"])
        with self.assertRaises(lane.Refusal) as raised:
            lane.require_scan_complete({"core": found})
        self.assertEqual(str(raised.exception), "distribution_scan_incomplete")

    def test_an_uncounted_distribution_nobody_asked_about_does_not_refuse(self):
        found = lane.scan_export(export_stream({
            SITE + "synthetic_other-1.0.dist-info/RECORD": b"synthetic"}))
        self.assertEqual(found["dist_info_without_metadata_file"], ["synthetic-other"])
        lane.require_scan_complete({"core": found})

    def test_the_package_requirements_are_read_as_rows_and_compared_by_name(self):
        metadata = (b"Metadata-Version: 2.4\nName: ha-mcp\n"
                    b"Requires-Dist: synthetic-one>=1.0\n"
                    b"Requires-Dist: Synthetic_Two[fast] (>=2,<3) ; python_version >= '3.13'\n"
                    b"Requires-Dist: synthetic-three ; extra == 'dev'\n"
                    b"Requires-Dist: synthetic-four @ https://synthetic.invalid/x\n"
                    b"\nRequires-Dist: in-the-body\n")
        found = lane.scan_export(export_stream({
            SITE + "ha_mcp-9.2.0.dist-info/METADATA": metadata,
            SITE + "other-1.0.dist-info/METADATA": b"Requires-Dist: not-read\n"}))
        rows = found["package_requires"]
        self.assertEqual([row["name"] for row in rows], [
            "synthetic-one", "synthetic-two", "synthetic-three", "synthetic-four"])
        self.assertEqual(rows[0], {"name": "synthetic-one", "specifier": ">=1.0",
                                   "marker": None})
        self.assertEqual(rows[1]["specifier"], "(>=2,<3)")
        self.assertEqual(rows[1]["marker"], "python_version >= '3.13'")
        self.assertEqual(rows[3]["specifier"], "unreadable")
        report = lane.package_requirement_report(rows, {"synthetic-one": "1.2"})
        self.assertEqual(report["absent_from_core_image"],
                         ["synthetic-four", "synthetic-two"])
        self.assertIs(report["rows"][2]["extra_only"], True)
        self.assertIs(report["specifiers_evaluated"], False)

    def test_an_image_without_the_package_metadata_reports_none(self):
        found = lane.scan_export(export_stream({SITE + "mcp-2.3.0.dist-info/METADATA": b"x"}))
        self.assertIsNone(found["package_requires"])

    def test_nothing_is_extracted(self):
        # A member that points outside the stream is read as a name, never written.
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode="w") as archive:
            link = tarfile.TarInfo(SITE + "evil-1.0.dist-info/METADATA")
            link.type, link.linkname = tarfile.SYMTYPE, "/etc/synthetic"
            archive.addfile(link)
        raw.seek(0)
        found = lane.scan_export(raw)
        self.assertEqual(found["distributions"], {})
        self.assertEqual(found["dist_info_without_metadata_file"], ["evil"])


class BindingTests(unittest.TestCase):
    def test_the_core_image_is_the_reviewed_digest(self):
        bound = lane.core_binding()
        self.assertEqual(bound["version"], "2026.11.0")
        self.assertRegex(bound["image"],
                         r"^ghcr\.io/home-assistant/home-assistant@sha256:[0-9a-f]{64}$")

    def test_component_requirements_pin_the_version_under_capture(self):
        for version in lane.VERSIONS:
            self.assertEqual(lane.COMPONENT_REQUIREMENTS[version][-1], "ha-mcp==" + version)


RUN = "0" * 32
HOLDER_ID = "synthetic-holder-id"
SYNTHETIC_TOKEN = "synthetic-header-000000.synthetic-payload-000000.synthetic-signature-000000"
OTHER_TOKEN = "synthetic-ordinary-user-token-0000000000"
IMAGE = {"image": "synthetic.invalid/core@sha256:" + "1" * 64, "config_digest": "sha256:" + "2" * 64}


def core_inspect(**changes):
    """A synthetic ``docker container inspect`` of a Core the lane would accept."""
    data = {
        "Image": IMAGE["config_digest"], "Config": {"Image": IMAGE["image"]},
        "State": {"Running": True},
        "HostConfig": {"Memory": 1536 * 1024 * 1024, "PidsLimit": 1024, "Privileged": False,
                       "CapAdd": None, "Devices": [], "SecurityOpt": ["no-new-privileges"],
                       "PortBindings": {}, "PublishAllPorts": False, "Binds": None,
                       "NetworkMode": "container:" + HOLDER_ID},
        "NetworkSettings": {"Networks": {}, "Ports": {}},
        "Mounts": [{"Type": "volume", "Name": "synthetic-volume", "Destination": "/config"}]}
    for key, value in changes.items():
        section, _, field = key.partition("__")
        if field:
            data[section][field] = value
        else:
            data[section] = value
    return data


def flag_values(argv, flag):
    return [argv[index + 1] for index, item in enumerate(argv) if item == flag]


class ArgvTests(unittest.TestCase):
    def all_argv(self):
        return {
            "holder": lane.idle_holder_argv("synthetic-holder", RUN, Path("/synthetic/out")),
            "core": lane.core_argv("synthetic-core", RUN, "synthetic-holder", "synthetic-volume",
                                   IMAGE["image"]),
            "peer": lane.peer_argv("synthetic-peer", RUN, "synthetic-holder", "synthetic-image")}

    def test_no_container_pulls_publishes_or_gains_privilege(self):
        for name, argv in self.all_argv().items():
            with self.subTest(container=name):
                self.assertEqual(flag_values(argv, "--pull"), ["never"])
                self.assertEqual(len(flag_values(argv, "--memory")), 1)
                self.assertEqual(flag_values(argv, "--security-opt"), ["no-new-privileges"])
                for refused in ("-p", "--publish", "-P", "--publish-all", "--privileged",
                                "--cap-add", "--device", "--add-host", "--dns", "-v", "--volume",
                                "--network-alias", "--link"):
                    self.assertNotIn(refused, argv)
                self.assertIn(f"{lane.s4.LANE_LABEL}={RUN}", flag_values(argv, "--label"))

    def test_the_only_networks_are_none_and_the_holder_namespace(self):
        argv = self.all_argv()
        self.assertEqual(flag_values(argv["holder"], "--network"), ["none"])
        self.assertEqual(flag_values(argv["core"], "--network"), ["container:synthetic-holder"])
        self.assertEqual(flag_values(argv["peer"], "--network"), ["container:synthetic-holder"])

    def test_the_holder_is_idle_and_mounts_code_read_only(self):
        argv = self.all_argv()["holder"]
        self.assertEqual(argv[:3], ["docker", "run", "--detach"])
        mounts = flag_values(argv, "--mount")
        self.assertEqual(len(mounts), 3)
        self.assertEqual([item for item in mounts if not item.endswith(",readonly")],
                         ["type=bind,source=/synthetic/out,target=/out"])
        image = argv.index(lane.s4.HELPER_IMAGE)
        self.assertEqual(argv[image + 1:image + 3], ["-I", "-c"])
        self.assertNotIn("fake_ha_read_gateway_contract_server", " ".join(argv))

    def test_core_is_created_not_run_with_one_volume_and_the_bound_image_last(self):
        argv = self.all_argv()["core"]
        self.assertEqual(argv[:2], ["docker", "create"])
        self.assertEqual(flag_values(argv, "--mount"),
                         ["type=volume,source=synthetic-volume,target=/config"])
        self.assertEqual(argv[-1], IMAGE["image"])
        self.assertEqual(flag_values(argv, "--env"), ["TZ=America/Chicago"])

    def test_the_token_reaches_the_peer_by_name_only(self):
        argv = self.all_argv()["peer"]
        environment = flag_values(argv, "--env")
        self.assertIn("HOMEASSISTANT_TOKEN", environment)
        self.assertIn("HOMEASSISTANT_URL=http://127.0.0.1:8123", environment)
        self.assertFalse(any(item.startswith("HOMEASSISTANT_TOKEN=") for item in argv))
        self.assertEqual(argv[-2:], ["synthetic-image", "ha-mcp-web"])
        forwarded = lane.with_token(SYNTHETIC_TOKEN)
        self.assertEqual(forwarded["HOMEASSISTANT_TOKEN"], SYNTHETIC_TOKEN)
        self.assertNotIn(SYNTHETIC_TOKEN, " ".join(argv))

    def test_the_lane_sets_no_redaction_variable_on_the_peer(self):
        names = [item.split("=", 1)[0] for item in flag_values(self.all_argv()["peer"], "--env")]
        self.assertEqual(sorted(names), sorted(
            [key for key, _ in lane.PEER_ENVIRONMENT] + ["HOMEASSISTANT_TOKEN"]))
        for name in names:
            self.assertNotRegex(name, r"(?i)redact|mask|privacy|sanit")

    def test_the_three_containers_fit_under_the_memory_ceiling(self):
        self.assertLessEqual(lane.memory_total(), lane.s4.host.memory_bytes("3g"))

    def test_names_are_distinct_and_carry_the_project(self):
        for command_name in ("selftest", "run"):
            wanted = lane.lane_names(command_name)
            self.assertEqual(len(set(wanted.values())), len(wanted))
            for value in wanted.values():
                self.assertTrue(value.startswith(lane.PROJECT + "-" + command_name + "-"))
            self.assertEqual(lane.container_keys(wanted)[-2:], ["core", "holder"])
            self.assertNotIn("volume", lane.container_keys(wanted))
        self.assertFalse(set(lane.lane_names("run").values())
                         & set(lane.lane_names("selftest").values()))


class ConfigurationArchiveTests(unittest.TestCase):
    def test_the_stream_holds_the_seed_files_and_keeps_the_non_ascii_name(self):
        raw = lane.config_archive(lane.seed_step.CONFIG_FILES)
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            members = archive.getmembers()
            self.assertEqual([item.name for item in members],
                             sorted(lane.seed_step.CONFIG_FILES))
            for item in members:
                self.assertTrue(item.isfile())
                self.assertEqual((item.mode, item.uid, item.gid, item.mtime), (0o644, 0, 0, 0))
            text = archive.extractfile("configuration.yaml").read().decode("utf-8")
        self.assertIn(lane.seed_step.GROUP_NAME, text)
        self.assertIn("server_host: 127.0.0.1", text)
        self.assertEqual(raw, lane.config_archive(dict(lane.seed_step.CONFIG_FILES)))

    def test_a_name_outside_the_reviewed_form_is_refused(self):
        for name in ("../configuration.yaml", "/configuration.yaml", "secrets.yml",
                     "custom_components/x.yaml", "CONFIGURATION.yaml"):
            with self.subTest(name=name), self.assertRaises(lane.Refusal) as raised:
                lane.config_archive({name: "synthetic: true\n"})
            self.assertEqual(str(raised.exception), "configuration_file_name_not_reviewed")


class CoreContainerTests(unittest.TestCase):
    def verify(self, **changes):
        return lane.verify_core(core_inspect(**changes), HOLDER_ID, IMAGE, "synthetic-volume")

    def test_the_expected_container_is_accepted(self):
        found = self.verify()
        self.assertEqual(found["docker_networks"], [])
        self.assertEqual(found["published_ports"], [])
        self.assertEqual(found["mounts"], ["volume:/config"])

    def test_every_departure_is_refused(self):
        other = {"Type": "bind", "Name": None, "Destination": "/synthetic"}
        volume = {"Type": "volume", "Name": "synthetic-volume", "Destination": "/config"}
        cases = (
            ({"Image": "sha256:" + "3" * 64}, "running_image_mismatch"),
            ({"Config__Image": "synthetic.invalid/core:latest"}, "running_image_mismatch"),
            ({"HostConfig__Memory": 0}, "running_container_limits_mismatch"),
            ({"HostConfig__PidsLimit": None}, "running_container_limits_mismatch"),
            ({"HostConfig__Privileged": True}, "core_has_added_privilege"),
            ({"HostConfig__CapAdd": ["NET_ADMIN"]}, "core_has_added_privilege"),
            ({"HostConfig__Devices": [{"PathOnHost": "/dev/synthetic"}]},
             "core_has_added_privilege"),
            ({"HostConfig__SecurityOpt": None}, "core_has_added_privilege"),
            ({"HostConfig__PortBindings": {"8123/tcp": [{"HostPort": "8123"}]}},
             "container_publishes_ports"),
            ({"HostConfig__PublishAllPorts": True}, "container_publishes_ports"),
            ({"NetworkSettings__Ports": {"8123/tcp": [{"HostPort": "8123"}]}},
             "container_publishes_ports"),
            ({"HostConfig__NetworkMode": "bridge"}, "core_network_not_holder_namespace"),
            ({"HostConfig__NetworkMode": "host"}, "core_network_not_holder_namespace"),
            ({"HostConfig__NetworkMode": "container:synthetic-other"},
             "core_network_not_holder_namespace"),
            ({"NetworkSettings__Networks": {"bridge": {}}}, "core_network_not_holder_namespace"),
            ({"Mounts": [volume, other]}, "core_mounts_not_the_run_volume"),
            ({"Mounts": []}, "core_mounts_not_the_run_volume"),
            ({"Mounts": [dict(volume, Name="synthetic-other-volume")]},
             "core_mounts_not_the_run_volume"),
            ({"HostConfig__Binds": ["/synthetic:/config"]}, "core_mounts_not_the_run_volume"),
            ({"State": {"Running": False}}, "container_not_running"),
        )
        for changes, token in cases:
            with self.subTest(changes=changes), self.assertRaises(lane.Refusal) as raised:
                self.verify(**changes)
            self.assertEqual(str(raised.exception), token)

    def test_an_absent_container_is_refused(self):
        with self.assertRaises(lane.Refusal):
            lane.verify_core({}, HOLDER_ID, IMAGE, "synthetic-volume")


class EgressTests(unittest.TestCase):
    GOOD = {"interfaces": ["lo"], "ipv4_routes": 0, "egress_errno": 101}

    def test_loopback_only_with_a_refused_connection_passes(self):
        self.assertEqual(lane.require_no_egress(dict(self.GOOD, extra="synthetic")), self.GOOD)

    def test_an_interface_a_route_or_a_connection_out_is_refused(self):
        cases = ((dict(self.GOOD, interfaces=["eth0", "lo"]), "namespace_not_loopback_only"),
                 (dict(self.GOOD, interfaces=[]), "namespace_not_loopback_only"),
                 (dict(self.GOOD, ipv4_routes=1), "namespace_not_loopback_only"),
                 (dict(self.GOOD, egress_errno=0), "namespace_egress_not_refused"),
                 (dict(self.GOOD, egress_errno=110), "namespace_egress_not_refused"),
                 ([], "namespace_not_loopback_only"), (None, "namespace_not_loopback_only"))
        for probe, token in cases:
            with self.subTest(probe=probe), self.assertRaises(lane.Refusal) as raised:
                lane.require_no_egress(probe)
            self.assertEqual(str(raised.exception), token)


class SeedOutputTests(unittest.TestCase):
    def line(self, value):
        return (json.dumps(value) + "\n").encode("ascii")

    def test_the_one_line_gives_the_result_and_the_tokens(self):
        tokens = {"administrator": SYNTHETIC_TOKEN, "ordinary_user": OTHER_TOKEN}
        self.assertEqual(lane.seed_output(self.line({"all_steps_ok": True, "tokens": tokens})),
                         (True, tokens))
        # A seed that stopped early still hands over what it minted, for the scan.
        self.assertEqual(
            lane.seed_output(self.line({"all_steps_ok": False,
                                        "tokens": {"administrator": SYNTHETIC_TOKEN}})),
            (False, {"administrator": SYNTHETIC_TOKEN}))
        self.assertEqual(lane.seed_output(self.line({"all_steps_ok": "yes", "tokens": {}})),
                         (False, {}))

    def test_anything_else_is_refused_with_a_fixed_token(self):
        good = self.line({"all_steps_ok": True, "tokens": {"administrator": SYNTHETIC_TOKEN}})
        cases = (b"", b"not json\n", good + good, self.line([]), self.line({"tokens": []}),
                 self.line({"tokens": {"administrator": 1}}),
                 self.line({"tokens": {"administrator": "short"}}),
                 self.line({"tokens": {"administrator": SYNTHETIC_TOKEN + " x"}}),
                 self.line({"tokens": {"synthetic_other": SYNTHETIC_TOKEN}}))
        for raw in cases:
            with self.subTest(raw=raw[:40]), self.assertRaises(lane.Refusal) as raised:
                lane.seed_output(raw)
            self.assertEqual(str(raised.exception), "seed_output_unreadable")


class StoredFileTests(unittest.TestCase):
    TOKENS = {"administrator": SYNTHETIC_TOKEN, "ordinary_user": OTHER_TOKEN}

    def test_a_file_with_a_token_or_its_signature_is_removed_and_listed(self):
        with tempfile.TemporaryDirectory() as scratch:
            run_dir = Path(scratch)
            (run_dir / "out").mkdir()
            (run_dir / "out" / "clean.json").write_bytes(b"{\"synthetic\": true}")
            (run_dir / "out" / "whole.json").write_bytes(b"x" + SYNTHETIC_TOKEN.encode() + b"x")
            (run_dir / "peer.log").write_bytes(b"Bearer ...synthetic-signature-000000\n")
            (run_dir / "other.log").write_bytes(OTHER_TOKEN.encode())
            scan = lane.secret_scan(run_dir, self.TOKENS)
            self.assertEqual(scan["files_removed_for_a_secret"],
                             ["other.log", "out/whole.json", "peer.log"])
            self.assertEqual((scan["files_scanned"], scan["needles"], scan["links"]), (4, 3, []))
            self.assertEqual(sorted(path.name for path in run_dir.rglob("*") if path.is_file()),
                             ["clean.json"])
            self.assertNotIn("synthetic-signature", json.dumps(scan))

    def test_a_clean_tree_is_left_alone_and_a_link_is_reported_not_followed(self):
        with tempfile.TemporaryDirectory() as scratch:
            run_dir, outside = Path(scratch) / "run", Path(scratch) / "outside"
            run_dir.mkdir()
            outside.write_bytes(SYNTHETIC_TOKEN.encode())
            (run_dir / "receipt.json").write_bytes(b"{}")
            os.symlink(outside, run_dir / "link.json")
            scan = lane.secret_scan(run_dir, self.TOKENS)
            self.assertEqual(scan["files_removed_for_a_secret"], [])
            self.assertEqual(scan["links"], ["link.json"])
            self.assertTrue(outside.exists())
            self.assertEqual(lane.secret_scan(run_dir, {})["needles"], 0)

    def test_the_header_segment_every_such_token_shares_is_not_a_needle(self):
        needles = lane.secret_needles(self.TOKENS)
        self.assertNotIn(b"synthetic-header-000000", needles)
        self.assertIn(b"synthetic-signature-000000", needles)

    def test_sums_cover_every_stored_file_but_themselves(self):
        with tempfile.TemporaryDirectory() as scratch:
            run_dir = Path(scratch)
            (run_dir / "out").mkdir()
            (run_dir / "out" / "a.json").write_bytes(b"synthetic")
            (run_dir / "receipt.json").write_bytes(b"{}")
            lane.write_sums(run_dir)
            lane.write_sums(run_dir)
            rows = (run_dir / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
        self.assertEqual([row.split("  ", 1)[1] for row in rows], ["out/a.json", "receipt.json"])
        self.assertEqual(rows[0].split("  ")[0], lane.s4.sha256_bytes(b"synthetic")[7:])
        self.assertRegex(rows[1], r"^[0-9a-f]{64}  receipt\.json$")

    def test_the_calls_summary_is_counts_only(self):
        with tempfile.TemporaryDirectory() as scratch:
            (Path(scratch) / "_index.json").write_text(json.dumps({
                "sessions_opened": 2, "calls": [
                    {"id": "synthetic-1", "is_error": False, "exchange_failure": None},
                    {"id": "synthetic-2", "is_error": True, "exchange_failure": None},
                    {"id": "synthetic-3", "is_error": None,
                     "exchange_failure": "peer_exchange_failed"}]}), encoding="utf-8")
            summary = lane.calls_summary(scratch)
        self.assertEqual(summary, {"calls": 3, "exchange_failures": 1, "is_error": 1,
                                   "sessions_opened": 2,
                                   "arguments_built_with_policy": "9.1.2"})


class ProfileTests(unittest.TestCase):
    def inventory(self, scratch, **changes):
        receipt = {"command": "inventory", "result": "PASS",
                   "bindings": {"core": {"image": IMAGE["image"]}},
                   "profile_b": {version: {"missing": []} for version in lane.VERSIONS}}
        receipt.update(changes)
        path = Path(scratch) / "receipt.json"
        path.write_text(json.dumps(receipt), encoding="utf-8")
        return path

    def refusal(self, *args):
        with self.assertRaises(lane.Refusal) as raised:
            lane.require_profile_b(*args)
        return str(raised.exception)

    def test_the_committed_inventory_refuses_profile_b_for_a_missing_requirement(self):
        self.assertEqual(self.refusal(lane.core_binding()), "profile_b_core_requirement_missing")

    def test_profile_b_refuses_even_when_nothing_is_missing_because_it_is_not_built(self):
        with tempfile.TemporaryDirectory() as scratch:
            self.assertEqual(self.refusal(IMAGE, self.inventory(scratch)),
                             "profile_b_overlay_not_built")

    def test_a_missing_failed_or_stale_inventory_is_refused(self):
        with tempfile.TemporaryDirectory() as scratch:
            self.assertEqual(self.refusal(IMAGE, Path(scratch) / "absent.json"),
                             "profile_b_inventory_missing")
            self.assertEqual(self.refusal(IMAGE, self.inventory(scratch, result="WITHHELD")),
                             "profile_b_inventory_missing")
            self.assertEqual(self.refusal(IMAGE, self.inventory(scratch, profile_b={})),
                             "profile_b_inventory_missing")
            self.assertEqual(
                self.refusal(IMAGE, self.inventory(
                    scratch, bindings={"core": {"image": "synthetic.invalid/other"}})),
                "profile_b_inventory_stale")

    def test_the_groups_are_the_calls_modules_and_profile_a_leaves_the_yaml_read_out(self):
        every = lane.CAPTURE_GROUPS + lane.RESTART_GROUPS + lane.PROFILE_B_GROUPS
        self.assertEqual(sorted(every), sorted(lane.calls_step.GROUPS))
        self.assertNotIn("yaml", lane.CAPTURE_GROUPS + lane.RESTART_GROUPS)
        self.assertIn(lane.ARGUMENT_POLICY, lane.VERSIONS)

    def test_run_needs_a_profile_and_selftest_takes_none(self):
        common = ["--run-dir", "synthetic", "--launch-log", "synthetic"]
        for argv in (["run", *common], ["run", *common, "--profile", "C"],
                     ["selftest", *common, "--profile", "A"]):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    lane.main(argv)

    def test_the_receipt_states_what_is_not_built(self):
        text = " ".join(lane.NOT_BUILT)
        for phrase in ("profile B overlay", "Supervisor facade", "token revocation"):
            self.assertIn(phrase, text)


def fake_digest(image):
    return "sha256:" + hashlib.sha256(image.encode("utf-8")).hexdigest()


class FakeDocker:
    """A synthetic stand-in for the Docker command line: state in memory, nothing started."""

    GOOD_PROBE = {"interfaces": ["lo"], "ipv4_routes": 0, "egress_errno": 101}

    def __init__(self, *, probe=None, peer_log=b"synthetic peer log\n", seed_ok=True):
        self.calls, self.events, self.forwarded = [], [], []
        self.containers, self.volumes, self.streams = {}, {}, []
        self.probe = probe or (lambda container, count: dict(self.GOOD_PROBE))
        self.probes, self.peer_log, self.seed_ok, self.out_root = 0, peer_log, seed_ok, None

    def __call__(self, *args, check=True, timeout=120, env=None):
        args = list(args)
        self.calls.append(args)
        if env is not None:
            self.forwarded.append((args, env.get("HOMEASSISTANT_TOKEN")))
        code, out = self.dispatch(args, env or {})
        if check and code:
            raise lane.Refusal("host_command_failed")
        return types.SimpleNamespace(returncode=code, stdout=out, stderr=b"")

    def stream(self, argv, raw, timeout=120):
        self.calls.append(list(argv))
        self.events.append("cp:" + argv[-1])
        self.streams.append(raw)

    def host_path(self, inside):
        assert inside.startswith("/out/"), inside
        return self.out_root / inside[len("/out/"):]

    def create(self, args):
        values = lambda flag: flag_values(args, flag)  # noqa: E731
        name, network = values("--name")[0], values("--network")[0]
        image = next(item for item in args if "@sha256:" in item)
        mounts = []
        for text in values("--mount"):
            fields = dict(item.split("=", 1) for item in text.split(",") if "=" in item)
            if fields["type"] == "bind" and fields["target"] == "/out":
                self.out_root = Path(fields["source"])
            if fields["type"] == "volume":
                assert fields["source"] in self.volumes, "volume must exist first"
            mounts.append({"Type": fields["type"], "Destination": fields["target"],
                           "Name": fields["source"] if fields["type"] == "volume" else None})
        if network.startswith("container:"):
            network = "container:" + self.containers[network.split(":", 1)[1]]["Id"]
        self.containers[name] = {
            "Id": "synthetic-id-" + name, "Image": fake_digest(image),
            "Config": {"Image": image,
                       "Labels": dict(item.split("=", 1) for item in values("--label"))},
            "State": {"Running": args[1] == "run", "OOMKilled": False},
            "HostConfig": {
                "Memory": lane.s4.host.memory_bytes(values("--memory")[0]),
                "PidsLimit": int(values("--pids-limit")[0]),
                "ReadonlyRootfs": "--read-only" in args, "Privileged": False,
                "PortBindings": {}, "PublishAllPorts": False, "CapAdd": None, "Devices": [],
                "SecurityOpt": values("--security-opt"), "Binds": None, "NetworkMode": network,
                "Tmpfs": {item: "" for item in values("--tmpfs")}},
            "NetworkSettings": {"Networks": {"none": {}} if network == "none" else {},
                                "Ports": {}},
            "Mounts": mounts}
        self.events.append(args[1] + ":" + name)
        return 0, b""

    def execute(self, args, env):
        rest = args[2:]
        while rest[0] == "--env":
            rest = rest[2:]
        container, rest = rest[0], rest[1:]
        if not self.containers.get(container, {}).get("State", {}).get("Running"):
            return 1, b""
        if lane.s4.NET_PROBE in rest:
            self.probes += 1
            self.events.append("probe:" + container)
            return 0, json.dumps(self.probe(container, self.probes)).encode("ascii")
        if lane.WAIT_RUNNING in rest:
            self.events.append("wait-running")
            return (0 if env.get("HOMEASSISTANT_TOKEN") == SYNTHETIC_TOKEN else 1), b""
        if lane.s4.WAIT_HTTP in rest:
            return 0, b""
        script = next((item for item in rest if item.startswith("/lane/scripts/")), None)
        if script and script.endswith("core_read_replay_seed.py"):
            self.events.append("seed")
            out = self.host_path(rest[rest.index("--out") + 1] + "/x").parent
            (out / "seed-report.json").write_text(json.dumps({
                "all_steps_ok": self.seed_ok, "tokens_issued": list(lane.TOKEN_NAMES),
                "steps": [{"step": "onboard", "status": "ok"}]}), encoding="utf-8")
            if self.seed_ok:
                (out / "seed.json").write_text("{}", encoding="utf-8")
            tokens = {"administrator": SYNTHETIC_TOKEN, "ordinary_user": OTHER_TOKEN}
            line = json.dumps({"all_steps_ok": self.seed_ok, "tokens": tokens}) + "\n"
            return (0 if self.seed_ok else 1), line.encode("ascii")
        if script and script.endswith("core_read_replay_calls.py"):
            if "--help" in rest:
                self.events.append("calls-help")
                return 0, b""
            target = rest[rest.index("--out") + 1]
            self.events.append("calls:" + target + ":" + rest[rest.index("--groups") + 1])
            out = self.host_path(target)
            out.mkdir(parents=False)
            (out / "_index.json").write_text(json.dumps({"sessions_opened": 1, "calls": [
                {"id": "synthetic", "is_error": False, "exchange_failure": None}]}),
                encoding="utf-8")
            return 0, b""
        raise AssertionError(rest[:4])

    def dispatch(self, args, env):
        assert args[0] == "docker", args
        if args[1] in ("run", "create"):
            return self.create(args)
        if args[1] == "exec":
            return self.execute(args, env)
        if args[1:3] == ["container", "inspect"]:
            data = self.containers.get(args[3])
            return (0, json.dumps([data]).encode("ascii")) if data else (1, b"")
        if args[1:3] == ["container", "ls"]:
            return 0, "\n".join(self.containers).encode("ascii")
        if args[1:3] == ["volume", "ls"]:
            return 0, "\n".join(self.volumes).encode("ascii")
        if args[1:3] == ["volume", "create"]:
            self.volumes[args[-1]] = {"Labels": dict(
                item.split("=", 1) for item in flag_values(args, "--label"))}
            return 0, b""
        if args[1:3] == ["volume", "inspect"]:
            data = self.volumes.get(args[3])
            return (0, json.dumps([data]).encode("ascii")) if data else (1, b"")
        if args[1:3] == ["volume", "rm"]:
            in_use = any(item.get("Name") == args[3] for data in self.containers.values()
                         for item in data["Mounts"])
            if in_use:
                return 1, b""
            del self.volumes[args[3]]
            return 0, b""
        if args[1] == "start":
            self.containers[args[2]]["State"]["Running"] = True
            self.events.append("start:" + args[2])
            return 0, b""
        if args[1] == "restart":
            self.events.append("restart:" + args[-1])
            return 0, b""
        if args[1] == "logs":
            return 0, self.peer_log if "-peer-" in args[2] else b"synthetic log\n"
        if args[1] == "rm":
            del self.containers[args[-1]]
            self.events.append("rm:" + args[-1])
            return 0, b""
        raise AssertionError(args[:4])


class LaneFlowTests(unittest.TestCase):
    """The whole lane against the synthetic Docker above; no process is started."""

    def launch(self, docker, *tail):
        def cached(image, labels):
            return {"image": image, "config_digest": fake_digest(image), "labels": dict(labels)}
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        run_dir, log = Path(scratch.name) / "run", Path(scratch.name) / "launches.jsonl"
        figures = {"memory_available_gb": 8.0, "load_1m": 1.0, "root_free_gb": 50.0,
                   "root_total_gb": 100.0}
        with contextlib.ExitStack() as stack:
            for target, name, value in (
                    (lane, "command", docker), (lane.s4, "command", docker),
                    (lane, "stream_in", docker.stream), (lane.s4, "cached_image", cached),
                    (lane.s4, "require_clean_checkout", lambda: "f" * 40),
                    (lane.s4, "host_figures", lambda: dict(figures)),
                    (lane.s4, "helper_runtime", lambda holder: {"synthetic": True}),
                    (lane.s4, "new_run_directory", Path)):
                stack.enter_context(mock.patch.object(target, name, value))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            code = lane.main([*tail, "--run-dir", str(run_dir), "--launch-log", str(log)])
        launch = json.loads(log.read_text(encoding="ascii").splitlines()[-1])
        receipt = (json.loads((run_dir / "receipt.json").read_text(encoding="ascii"))
                   if (run_dir / "receipt.json").exists() else None)
        return code, launch, receipt, run_dir

    def test_profile_a_captures_both_versions_one_peer_at_a_time_and_cleans_up(self):
        docker = FakeDocker()
        code, launch, receipt, run_dir = self.launch(docker, "run", "--profile", "A")
        self.assertEqual((code, launch["result"], receipt["result"]), (0, "PASS", "PASS"))
        self.assertEqual(receipt["cleanup"], {"clean": True, "containers_absent": True,
                                              "volume_absent": True,
                                              "docker_networks_created": 0})
        self.assertEqual((docker.containers, docker.volumes), ({}, {}))
        self.assertEqual(sorted(receipt["captures"]), ["9.1.2", "9.2.0"])
        events = docker.events
        peers = [item for item in events if item.startswith(("run:", "rm:")) and "-peer-" in item]
        self.assertEqual(peers, [
            "run:hamcp371-s6-run-peer-9-1-2", "rm:hamcp371-s6-run-peer-9-1-2",
            "run:hamcp371-s6-run-peer-9-2-0", "rm:hamcp371-s6-run-peer-9-2-0"])
        # Configuration goes in before Core starts; no seed or peer before a posture check.
        self.assertLess(events.index("cp:hamcp371-s6-run-core:/config"),
                        events.index("start:hamcp371-s6-run-core"))
        self.assertLess(events.index("probe:hamcp371-s6-run-core"), events.index("seed"))
        for peer in ("run:hamcp371-s6-run-peer-9-1-2", "run:hamcp371-s6-run-peer-9-2-0"):
            before = events[:events.index(peer)]
            self.assertEqual(before[-2:], ["probe:hamcp371-s6-run-holder",
                                           "probe:hamcp371-s6-run-core"])
        self.assertEqual([item for item in events if item.startswith("calls:")], [
            "calls:/out/9.1.2/calls:reads,variants,errors,transport",
            "calls:/out/9.1.2/calls-after-core-restart:after_restart",
            "calls:/out/9.2.0/calls:reads,variants,errors,transport",
            "calls:/out/9.2.0/calls-after-core-restart:after_restart"])
        self.assertEqual(events.count("restart:hamcp371-s6-run-core"), 2)
        # Cleanup removes the Core and then the holder that owned the namespace, last.
        self.assertEqual(events[-2:], ["rm:hamcp371-s6-run-core", "rm:hamcp371-s6-run-holder"])
        # The token is forwarded by environment only and stored nowhere.
        for argv in docker.calls:
            self.assertNotIn(SYNTHETIC_TOKEN, " ".join(argv))
            self.assertNotIn(OTHER_TOKEN, " ".join(argv))
        self.assertTrue(docker.forwarded)
        self.assertEqual({token for _argv, token in docker.forwarded}, {SYNTHETIC_TOKEN})
        for path in run_dir.rglob("*"):
            if path.is_file():
                self.assertNotIn(SYNTHETIC_TOKEN.encode(), path.read_bytes())
                self.assertNotIn(OTHER_TOKEN.encode(), path.read_bytes())
        self.assertEqual(receipt["secret_scan"]["files_removed_for_a_secret"], [])
        self.assertEqual(receipt["redaction"]["setting"], "image defaults")
        self.assertEqual(receipt["captures"]["9.2.0"]["calls"]["arguments_built_with_policy"],
                         "9.1.2")
        sums = (run_dir / "SHA256SUMS").read_text(encoding="utf-8")
        for name in ("receipt.json", "peer-9.1.2.log", "peer-9.2.0.log", "core.log",
                     "out/seed/seed-report.json", "out/9.2.0/calls/_index.json"):
            self.assertIn("  " + name + "\n", sums)
        # The stream that went into Core is the reviewed configuration, nothing else.
        self.assertEqual(docker.streams, [lane.config_archive(lane.seed_step.CONFIG_FILES)])

    def test_the_selftest_starts_core_but_no_upstream_image(self):
        docker = FakeDocker()
        code, _launch, receipt, _run_dir = self.launch(docker, "selftest")
        self.assertEqual((code, receipt["result"]), (0, "PASS"))
        self.assertEqual(receipt["upstream_images"], {})
        self.assertEqual(receipt["captures"], {})
        self.assertFalse(any("-peer-" in item for item in docker.events))
        self.assertFalse(any(lane.s4.UPSTREAM_REPOSITORY in " ".join(argv)
                             for argv in docker.calls))
        self.assertIn("restart:hamcp371-s6-selftest-core", docker.events)
        self.assertIn("calls-help", docker.events)
        self.assertIn("after_core_restart", receipt["network_posture"])
        self.assertEqual((docker.containers, docker.volumes), ({}, {}))

    def test_a_route_in_the_namespace_withholds_before_the_seed_and_any_peer(self):
        def probe(container, count):
            # The holder alone looks right; with Core in the namespace a route appears.
            return dict(FakeDocker.GOOD_PROBE, ipv4_routes=0 if count == 1 else 1)
        docker = FakeDocker(probe=probe)
        code, launch, receipt, _run_dir = self.launch(docker, "run", "--profile", "A")
        self.assertEqual((code, launch["result"], receipt["failure"]),
                         (1, "WITHHELD", "namespace_not_loopback_only"))
        self.assertNotIn("seed", docker.events)
        self.assertFalse(any("-peer-" in item for item in docker.events))
        self.assertIs(receipt["cleanup"]["clean"], True)
        self.assertEqual((docker.containers, docker.volumes), ({}, {}))

    def test_egress_seen_only_from_inside_core_also_withholds(self):
        def probe(container, count):
            return dict(FakeDocker.GOOD_PROBE,
                        egress_errno=0 if container.endswith("-core") else 101)
        docker = FakeDocker(probe=probe)
        code, _launch, receipt, _run_dir = self.launch(docker, "selftest")
        self.assertEqual((code, receipt["failure"]), (1, "namespace_egress_not_refused"))
        self.assertNotIn("seed", docker.events)

    def test_a_seed_that_did_not_finish_withholds_before_any_peer(self):
        docker = FakeDocker(seed_ok=False)
        code, _launch, receipt, run_dir = self.launch(docker, "run", "--profile", "A")
        self.assertEqual((code, receipt["failure"]), (1, "seed_incomplete"))
        self.assertFalse(any("-peer-" in item for item in docker.events))
        self.assertIs(receipt["seed"]["all_steps_ok"], False)
        self.assertEqual(receipt["secret_scan"]["needles"], 3)
        self.assertEqual((docker.containers, docker.volumes), ({}, {}))

    def test_a_token_in_a_peer_log_removes_the_log_and_withholds(self):
        docker = FakeDocker(peer_log=b"Authorization: Bearer " + SYNTHETIC_TOKEN.encode())
        code, launch, receipt, run_dir = self.launch(docker, "run", "--profile", "A")
        self.assertEqual((code, launch["result"]), (1, "WITHHELD"))
        self.assertEqual(receipt["failure"], "secret_or_link_in_stored_files")
        self.assertEqual(receipt["secret_scan"]["files_removed_for_a_secret"],
                         ["peer-9.1.2.log", "peer-9.2.0.log"])
        self.assertFalse((run_dir / "peer-9.1.2.log").exists())
        self.assertIs(receipt["cleanup"]["clean"], True)

    def test_profile_b_is_a_preflight_refusal_that_touches_nothing(self):
        docker = FakeDocker()
        code, launch, receipt, run_dir = self.launch(docker, "run", "--profile", "B")
        self.assertEqual((code, launch["result"], launch["failure"]),
                         (2, "REFUSED", "profile_b_core_requirement_missing"))
        self.assertIs(launch["execution_started"], False)
        self.assertEqual(docker.calls, [])
        self.assertIsNone(receipt)
        self.assertFalse(run_dir.exists())

    def test_a_name_already_in_use_is_a_preflight_refusal(self):
        docker = FakeDocker()
        docker.volumes["hamcp371-s6-run-config"] = {"Labels": {}}
        code, launch, _receipt, run_dir = self.launch(docker, "run", "--profile", "A")
        self.assertEqual((code, launch["failure"]), (2, "disposable_name_already_exists"))
        self.assertFalse(run_dir.exists())
        self.assertEqual(docker.volumes, {"hamcp371-s6-run-config": {"Labels": {}}})


if __name__ == "__main__":
    unittest.main()
