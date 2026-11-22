"""Offline checks of the lane host's CI mode (HAMCP-443 D2/D5/D6a, carried items 7b-7e).

No Docker, network, Home Assistant, GitHub credential or production key: every host
command is a mock, every context a synthetic mapping, and every socket a loopback
one the system chose. CI mode refuses, in order and before any resource, off
Actions, without the run's verified context, on another anchor, on a checkout that
is not the run's own commit and on an archive that is not the release's. Local
mode's port check keeps its original EADDRINUSE and waits only for TIME_WAIT.
"""
import argparse
import contextlib
import errno
import io
import json
import os
from pathlib import Path
import shutil
import socket
import stat
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT / "scripts")]

import core_created_object_local as created_object  # noqa: E402
import core_device_configuration_local as device  # noqa: E402
import core_helper_value_local as helper  # noqa: E402
import core_lane_host as lane_host  # noqa: E402
import core_lane_proof  # noqa: E402
import core_marketplace_read_local as marketplace  # noqa: E402
import core_security_metadata_local as security  # noqa: E402
import core_thread_matter_topology_a2_local as a2  # noqa: E402
import core_thread_matter_topology_local as a1  # noqa: E402
import created_object_contract_acceptance  # noqa: E402
import helper_value_contract_acceptance  # noqa: E402

HARNESSES = (helper, marketplace, security, a1, device, created_object, a2)
ACCEPTANCE = {helper: helper_value_contract_acceptance, created_object: created_object_contract_acceptance}
SHA = "c" * 40
RUNTIME = "d" * 40
ARCHIVE = "e" * 64
DIGEST = "sha256:" + "f" * 64


class Reached(Exception):
    """Raised by a mock that must not be reached."""


def context(anchor="2026.11.0", version="2026.11.1", sha=SHA):
    return {"anchor": anchor, "run": {"GITHUB_SHA": sha},
            "provenance": {"version": version, "image_index_digest": DIGEST, "source_commit": "a" * 40,
                           "source_archive_sha256": ARCHIVE}}


def git(revision=SHA, status=b""):
    def command(*args, **_kwargs):
        if args[:2] == ("git", "status"):
            return mock.Mock(returncode=0, stdout=status)
        if args[:3] == ("git", "rev-parse", "HEAD"):
            return mock.Mock(returncode=0, stdout=revision.encode() + b"\n")
        if args[:2] == ("git", "rev-parse"):
            return mock.Mock(returncode=0, stdout=RUNTIME.encode() + b"\n")
        raise Reached(args)
    return command


def actions(**extra):
    return {**{k: v for k, v in os.environ.items()
               if k not in ("GITHUB_ACTIONS", "CORE_EVIDENCE_CONTEXT", "RUNNER_TEMP")}, **extra}


class ModeArguments(unittest.TestCase):
    def parsed(self, mode, pull, journal):
        parser = argparse.ArgumentParser()
        args = argparse.Namespace(mode=mode, pull=pull, journal=journal)
        with mock.patch.object(parser, "error", side_effect=SystemExit(2)) as error:
            try:
                lane_host.require_mode_arguments(parser, args)
            except SystemExit:
                return False
        error.assert_not_called()
        return True

    def test_exactly_one_pair_per_mode(self):
        # 7d: the admitted forms, one each; every other combination refuses.
        self.assertTrue(self.parsed("local", "never", Path("j")))
        self.assertTrue(self.parsed("ci", "digest", None))
        for mode, pull, journal in (("local", "never", None), ("local", "digest", Path("j")),
                                    ("local", "digest", None), ("ci", "digest", Path("j")),
                                    ("ci", "never", None), ("ci", "never", Path("j"))):
            with self.subTest(mode=mode, pull=pull, journal=journal):
                self.assertFalse(self.parsed(mode, pull, journal))

    def test_every_harness_takes_the_mode_pull_and_journal_forms(self):
        for module in HARNESSES:
            argv = ["--source-archive", "a", "--run-dir", "r"] + (
                ["--lane", "scene_create_delete"] if module is created_object else [])
            for wrong in (["--mode", "ci", "--pull", "never"], ["--mode", "local", "--pull", "digest", "--journal", "j"],
                          ["--mode", "ci", "--pull", "digest", "--journal", "j"], ["--mode", "local", "--pull", "never"]):
                with self.subTest(module=module.__name__, argv=wrong), \
                        mock.patch.object(module, "command", side_effect=Reached), \
                        mock.patch.object(lane_host.release, "context", side_effect=Reached), \
                        mock.patch("sys.stderr"), self.assertRaises(SystemExit):
                    module._main(wrong + argv)


class CiContext(unittest.TestCase):
    def check(self, environ, ctx=None, command=None):
        with mock.patch.dict(os.environ, environ, clear=True), \
                mock.patch.object(lane_host.release, "context", return_value=ctx) as read, \
                mock.patch.object(helper, "command", command or git()):
            return helper.LANE_HOST.ci_context(), read

    def refused(self, environ, ctx=None, command=None):
        with self.assertRaises(helper.Refusal) as caught:
            self.check(environ, ctx, command)
        return str(caught.exception)

    def test_off_actions_refuses_before_the_context_is_read(self):
        for value in (None, "", "True", "1", "false", " true"):
            environ = actions() if value is None else actions(GITHUB_ACTIONS=value)
            with self.subTest(value=value), mock.patch.dict(os.environ, environ, clear=True), \
                    mock.patch.object(lane_host.release, "context", side_effect=Reached), \
                    mock.patch.object(helper, "command", side_effect=Reached), \
                    self.assertRaises(helper.Refusal) as caught:
                helper.LANE_HOST.ci_context()
            self.assertEqual(str(caught.exception), "ci_mode_requires_actions")

    def test_no_verified_context_refuses_before_any_command(self):
        self.assertEqual(self.refused(actions(GITHUB_ACTIONS="true"), None, mock.Mock(side_effect=Reached)),
                         "ci_context_required")

    def test_the_verification_is_core_release_evidence_context(self):
        # The run_specialized groups' check, not a second one: its refusal propagates unchanged.
        with mock.patch.dict(os.environ, actions(GITHUB_ACTIONS="true"), clear=True), \
                mock.patch.object(lane_host.release, "context",
                                  side_effect=ValueError("binding artifact digest mismatch")), \
                mock.patch.object(helper, "command", side_effect=Reached), \
                self.assertRaises(ValueError) as caught:
            helper.LANE_HOST.ci_context()
        self.assertEqual(str(caught.exception), "binding artifact digest mismatch")

    def test_another_anchor_refuses_before_any_command(self):
        for anchor in ("2026.12.0", "2026.10.0", "2026.11", None):
            with self.subTest(anchor=anchor):
                self.assertEqual(self.refused(actions(GITHUB_ACTIONS="true"), context(anchor=anchor),
                                              mock.Mock(side_effect=Reached)), "lane_anchor_unreviewed")

    def test_a_dirty_checkout_or_another_commit_refuses(self):
        environ = actions(GITHUB_ACTIONS="true")
        self.assertEqual(self.refused(environ, context(), git(status=b"?? x\n")),
                         "clean_committed_checkout_required")
        self.assertEqual(self.refused(environ, context(), git(revision="9" * 40)), "ci_revision_mismatch")

    def test_the_run_s_own_commit_passes(self):
        (ctx, revision), read = self.check(actions(GITHUB_ACTIONS="true"), context())
        self.assertEqual((ctx, revision), (context(), SHA))
        read.assert_called_once_with()


class CiInputs(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.directory)

    def test_the_archive_must_be_the_release_s_before_the_baseline_is_read(self):
        archive = self.directory / "core.tar.gz"
        archive.write_bytes(b"synthetic")
        with mock.patch.object(core_lane_proof, "authenticated_baseline", side_effect=Reached), \
                self.assertRaises(helper.Refusal) as caught:
            helper.LANE_HOST.ci_entry(context(), archive)
        self.assertEqual(str(caught.exception), "input_hash_mismatch")
        ctx = context()
        ctx["provenance"]["source_archive_sha256"] = lane_host.sha256(archive)
        with mock.patch.object(core_lane_proof, "authenticated_baseline", return_value="baseline") as baseline, \
                mock.patch.object(core_lane_proof, "release_entry", return_value="entry") as entry:
            self.assertEqual(helper.LANE_HOST.ci_entry(ctx, archive), "entry")
        baseline.assert_called_once_with(ctx)
        entry.assert_called_once_with("baseline", ctx)

    def test_images_are_the_release_core_then_the_anchor_s_child_each_pulled_and_verified(self):
        calls = []
        with mock.patch.object(helper, "command", lambda *a, **k: calls.append(("command", a, k))), \
                mock.patch.object(helper, "cached_image", lambda image: calls.append(("cached", image))):
            images = helper.LANE_HOST.ci_images(context(), upstream=True)
        anchor = helper.LANE_HOST.images(upstream=True)
        release_core = "ghcr.io/home-assistant/home-assistant:2026.11.1@" + DIGEST
        self.assertEqual(images, (release_core, *anchor[1:]))
        self.assertNotEqual(release_core, anchor[0])
        expected = []
        for image in images:
            expected += [("command", ("docker", "pull", image), {"timeout": lane_host.PULL_TIMEOUT}),
                         ("cached", image)]
        self.assertEqual(calls, expected)
        with mock.patch.object(marketplace, "command"), mock.patch.object(marketplace, "cached_image"):
            self.assertEqual(len(marketplace.LANE_HOST.ci_images(context(), upstream=False)), 2)

    def test_a_pulled_image_without_its_digest_refuses(self):
        with mock.patch.object(helper, "command"), \
                mock.patch.object(helper, "cached_image", side_effect=helper.Refusal("cached_image_digest_mismatch")), \
                self.assertRaises(helper.Refusal) as caught:
            helper.LANE_HOST.ci_images(context(), upstream=False)
        self.assertEqual(str(caught.exception), "cached_image_digest_mismatch")


class CachedImage(unittest.TestCase):
    def inspected(self, digests):
        value = [{"Id": "sha256:cached", "RepoDigests": digests}]
        with mock.patch.object(helper, "command", return_value=mock.Mock(stdout=json.dumps(value).encode())):
            return helper.LANE_HOST.cached_image(CORE_IMAGE)

    def test_the_pinned_digest_yields_the_image_id(self):
        self.assertEqual(self.inspected(["ghcr.io/home-assistant/qemux86-64-homeassistant@" + DIGEST]), "sha256:cached")

    def test_an_image_without_the_pinned_digest_refuses(self):
        for digests in ([], ["ghcr.io/home-assistant/qemux86-64-homeassistant@sha256:" + "0" * 64]):
            with self.subTest(digests=digests), self.assertRaises(helper.Refusal) as caught:
                self.inspected(digests)
            self.assertEqual(str(caught.exception), "cached_image_digest_mismatch")


class MetadataBlock(unittest.TestCase):
    RULE = ("DOCKER-USER", "--destination", "169.254.169.254/32", "--jump", "DROP")

    def run_block(self, present, inserted=True):
        calls, state = [], {"present": present}

        def command(*args, check=True, **_kwargs):
            calls.append(args)
            if "-C" in args:
                code = 0 if state["present"] else 1
                if check and code:
                    raise helper.Refusal("host_command_failed")
                return mock.Mock(returncode=code)
            state["present"] = inserted
            return mock.Mock(returncode=0)
        with mock.patch.object(helper, "command", command):
            helper.LANE_HOST.block_metadata()
        return calls

    def test_the_rule_is_the_metadata_address_only(self):
        self.assertEqual(lane_host.METADATA_RULE, self.RULE)

    def test_an_absent_rule_is_inserted_and_read_back(self):
        check, insert = ("sudo", "-n", "iptables", "-w", "-C", *self.RULE), ("sudo", "-n", "iptables", "-w", "-I", *self.RULE)
        self.assertEqual(self.run_block(False), [check, insert, check])
        self.assertEqual(self.run_block(True), [check])

    def test_a_rule_that_does_not_read_back_refuses(self):
        with self.assertRaises(helper.Refusal) as caught:
            self.run_block(False, inserted=False)
        self.assertEqual(str(caught.exception), "host_command_failed")


class CiClaim(unittest.TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.temp)

    def claim(self, path, temp):
        environ = actions() if temp is None else actions(RUNNER_TEMP=str(temp))
        with mock.patch.dict(os.environ, environ, clear=True):
            return helper.LANE_HOST.ci_claim(path)

    def test_only_a_new_directory_directly_under_runner_temp(self):
        claimed = self.claim(self.temp / "run", self.temp)
        self.assertEqual(claimed, self.temp / "run")
        self.assertEqual(stat.S_IMODE(claimed.stat().st_mode), 0o700)
        with self.assertRaises(FileExistsError):
            self.claim(self.temp / "run", self.temp)
        (self.temp / "real").mkdir()
        (self.temp / "link").symlink_to(self.temp / "real")
        (self.temp / "loop").symlink_to(self.temp / "loop")
        for path, temp in ((self.temp / "a" / "run", self.temp), (self.temp / "run2", None),
                           (self.temp / "run2", "relative"), (self.temp / "link" / "run", self.temp / "link"),
                           (self.temp / "link" / "run", self.temp), (self.temp, self.temp),
                           (self.temp / "run2", self.temp / "real"), (self.temp / "loop" / "run", self.temp / "loop")):
            with self.subTest(path=path, temp=temp), self.assertRaises(helper.Refusal) as caught:
                self.claim(path, temp)
            self.assertEqual(str(caught.exception), "runner_temp_run_directory_required")
        self.assertEqual(sorted(p.name for p in self.temp.iterdir()), ["link", "loop", "real", "run"])


class CiAdmission(unittest.TestCase):
    STEPS = ("ci_context", "inputs", "ci_images", "block_metadata", "require_unclaimed", "ci_claim")

    def test_every_step_runs_in_order_and_a_refusal_stops_the_rest(self):
        for failing in (None, *self.STEPS):
            calls = []

            def step(name, value):
                def called(*args, **kwargs):
                    calls.append(name)
                    if name == failing:
                        raise helper.Refusal(name)
                    return value
                return called
            host = helper.LANE_HOST
            with self.subTest(failing=failing), \
                    mock.patch.object(host, "ci_context", step("ci_context", (context(), SHA))), \
                    mock.patch.object(host, "ci_images", step("ci_images", ("core", "fixture"))), \
                    mock.patch.object(host, "block_metadata", step("block_metadata", None)), \
                    mock.patch.object(host, "require_unclaimed", step("require_unclaimed", None)), \
                    mock.patch.object(host, "ci_claim", step("ci_claim", Path("/run"))):
                try:
                    result = host.ci_admission(Path("/run"), step("inputs", "values"), upstream=False,
                                               containers=("c",), network="n")
                except helper.Refusal as exc:
                    self.assertEqual(str(exc), failing)
                    self.assertEqual(calls, list(self.STEPS[:self.STEPS.index(failing) + 1]))
                else:
                    self.assertIsNone(failing)
                    self.assertEqual(calls, list(self.STEPS))
                    self.assertEqual(result, (context(), SHA, "values", ("core", "fixture"), Path("/run")))

    def test_ci_fields_come_from_the_context_and_the_run_s_own_tree(self):
        calls = []

        def command(*args, **_kwargs):
            calls.append(args)
            return mock.Mock(stdout=RUNTIME.encode() + b"\n")
        with mock.patch.object(helper, "command", command):
            fields = helper.LANE_HOST.ci_fields(context())
        self.assertEqual(fields, {"mode": "ci", "pull": "digest", "journal_sha256": None, "host_admission": None,
                                  "core_version": "2026.11.1", "runtime_tree": RUNTIME})
        # 7e: the expectation is the checkout's own tree, never a pinned constant.
        self.assertEqual(calls, [("git", "rev-parse", "HEAD:hass_mcp_engineering_beta/ha_mcp_engineering")])


class EveryHarnessRefusesCiOffActions(unittest.TestCase):
    def argv(self, module):
        argv = ["--mode", "ci", "--pull", "digest", "--source-archive", "a", "--run-dir", "r"]
        return argv + (["--lane", "scene_create_delete"] if module is created_object else [])

    def outcome(self, module, environ, ctx=None):
        with mock.patch.dict(os.environ, environ, clear=True), \
                mock.patch.object(module, "command", side_effect=Reached), \
                mock.patch.object(a1, "command", side_effect=Reached), \
                mock.patch.object(lane_host.release, "context", return_value=ctx), \
                self.assertRaises(Exception) as caught:
            module._main(self.argv(module))
        return type(caught.exception).__name__, str(caught.exception)

    def test_off_actions_or_without_a_context_nothing_is_touched(self):
        for module in HARNESSES:
            with self.subTest(module=module.__name__):
                self.assertEqual(self.outcome(module, actions()), ("Refusal", "ci_mode_requires_actions"))
                self.assertEqual(self.outcome(module, actions(GITHUB_ACTIONS="true")), ("Refusal", "ci_context_required"))
                self.assertEqual(self.outcome(module, actions(GITHUB_ACTIONS="true"), context(anchor="2026.12.0")),
                                 ("Refusal", "lane_anchor_unreviewed"))


class NetworkPosture(unittest.TestCase):
    def test_no_lane_core_runs_on_the_host_network(self):
        for network, options in (("host", ()), ("bridge-x", ("--network", "host")), ("bridge-x", ("--network=host",)),
                                 ("bridge-x", ("--net", "host")), ("bridge-x", ("--net=host",)),
                                 ("bridge-x", ("--network=bridge-y",))):
            with self.subTest(network=network, options=options), \
                    mock.patch.object(helper, "command", side_effect=Reached), \
                    self.assertRaises(helper.Refusal) as caught:
                helper.LANE_HOST.start_core("core", network, "run", "2g", Path("/owned"), "image", *options)
            self.assertEqual(str(caught.exception), "lane_core_host_network_refused")
        calls = []
        with mock.patch.object(helper, "command", lambda *a, **k: calls.append(a)):
            helper.LANE_HOST.start_core("core", "bridge-x", "run", "2g", Path("/owned"), "image",
                                        "--publish", "127.0.0.1:18123:8123")
        self.assertEqual(calls[0][calls[0].index("--network") + 1], "bridge-x")
        self.assertEqual(calls[0].count("--network"), 1)

    def test_bridges_are_dedicated_and_only_a2_s_is_internal(self):
        # D6a: marketplace and the other hosted lanes on a non-internal bridge; A2's internal, without egress.
        for module in (helper, marketplace, security, a1, device, created_object):
            with self.subTest(module=module.__name__):
                self.assertEqual(module.NETWORK_POSTURE["driver"], "bridge")
                self.assertNotIn("internal", module.NETWORK_POSTURE)
                self.assertEqual(module.NETWORK_POSTURE["host_bind_address"], "127.0.0.1")
        self.assertEqual((a2.NETWORK_POSTURE["driver"], a2.NETWORK_POSTURE["internal"],
                          a2.NETWORK_POSTURE["egress_allowed"], a2.NETWORK_POSTURE["published_ports"]),
                         ("bridge", True, False, False))

    def test_create_bridge_refuses_an_internal_or_foreign_network(self):
        for data in ({"Driver": "bridge", "Internal": True}, {"Driver": "host", "Internal": False},
                     {"Driver": "bridge"}, None):
            with self.subTest(data=data), mock.patch.object(helper, "command"), \
                    mock.patch.object(helper, "inspect_owned", return_value=data), \
                    self.assertRaises(helper.Refusal):
                helper.LANE_HOST.create_bridge("net", "run")


class LoopbackPorts(unittest.TestCase):
    """7c: a bounded wait for TIME_WAIT between lanes; a live listener or any other holder raises at once."""

    def require(self, ports, **kwargs):
        sleeps = []
        with mock.patch.object(helper, "PORTS", ports):
            helper.LANE_HOST.require_free_ports(sleep=sleeps.append, **kwargs)
        return sleeps

    def raised(self, ports, **kwargs):
        sleeps = []
        with mock.patch.object(helper, "PORTS", ports), self.assertRaises(OSError) as caught:
            helper.LANE_HOST.require_free_ports(sleep=sleeps.append, **kwargs)
        self.assertEqual(caught.exception.errno, errno.EADDRINUSE)
        self.assertNotIsInstance(caught.exception, helper.Refusal)
        return sleeps

    def test_a_listener_raises_at_once_and_is_never_waited_out(self):
        for reuse in (False, True):
            with self.subTest(reuse=reuse), socket.socket() as listener:
                if reuse:
                    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                listener.bind(("127.0.0.1", 0))
                listener.listen(1)
                self.assertEqual(self.raised((listener.getsockname()[1],)), [])

    def test_a_bound_holder_raises_at_once(self):
        with socket.socket() as held:
            held.bind(("127.0.0.1", 0))
            self.assertEqual(self.raised((held.getsockname()[1],)), [])

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux TIME_WAIT semantics")
    def test_time_wait_is_waited_for_a_bounded_time(self):
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            port = listener.getsockname()[1]
            client = socket.create_connection(("127.0.0.1", port))
            accepted, _ = listener.accept()
            accepted.close()  # The listener's side closes first, so its port stays in TIME_WAIT.
            client.close()
        self.assertFalse(lane_host.listening(port))
        self.assertTrue(lane_host.time_wait_only(port))
        ticks = iter(range(100))
        self.assertEqual(self.raised((port,), wait=5, clock=lambda: next(ticks)), [1] * 4)

    def test_the_wait_ends_when_the_port_frees(self):
        held = socket.socket()
        held.bind(("127.0.0.1", 0))
        port = held.getsockname()[1]

        def freed(_seconds):
            held.close()
        with mock.patch.object(lane_host, "time_wait_only", return_value=True), \
                mock.patch.object(lane_host, "listening", return_value=False), \
                mock.patch.object(helper, "PORTS", (port,)):
            helper.LANE_HOST.require_free_ports(sleep=freed, clock=lambda: 0)

    def test_free_ports_pass_and_hold_nothing(self):
        with socket.socket() as spare:
            spare.bind(("127.0.0.1", 0))
            port = spare.getsockname()[1]
        self.assertEqual(self.require((port, port)), [])
        self.assertEqual(self.require((port,)), [])

    def test_a_listener_raises_at_once_even_when_a_reusable_bind_would_pass(self):
        # listening() is the barrier on its own, whatever time_wait_only() says.
        with socket.socket() as listener, mock.patch.object(lane_host, "time_wait_only", return_value=True):
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            ticks = iter(range(100))
            self.assertEqual(self.raised((listener.getsockname()[1],), wait=5, clock=lambda: next(ticks)), [])

    def test_another_bind_error_raises_at_once(self):
        refused = OSError(errno.EACCES, "synthetic")

        class Socket:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def bind(self, _address):
                raise refused
        sleeps, ticks = [], iter(range(100))
        with mock.patch.object(lane_host.socket, "socket", Socket), \
                mock.patch.object(lane_host, "listening", return_value=False), \
                mock.patch.object(lane_host, "time_wait_only", return_value=True), \
                mock.patch.object(helper, "PORTS", (1,)), self.assertRaises(OSError) as caught:
            helper.LANE_HOST.require_free_ports(sleep=sleeps.append, wait=5, clock=lambda: next(ticks))
        self.assertIs(caught.exception, refused)
        self.assertEqual(sleeps, [])

    def test_the_default_wait_is_bounded(self):
        # Linux holds TIME_WAIT for 60 s; the default wait outlasts it and then raises.
        ticks = iter(range(200))
        with socket.socket() as held, \
                mock.patch.object(lane_host, "listening", return_value=False), \
                mock.patch.object(lane_host, "time_wait_only", return_value=True):
            held.bind(("127.0.0.1", 0))
            sleeps = self.raised((held.getsockname()[1],), clock=lambda: next(ticks))
        self.assertTrue(60 <= len(sleeps) <= 120, len(sleeps))



CORE_IMAGE = "ghcr.io/home-assistant/qemux86-64-homeassistant@" + DIGEST
SIDE_IMAGES = ("synthetic.invalid/upstream@" + DIGEST, "synthetic.invalid/fixture@" + DIGEST)


class CiExercisesTheReleaseCore(unittest.TestCase):
    """Rule 5 in CI mode: every harness compares the running Core with the context's release
    version (never the anchor's) and with the release Core image the admission pulled."""

    def admitted(self, module, run_dir):
        entry, reference, sources = {"version": "2026.11.0"}, {"contract_fingerprint": "f"}, {}
        upstream, fixture = SIDE_IMAGES
        if module is helper:
            return context(), SHA, entry, (CORE_IMAGE, upstream, fixture), run_dir
        if module is device:
            stub = {"stub_sha256": "s", "entry_sha256": "e", "dump_hashes": {}}
            return context(), SHA, ({"profile": 1}, (entry, sources, stub)), (CORE_IMAGE, upstream, fixture), run_dir
        return context(), SHA, (entry, reference, sources), (CORE_IMAGE, fixture), run_dir

    def exercised(self, module):
        seen = {}

        async def exercise(*args, **kwargs):
            seen.update(args=args, kwargs=kwargs, expected=os.environ["REAL_HA_EXPECTED_VERSION"])
            raise Reached

        argv = ["--mode", "ci", "--pull", "digest", "--source-archive", "a", "--run-dir", "r"]
        argv += ["--lane", "scene_create_delete"] if module is created_object else []
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, actions(GITHUB_ACTIONS="true"), clear=True), \
                mock.patch.object(module.LANE_HOST, "ci_admission", return_value=self.admitted(module, Path(tmp))), \
                mock.patch.object(module.LANE_HOST, "ci_fields", return_value={}), \
                mock.patch.object(module.LANE_HOST, "configuration", return_value=Path(tmp) / "owned"), \
                mock.patch.object(module.LANE_HOST, "create_bridge", return_value={}), \
                mock.patch.object(module.LANE_HOST, "start_core"), \
                mock.patch.object(module.LANE_HOST, "execute", side_effect=lambda result, receipt, body, **_: body()), \
                mock.patch.object(module, "verify_port_mapping"), \
                mock.patch.object(module, "exercise", side_effect=exercise), \
                mock.patch.object(device, "start_stub"), \
                mock.patch.object(device, "stub_address", return_value="172.18.0.2"), \
                self.assertRaises(Reached):
            module._main(argv)
        return seen

    def test_every_single_process_harness_exercises_the_release_version_and_image(self):
        # The release Core image's position in each harness's exercise().
        for module, index in ((helper, 2), (marketplace, 2), (security, 2), (a1, 2), (device, 2),
                              (created_object, 3)):
            with self.subTest(module=module.__name__):
                seen = self.exercised(module)
                self.assertEqual((seen["kwargs"]["version"], seen["kwargs"]["mode"], seen["expected"]),
                                 ("2026.11.1", "ci", "2026.11.1"))
                self.assertEqual(seen["args"][index], CORE_IMAGE)

    def a2_run(self, probe, network=None):
        seen, self.commands = {}, []

        async def exercise(*args, **kwargs):
            seen.update(args=args, kwargs=kwargs, expected=os.environ["REAL_HA_EXPECTED_VERSION"])
            raise Reached

        def command(*args, **_kwargs):
            self.commands.append(args)
            if args[:3] == ("docker", "container", "inspect") or args[:3] == ("docker", "network", "inspect"):
                return mock.Mock(returncode=1, stdout=b"")
            if args[:3] == ("docker", "exec", a2.CORE):
                return mock.Mock(returncode=0, stdout=probe)
            return mock.Mock(returncode=0, stdout=b"0" * 64)

        argv = ["--mode", "ci", "--pull", "digest", "--source-archive", "a", "--run-dir", "r"]
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, actions(GITHUB_ACTIONS="true"), clear=True), \
                mock.patch.object(a1.LANE_HOST, "ci_context", return_value=(context(), SHA)), \
                mock.patch.object(a2, "verify_stub", return_value={}), \
                mock.patch.object(a1, "ci_inputs", return_value=({}, {"contract_fingerprint": "f"}, {})), \
                mock.patch.object(a1.LANE_HOST, "ci_images", return_value=(CORE_IMAGE, SIDE_IMAGES[1])), \
                mock.patch.object(a1.LANE_HOST, "block_metadata") as metadata, \
                mock.patch.object(a1.LANE_HOST, "ci_claim", return_value=Path(tmp)), \
                mock.patch.object(a1.LANE_HOST, "ci_fields", return_value={}), \
                mock.patch.object(a2, "command", side_effect=command), \
                mock.patch.object(a2, "inspect_owned", side_effect=[network or {"Driver": "bridge", "Internal": True}]
                                  + [None] * 8), \
                mock.patch.object(a2, "created_network"), \
                mock.patch.object(a2, "hand_over_core", return_value=("http://172.30.0.2:8123",
                                                                      {"subnet": "172.30.0.0/24"})), \
                mock.patch.object(a2, "stub_origin", return_value="http://172.30.0.3:5580"), \
                mock.patch.object(a2.host, "local_remove"), \
                mock.patch.object(a2, "exercise", side_effect=exercise):
            with contextlib.redirect_stdout(io.StringIO()):
                code = a2._main(argv)
            receipt = (Path(tmp) / a2.RECEIPT).read_text()
        metadata.assert_called_once_with()
        return code, receipt, seen

    def test_a2_exercises_the_release_version_image_and_anchor_behind_its_blocked_egress(self):
        code, receipt, seen = self.a2_run(b"blocked: timeout")
        self.assertEqual(code, 1)
        self.assertIn('"failure_type":"Reached"', receipt)
        self.assertEqual((seen["kwargs"]["version"], seen["kwargs"]["anchor"], seen["kwargs"]["mode"],
                          seen["expected"]), ("2026.11.1", "2026.11.0", "ci", "2026.11.1"))
        self.assertEqual(seen["args"][3], CORE_IMAGE)

    def test_a2_with_reachable_egress_refuses_before_any_exercise(self):
        code, receipt, seen = self.a2_run(b"reachable")
        self.assertEqual(code, 1)
        self.assertIn('"failure_type":"Refusal"', receipt)
        self.assertEqual(seen, {})

    def test_a2_network_that_does_not_read_back_internal_refuses_before_the_stub_runs(self):
        code, receipt, seen = self.a2_run(b"blocked: timeout", network={"Driver": "bridge", "Internal": False})
        self.assertEqual(code, 1)
        self.assertIn('"failure_type":"Refusal"', receipt)
        self.assertEqual(seen, {})
        self.assertFalse([c for c in self.commands if c[:2] == ("docker", "run")])


class RunningReleaseVersion(unittest.IsolatedAsyncioTestCase):
    """The running Core must report exactly the version the harness was given: in CI the context's
    release, which is not the anchor's VERSION (helper_value and created_object)."""

    CALLS = {
        helper: lambda tmp: helper.exercise(tmp, {}, CORE_IMAGE, SIDE_IMAGES[0], "run", "2g", "1g",
                                            version="2026.11.1", mode="ci"),
        created_object: lambda tmp: created_object.exercise("scene_create_delete", tmp, {}, CORE_IMAGE, "run",
                                                            "2g", version="2026.11.1", mode="ci"),
    }

    async def identity(self, module, observed, call, container=None):
        import real_ha_contract_tests as harness
        acceptance = mock.AsyncMock()
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(harness, "UPSTREAM_IMAGE", ""), \
                mock.patch.object(harness, "bootstrap_disposable_admin", new=mock.AsyncMock(return_value="t")), \
                mock.patch.object(harness, "wait_for_runtime_ready",
                                  new=mock.AsyncMock(return_value={"version": observed})), \
                mock.patch.object(harness, "_start_exact_upstream", new=mock.AsyncMock()), \
                mock.patch.object(module, "inspect_owned", return_value=container), \
                mock.patch.object(module, "cached_image", return_value="sha256:running"), \
                mock.patch.object(ACCEPTANCE[module], "run", new=acceptance):
            with self.assertRaises(module.Refusal) as caught:
                await call(Path(tmp))
        acceptance.assert_not_called()
        return str(caught.exception)

    async def test_the_release_version_passes_and_the_anchor_version_refuses(self):
        for module, call in self.CALLS.items():
            with self.subTest(module=module.__name__):
                self.assertNotEqual(module.VERSION, "2026.11.1")
                self.assertEqual(await self.identity(module, module.VERSION, call), "running_core_version_mismatch")
                # Past the version check only the release's version gets: the next refusal is the image.
                self.assertEqual(await self.identity(module, "2026.11.1", call), "running_image_mismatch")

    async def test_the_running_core_must_be_the_pulled_release_image_with_its_memory(self):
        right = {"Image": "sha256:running", "Config": {"Image": CORE_IMAGE},
                 "HostConfig": {"Memory": helper.host.memory_bytes("2g")}}
        cases = (({"Image": "sha256:other"}, "running_image_mismatch"),
                 ({"Config": {"Image": SIDE_IMAGES[0]}}, "running_image_mismatch"),
                 ({"HostConfig": {"Memory": 1}}, "running_container_memory_mismatch"))
        for module, call in self.CALLS.items():
            for change, refusal in cases:
                with self.subTest(module=module.__name__, change=change):
                    self.assertEqual(await self.identity(module, "2026.11.1", call, {**right, **change}), refusal)


if __name__ == "__main__":
    unittest.main()
