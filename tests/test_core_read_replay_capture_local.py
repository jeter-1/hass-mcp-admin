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
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import types
import unittest
from unittest import mock
import warnings
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import core_read_replay_capture_local as lane  # noqa: E402
from support import frozen_capture_baseline as baseline  # noqa: E402


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

    def test_the_peer_gets_production_flags_once_each(self):
        environment = flag_values(self.all_argv()["peer"], "--env")
        for name, value in (("ENABLE_BETA_FEATURES", "true"),
                            ("HAMCP_ENABLE_FILESYSTEM_TOOLS", "true"),
                            ("ENABLE_YAML_CONFIG_EDITING", "false"),
                            ("DISABLED_TOOLS", "ha_write_file,ha_delete_file")):
            with self.subTest(flag=name):
                self.assertEqual([item for item in environment
                                  if item.split("=", 1)[0] == name], [f"{name}={value}"])
        self.assertEqual(lane.ABSENT_FROM_CATALOG,
                         ("ha_write_file", "ha_delete_file", "ha_config_set_yaml"))

    def test_the_three_containers_fit_under_the_memory_ceiling(self):
        self.assertLessEqual(lane.memory_total(), lane.s4.host.memory_bytes("3g"))

    def test_names_are_distinct_and_carry_the_project(self):
        for command_name in ("selftest", "run", "recapture"):
            wanted = lane.lane_names(command_name)
            self.assertEqual(len(set(wanted.values())), len(wanted))
            for value in wanted.values():
                self.assertTrue(value.startswith(lane.PROJECT + "-" + command_name + "-"))
            self.assertEqual(lane.container_keys(wanted)[-2:], ["core", "holder"])
            self.assertNotIn("volume", lane.container_keys(wanted))
        self.assertFalse(set(lane.lane_names("run").values())
                         & set(lane.lane_names("selftest").values()))


class PeerFlagTests(unittest.TestCase):
    GOOD = ["PATH=/usr/bin", "HOMEASSISTANT_TOKEN=" + SYNTHETIC_TOKEN,
            *(f"{name}={value}" for name, value in lane.PRODUCTION_FLAGS)]

    def test_each_flag_once_with_its_value_passes_and_reads_nothing_else(self):
        found = lane.peer_flags({"Config": {"Env": list(self.GOOD)}})
        self.assertEqual(found, dict(lane.PRODUCTION_FLAGS))
        self.assertNotIn(SYNTHETIC_TOKEN, json.dumps(found))

    def test_a_missing_wrong_or_repeated_flag_is_refused(self):
        for name, value in lane.PRODUCTION_FLAGS:
            entry = f"{name}={value}"
            for case, env in (
                    ("missing", [item for item in self.GOOD if item != entry]),
                    ("wrong", [item if item != entry else name + "=other"
                               for item in self.GOOD]),
                    ("repeated", [*self.GOOD, entry]),
                    ("bare", [item if item != entry else name for item in self.GOOD])):
                with self.subTest(flag=name, case=case):
                    with self.assertRaisesRegex(lane.Refusal, "^peer_flags_not_set$"):
                        lane.peer_flags({"Config": {"Env": env}})
        with self.assertRaisesRegex(lane.Refusal, "^peer_flags_not_set$"):
            lane.peer_flags({})


class PeerCatalogTests(unittest.TestCase):
    def check(self, value):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        path = Path(scratch.name) / "peer-9-2-0-1.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return lane.catalog_check(path)

    def test_a_catalog_without_the_disabled_tools_and_with_the_yaml_read_passes(self):
        result = self.check({"tool_names_in_wire_order": list(SYNTHETIC_CATALOG)})
        self.assertEqual((result["file"], result["tool_count"]),
                         ("peer-9-2-0-1.json", len(SYNTHETIC_CATALOG)))
        self.assertEqual(result["absent"], list(lane.ABSENT_FROM_CATALOG))
        self.assertRegex(result["sha256"], "^[0-9a-f]{64}$")

    def test_each_disabled_tool_in_the_catalog_is_refused(self):
        for name in ("ha_write_file", "ha_delete_file", "ha_config_set_yaml"):
            with self.subTest(tool=name):
                with self.assertRaisesRegex(lane.Refusal,
                                            "^peer_catalog_advertises_a_disabled_tool$"):
                    self.check({"tool_names_in_wire_order": [*SYNTHETIC_CATALOG, name]})

    def test_a_catalog_without_the_yaml_read_or_unreadable_is_refused(self):
        with self.assertRaisesRegex(lane.Refusal, "^peer_catalog_lacks_the_yaml_read$"):
            self.check({"tool_names_in_wire_order": ["ha_get_state"]})
        for value in ({}, {"tool_names_in_wire_order": "ha_get_state"},
                      {"tool_names_in_wire_order": [1]}):
            with self.subTest(value=value):
                with self.assertRaisesRegex(lane.Refusal, "^peer_catalog_unreadable$"):
                    self.check(value)


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


def inventory_rows(version, **core_versions):
    """Requirement rows as the inventory writes them: ``mcp`` from Core, two missing."""
    rows = []
    for requirement in lane.COMPONENT_REQUIREMENTS[version]:
        name = lane.parse_requirement(requirement)[0]
        installed = core_versions.get(name, "2.3.0" if name == "mcp" else None)
        source = ("pinned ha-mcp image" if name == "ha-mcp"
                  else "core image" if installed is not None else "missing")
        rows.append({"requirement": requirement, "core_image_version": installed,
                     "source": source})
    return rows


# The two N32 pins as ``read_wheels`` reports them; no wheel file is read.
WHEELS = {"wheels": [{"distribution": pin["distribution"], "version": pin["version"]}
                     for pin in lane.N32_WHEELS]}


class ProfileTests(unittest.TestCase):
    def inventory(self, scratch, rows=None, **changes):
        reports = {}
        for version in lane.VERSIONS:
            found = (rows or {}).get(version) or inventory_rows(version)
            reports[version] = {"version": version, "requirements": found,
                                "missing": [row["requirement"] for row in found
                                            if row["source"] == "missing"]}
        receipt = {"command": "inventory", "result": "PASS",
                   "bindings": {"core": {"image": IMAGE["image"]}}, "profile_b": reports,
                   "package_source_in_image": {
                       version: {"path": lane.PACKAGE_SOURCE, "files": 3, "bytes": 30}
                       for version in lane.VERSIONS},
                   "upstream_python": {version: ["3.14"] for version in lane.VERSIONS},
                   "core_python": ["3.14"]}
        receipt.update(changes)
        # One file per call: a test may hold several inventories at once.
        path = Path(scratch) / f"receipt-{len(list(Path(scratch).iterdir()))}.json"
        path.write_text(json.dumps(receipt), encoding="utf-8")
        return path

    def refusal(self, *args):
        with self.assertRaises(lane.Refusal) as raised:
            lane.require_profile_b(*args)
        return str(raised.exception)

    def test_the_committed_inventory_refuses_profile_b_without_the_wheels(self):
        for version in lane.VERSIONS:
            with self.subTest(version=version):
                self.assertEqual(self.refusal(lane.core_binding(), version),
                                 "profile_b_core_requirement_missing")

    def test_the_committed_inventory_with_the_two_n32_wheels_is_feasible(self):
        for version in lane.VERSIONS:
            with self.subTest(version=version):
                built = lane.require_profile_b(lane.core_binding(), version, WHEELS)
                self.assertEqual([row["source"] for row in built["requirements"]],
                                 ["core image", lane.WHEEL_SOURCE, lane.WHEEL_SOURCE,
                                  "pinned ha-mcp image"])
                self.assertEqual([row.get("wheel_version") for row in built["requirements"]],
                                 [None, "0.19.1", "0.4.1", None])
                self.assertEqual(built["package_source"]["path"], lane.PACKAGE_SOURCE)
                self.assertEqual(built["inventory"],
                                 "docs/evidence/hamcp371/s5/inventory-3/receipt.json")
                self.assertEqual(built["core_python"], ["3.14"])

    def test_a_missing_failed_or_stale_inventory_is_refused(self):
        with tempfile.TemporaryDirectory() as scratch:
            def refused(path):
                return self.refusal(IMAGE, "9.2.0", WHEELS, path)
            self.assertEqual(lane.require_profile_b(
                IMAGE, "9.2.0", WHEELS, self.inventory(scratch))["package_source"],
                {"path": lane.PACKAGE_SOURCE, "files": 3, "bytes": 30})
            reordered = inventory_rows("9.2.0")[::-1]
            cases = (
                (Path(scratch) / "absent.json", "profile_b_inventory_missing"),
                (self.inventory(scratch, result="WITHHELD"), "profile_b_inventory_missing"),
                (self.inventory(scratch, command="run"), "profile_b_inventory_missing"),
                (self.inventory(scratch, profile_b={}), "profile_b_inventory_missing"),
                (self.inventory(scratch, bindings={"core": {"image": "synthetic.invalid/other"}}),
                 "profile_b_inventory_stale"),
                (self.inventory(scratch, rows={"9.2.0": reordered}), "profile_b_inventory_stale"),
                (self.inventory(scratch, package_source_in_image={}),
                 "profile_b_inventory_missing"),
                (self.inventory(scratch, package_source_in_image={
                    "9.2.0": {"path": "/synthetic/", "files": 3, "bytes": 30}}),
                 "profile_b_inventory_missing"))
            for path, token in cases:
                with self.subTest(token=token, path=path.name):
                    self.assertEqual(refused(path), token)
            stale = json.loads(self.inventory(scratch).read_text(encoding="utf-8"))
            stale["profile_b"]["9.2.0"]["missing"] = []
            path = Path(scratch) / "stale.json"
            path.write_text(json.dumps(stale), encoding="utf-8")
            self.assertEqual(refused(path), "profile_b_inventory_stale")

    def test_a_wheel_must_meet_its_requirement_and_never_shadow_the_core_image(self):
        with tempfile.TemporaryDirectory() as scratch:
            low = {"wheels": [dict(WHEELS["wheels"][0], version="0.17.9"),
                              WHEELS["wheels"][1]]}
            self.assertEqual(self.refusal(IMAGE, "9.2.0", low, self.inventory(scratch)),
                             "n32_wheel_does_not_meet_requirement")
            for name, installed in (("ruamel-yaml", "0.18.6"), ("ha-mcp", "9.2.0")):
                with self.subTest(name=name):
                    path = self.inventory(
                        scratch, rows={"9.2.0": inventory_rows("9.2.0", **{name: installed})})
                    self.assertEqual(self.refusal(IMAGE, "9.2.0", WHEELS, path),
                                     "overlay_distribution_in_core_image")
            # Only one wheel: the other requirement is still missing.
            one = {"wheels": WHEELS["wheels"][:1]}
            self.assertEqual(self.refusal(IMAGE, "9.2.0", one, self.inventory(scratch)),
                             "profile_b_core_requirement_missing")

    def test_the_two_pins_are_the_two_missing_requirements_at_versions_that_meet_them(self):
        missing = [row["requirement"] for row in inventory_rows("9.2.0")
                   if row["source"] == "missing"]
        self.assertEqual([pin["requirement"] for pin in lane.N32_WHEELS], missing)
        for pin in lane.N32_WHEELS:
            with self.subTest(pin=pin["file"]):
                self.assertTrue(lane.satisfied(pin["requirement"], pin["version"]))
                self.assertRegex(pin["sha256"], r"^[0-9a-f]{64}$")
                self.assertTrue(pin["source"].startswith("https://files.pythonhosted.org/"))
                self.assertTrue(pin["source"].endswith("/" + pin["file"]))
                self.assertEqual(pin["tops"][1], pin["file"].split("-py3-")[0] + ".dist-info")
        self.assertFalse({lane.normalized(pin["distribution"]) for pin in lane.N32_WHEELS}
                         & {lane.normalized(name) for name in lane.OTHER_REQUIREMENTS})

    def test_the_groups_are_the_calls_modules_and_profile_a_leaves_the_yaml_read_out(self):
        every = (lane.CAPTURE_GROUPS + lane.RESTART_GROUPS + lane.PROFILE_B_GROUPS
                 + lane.FORCED_GROUPS + lane.ORDER_GROUPS + lane.PROBE_GROUPS)
        self.assertEqual(sorted(every), sorted(lane.calls_step.GROUPS))
        self.assertEqual(len(every), len(set(every)))
        self.assertNotIn("yaml", lane.CAPTURE_GROUPS + lane.RESTART_GROUPS)
        self.assertNotIn("yaml", lane.RECAPTURE_GROUPS)
        self.assertIn(lane.ARGUMENT_POLICY, lane.VERSIONS)

    def test_only_recapture_selects_the_forced_and_order_cases(self):
        run_selects = set(lane.CAPTURE_GROUPS + lane.RESTART_GROUPS)
        self.assertFalse(run_selects & set(lane.FORCED_GROUPS + lane.ORDER_GROUPS))
        self.assertEqual(lane.RECAPTURE_GROUPS,
                         lane.CAPTURE_GROUPS + lane.FORCED_GROUPS + lane.ORDER_GROUPS)
        forced = [case for case in lane.calls_step.CASES if case["group"] == "forced"]
        self.assertEqual([case["id"] for case in forced], ["f-invalid-json"])

    def test_only_probes_selects_the_review_probes(self):
        others = set(lane.CAPTURE_GROUPS + lane.RESTART_GROUPS + lane.PROFILE_B_GROUPS
                     + lane.RECAPTURE_GROUPS)
        self.assertFalse(others & set(lane.PROBE_GROUPS))
        self.assertEqual(lane.PROBE_GROUPS, ("review_probes",))

    def test_run_needs_a_profile_and_selftest_takes_none(self):
        common = ["--run-dir", "synthetic", "--launch-log", "synthetic"]
        for argv in (["run", *common], ["run", *common, "--profile", "C"],
                     ["selftest", *common, "--profile", "A"],
                     ["recapture", *common, "--profile", "A"],
                     ["probes", *common, "--profile", "A"],
                     ["probes", *common, "--version", "9.2.0"]):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    lane.main(argv)

    def test_the_receipt_states_what_is_not_built(self):
        text = " ".join(lane.NOT_BUILT)
        for phrase in ("profile B overlay", "Supervisor facade", "token revocation"):
            self.assertIn(phrase, text)


SYNTHETIC_CATALOG = ("ha_get_state", "ha_search", "ha_config_get_yaml", "ha_list_files")


def fake_digest(image):
    return "sha256:" + hashlib.sha256(image.encode("utf-8")).hexdigest()


class FakeDocker:
    """A synthetic stand-in for the Docker command line: state in memory, nothing started."""

    GOOD_PROBE = {"interfaces": ["lo"], "ipv4_routes": 0, "egress_errno": 101}

    def __init__(self, *, probe=None, peer_log=b"synthetic peer log\n", seed_ok=True,
                 calls_fail=(), core_log=b"synthetic log\n", component="9.2.0", export=b"",
                 resolved=None, catalog=None, env_filter=None):
        self.calls, self.events, self.forwarded = [], [], []
        # The tool names each peer start's tools/list observation reports.
        self.catalog = catalog or (lambda container: list(SYNTHETIC_CATALOG))
        # What Docker would report as a container's environment, from its --env.
        self.env_filter = env_filter or (lambda name, env: env)
        self.calls_fail = set(calls_fail)
        self.containers, self.volumes, self.streams = {}, {}, []
        self.probe = probe or (lambda container, count: dict(self.GOOD_PROBE))
        self.probes, self.peer_log, self.seed_ok, self.out_root = 0, peer_log, seed_ok, None
        # Profile B: Core's log, the component version the holder's check reports,
        # the ``docker export`` stream of the package container, what Core resolves.
        self.core_log, self.component, self.export = core_log, component, export
        self.resolved = resolved or (lambda version: synthetic_resolved(version))

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
                       "Labels": dict(item.split("=", 1) for item in values("--label")),
                       "Env": self.env_filter(
                           name, [item for item in values("--env") if "=" in item])},
            "State": {"Running": args[1] == "run", "OOMKilled": False,
                      "Status": "running" if args[1] == "run" else "created"},
            "HostConfig": {
                "Memory": lane.s4.host.memory_bytes(values("--memory")[0]),
                "PidsLimit": int(values("--pids-limit")[0]) if values("--pids-limit") else None,
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
        while rest[0] in ("--env", "--workdir"):
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
        if lane.COMPONENT_CHECK in rest:
            # url, mode, limit, out: the report goes where the holder would write it.
            mode, out = rest[rest.index(lane.COMPONENT_CHECK) + 2], rest[-1]
            self.events.append("component:" + mode)
            if env.get("HOMEASSISTANT_TOKEN") != SYNTHETIC_TOKEN:
                return 1, b""
            self.host_path(out).write_text(json.dumps(synthetic_component_report(
                mode, self.component)), encoding="utf-8")
            return 0, b'{"ok": true}\n'
        if lane.RESOLVED in rest:
            self.events.append("resolved:" + container)
            assert rest[rest.index(lane.RESOLVED) + 1:] == list(lane.RESOLVED_NAMES)
            return 0, json.dumps(self.resolved(self.component)).encode("utf-8")
        script = next((item for item in rest if item.startswith("/lane/scripts/")), None)
        if script and script.endswith("mcp_raw_observation.py"):
            out = rest[rest.index("--output") + 1]
            self.events.append("catalog:" + out)
            names = self.catalog(container)
            self.host_path(out).write_text(json.dumps({
                "tool_count": len(names), "tool_names_in_wire_order": names}),
                encoding="utf-8")
            return 0, b""
        if script and script.endswith("core_read_replay_seed.py"):
            access = "--phase" in rest and rest[rest.index("--phase") + 1] == "access"
            self.events.append("seed:access" if access else "seed")
            out = self.host_path(rest[rest.index("--out") + 1] + "/x").parent
            (out / "seed-report.json").write_text(json.dumps({
                "all_steps_ok": self.seed_ok, "tokens_issued": list(lane.TOKEN_NAMES),
                "phase": "access" if access else "all",
                "steps": [{"step": "onboard", "status": "ok"}]}), encoding="utf-8")
            if self.seed_ok and not access:
                (out / "seed.json").write_text("{}", encoding="utf-8")
            tokens = {"administrator": SYNTHETIC_TOKEN, "ordinary_user": OTHER_TOKEN}
            line = json.dumps({"all_steps_ok": self.seed_ok, "tokens": tokens}) + "\n"
            return (0 if self.seed_ok else 1), line.encode("ascii")
        if script and script.endswith("core_read_replay_calls.py"):
            if "--help" in rest:
                self.events.append("calls-help")
                return 0, b""
            target = rest[rest.index("--out") + 1]
            self.events.append("calls:" + target + ":" + rest[rest.index("--groups") + 1]
                               + ("" if "--seed" in rest else ":no-seed"))
            if target in self.calls_fail:
                return 1, b""
            out = self.host_path(target)
            out.mkdir(parents=False)
            (out / "_index.json").write_text(json.dumps({"sessions_opened": 1, "calls": [
                {"id": "synthetic", "is_error": False, "exchange_failure": None},
                {"id": "synthetic-state", "upstream_tool": "ha_get_state",
                 "file": "synthetic-state.json", "sha256": "0" * 64, "is_error": False,
                 "exchange_failure": None, "arguments": {"synthetic": True}},
                {"id": "synthetic-yaml", "upstream_tool": lane.YAML_READ,
                 "file": "synthetic-yaml.json", "sha256": "1" * 64, "is_error": True,
                 "exchange_failure": None}]}),
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
            self.containers[args[2]]["State"].update(Running=True, Status="running")
            self.events.append("start:" + args[2])
            return 0, b""
        if args[1] == "restart":
            self.events.append("restart:" + args[-1])
            return 0, b""
        if args[1] == "logs":
            if "-peer-" in args[2]:
                return 0, self.peer_log
            return 0, self.core_log if args[2].endswith("-core") else b"synthetic log\n"
        if args[1] == "rm":
            del self.containers[args[-1]]
            self.events.append("rm:" + args[-1])
            return 0, b""
        raise AssertionError(args[:4])

    def popen(self, argv, **kwargs):
        """``docker export`` of a created container: the configured stream, once."""
        assert argv[:2] == ["docker", "export"] and len(argv) == 3, argv
        assert kwargs.get("stdout") is lane.subprocess.PIPE, kwargs
        self.calls.append(list(argv))
        data = self.containers.get(argv[2])
        assert data is not None and data["State"]["Status"] == "created", argv
        self.events.append("export:" + argv[2])
        stream = io.BytesIO(self.export(data["Config"]["Image"]) if callable(self.export)
                            else self.export)
        return types.SimpleNamespace(stdout=stream, wait=lambda timeout=None: 0)


def synthetic_component_report(mode, component):
    """What the holder's component check writes when setup and both reads succeed."""
    report = {"mode": mode, "ok": True,
              "entries": [{"domain": lane.COMPONENT_DOMAIN, "title": "synthetic",
                           "source": "user", "state": "loaded", "disabled_by": None,
                           "reason": None}],
              "components": {"webhook": True, "ha_mcp_tools": True},
              "manifest": {"success": True, "error_code": None, "result": {
                  "version": component, "is_built_in": False,
                  "requirements": list(lane.COMPONENT_REQUIREMENTS[component])}},
              "info": {"success": True, "error_code": None, "result": {
                  "component_version": component, "schema_version": 1,
                  "tools_services": ["synthetic"], "webhook_auth": "synthetic",
                  "timezone": "synthetic", "capabilities": {}, "limits": {},
                  "unlisted": "synthetic-not-kept"}}}
    if mode == "setup":
        report["flow"] = {"menu_step": "user", "menu_options": ["tools"], "form_step": "tools",
                          "fields": [], "result_type": "create_entry", "title": "synthetic"}
    return report


def synthetic_resolved(version):
    """Core resolves the three from the overlay and ``mcp`` from its own image."""
    found = {name: None for name in lane.RESOLVED_NAMES}
    found["ha-mcp"] = {"version": version, "location": lane.OVERLAY_TARGET}
    for pin in lane.N32_WHEELS:
        found[pin["requirement"].split(">=")[0]] = {"version": pin["version"],
                                                    "location": lane.OVERLAY_TARGET}
    found["mcp"] = {"version": "2.3.0", "location": "/" + SITE.rstrip("/")}
    return {"distributions": found, "core_python_path": [lane.OVERLAY_TARGET]}


class LaneFlowTests(unittest.TestCase):
    """The whole lane against the synthetic Docker above; no process is started."""

    def setUp(self):
        # Historical capture-lane coverage; shipped default refuses (HAMCP-437).
        patcher = baseline.patch_binding()
        patcher.start()
        self.addCleanup(patcher.stop)

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
        # Every peer start checks its flags and reads its catalog before any call.
        self.assertEqual([item for item in events if item.startswith("catalog:")], [
            "catalog:/out/catalog/peer-9.1.2-1.json", "catalog:/out/catalog/peer-9.2.0-2.json"])
        for version, number in (("9.1.2", 1), ("9.2.0", 2)):
            capture = receipt["captures"][version]
            self.assertEqual(capture["peer_flags"], dict(lane.PRODUCTION_FLAGS))
            self.assertEqual(capture["peer_catalog"]["file"], f"peer-{version}-{number}.json")
            self.assertLess(events.index(f"catalog:/out/catalog/peer-{version}-{number}.json"),
                            events.index(f"calls:/out/{version}/calls:reads,variants,errors,"
                                         "transport"))
        self.assertEqual(receipt["peer_flags"]["set"],
                         [list(item) for item in lane.PRODUCTION_FLAGS])
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

    def test_recapture_runs_both_versions_either_side_of_one_core_restart(self):
        docker = FakeDocker()
        code, launch, receipt, run_dir = self.launch(docker, "recapture")
        self.assertEqual((code, launch["result"], receipt["result"]), (0, "PASS", "PASS"))
        self.assertEqual((receipt["command"], receipt["profile"]), ("recapture", "A"))
        self.assertIs(receipt["cleanup"]["clean"], True)
        self.assertEqual((docker.containers, docker.volumes), ({}, {}))
        events = docker.events
        stem = "hamcp371-s6-recapture-"
        peers = [item for item in events if item.startswith(("run:", "rm:")) and "-peer-" in item]
        self.assertEqual(peers, [
            f"run:{stem}peer-9-1-2", f"rm:{stem}peer-9-1-2",
            f"run:{stem}peer-9-2-0", f"rm:{stem}peer-9-2-0",
            f"run:{stem}peer-9-1-2", f"rm:{stem}peer-9-1-2",
            f"run:{stem}peer-9-2-0", f"rm:{stem}peer-9-2-0"])
        after = "reads,variants,errors,transport,forced,order"
        self.assertEqual([item for item in events if item.startswith("calls:")], [
            "calls:/out/9.1.2/order-before-core-restart:order",
            "calls:/out/9.2.0/order-before-core-restart:order",
            "calls:/out/9.1.2/calls-after-core-restart:" + after,
            "calls:/out/9.2.0/calls-after-core-restart:" + after])
        # One restart, with no peer in the namespace, between the two halves.
        self.assertEqual(events.count(f"restart:{stem}core"), 1)
        restart = events.index(f"restart:{stem}core")
        self.assertEqual(len([item for item in events[:restart] if item.startswith("rm:")]), 2)
        self.assertFalse(any(item.startswith("run:") for item in events[restart:]
                             if "-peer-" not in item))
        self.assertEqual(sorted(receipt["captures"]["before_core_restart"]), ["9.1.2", "9.2.0"])
        self.assertEqual(sorted(receipt["captures"]["after_core_restart"]), ["9.1.2", "9.2.0"])
        self.assertEqual(receipt["captures"]["design"], lane.RECAPTURE_DESIGN)
        self.assertIn("network_after_core_restart", receipt["captures"])
        self.assertEqual(
            receipt["captures"]["after_core_restart"]["9.2.0"]["calls"]["groups"],
            list(lane.RECAPTURE_GROUPS))
        sums = (run_dir / "SHA256SUMS").read_text(encoding="utf-8")
        for name in ("peer-9.1.2-before-core-restart.log", "peer-9.2.0-after-core-restart.log",
                     "out/9.1.2/order-before-core-restart/_index.json",
                     "out/9.2.0/calls-after-core-restart/_index.json"):
            self.assertIn("  " + name + "\n", sums)
        for path in run_dir.rglob("*"):
            if path.is_file():
                self.assertNotIn(SYNTHETIC_TOKEN.encode(), path.read_bytes())
                self.assertNotIn(OTHER_TOKEN.encode(), path.read_bytes())

    def test_a_failed_recapture_step_withholds_and_nothing_later_runs_or_reruns(self):
        docker = FakeDocker(calls_fail={"/out/9.1.2/calls-after-core-restart"})
        code, launch, receipt, _run_dir = self.launch(docker, "recapture")
        self.assertEqual((code, launch["result"], receipt["failure"]),
                         (1, "WITHHELD", "calls_step_failed"))
        calls = [item for item in docker.events if item.startswith("calls:")]
        self.assertEqual(len(calls), 3)
        self.assertTrue(calls[-1].startswith("calls:/out/9.1.2/calls-after-core-restart:"))
        restart = docker.events.index("restart:hamcp371-s6-recapture-core")
        self.assertNotIn("run:hamcp371-s6-recapture-peer-9-2-0", docker.events[restart:])
        self.assertEqual(docker.events.count("restart:hamcp371-s6-recapture-core"), 1)
        self.assertIs(receipt["cleanup"]["clean"], True)
        self.assertEqual((docker.containers, docker.volumes), ({}, {}))

    def test_probes_run_with_both_versions_on_a_core_with_no_seeded_object(self):
        docker = FakeDocker()
        code, launch, receipt, run_dir = self.launch(docker, "probes")
        self.assertEqual((code, launch["result"], receipt["result"]), (0, "PASS", "PASS"))
        self.assertEqual((receipt["command"], receipt["profile"]), ("probes", "A"))
        self.assertEqual(sorted(receipt["captures"]), ["9.1.2", "9.2.0", "design"])
        self.assertEqual(receipt["captures"]["design"], lane.PROBE_DESIGN)
        events = docker.events
        # The access phase only: no full seed, no seed file, before the first peer.
        self.assertEqual([item for item in events if item.startswith("seed")], ["seed:access"])
        self.assertFalse((run_dir / "out" / "seed" / "seed.json").exists())
        self.assertLess(events.index("seed:access"),
                        events.index("run:hamcp371-s6-probes-peer-9-1-2"))
        # Each version once, one peer at a time, the probe group alone and no seed file.
        peers = [item for item in events if item.startswith(("run:", "rm:")) and "-peer-" in item]
        self.assertEqual(peers, [
            "run:hamcp371-s6-probes-peer-9-1-2", "rm:hamcp371-s6-probes-peer-9-1-2",
            "run:hamcp371-s6-probes-peer-9-2-0", "rm:hamcp371-s6-probes-peer-9-2-0"])
        self.assertEqual([item for item in events if item.startswith("calls:")], [
            "calls:/out/9.1.2/review-probes:review_probes:no-seed",
            "calls:/out/9.2.0/review-probes:review_probes:no-seed"])
        self.assertNotIn("restart:hamcp371-s6-probes-core", events)
        for version, number in (("9.1.2", 1), ("9.2.0", 2)):
            capture = receipt["captures"][version]
            self.assertEqual(capture["peer_flags"], dict(lane.PRODUCTION_FLAGS))
            self.assertEqual(capture["calls"]["groups"], ["review_probes"])
            self.assertLess(events.index(f"catalog:/out/catalog/peer-{version}-{number}.json"),
                            events.index(f"calls:/out/{version}/review-probes:review_probes:"
                                         "no-seed"))
        self.assertEqual(receipt["cleanup"]["clean"], True)
        self.assertEqual((docker.containers, docker.volumes), ({}, {}))
        self.assertEqual({token for _argv, token in docker.forwarded}, {SYNTHETIC_TOKEN})
        for path in run_dir.rglob("*"):
            if path.is_file():
                self.assertNotIn(SYNTHETIC_TOKEN.encode(), path.read_bytes())
                self.assertNotIn(OTHER_TOKEN.encode(), path.read_bytes())
        sums = (run_dir / "SHA256SUMS").read_text(encoding="utf-8")
        for name in ("receipt.json", "peer-9.1.2.log", "peer-9.2.0.log",
                     "out/seed/seed-report.json", "out/9.2.0/review-probes/_index.json"):
            self.assertIn("  " + name + "\n", sums)

    def test_a_failed_probe_step_withholds_and_the_later_version_never_starts(self):
        docker = FakeDocker(calls_fail={"/out/9.1.2/review-probes"})
        code, launch, receipt, _run_dir = self.launch(docker, "probes")
        self.assertEqual((code, launch["result"], receipt["failure"]),
                         (1, "WITHHELD", "calls_step_failed"))
        self.assertEqual(len([item for item in docker.events if item.startswith("calls:")]), 1)
        self.assertNotIn("run:hamcp371-s6-probes-peer-9-2-0", docker.events)
        self.assertIs(receipt["cleanup"]["clean"], True)
        self.assertEqual((docker.containers, docker.volumes), ({}, {}))

    def test_a_probe_run_whose_access_phase_fails_starts_no_peer(self):
        docker = FakeDocker(seed_ok=False)
        code, launch, receipt, _run_dir = self.launch(docker, "probes")
        self.assertEqual((code, launch["result"], receipt["failure"]),
                         (1, "WITHHELD", "seed_incomplete"))
        self.assertFalse([item for item in docker.events if "-peer-" in item])
        self.assertIs(receipt["cleanup"]["clean"], True)

    def test_a_peer_advertising_a_disabled_tool_withholds_before_its_calls(self):
        docker = FakeDocker(catalog=lambda container: (
            [*SYNTHETIC_CATALOG, "ha_write_file"] if container.endswith("holder")
            and any("peer-9-2-0" in name for name in docker.containers)
            else list(SYNTHETIC_CATALOG)))
        code, launch, receipt, _run_dir = self.launch(docker, "run", "--profile", "A")
        self.assertEqual((code, launch["result"], receipt["failure"]),
                         (1, "WITHHELD", "peer_catalog_advertises_a_disabled_tool"))
        self.assertFalse(any(item.startswith("calls:/out/9.2.0/") for item in docker.events))
        self.assertIs(receipt["cleanup"]["clean"], True)
        self.assertEqual((docker.containers, docker.volumes), ({}, {}))

    def test_a_peer_without_a_production_flag_withholds_before_its_endpoint(self):
        docker = FakeDocker(env_filter=lambda name, env: [
            item for item in env if not item.startswith("DISABLED_TOOLS=")]
            if "-peer-" in name else env)
        code, launch, receipt, _run_dir = self.launch(docker, "run", "--profile", "A")
        self.assertEqual((code, launch["result"], receipt["failure"]),
                         (1, "WITHHELD", "peer_flags_not_set"))
        self.assertFalse(any(item.startswith(("calls:", "catalog:")) for item in docker.events))
        self.assertIs(receipt["cleanup"]["clean"], True)
        self.assertEqual((docker.containers, docker.volumes), ({}, {}))

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

    def assert_preflight(self, docker, result, token):
        code, launch, receipt, run_dir = result
        self.assertEqual((code, launch["result"], launch["failure"]), (2, "REFUSED", token))
        self.assertIs(launch["execution_started"], False)
        self.assertEqual(docker.calls, [])
        self.assertIsNone(receipt)
        self.assertFalse(run_dir.exists())

    def test_the_profile_b_options_are_all_three_with_profile_b_and_none_without(self):
        cases = (
            (["--profile", "B"], "profile_b_options_missing"),
            (["--profile", "B", "--version", "9.2.0", "--wheel-dir", "synthetic"],
             "profile_b_options_missing"),
            (["--profile", "A", "--version", "9.2.0"], "profile_a_takes_no_profile_b_options"),
            (["--profile", "A", "--upstream-git", "synthetic"],
             "profile_a_takes_no_profile_b_options"))
        for tail, token in cases:
            with self.subTest(tail=tail):
                docker = FakeDocker()
                self.assert_preflight(docker, self.launch(docker, "run", *tail), token)

    def test_profile_b_without_the_wheels_or_the_clone_touches_nothing(self):
        with tempfile.TemporaryDirectory() as scratch:
            docker = FakeDocker()
            self.assert_preflight(docker, self.launch(
                docker, "run", "--profile", "B", "--version", "9.2.0", "--wheel-dir", scratch,
                "--upstream-git", str(Path(scratch) / "synthetic.git")), "n32_wheel_missing")
            # The wheels are read; the clone that is not there is not.
            pins = synthetic_wheels(Path(scratch))
            docker = FakeDocker()
            with mock.patch.object(lane, "N32_WHEELS", pins):
                self.assert_preflight(docker, self.launch(
                    docker, "run", "--profile", "B", "--version", "9.2.0", "--wheel-dir",
                    scratch, "--upstream-git", str(Path(scratch) / "absent.git")),
                    "upstream_clone_read_failed")

    def launch_b(self, docker, version, figures=None):
        """``run --profile B`` with synthetic wheels, component and package figures."""
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        wheel_dir, git_dir = Path(scratch.name) / "wheels", Path(scratch.name) / "synthetic.git"
        wheel_dir.mkdir()
        pins, read = synthetic_wheels(wheel_dir), []
        real = lane.require_profile_b

        def component(path, name, bound):
            read.append((path, name, bound))
            return synthetic_component(name)

        def profile_b(core, wanted, wheels=None):
            # The committed inventory, with the synthetic export's own figures.
            built = real(core, wanted, wheels)
            built["package_source"] = dict(
                built["package_source"], **(figures or package_figures(package_entries(wanted))))
            return built
        with contextlib.ExitStack() as stack:
            for name, value in (("subprocess", fake_subprocess(Popen=docker.popen)),
                                ("N32_WHEELS", pins), ("read_component", component),
                                ("require_profile_b", profile_b)):
                stack.enter_context(mock.patch.object(lane, name, value))
            result = self.launch(docker, "run", "--profile", "B", "--version", version,
                                 "--wheel-dir", str(wheel_dir), "--upstream-git", str(git_dir))
        self.assertEqual(read, [(git_dir, version, lane.s4.binding(version)["source_commit"])])
        return result

    def assert_clean(self, docker, receipt, run_dir):
        self.assertIs(receipt["cleanup"]["clean"], True)
        self.assertEqual((docker.containers, docker.volumes), ({}, {}))
        for argv in docker.calls:
            self.assertNotIn(SYNTHETIC_TOKEN, " ".join(argv))
            self.assertNotIn(OTHER_TOKEN, " ".join(argv))
        for path in run_dir.rglob("*"):
            if path.is_file():
                self.assertNotIn(SYNTHETIC_TOKEN.encode(), path.read_bytes())
                self.assertNotIn(OTHER_TOKEN.encode(), path.read_bytes())

    def test_profile_b_captures_9_2_0_on_its_component_through_the_overlay(self):
        version, stem = "9.2.0", "hamcp371-s6-run-"
        docker = FakeDocker(export=tar_stream(package_entries(version)).getvalue())
        code, launch, receipt, run_dir = self.launch_b(docker, version)
        self.assertEqual((code, launch["result"], receipt["result"]), (0, "PASS", "PASS"))
        self.assert_clean(docker, receipt, run_dir)
        events = docker.events
        # The package is copied from a container that is created, exported and
        # removed before anything else is made.
        self.assertEqual(events[:3], [f"create:{stem}package", f"export:{stem}package",
                                      f"rm:{stem}package"])
        self.assertEqual(events[3], f"run:{stem}holder")
        start = events.index(f"start:{stem}core")
        self.assertEqual([item for item in events if item.startswith("cp:")],
                         [f"cp:{stem}core:/config"] * 2 + [f"cp:{stem}core:/"])
        self.assertLess(events.index(f"cp:{stem}core:/"), start)
        self.assertEqual([item for item in events if item.startswith("component:")],
                         ["component:setup", "component:status"])
        self.assertLess(events.index("seed"), events.index("component:setup"))
        self.assertLess(events.index("component:setup"), events.index(f"run:{stem}peer-9-2-0"))
        self.assertEqual(events.count(f"restart:{stem}core"), 1)
        self.assertLess(events.index(f"restart:{stem}core"), events.index("component:status"))
        self.assertEqual(events.count(f"resolved:{stem}core"), 2)
        self.assertEqual([item for item in events if item.startswith("calls:")], [
            "calls:/out/9.2.0/calls:reads,variants,errors,transport,yaml",
            "calls:/out/9.2.0/calls-after-core-restart:after_restart",
            "calls:/out/9.2.0/yaml-ordinary-user:yaml"])
        self.assertEqual([item for item in events
                          if item.startswith(("run:", "rm:")) and "-peer-" in item], [
            f"run:{stem}peer-9-2-0", f"rm:{stem}peer-9-2-0",
            f"run:{stem}peer-9-2-0-ordinary-user", f"rm:{stem}peer-9-2-0-ordinary-user"])
        # Both peers, the ordinary user's too, check their flags and catalog.
        self.assertEqual([item for item in events if item.startswith("catalog:")], [
            "catalog:/out/catalog/peer-9.2.0-1.json",
            f"catalog:/out/catalog/{lane.ORDINARY_PEER}-2.json"])
        capture = receipt["captures"][version]
        for section in (capture, capture["ordinary_user"]):
            self.assertEqual(section["peer_flags"], dict(lane.PRODUCTION_FLAGS))
            self.assertIn("peer_catalog", section)
        # The ordinary user's token goes to its own peer only, by environment.
        ordinary = [argv for argv, token in docker.forwarded if token == OTHER_TOKEN]
        self.assertEqual(len(ordinary), 1)
        self.assertEqual(ordinary[0][:2], ["docker", "run"])
        self.assertEqual(flag_values(ordinary[0], "--name"), [f"{stem}peer-9-2-0-ordinary-user"])
        self.assertEqual({token for _argv, token in docker.forwarded},
                         {SYNTHETIC_TOKEN, OTHER_TOKEN})
        # Core's environment names the overlay; the receipt holds every stream's hash.
        core = next(argv for argv in docker.calls if argv[:2] == ["docker", "create"]
                    and flag_values(argv, "--name") == [f"{stem}core"])
        self.assertEqual(flag_values(core, "--env"),
                         ["TZ=America/Chicago", lane.OVERLAY_ENVIRONMENT])
        self.assertEqual(receipt["core_container"]["python_path"], [lane.OVERLAY_TARGET])
        section = receipt["profile_b"]
        self.assertEqual(section["version"], version)
        self.assertEqual(section["overlay"]["archives"], {
            "component": {"into": "/config", "bytes": len(docker.streams[1]),
                          "sha256": hashlib.sha256(docker.streams[1]).hexdigest()},
            "overlay": {"into": "/", "bytes": len(docker.streams[2]),
                        "sha256": hashlib.sha256(docker.streams[2]).hexdigest()}})
        self.assertEqual(docker.streams[0], lane.config_archive(lane.seed_step.CONFIG_FILES))
        component = synthetic_component(version)
        self.assertEqual(tar_files(docker.streams[1]), {
            lane.COMPONENT_PATH + "/" + path: body for path, body in component["files"].items()})
        self.assertEqual(section["component"]["records"], component["records"])
        self.assertEqual(section["component"]["source"]["tag"], "v9.2.0")
        prefix = lane.OVERLAY_TARGET.lstrip("/") + "/"
        overlay = tar_files(docker.streams[2])
        self.assertEqual(sorted(path[len(prefix):] for path in overlay), sorted([
            "ruamel/__init__.py", "ruamel_yaml-0.19.1.dist-info/METADATA",
            "ruamel_yaml-0.19.1.dist-info/RECORD", "voluptuous_openapi/__init__.py",
            "voluptuous_openapi-0.4.1.dist-info/METADATA",
            "voluptuous_openapi-0.4.1.dist-info/RECORD", "ha_mcp/__init__.py",
            "ha_mcp/tools/reads.py", "ha_mcp/tools/copy.py",
            "ha_mcp-9.2.0.dist-info/METADATA", "ha_mcp-9.2.0.dist-info/RECORD"]))
        self.assertEqual(overlay[prefix + "ha_mcp/tools/copy.py"], b"synthetic reads\n")
        self.assertEqual(section["wheels"]["records"][0]["wheel"], lane.N32_WHEELS[0]["file"])
        copied = section["package_copy"]["source"]
        self.assertEqual(copied["figures_in_image"], package_figures(package_entries(version)))
        self.assertEqual((copied["files_copied"], copied["site_packages_ha_mcp_members"]), (5, 1))
        self.assertEqual(copied["dist_info"],
                         "/" + VENV + "ha_mcp-9.2.0.dist-info")
        self.assertEqual(sorted(section["phases"]), ["after-core-restart", "after-setup"])
        for phase in section["phases"].values():
            self.assertEqual(phase["component"]["info"]["component_version"], version)
            self.assertNotIn("unlisted", phase["component"]["info"])
            self.assertIsNone(phase["core_log"]["refused"])
            self.assertIs(phase["resolved"]["readable"], True)
        capture = receipt["captures"][version]
        self.assertEqual(capture["token"], "administrator")
        self.assertEqual(capture["calls"]["groups"],
                         list(lane.CAPTURE_GROUPS + lane.PROFILE_B_GROUPS))
        self.assertEqual([row["upstream_tool"] for row in capture["component_reads"]],
                         ["ha_get_state", lane.YAML_READ])
        self.assertEqual(capture["ordinary_user"]["token"], "ordinary_user")
        self.assertEqual(capture["ordinary_user"]["calls"]["groups"], ["yaml"])
        self.assertEqual(receipt["secret_scan"]["needles"], 3)
        sums = (run_dir / "SHA256SUMS").read_text(encoding="utf-8")
        for name in ("receipt.json", "peer-9.2.0.log", "peer-9.2.0-ordinary-user.log",
                     "out/profile-b/component-after-setup.json",
                     "out/profile-b/component-after-core-restart.json",
                     "out/9.2.0/yaml-ordinary-user/_index.json"):
            self.assertIn("  " + name + "\n", sums)

    def test_profile_b_captures_9_1_2_without_an_ordinary_user_peer(self):
        version, stem = "9.1.2", "hamcp371-s6-run-"
        docker = FakeDocker(export=tar_stream(package_entries(version)).getvalue(),
                            component=version)
        code, launch, receipt, run_dir = self.launch_b(docker, version)
        self.assertEqual((code, launch["result"], receipt["result"]), (0, "PASS", "PASS"))
        self.assert_clean(docker, receipt, run_dir)
        self.assertEqual([item for item in docker.events
                          if item.startswith(("run:", "rm:")) and "-peer-" in item],
                         [f"run:{stem}peer-9-1-2", f"rm:{stem}peer-9-1-2"])
        self.assertEqual([item for item in docker.events if item.startswith("calls:")], [
            "calls:/out/9.1.2/calls:reads,variants,errors,transport,yaml",
            "calls:/out/9.1.2/calls-after-core-restart:after_restart"])
        self.assertEqual({token for _argv, token in docker.forwarded}, {SYNTHETIC_TOKEN})
        self.assertNotIn("ordinary_user", receipt["captures"][version])
        self.assertEqual(sorted(receipt["captures"]), [version])

    def test_a_core_that_asks_for_an_install_withholds_before_any_peer(self):
        for line, token in (
                (b"WARNING Unable to install package ruamel.yaml==0.19.1\n",
                 "core_requested_a_requirement"),
                (b"ERROR Unable to install package cyclopts\n", "core_requested_one_of_the_ten"),
                (b"ERROR No module named 'synthetic'\n", "core_reported_a_missing_module")):
            with self.subTest(token=token):
                docker = FakeDocker(export=tar_stream(package_entries("9.2.0")).getvalue(),
                                    core_log=b"synthetic log\n" + line)
                code, launch, receipt, run_dir = self.launch_b(docker, "9.2.0")
                self.assertEqual((code, launch["result"], receipt["failure"]),
                                 (1, "WITHHELD", token))
                self.assertFalse(any("-peer-" in item for item in docker.events))
                phase = receipt["profile_b"]["phases"]["after-setup"]
                self.assertEqual(phase["core_log"]["refused"], token)
                self.assertEqual(phase["component"]["info"]["component_version"], "9.2.0")
                self.assert_clean(docker, receipt, run_dir)

    def test_a_package_copy_unlike_the_inventory_withholds_before_the_holder(self):
        docker = FakeDocker(export=tar_stream(package_entries("9.2.0")).getvalue())
        code, launch, receipt, run_dir = self.launch_b(docker, "9.2.0",
                                                       figures={"files": 4, "bytes": 44})
        self.assertEqual((code, launch["result"], receipt["failure"]),
                         (1, "WITHHELD", "package_copy_does_not_match_inventory"))
        self.assertEqual(docker.events, ["create:hamcp371-s6-run-package",
                                         "export:hamcp371-s6-run-package",
                                         "rm:hamcp371-s6-run-package"])
        self.assertIsNone(receipt["profile_b"]["package_copy"])
        self.assert_clean(docker, receipt, run_dir)

    def test_a_component_of_another_version_withholds_before_any_peer(self):
        docker = FakeDocker(export=tar_stream(package_entries("9.2.0")).getvalue(),
                            component="9.1.2")
        code, launch, receipt, run_dir = self.launch_b(docker, "9.2.0")
        self.assertEqual((code, launch["result"], receipt["failure"]),
                         (1, "WITHHELD", "component_version_mismatch"))
        self.assertFalse(any("-peer-" in item for item in docker.events))
        self.assert_clean(docker, receipt, run_dir)

    def test_a_failed_ordinary_user_read_is_reported_and_not_run_again(self):
        docker = FakeDocker(export=tar_stream(package_entries("9.2.0")).getvalue(),
                            calls_fail={"/out/9.2.0/yaml-ordinary-user"})
        code, launch, receipt, run_dir = self.launch_b(docker, "9.2.0")
        self.assertEqual((code, launch["result"], receipt["failure"]),
                         (1, "WITHHELD", "calls_step_failed"))
        self.assertEqual(len([item for item in docker.events if item.startswith("calls:")]), 3)
        capture = receipt["captures"]["9.2.0"]
        self.assertEqual(len(capture["component_reads"]), 2)
        self.assertEqual(capture["ordinary_user"]["token"], "ordinary_user")
        self.assertNotIn("calls", capture["ordinary_user"])
        # The ordinary peer still running at the failure is logged and removed.
        self.assertTrue((run_dir / "peer-9.2.0-ordinary-user.log").exists())
        self.assert_clean(docker, receipt, run_dir)

    def test_a_name_already_in_use_is_a_preflight_refusal(self):
        docker = FakeDocker()
        docker.volumes["hamcp371-s6-run-config"] = {"Labels": {}}
        code, launch, _receipt, run_dir = self.launch(docker, "run", "--profile", "A")
        self.assertEqual((code, launch["failure"]), (2, "disposable_name_already_exists"))
        self.assertFalse(run_dir.exists())
        self.assertEqual(docker.volumes, {"hamcp371-s6-run-config": {"Labels": {}}})

    def test_the_shipped_registry_refuses_the_9_1_2_baseline_at_the_entry_point(self):
        # The lane's own binding, not the frozen one: the shipped registry has no
        # 9.1.2 row after HAMCP-437, so the run is a preflight refusal.
        self.assertIs(baseline.s4, lane.s4)
        docker = FakeDocker()
        with mock.patch.object(lane.s4, "binding", baseline.SHIPPED_BINDING):
            code, launch, receipt, run_dir = self.launch(docker, "run", "--profile", "A")
        self.assertEqual((code, launch["result"], launch["failure"]),
                         (2, "REFUSED", "baseline_registry_row_missing"))
        self.assertFalse(launch["execution_started"])
        self.assertIsNone(receipt)
        self.assertFalse(run_dir.exists())


# --- Profile B sources: synthetic export streams, wheels and component clones ---

def tar_stream(entries):
    """A synthetic tar stream: ``(name, kind, value)``, kind ``file|dir|link|symlink``."""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as archive:
        for name, kind, value in entries:
            info = tarfile.TarInfo(name)
            if kind == "file":
                info.size = len(value)
                archive.addfile(info, io.BytesIO(value))
                continue
            if kind == "dir":
                info.type = tarfile.DIRTYPE
            else:
                info.type = tarfile.LNKTYPE if kind == "link" else tarfile.SYMTYPE
                info.linkname = value
            archive.addfile(info)
    raw.seek(0)
    return raw


def tar_files(raw):
    """Regular-file bodies of one tar stream, by name."""
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r") as archive:
        return {member.name: archive.extractfile(member).read()
                for member in archive.getmembers() if member.isreg()}


VENV = "app/.venv/lib/python3.14/site-packages/"


def package_entries(version, metadata_version=None):
    """An image export with the package source, its dist-info and a site-packages copy."""
    metadata = ("Metadata-Version: 2.4\nName: ha-mcp\nVersion: " + (metadata_version or version)
                + "\n\nsynthetic body\n").encode("ascii")
    return [("app/", "dir", None), ("app/src/ha_mcp/", "dir", None),
            ("app/src/ha_mcp/__init__.py", "file", b"synthetic = 1\n"),
            ("app/src/ha_mcp/tools/reads.py", "file", b"synthetic reads\n"),
            ("app/src/ha_mcp/__pycache__/__init__.cpython-314.pyc", "file", b"synthetic-pyc"),
            ("app/src/ha_mcp/tools/copy.py", "link", "app/src/ha_mcp/tools/reads.py"),
            (VENV + f"ha_mcp-{version}.dist-info/METADATA", "file", metadata),
            (VENV + f"ha_mcp-{version}.dist-info/RECORD", "file", b"synthetic record\n"),
            (VENV + "ha_mcp/__init__.py", "file", b"synthetic site copy\n"),
            ("usr/bin/synthetic", "file", b"synthetic binary")]


def package_figures(entries):
    """What the inventory counts below the package source: files and links, file bytes."""
    kept = [(kind, value) for name, kind, value in entries
            if name.startswith(lane.PACKAGE_SOURCE.lstrip("/")) and kind in ("file", "link")]
    return {"files": len(kept), "bytes": sum(len(value) for kind, value in kept
                                             if kind == "file")}


def fake_subprocess(**over):
    return types.SimpleNamespace(
        PIPE=subprocess.PIPE, DEVNULL=subprocess.DEVNULL,
        SubprocessError=subprocess.SubprocessError, TimeoutExpired=subprocess.TimeoutExpired,
        **over)


def wheel_bytes(members, symlinks=()):
    """A synthetic wheel: ``(name, body)`` members, fixed dates; duplicates allowed."""
    raw = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # A duplicate member name is written on purpose.
        with zipfile.ZipFile(raw, "w") as archive:
            for name, body in members:
                info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
                if name in symlinks:
                    info.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(info, body)
    return raw.getvalue()


def wheel_members(pin, version=None, name=None):
    """A package file and the dist-info; the display name is not the distribution name."""
    top, info = pin["tops"]
    display = name or {"ruamel-yaml": "ruamel.yaml"}.get(pin["distribution"],
                                                          pin["distribution"].replace("-", "_"))
    metadata = (f"Metadata-Version: 2.1\nName: {display}\nVersion: {version or pin['version']}"
                "\n\nsynthetic\n").encode("ascii")
    return [(top + "/__init__.py", b"synthetic\n"), (info + "/METADATA", metadata),
            (info + "/RECORD", b"synthetic\n")]


def synthetic_wheels(directory, members=None, symlinks=()):
    """Both wheel files written; ``members`` replaces the first.  Pins with their real hashes."""
    pins = []
    for index, pin in enumerate(lane.N32_WHEELS):
        first = index == 0
        raw = wheel_bytes(members if first and members is not None else wheel_members(pin),
                          symlinks if first else ())
        (Path(directory) / pin["file"]).write_bytes(raw)
        pins.append(dict(pin, sha256=hashlib.sha256(raw).hexdigest()))
    return tuple(pins)


GIT = shutil.which("git", path="/usr/bin:/bin")
GIT_FIXTURE_ENVIRONMENT = {
    "PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    **{f"GIT_{role}_{field}": value for role in ("AUTHOR", "COMMITTER")
       for field, value in (("NAME", "synthetic"), ("EMAIL", "synthetic@example.invalid"),
                            ("DATE", "2026-01-01T00:00:00+0000"))}}


def git(directory, *args):
    done = subprocess.run(
        [GIT, "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false",
         "-c", "init.defaultBranch=main", "-C", str(directory), *args],
        env=dict(GIT_FIXTURE_ENVIRONMENT), capture_output=True, check=True)
    return done.stdout.decode("ascii").strip()


def component_repo(scratch, component="9.2.0", manifest=None, extra=None, executable=(),
                   links=()):
    """A disposable clone with one annotated tag over a synthetic component tree.

    Returns the git directory and ``COMPONENT_SOURCES[component]`` with this
    clone's own ids.  ``extra`` adds or replaces files; ``None`` removes one.
    """
    top = Path(scratch) / "upstream"
    base = top / lane.COMPONENT_PATH
    body = {"domain": lane.COMPONENT_DOMAIN, "name": "synthetic", "version": component,
            "requirements": list(lane.COMPONENT_REQUIREMENTS[component]),
            "dependencies": ["webhook"], **(manifest or {})}
    files = {"manifest.json": json.dumps(body).encode("utf-8"), "__init__.py": b"synthetic\n",
             "tools/sub.py": b"synthetic sub\n",
             "translations/en.json": '{"title": "synthétique"}\n'.encode("utf-8")}
    for path, value in (extra or {}).items():
        if value is None:
            files.pop(path, None)
        else:
            files[path] = value
    for path, value in files.items():
        (base / path).parent.mkdir(parents=True, exist_ok=True)
        (base / path).write_bytes(value)
        (base / path).chmod(0o755 if path in executable else 0o644)
    for path, target in links:
        (base / path).symlink_to(target)
    (top / "README.md").write_bytes(b"synthetic\n")
    git(top, "init", "-q")
    git(top, "add", "-A")
    git(top, "commit", "-q", "-m", "synthetic")
    tag = lane.COMPONENT_SOURCES[component]["tag"]
    git(top, "tag", "-a", tag, "-m", "synthetic")
    source = dict(lane.COMPONENT_SOURCES[component],
                  tag_object=git(top, "rev-parse", "refs/tags/" + tag),
                  commit=git(top, "rev-parse", "HEAD"),
                  root_tree=git(top, "rev-parse", "HEAD^{tree}"),
                  tree=git(top, "rev-parse", "HEAD:" + lane.COMPONENT_PATH))
    return top / ".git", source


def git_id(kind, body):
    return hashlib.sha1(f"{kind} {len(body)}\0".encode("ascii") + body).hexdigest()


def batch(objects):
    """``git cat-file --batch`` output for ``{oid: (kind, body)}``, in id order."""
    return b"".join(f"{oid} {kind} {len(body)}\n".encode("ascii") + body + b"\n"
                    for oid, (kind, body) in sorted(objects.items()))


def synthetic_component(component):
    """What ``read_component`` returns, without a clone."""
    files = {"manifest.json": json.dumps({"domain": lane.COMPONENT_DOMAIN,
                                          "version": component}).encode("ascii"),
             "__init__.py": b"synthetic\n", "tools/sub.py": b"synthetic sub\n"}
    return {"files": files,
            "records": [{"path": path, "size": len(body),
                         "sha256": hashlib.sha256(body).hexdigest(), "git_blob": "0" * 40}
                        for path, body in sorted(files.items())],
            "manifest": {"domain": lane.COMPONENT_DOMAIN, "version": component,
                         "requirements": list(lane.COMPONENT_REQUIREMENTS[component]),
                         "dependencies": list(lane.COMPONENT_DEPENDENCIES)},
            "source": dict(lane.COMPONENT_SOURCES[component], component=component,
                           path=lane.COMPONENT_PATH, files=len(files),
                           bytes=sum(map(len, files.values())), read="synthetic")}


@unittest.skipUnless(GIT, "git is needed for the disposable component clone")
class ComponentSourceTests(unittest.TestCase):
    """The component tree from a disposable local clone; nothing is fetched."""

    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.scratch = Path(scratch.name)

    def clone(self, component="9.2.0", **repo):
        return component_repo(tempfile.mkdtemp(dir=self.scratch), component, **repo)

    def read(self, component="9.2.0", bound=None, source_changes=None, **repo):
        git_dir, source = self.clone(component, **repo)
        source.update(source_changes or {})
        with mock.patch.dict(lane.COMPONENT_SOURCES, {component: source}):
            return lane.read_component(git_dir, component, bound), source, git_dir

    def refused(self, token, **options):
        with self.assertRaises(lane.Refusal) as caught:
            self.read(**options)
        self.assertEqual(str(caught.exception), token)

    def test_the_tree_is_read_from_the_tag_down_with_a_record_per_file(self):
        for component in ("9.2.0", "2.2.1"):
            with self.subTest(component=component):
                found, source, git_dir = self.read(component)
                self.assertEqual([row["path"] for row in found["records"]], [
                    "__init__.py", "manifest.json", "tools/sub.py", "translations/en.json"])
                for row in found["records"]:
                    body = found["files"][row["path"]]
                    self.assertEqual((row["size"], row["sha256"]),
                                     (len(body), hashlib.sha256(body).hexdigest()))
                    self.assertEqual(row["git_blob"], git(
                        git_dir.parent, "rev-parse", f"HEAD:{lane.COMPONENT_PATH}/{row['path']}"))
                self.assertEqual(found["manifest"], {
                    "domain": lane.COMPONENT_DOMAIN, "version": component,
                    "requirements": list(lane.COMPONENT_REQUIREMENTS[component]),
                    "dependencies": ["webhook"]})
                for key in ("tag", "tag_object", "commit", "root_tree", "tree"):
                    self.assertEqual(found["source"][key], source[key])
                self.assertEqual((found["source"]["component"], found["source"]["path"],
                                  found["source"]["files"], found["source"]["bytes"]),
                                 (component, lane.COMPONENT_PATH, 4,
                                  sum(map(len, found["files"].values()))))
                self.assertIn("synthétique",
                              found["files"]["translations/en.json"].decode("utf-8"))

    def test_a_commit_other_than_the_bound_one_is_refused_before_any_read(self):
        with mock.patch.object(lane, "git_objects", side_effect=AssertionError):
            with self.assertRaises(lane.Refusal) as caught:
                lane.read_component(self.scratch / "absent.git", "9.2.0", "f" * 40)
        self.assertEqual(str(caught.exception), "component_source_not_the_bound_commit")

    def test_every_id_on_the_chain_must_be_the_one_bound(self):
        git_dir, source = self.clone()
        for key, value in (("tag", "v0.0.0"), ("commit", "0" * 39 + "1"),
                           ("root_tree", source["tree"]), ("tree", source["root_tree"]),
                           ("tag_object", source["commit"])):
            with self.subTest(key=key), mock.patch.dict(
                    lane.COMPONENT_SOURCES, {"9.2.0": dict(source, **{key: value})}):
                with self.assertRaises(lane.Refusal) as caught:
                    lane.read_component(git_dir, "9.2.0")
                self.assertEqual(str(caught.exception), "component_source_mismatch")

    def test_the_manifest_must_be_the_one_the_lane_expects(self):
        reversed_requirements = list(reversed(lane.COMPONENT_REQUIREMENTS["9.2.0"]))
        for repo in ({"manifest": {"version": "9.1.2"}},
                     {"manifest": {"requirements": reversed_requirements}},
                     {"manifest": {"dependencies": []}},
                     {"manifest": {"domain": "synthetic"}},
                     {"extra": {"manifest.json": None}},
                     {"extra": {"manifest.json": b"not json"}},
                     {"extra": {"manifest.json": b"[]"}}):
            with self.subTest(repo=repo):
                self.refused("component_manifest_mismatch", **repo)

    def test_an_executable_a_link_or_an_unreviewed_name_is_refused(self):
        for token, repo in (
                ("component_entry_not_reviewed", {"executable": ("__init__.py",)}),
                ("component_entry_not_reviewed", {"links": (("link.py", "__init__.py"),)}),
                ("component_name_not_reviewed", {"extra": {"tools/a\\b.py": b"synthetic\n"}})):
            with self.subTest(repo=repo):
                self.refused(token, **repo)

    def test_the_bounds_on_files_bytes_and_depth_are_refusals(self):
        for name, value in (("COMPONENT_FILES", 1), ("COMPONENT_BYTES", 10),
                            ("COMPONENT_DEPTH", 1)):
            with self.subTest(name=name), mock.patch.object(lane, name, value):
                self.refused("component_above_bounds")

    def test_a_missing_object_an_unreviewed_id_or_no_clone_is_refused(self):
        self.refused("component_object_missing", source_changes={"tag_object": "0" * 40})
        self.refused("component_object_id_not_reviewed", source_changes={"tag_object": "Z" * 40})
        with self.assertRaises(lane.Refusal) as caught:
            lane.read_component(self.scratch / "absent.git", "9.2.0")
        self.assertEqual(str(caught.exception), "upstream_clone_read_failed")


class GitObjectTests(unittest.TestCase):
    """``git cat-file --batch`` output, parsed and checked id by id; no git is run."""

    OBJECTS = {git_id("blob", b"one\n"): ("blob", b"one\n"),
               git_id("blob", b"two\n"): ("blob", b"two\n")}

    def objects(self, output=None, returncode=0, raises=None, ids=None):
        seen = []

        def run(argv, **kwargs):
            seen.append((argv, kwargs))
            if raises is not None:
                raise raises
            return types.SimpleNamespace(returncode=returncode, stderr=b"",
                                         stdout=batch(self.OBJECTS) if output is None else output)
        with mock.patch.object(lane, "subprocess", fake_subprocess(run=run)):
            return lane.git_objects(Path("/synthetic.git"), ids or list(self.OBJECTS)), seen

    def refused(self, token, **options):
        with self.assertRaises(lane.Refusal) as caught:
            self.objects(**options)
        self.assertEqual(str(caught.exception), token)

    def test_objects_are_read_once_each_with_the_fixed_environment(self):
        ids = sorted(self.OBJECTS)
        found, seen = self.objects(ids=[ids[1], ids[0], ids[1]])
        self.assertEqual(found, self.OBJECTS)
        (argv, kwargs), = seen
        self.assertEqual(argv, ["git", "--no-replace-objects", "-c", "protocol.allow=never",
                                "-c", "core.fsmonitor=false", "--git-dir=/synthetic.git",
                                "cat-file", "--batch"])
        self.assertEqual(kwargs, {"cwd": "/", "env": lane.GIT_ENVIRONMENT, "capture_output": True,
                                  "timeout": lane.GIT_SECONDS,
                                  "input": "".join(oid + "\n" for oid in ids).encode("ascii")})
        self.assertEqual(lane.GIT_ENVIRONMENT["GIT_NO_LAZY_FETCH"], "1")

    def test_a_body_that_is_not_its_id_is_refused(self):
        oid = git_id("blob", b"one\n")
        self.refused("component_object_hash_mismatch", ids=[oid],
                     output=f"{oid} blob 4\n".encode("ascii") + b"onf\n\n")

    def test_malformed_output_or_a_failed_git_is_refused(self):
        oid, other = sorted(self.OBJECTS)
        for options in (
                {"output": f"{oid} blob 4\n".encode("ascii") + b"one"},
                {"output": f"{oid} blob 4\n".encode("ascii") + b"one\n\nextra"},
                {"output": f"{other} blob 4\n".encode("ascii") + b"two\n\n"},
                {"output": f"{oid} blob four\n".encode("ascii") + b"one\n\n"},
                {"returncode": 128, "output": b""},
                {"raises": OSError("synthetic")},
                {"raises": subprocess.TimeoutExpired("git", 1)}):
            with self.subTest(options=options):
                self.refused("upstream_clone_read_failed", ids=[oid], **options)

    def test_a_missing_object_is_never_fetched_and_is_refused(self):
        oid = "0" * 40
        self.refused("component_object_missing", ids=[oid], output=f"{oid} missing\n".encode())
        self.refused("component_object_id_not_reviewed", ids=["0" * 39])

    def test_tree_entries_are_parsed_or_refused(self):
        body = (b"100644 a.py\0" + bytes.fromhex("1" * 40)
                + b"40000 sub\0" + bytes.fromhex("2" * 40))
        self.assertEqual(lane.tree_entries(body), [("100644", "a.py", "1" * 40),
                                                   ("40000", "sub", "2" * 40)])
        for token, raw in (("component_tree_unreadable", body[:-1]),
                           ("component_tree_unreadable", b"100644 a.py"),
                           ("component_name_not_reviewed",
                            b"100644 \xff\0" + bytes.fromhex("1" * 40))):
            with self.subTest(raw=raw), self.assertRaises(lane.Refusal) as caught:
                lane.tree_entries(raw)
            self.assertEqual(str(caught.exception), token)


class WheelTests(unittest.TestCase):
    """The two N32 wheels by hash, member by member; nothing is installed."""

    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.directory = Path(scratch.name)

    def read(self, pins):
        with mock.patch.object(lane, "N32_WHEELS", pins):
            return lane.read_wheels(self.directory)

    def refused(self, token, pins):
        with self.assertRaises(lane.Refusal) as caught:
            self.read(pins)
        self.assertEqual(str(caught.exception), token)

    def test_both_wheels_are_read_with_a_record_per_member(self):
        pins = synthetic_wheels(self.directory)
        found = self.read(pins)
        self.assertEqual(sorted(found["files"]), sorted(
            name for pin in pins for name, _body in wheel_members(pin)))
        self.assertEqual([row["wheel"] for row in found["records"]],
                         [pins[0]["file"]] * 3 + [pins[1]["file"]] * 3)
        for row in found["records"]:
            body = found["files"][row["path"]]
            self.assertEqual(row["sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(found["wheels"], [
            {key: pin[key] for key in ("file", "sha256", "source", "version", "distribution",
                                       "requirement")} for pin in pins])

    def test_a_wheel_that_is_absent_or_not_the_pinned_bytes_is_refused(self):
        self.refused("n32_wheel_missing", lane.N32_WHEELS)
        synthetic_wheels(self.directory)
        self.refused("n32_wheel_hash_mismatch", lane.N32_WHEELS)

    def test_a_link_a_directory_a_fifo_an_oversize_or_a_non_zip_is_unreadable(self):
        pins = synthetic_wheels(self.directory)
        first = self.directory / pins[0]["file"]
        first.unlink()
        first.symlink_to(self.directory / pins[1]["file"])
        self.refused("n32_wheel_unreadable", pins)
        first.unlink()
        first.mkdir()
        self.refused("n32_wheel_unreadable", pins)
        first.rmdir()
        os.mkfifo(first)
        self.refused("n32_wheel_unreadable", pins)
        first.unlink()
        pins = synthetic_wheels(self.directory)
        with mock.patch.object(lane, "WHEEL_BYTES", 10):
            self.refused("n32_wheel_unreadable", pins)
        first.write_bytes(b"not a zip")
        self.refused("n32_wheel_unreadable", (dict(pins[0], sha256=hashlib.sha256(
            b"not a zip").hexdigest()), pins[1]))

    def test_a_member_outside_the_reviewed_form_is_refused(self):
        pin = lane.N32_WHEELS[0]
        good = wheel_members(pin)
        for name, symlinks in (("other/x.py", ()), ("ruamel/sub/", ()),
                               ("ruamel/link.py", ("ruamel/link.py",)), ("ruamel", ()),
                               ("ruamel/../x.py", ()), ("ruamel/a\\b.py", ()),
                               ("ruamel//x.py", ())):
            with self.subTest(name=name):
                pins = synthetic_wheels(self.directory, [*good, (name, b"")], symlinks)
                self.refused("n32_wheel_member_not_reviewed", pins)

    def test_the_metadata_must_name_the_pinned_distribution_and_version(self):
        pin = lane.N32_WHEELS[0]
        info = pin["tops"][1] + "/METADATA"
        twice = b"Metadata-Version: 2.1\nName: ruamel.yaml\nName: other\nVersion: 0.19.1\n\n"
        for members in (wheel_members(pin, version="0.18.0"),
                        wheel_members(pin, name="other"),
                        [item for item in wheel_members(pin) if item[0] != info],
                        [*(item for item in wheel_members(pin) if item[0] != info),
                         (info, twice)]):
            with self.subTest(members=members):
                self.refused("n32_wheel_metadata_mismatch",
                             synthetic_wheels(self.directory, members))

    def test_a_member_named_twice_is_a_collision(self):
        pin = lane.N32_WHEELS[0]
        members = wheel_members(pin)
        self.refused("overlay_member_collision",
                     synthetic_wheels(self.directory, [*members, members[0]]))


class PackageMemberTests(unittest.TestCase):
    """The ``ha_mcp`` package from an export stream; nothing is extracted."""

    def members(self, entries, version="9.2.0", figures=None):
        return lane.package_members(tar_stream(entries), version,
                                    figures or package_figures(entries))

    def refused(self, token, entries, **options):
        with self.assertRaises(lane.Refusal) as caught:
            self.members(entries, **options)
        self.assertEqual(str(caught.exception), token)

    def test_the_source_and_dist_info_are_copied_and_compiled_files_counted_only(self):
        entries = package_entries("9.2.0")
        found = self.members(entries)
        self.assertEqual(sorted(found["files"]), [
            "ha_mcp-9.2.0.dist-info/METADATA", "ha_mcp-9.2.0.dist-info/RECORD",
            "ha_mcp/__init__.py", "ha_mcp/tools/copy.py", "ha_mcp/tools/reads.py"])
        self.assertEqual(found["files"]["ha_mcp/tools/copy.py"], b"synthetic reads\n")
        self.assertEqual([row["path"] for row in found["records"]], sorted(found["files"]))
        self.assertEqual(found["source"], {
            "package_source": lane.PACKAGE_SOURCE,
            "dist_info": "/" + VENV + "ha_mcp-9.2.0.dist-info",
            "figures_in_image": {"files": 4, "bytes": 43}, "files_copied": 5,
            "pycache_counted_not_copied": True, "symbolic_links_not_copied": [],
            "site_packages_ha_mcp_members": 1})

    def test_a_markdown_link_to_a_sibling_is_recorded_not_copied_or_counted(self):
        entries = package_entries("9.2.0") + [
            ("app/src/ha_mcp/settings_ui/AGENTS.md", "file", b"synthetic\n"),
            ("app/src/ha_mcp/settings_ui/CLAUDE.md", "symlink", "AGENTS.md")]
        found = self.members(entries, figures={"files": 5, "bytes": 53})
        self.assertNotIn("ha_mcp/settings_ui/CLAUDE.md", found["files"])
        self.assertEqual(found["files"]["ha_mcp/settings_ui/AGENTS.md"], b"synthetic\n")
        self.assertEqual(found["source"]["symbolic_links_not_copied"],
                         [{"path": "ha_mcp/settings_ui/CLAUDE.md", "link": "AGENTS.md"}])
        self.assertEqual(found["source"]["figures_in_image"], {"files": 5, "bytes": 53})

    def test_any_other_symbolic_link_is_refused(self):
        entries = package_entries("9.2.0")
        for name, link in (("app/src/ha_mcp/tools/s.py", "reads.py"),
                           ("app/src/ha_mcp/tools/s.md", "reads.py"),
                           ("app/src/ha_mcp/tools/s.py", "AGENTS.md"),
                           ("app/src/ha_mcp/tools/s.md", "../AGENTS.md"),
                           ("app/src/ha_mcp/tools/s.md", "/etc/AGENTS.md"),
                           ("app/src/ha_mcp/tools/s.md", ".."),
                           ("app/src/ha_mcp/a\\b.md", "AGENTS.md"),
                           (VENV + "ha_mcp-9.2.0.dist-info/s.md", "METADATA.md")):
            with self.subTest(name=name, link=link):
                self.refused("package_member_not_reviewed",
                             entries + [(name, "symlink", link)])

    def test_a_hard_link_to_the_site_packages_copy_resolves(self):
        # The link comes after the site-packages member it names, as in an export.
        entries = [item for item in package_entries("9.2.0")
                   if item[0] != "app/src/ha_mcp/tools/copy.py"]
        entries.append(("app/src/ha_mcp/tools/copy.py", "link", VENV + "ha_mcp/__init__.py"))
        found = self.members(entries)
        self.assertEqual(found["files"]["ha_mcp/tools/copy.py"], b"synthetic site copy\n")

    def test_figures_unlike_the_inventory_are_refused(self):
        entries = package_entries("9.2.0")
        for figures in ({"files": 5, "bytes": 43}, {"files": 4, "bytes": 44}):
            with self.subTest(figures=figures):
                self.refused("package_copy_does_not_match_inventory", entries, figures=figures)

    def test_the_dist_info_must_be_one_and_name_the_bound_version(self):
        entries = package_entries("9.2.0")
        self.refused("package_dist_info_version_mismatch", entries, version="9.1.2")
        self.refused("package_dist_info_version_mismatch",
                     package_entries("9.2.0", metadata_version="9.1.2"))
        other = [(VENV + "ha_mcp-9.1.2.dist-info/METADATA", "file", b"synthetic\n")]
        self.refused("package_dist_info_not_unique", entries + other)
        self.refused("package_dist_info_not_unique",
                     [item for item in entries if ".dist-info/" not in item[0]])

    def test_a_link_out_of_reach_a_symlink_or_an_unreviewed_name_is_refused(self):
        entries = package_entries("9.2.0")
        self.refused("package_member_not_reviewed",
                     entries + [("app/src/ha_mcp/tools/s.py", "symlink", "reads.py")])
        self.refused("package_member_link_unresolved",
                     entries + [("app/src/ha_mcp/tools/bin.py", "link", "usr/bin/synthetic")])
        self.refused("package_member_not_reviewed",
                     entries + [("app/src/ha_mcp/a\\b.py", "file", b"synthetic\n")])
        self.refused("package_member_not_reviewed",
                     entries + [("app/src/ha_mcp/__init__.py", "file", b"synthetic = 1\n")])


class PackageCopyTests(unittest.TestCase):
    """The package container is created, exported and removed; it never starts."""

    UPSTREAM = {"image": "synthetic.invalid/ha-mcp@sha256:" + "3" * 64}

    def copy(self, docker, version="9.2.0", figures=None):
        with contextlib.ExitStack() as stack:
            for target, name, value in ((lane, "command", docker), (lane.s4, "command", docker),
                                        (lane, "subprocess", fake_subprocess(Popen=docker.popen))):
                stack.enter_context(mock.patch.object(target, name, value))
            return lane.copy_package("synthetic-package", RUN, self.UPSTREAM, version,
                                     figures or package_figures(package_entries(version)))

    def refused(self, token, docker, **options):
        with self.assertRaises(lane.Refusal) as caught:
            self.copy(docker, **options)
        self.assertEqual(str(caught.exception), token)
        self.assertEqual(docker.containers, {})

    def test_the_container_is_created_exported_and_removed(self):
        docker = FakeDocker(export=tar_stream(package_entries("9.2.0")).getvalue())
        found = self.copy(docker)
        self.assertEqual(found["source"]["files_copied"], 5)
        self.assertEqual(docker.events, ["create:synthetic-package", "export:synthetic-package",
                                         "rm:synthetic-package"])
        self.assertEqual(docker.containers, {})
        create = docker.calls[0]
        self.assertEqual(create[:2], ["docker", "create"])
        self.assertEqual(flag_values(create, "--pull"), ["never"])
        self.assertEqual(flag_values(create, "--network"), ["none"])
        self.assertEqual(flag_values(create, "--memory"), ["64m"])
        self.assertEqual(create[-1], self.UPSTREAM["image"])

    def test_a_refused_copy_still_removes_the_container(self):
        docker = FakeDocker(export=tar_stream(package_entries("9.2.0")).getvalue())
        self.refused("package_copy_does_not_match_inventory", docker,
                     figures={"files": 4, "bytes": 44})
        self.assertEqual(docker.events[-1], "rm:synthetic-package")

    def test_a_started_container_an_unreadable_or_a_failed_export_is_refused(self):
        stream = tar_stream(package_entries("9.2.0")).getvalue()
        docker = FakeDocker()

        def started(_image):
            docker.containers["synthetic-package"]["State"].update(Running=True,
                                                                  Status="running")
            return stream
        docker.export = started
        self.refused("inspection_container_was_started", docker)
        self.refused("image_export_unreadable", FakeDocker(export=b"not a tar stream"))
        failed = FakeDocker(export=stream)

        def popen(argv, **kwargs):
            process = FakeDocker.popen(failed, argv, **kwargs)
            return types.SimpleNamespace(stdout=process.stdout, wait=lambda timeout=None: 1)
        failed.popen = popen
        self.refused("image_export_failed", failed)


class OverlayArchiveTests(unittest.TestCase):
    """The component and overlay streams for ``docker cp -``; built in memory."""

    def test_the_stream_is_deterministic_directories_first_with_fixed_modes(self):
        files = {"base/b/two.py": b"two\n", "base/one.py": b"one\n"}
        raw = lane.tree_archive(["base"], files)
        self.assertEqual(raw, lane.tree_archive(["base"], dict(reversed(files.items()))))
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r") as archive:
            members = archive.getmembers()
        self.assertEqual([(item.name, item.isdir(), item.mode, item.mtime) for item in members], [
            ("base", True, 0o755, 0), ("base/b", True, 0o755, 0),
            ("base/b/two.py", False, 0o644, 0), ("base/one.py", False, 0o644, 0)])
        self.assertEqual(tar_files(raw), files)

    def test_the_component_stream_adds_its_parent_but_nothing_above(self):
        raw = lane.tree_archive(["custom_components", lane.COMPONENT_PATH],
                                {lane.COMPONENT_PATH + "/tools/sub.py": b"synthetic\n"})
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r") as archive:
            self.assertEqual(archive.getnames(), [
                "custom_components", lane.COMPONENT_PATH, lane.COMPONENT_PATH + "/tools",
                lane.COMPONENT_PATH + "/tools/sub.py"])

    def test_a_member_outside_the_base_or_unreviewed_or_colliding_is_refused(self):
        for token, files in (("archive_member_outside_base", {"elsewhere/x.py": b""}),
                             ("archive_member_outside_base", {"basement/x.py": b""}),
                             ("archive_member_not_reviewed", {"base/../x.py": b""}),
                             ("archive_member_not_reviewed", {"base//x.py": b""}),
                             ("overlay_member_collision", {"base/a": b"", "base/a/b": b""})):
            with self.subTest(files=files), self.assertRaises(lane.Refusal) as caught:
                lane.tree_archive(["base"], files)
            self.assertEqual(str(caught.exception), token)

    def test_the_overlay_holds_the_wheels_and_the_package_below_its_target(self):
        prefix = lane.OVERLAY_TARGET.lstrip("/") + "/"
        merged = lane.overlay_files({"files": {"ruamel/__init__.py": b"one"}},
                                    {"files": {"ha_mcp/__init__.py": b"two"}})
        self.assertEqual(merged, {prefix + "ruamel/__init__.py": b"one",
                                  prefix + "ha_mcp/__init__.py": b"two"})
        with self.assertRaises(lane.Refusal) as caught:
            lane.overlay_files({"files": {"ha_mcp/__init__.py": b"one"}},
                               {"files": {"ha_mcp/__init__.py": b"two"}})
        self.assertEqual(str(caught.exception), "overlay_member_collision")


class OverlayCoreTests(unittest.TestCase):
    """Core is told about the overlay by one environment entry, and checked for it."""

    def test_core_is_created_with_the_overlay_on_its_path_in_profile_b_only(self):
        for overlay, expected in ((False, ["TZ=America/Chicago"]),
                                  (True, ["TZ=America/Chicago", lane.OVERLAY_ENVIRONMENT])):
            argv = lane.core_argv("synthetic-core", RUN, "synthetic-holder", "synthetic-volume",
                                  IMAGE["image"], overlay=overlay)
            self.assertEqual(flag_values(argv, "--env"), expected)

    def test_the_overlay_entry_must_be_there_exactly_when_expected(self):
        inspect = lambda env: core_inspect(Config={"Image": IMAGE["image"], "Env": env})  # noqa: E731
        found = lane.verify_core(inspect(["TZ=x", lane.OVERLAY_ENVIRONMENT]), HOLDER_ID, IMAGE,
                                 "synthetic-volume", overlay=True)
        self.assertEqual(found["python_path"], [lane.OVERLAY_TARGET])
        self.assertEqual(lane.verify_core(inspect(["TZ=x"]), HOLDER_ID, IMAGE,
                                          "synthetic-volume")["python_path"], [])
        for env, overlay in (([], True), ([lane.OVERLAY_ENVIRONMENT], False),
                             ([lane.OVERLAY_ENVIRONMENT, "PYTHONPATH=/elsewhere"], True),
                             (["PYTHONPATH=/elsewhere"], True)):
            with self.subTest(env=env), self.assertRaises(lane.Refusal) as caught:
                lane.verify_core(inspect(env), HOLDER_ID, IMAGE, "synthetic-volume",
                                 overlay=overlay)
            self.assertEqual(str(caught.exception), "core_overlay_environment_mismatch")


class ProfileBCheckTests(unittest.TestCase):
    """Core's log, the component report and what Core resolves, each reduced and judged."""

    def test_each_log_rule_refuses_with_its_token_and_the_ten_come_first(self):
        domain = lane.COMPONENT_DOMAIN
        for token, line in (
                ("core_requested_one_of_the_ten", "Unable to install package watchfiles"),
                ("core_requested_one_of_the_ten", "No module named 'jsonschema_path'"),
                ("core_requested_a_requirement", "Unable to install package ruamel.yaml"),
                ("core_requested_a_requirement", f"Requirements for {domain} not found"),
                ("core_reported_a_missing_module", "No module named 'synthetic'"),
                ("core_component_setup_failed",
                 f"Setup failed for custom integration '\x1b[1m{domain}\x1b[0m'"),
                ("core_component_setup_failed", f"Error setting up entry synthetic for {domain}")):
            with self.subTest(line=line):
                summary, failure = lane.core_log_check("2026 ERROR (synthetic) " + line + "\n")
                self.assertEqual((failure, summary["refused"]), (token, token))
        _summary, failure = lane.core_log_check(
            "Unable to install package ruamel.yaml\nUnable to install package cyclopts\n")
        self.assertEqual(failure, "core_requested_one_of_the_ten")

    def test_other_lines_are_counted_not_refused(self):
        text = ("2026 WARNING (MainThread) We found a custom integration "
                f"{lane.COMPONENT_DOMAIN} which has not been tested\n"
                "2026 ERROR (MainThread) [synthetic.other] Synthetic failure\n"
                "\x1b[31m2026 INFO (MainThread) Synthetic\x1b[0m\n")
        summary, failure = lane.core_log_check(text)
        self.assertIsNone(failure)
        self.assertEqual(summary, {"lines": 3, "custom_integration_warnings": 1,
                                   "component_lines": 1, "error_lines": 1, "refused": None})

    def test_the_component_report_is_reduced_and_judged(self):
        report = synthetic_component_report("setup", "9.2.0")
        summary, failure = lane.component_check(report, "9.2.0")
        self.assertIsNone(failure)
        self.assertEqual(summary["info"]["component_version"], "9.2.0")
        self.assertNotIn("unlisted", summary["info"])
        self.assertEqual(summary["manifest"]["requirements"],
                         list(lane.COMPONENT_REQUIREMENTS["9.2.0"]))
        self.assertEqual(lane.component_check(None, "9.2.0"),
                         ({"readable": False}, "component_check_unreadable"))
        failed = dict(report, ok=False, failure="entry_not_loaded")
        self.assertEqual(lane.component_check(failed, "9.2.0")[0]["failure"], "entry_not_loaded")
        self.assertEqual(lane.component_check(dict(failed, failure="synthetic"), "9.2.0")[0][
            "failure"], None)
        for changed, token in (
                (failed, "component_setup_failed"),
                (dict(report, info=dict(report["info"], success=False)), "component_info_failed"),
                (dict(report, manifest=dict(report["manifest"], success=False)),
                 "component_info_failed"),
                (synthetic_component_report("setup", "9.1.2"), "component_version_mismatch")):
            with self.subTest(token=token):
                self.assertEqual(lane.component_check(changed, "9.2.0")[1], token)

    def test_the_overlay_must_supply_the_three_and_the_image_mcp(self):
        good = synthetic_resolved("9.2.0")
        summary, failure = lane.resolved_check(json.dumps(good).encode(), "9.2.0")
        self.assertIsNone(failure)
        self.assertIs(summary["readable"], True)
        unreadable = dict(good, core_python_path="unreadable")
        self.assertIsNone(lane.resolved_check(json.dumps(unreadable).encode(), "9.2.0")[1])

        def changed(name, value):
            return dict(good, distributions=dict(good["distributions"], **{name: value}))
        for value in (
                changed("ha-mcp", {"version": "9.1.2", "location": lane.OVERLAY_TARGET}),
                changed("ruamel.yaml", {"version": "0.19.1", "location": "/" + SITE}),
                changed("voluptuous-openapi", None),
                changed("mcp", {"version": "2.3.0", "location": lane.OVERLAY_TARGET}),
                changed("mcp", None), dict(good, core_python_path=[]),
                dict(good, distributions=None), "synthetic"):
            with self.subTest(value=value):
                self.assertEqual(lane.resolved_check(json.dumps(value).encode(), "9.2.0")[1],
                                 "overlay_not_resolved_in_core")
        self.assertEqual(lane.resolved_check(b"\xff", "9.2.0"),
                         ({"readable": False}, "overlay_not_resolved_in_core"))


class ProfileBPlanTests(unittest.TestCase):
    """The calls plan, the bound sources and the names a profile B run uses."""

    def setUp(self):
        # Historical capture-lane coverage; shipped default refuses (HAMCP-437).
        patcher = baseline.patch_binding()
        patcher.start()
        self.addCleanup(patcher.stop)

    SEED = {
        "automation_id": "synthetic_automation", "automation_entity": "automation.synthetic",
        "calendar_entity": "calendar.synthetic", "scene_id": "synthetic_scene",
        "script_id": "synthetic_script", "template": "{{ 1 + 1 }}",
        "helper_entity": "input_boolean.synthetic", "statistics_entity": "sensor.synthetic",
        "todo_entity": "todo.synthetic", "zone_id": "synthetic_zone", "search_text": "synthetic",
        "window_start": "2026-01-01T00:00:00-06:00", "window_end": "2026-01-02T00:00:00-06:00",
        "window_start_offset_free": "2026-01-01T00:00:00",
        "window_end_offset_free": "2026-01-02T00:00:00",
    }

    def test_the_profile_b_plan_covers_every_component_read_and_the_yaml_read(self):
        groups = list(lane.CAPTURE_GROUPS + lane.PROFILE_B_GROUPS)
        cases = lane.calls_step.plan(lane.ARGUMENT_POLICY, groups, self.SEED)
        self.assertEqual(len(cases), 47)
        tools = {case["upstream_tool"] for case in cases}
        self.assertEqual(set(lane.COMPONENT_READS) - tools, set())
        yaml = lane.calls_step.plan(lane.ARGUMENT_POLICY, list(lane.PROFILE_B_GROUPS), self.SEED)
        self.assertEqual([case["id"] for case in yaml], ["y-group", "y-absent-key"])
        self.assertEqual({case["upstream_tool"] for case in yaml}, {lane.YAML_READ})

    def test_each_component_source_is_the_bound_upstream_commit(self):
        for version in lane.VERSIONS:
            self.assertEqual(lane.COMPONENT_FOR[version], version)
            self.assertEqual(lane.s4.binding(version)["source_commit"],
                             lane.COMPONENT_SOURCES[version]["commit"])
        self.assertEqual(lane.COMPONENT_SOURCES["2.2.1"]["tag"], "v8.6.0")

    def test_the_names_and_the_removal_order_of_a_profile_b_run(self):
        candidate = lane.lane_names("run", "9.2.0")
        self.assertEqual(candidate[lane.ORDINARY_PEER],
                         "hamcp371-s6-run-peer-9-2-0-ordinary-user")
        self.assertEqual(candidate["package"], "hamcp371-s6-run-package")
        baseline = lane.lane_names("run", "9.1.2")
        self.assertIn("package", baseline)
        self.assertNotIn(lane.ORDINARY_PEER, baseline)
        self.assertNotIn("package", lane.lane_names("run"))
        self.assertEqual(lane.container_keys(candidate), [
            "peer-9.1.2", "peer-9.2.0", lane.ORDINARY_PEER, "package", "core", "holder"])

    def test_profile_b_options_belong_to_run_and_name_a_bound_version(self):
        common = ["--run-dir", "synthetic", "--launch-log", "synthetic"]
        for argv in (["selftest", *common, "--version", "9.2.0"],
                     ["recapture", *common, "--wheel-dir", "synthetic"],
                     ["recapture", *common, "--upstream-git", "synthetic"],
                     ["run", *common, "--profile", "B", "--version", "9.0.0"]):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    lane.main(argv)


if __name__ == "__main__":
    unittest.main()
