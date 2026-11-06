"""HAMCP-309 Part 2: Core evidence owned-directory host operations off Actions.

Offline only: Docker is emulated by running the helper's in-container code
with ``/owned`` and ``/source`` rewritten to temporary paths, after checking
the exact ``docker run`` isolation argv.  The emulated local image cache
holds the pinned image unless a test removes it; nothing ever pulls.  ``sudo`` is never executed; the
Actions mode's exec is captured.
"""

import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import tempfile
import unittest

import yaml

from tests.test_signing_workflow_supply_chain import _isolated_run, _sibling_imports, _stdlib_violations


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "core_evidence_host.py"
WORKFLOW = ROOT / ".github" / "workflows" / "core-release-evidence.yml"
ALARMO_PROFILE = ROOT / "tests" / "fixtures" / "integration_inspection" / "alarmo_profile.json"
sys.path.insert(0, str(ROOT / "scripts"))
import core_evidence_host as host  # noqa: E402

LANE = "ha-2026-11-0"
# The two commands the workflow ran before HAMCP-309, verbatim.
FORMER_STAGE = 'sudo install -m 600 "$overlay/.storage/alarmo.storage" "$REAL_HA_CONTRACT_DIR/.storage/alarmo.storage"'
FORMER_REMOVE = 'sudo rm -rf "$REAL_HA_CONTRACT_DIR"'


class Exec(Exception):
    pass


INSPECT = ["docker", "image", "inspect", "--format", "{{.Id}}", host.FIXTURE_IMAGE]


class FakeDocker:
    """Checks the isolation argv, then runs the code as the container would.

    ``calls`` records ``docker run`` invocations and ``inspections`` the local
    image lookups; any other Docker command (``pull`` included) fails the test.
    """

    def __init__(self, owned, source=None, after=None, returncode=None, stdout=None, present=True):
        self.owned, self.source, self.present = owned, source, present
        self.after, self.returncode, self.stdout = after, returncode, stdout
        self.calls, self.inspections, self.commands = [], [], []

    def __call__(self, argv, check, capture_output, timeout):
        self.commands.append(argv)
        assert check is False and capture_output is True and timeout == host.DOCKER_TIMEOUT
        assert "pull" not in argv[:2], argv
        if argv[:3] == ["docker", "image", "inspect"]:
            assert argv == INSPECT, argv
            self.inspections.append(argv)
            if not self.present:
                return subprocess.CompletedProcess(argv, 1, b"", b"Error: No such image\n")
            return subprocess.CompletedProcess(argv, 0, b"sha256:" + b"1" * 64 + b"\n", b"")
        assert argv[:2] == ["docker", "run"], argv
        self.calls.append(argv)
        assert "--memory" in argv, argv
        assert host.memory_bytes(argv[argv.index("--memory") + 1]) > 0, argv
        # --pull never: an absent image fails the run rather than being fetched.
        assert argv[argv.index("--pull") + 1] == "never", argv
        if not self.present:
            return subprocess.CompletedProcess(argv, 125, b"", b"No such image\n")
        image = argv.index(host.FIXTURE_IMAGE)
        options, (code, *extra) = argv[:image], argv[image + 2:]
        assert argv[image + 1] == "-c"
        mounts = [options[i + 1] for i, item in enumerate(options) if item == "--mount"]
        expected = [f"type=bind,source={self.owned},target=/owned"]
        if self.source is not None:
            expected.append(f"type=bind,source={self.source},target=/source,readonly")
        assert mounts == expected, mounts
        assert options[:5] == ["docker", "run", "--rm", "--pull", "never"]
        for flag in (["--network", "none"], ["--user", "0:0"], ["--entrypoint", "python"]):
            assert any(options[i:i + 2] == flag for i in range(len(options))), flag
        assert "--privileged" not in options and "-v" not in options and "--volume" not in options
        code = code.replace("'/owned'", repr(str(self.owned)))
        if self.source is not None:
            code = code.replace("'/source'", repr(str(self.source)))
        result = subprocess.run([sys.executable, "-I", "-c", code, *extra],
                                capture_output=True, timeout=60)
        if self.after:
            self.after()
        return subprocess.CompletedProcess(
            argv, result.returncode if self.returncode is None else self.returncode,
            result.stdout if self.stdout is None else self.stdout, result.stderr)


class Base(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.temp = Path(directory.name).resolve()
        self.owned = self.temp / f"beta25-real-ha-{LANE}"
        (self.owned / ".storage").mkdir(parents=True)
        (self.owned / ".storage" / "core.config").write_text("{}")
        (self.owned / "configuration.yaml").write_text("default_config:\n")
        self.source = self.temp / f"alarmo-fixture-{LANE}" / ".storage" / "alarmo.storage"
        self.source.parent.mkdir(parents=True)
        self.source.write_bytes(b'{"synthetic": "alarmo"}\n')
        self.destination = self.owned / ".storage" / "alarmo.storage"
        self.environ = {"CORE_EVIDENCE_HOST_MODE": "local", "RUNNER_TEMP": str(self.temp),
                        "HA_CONTRACT_LANE": LANE, "HA_FIXTURE_WRITER_IMAGE": host.FIXTURE_IMAGE}

    def main(self, argv, environ=None, run=None):
        stdout, stderr, execs = io.StringIO(), io.StringIO(), []

        def execvp(file, args):
            execs.append((file, list(args)))
            raise Exec()

        run = run or FakeDocker(self.owned, self.source if argv[0] == "stage" else None)
        try:
            code = host.main(argv, self.environ if environ is None else environ, run, execvp,
                             stdout, stderr)
        except Exec:
            code = "exec"
        return code, stdout.getvalue(), stderr.getvalue(), execs

    def stage_argv(self, source=None, destination=None, mode="600"):
        return ["stage", "--mode", mode, "--source", str(source or self.source),
                "--destination", str(destination or self.destination)]


class ModeSelectionTests(Base):
    def test_actions_mode_execs_exactly_the_former_sudo_commands(self):
        overlay, contract = "/home/runner/work/_temp/alarmo-fixture-x", "/home/runner/work/_temp/beta25-real-ha-x"
        variables = {"$overlay": overlay, "$REAL_HA_CONTRACT_DIR": contract, '"': ""}
        former_stage, former_remove = FORMER_STAGE, FORMER_REMOVE
        for name, value in variables.items():
            former_stage, former_remove = former_stage.replace(name, value), former_remove.replace(name, value)
        for environ in ({"GITHUB_ACTIONS": "true"},
                        {"GITHUB_ACTIONS": "true", "CORE_EVIDENCE_HOST_MODE": "actions"},
                        # No sudo detection: an empty PATH or missing lane context changes nothing.
                        {"GITHUB_ACTIONS": "true", "PATH": ""}):
            for argv, former in (
                (["stage", "--mode", "600", "--source", f"{overlay}/.storage/alarmo.storage",
                  "--destination", f"{contract}/.storage/alarmo.storage"], former_stage),
                (["remove-owned-dir", "--directory", contract], former_remove),
                (["remove-owned-dir", "--directory", ""], None),
            ):
                with self.subTest(environ=environ, argv=argv):
                    run = FakeDocker(self.owned)
                    code, stdout, stderr, execs = self.main(argv, environ, run)
                    self.assertEqual(code, "exec")
                    expected = shlex.split(former) if former else ["sudo", "rm", "-rf", ""]
                    self.assertEqual(execs, [("sudo", expected)])
                    self.assertEqual((run.calls, stdout), ([], ""))
                    self.assertEqual(stderr, f"core_evidence_host: mode actions ({argv[0]})\n")

    def test_mode_is_never_silent_or_inferred(self):
        for environ, reason in (
            ({}, "off GitHub Actions the mode must be explicit"),
            ({"CORE_EVIDENCE_HOST_MODE": "actions"}, "off GitHub Actions the mode must be explicit"),
            ({"CORE_EVIDENCE_HOST_MODE": "LOCAL"}, "off GitHub Actions the mode must be explicit"),
            ({"GITHUB_ACTIONS": "false", "CORE_EVIDENCE_HOST_MODE": "sudo"},
             "off GitHub Actions the mode must be explicit"),
            ({"GITHUB_ACTIONS": "true", "CORE_EVIDENCE_HOST_MODE": "local"},
             "CORE_EVIDENCE_HOST_MODE conflicts with GitHub Actions"),
        ):
            for argv in (self.stage_argv(), ["remove-owned-dir", "--directory", str(self.owned)]):
                with self.subTest(environ=environ, command=argv[0]):
                    run = FakeDocker(self.owned)
                    code, stdout, stderr, execs = self.main(argv, dict(self.environ, **environ)
                                                            if "CORE_EVIDENCE_HOST_MODE" in environ
                                                            else {}, run)
                    self.assertEqual(code, 1)
                    self.assertIn(f"core_evidence_host: refused: {reason}", stderr)
                    self.assertEqual((execs, run.calls, stdout), ([], [], ""))
                    self.assertTrue(self.owned.is_dir())
                    self.assertFalse(self.destination.exists())

    def test_local_mode_is_logged(self):
        code, _, stderr, execs = self.main(self.stage_argv())
        self.assertEqual((code, execs), (0, []))
        self.assertTrue(stderr.startswith("core_evidence_host: mode local (stage)\n"))

    def test_helper_is_standard_library_only_and_self_contained(self):
        self.assertEqual(_stdlib_violations(HELPER), [])
        self.assertEqual(_sibling_imports(HELPER), set())
        # It runs as the workflow runs it (python3 -I -S, empty directory) and
        # refuses off Actions without the explicit local mode.
        result = _isolated_run(HELPER, "remove-owned-dir", "--directory", str(self.owned))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "core_evidence_host: refused: off GitHub Actions the mode "
                                        "must be explicit: CORE_EVIDENCE_HOST_MODE=local\n")
        self.assertTrue(self.owned.is_dir())


class LocalStageTests(Base):
    def test_stage_writes_exact_bytes_mode_and_destination_in_the_pinned_image(self):
        run = FakeDocker(self.owned, self.source)
        code, stdout, stderr, _ = self.main(self.stage_argv(), run=run)
        self.assertEqual(code, 0, stderr)
        digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.assertEqual(json.loads(stdout), {"destination": ".storage/alarmo.storage", "mode": "600",
                                              "regular": True, "sha256": digest})
        self.assertEqual(self.destination.read_bytes(), self.source.read_bytes())
        self.assertEqual(stat.S_IMODE(self.destination.stat().st_mode), 0o600)
        argv = run.calls[0]
        self.assertEqual(argv[argv.index(host.FIXTURE_IMAGE) + 3:], [digest, ".storage/alarmo.storage", "600"])
        self.assertEqual(run.commands, [INSPECT, argv])
        # A pre-existing destination is truncated and re-moded, like install -m.
        self.destination.write_bytes(b"older and longer content " * 8)
        self.destination.chmod(0o644)
        self.assertEqual(self.main(self.stage_argv(), run=FakeDocker(self.owned, self.source))[0], 0)
        self.assertEqual(self.destination.read_bytes(), self.source.read_bytes())
        self.assertEqual(stat.S_IMODE(self.destination.stat().st_mode), 0o600)

    def test_stage_refuses_paths_outside_the_owned_directory_before_docker(self):
        outside = self.temp / "elsewhere"
        outside.mkdir()
        foreign = Path(tempfile.mkdtemp(dir=os.environ.get("TMPDIR"))).resolve()
        self.addCleanup(lambda: (foreign / "x").unlink(missing_ok=True) or foreign.rmdir())
        (foreign / "x").write_bytes(b"x")
        link = self.temp / "linked-source"
        link.symlink_to(self.source)
        for argv in (
            self.stage_argv(destination=outside / "alarmo.storage"),
            self.stage_argv(destination=self.owned / ".storage" / ".." / ".." / "escape"),
            self.stage_argv(destination=Path(".storage/alarmo.storage")),
            self.stage_argv(source=foreign / "x"),
            self.stage_argv(source=link),
            self.stage_argv(source=self.temp / "missing"),
            self.stage_argv(mode="0600"),
            self.stage_argv(mode="u=rw"),
        ):
            with self.subTest(argv=argv):
                run = FakeDocker(self.owned, self.source)
                code, stdout, stderr, _ = self.main(argv, run=run)
                self.assertEqual((code, run.calls, stdout), (1, [], ""))
                self.assertIn("refused", stderr)
                self.assertFalse(self.destination.exists())
                self.assertFalse((outside / "alarmo.storage").exists())

    def test_stage_refuses_an_unpinned_image_or_wrong_lane_context(self):
        for change in ({"HA_FIXTURE_WRITER_IMAGE": host.FIXTURE_IMAGE.replace("2026.7.2", "2026.7.3")},
                       {"HA_FIXTURE_WRITER_IMAGE": "ghcr.io/home-assistant/home-assistant:2026.7.2"},
                       {"HA_CONTRACT_LANE": "other-lane"}, {"HA_CONTRACT_LANE": "../x"},
                       {"RUNNER_TEMP": ""}, {"RUNNER_TEMP": "relative"}):
            with self.subTest(change=change):
                run = FakeDocker(self.owned, self.source)
                code, _, stderr, _ = self.main(self.stage_argv(), dict(self.environ, **change), run)
                self.assertEqual((code, run.calls), (1, []))
                self.assertFalse(self.destination.exists())

    def test_symlinked_owned_directory_parent_or_destination_refuses(self):
        target = self.temp / "target"
        target.mkdir()
        # A symlinked destination parent inside the owned directory.
        (self.owned / ".storage" / "core.config").unlink()
        (self.owned / ".storage").rmdir()
        (self.owned / ".storage").symlink_to(target)
        code, _, stderr, _ = self.main(self.stage_argv(), run=FakeDocker(self.owned, self.source))
        self.assertEqual(code, 1)
        self.assertIn("pinned fixture image staging failed", stderr)
        self.assertEqual(list(target.iterdir()), [])
        # A symlinked destination is never followed.
        (self.owned / ".storage").unlink()
        (self.owned / ".storage").mkdir()
        self.destination.symlink_to(target / "victim")
        code, _, _, _ = self.main(self.stage_argv(), run=FakeDocker(self.owned, self.source))
        self.assertEqual(code, 1)
        self.assertFalse((target / "victim").exists())
        # A symlinked owned directory refuses before Docker.
        self.destination.unlink()
        real = self.temp / "real-owned"
        self.owned.rename(real)
        self.owned.symlink_to(real)
        run = FakeDocker(self.owned, self.source)
        self.assertEqual((self.main(self.stage_argv(), run=run)[0], run.calls), (1, []))

    def test_receipt_mismatch_or_container_failure_refuses(self):
        digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        receipt = {"destination": ".storage/alarmo.storage", "mode": "600", "regular": True,
                   "sha256": digest}
        for override, returncode in (
            (dict(receipt, sha256="0" * 64), None),
            (dict(receipt, mode="644"), None),
            (dict(receipt, destination=".storage/other"), None),
            (dict(receipt, regular=False), None),
            (dict(receipt, verified=True), None),
            (receipt, 1),
        ):
            with self.subTest(override=override, returncode=returncode):
                run = FakeDocker(self.owned, self.source, returncode=returncode,
                                 stdout=json.dumps(override).encode())
                code, stdout, stderr, _ = self.main(self.stage_argv(), run=run)
                self.assertEqual((code, stdout), (1, ""))
                self.assertIn("refused", stderr)
        for stdout in (b"not json", b"{" * (host.RECEIPT_BOUND + 1)):
            run = FakeDocker(self.owned, self.source, stdout=stdout)
            self.assertEqual(self.main(self.stage_argv(), run=run)[0], 1)

    def test_source_changed_after_hashing_refuses_inside_the_container(self):
        def run(argv, **kwargs):
            self.source.write_bytes(b"swapped")
            return FakeDocker(self.owned, self.source)(argv, **kwargs)

        code, _, stderr, _ = self.main(self.stage_argv(), run=run)
        self.assertEqual(code, 1)
        self.assertFalse(self.destination.exists())


class LocalRemoveTests(Base):
    def argv(self, directory=None):
        return ["remove-owned-dir", "--directory", str(self.owned if directory is None else directory)]

    def test_remove_clears_the_owned_directory_without_following_symlinks(self):
        outside = self.temp / "keep"
        outside.mkdir()
        (outside / "file").write_text("keep")
        (self.owned / "custom_components" / "alarmo").mkdir(parents=True)
        (self.owned / "custom_components" / "alarmo" / "__init__.py").write_text("")
        (self.owned / "linked-dir").symlink_to(outside)
        (self.owned / "linked-file").symlink_to(outside / "file")
        run = FakeDocker(self.owned)
        code, stdout, stderr, _ = self.main(self.argv(), run=run)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(json.loads(stdout), {"directory": str(self.owned), "removed": True})
        self.assertFalse(os.path.lexists(self.owned))
        self.assertEqual((outside / "file").read_text(), "keep")
        self.assertEqual(len(run.calls), 1)
        self.assertEqual(run.commands, [INSPECT, run.calls[0]])

    def test_residue_left_behind_refuses(self):
        for after in (lambda: (self.owned / "late").write_text("residue"),
                      lambda: (self.owned / "late-dir").mkdir()):
            with self.subTest():
                code, stdout, stderr, _ = self.main(self.argv(), run=FakeDocker(self.owned, after=after))
                self.assertEqual((code, stdout), (1, ""))
                self.assertIn("refused: owned disposable residue remains", stderr)
                self.assertTrue(self.owned.is_dir())
                for child in self.owned.iterdir():
                    child.rmdir() if child.is_dir() else child.unlink()

    def test_container_failure_refuses_and_keeps_the_directory(self):
        code, _, stderr, _ = self.main(self.argv(), run=FakeDocker(self.owned, returncode=125))
        self.assertEqual(code, 1)
        self.assertIn("pinned fixture image cleanup failed", stderr)
        self.assertTrue(self.owned.is_dir())

    def test_only_the_lanes_owned_directory_is_ever_removed(self):
        other = self.temp / "beta25-real-ha-other"
        other.mkdir()
        for directory, environ in (
            (other, self.environ),
            (self.temp, self.environ),
            (str(self.owned) + "/", self.environ),
            (self.owned / ".storage", self.environ),
            (self.owned, dict(self.environ, HA_FIXTURE_WRITER_IMAGE="busybox")),
        ):
            with self.subTest(directory=directory, environ=environ):
                run = FakeDocker(self.owned)
                code, _, _, _ = self.main(self.argv(directory), environ, run)
                if str(directory) == str(self.owned) + "/":
                    # Path normalizes the trailing slash to the same owned directory.
                    self.assertEqual(code, 0)
                    self.owned.mkdir()
                    continue
                self.assertEqual((code, run.calls), (1, []))
                self.assertTrue(other.is_dir() and self.owned.is_dir())

    def test_unset_or_absent_directory_is_a_logged_no_op(self):
        code, stdout, _, _ = self.main(["remove-owned-dir", "--directory", ""],
                                       {"CORE_EVIDENCE_HOST_MODE": "local"})
        self.assertEqual((code, json.loads(stdout)["removed"]), (0, False))
        for child in sorted(self.owned.rglob("*"), reverse=True):
            child.rmdir() if child.is_dir() else child.unlink()
        self.owned.rmdir()
        run = FakeDocker(self.owned)
        code, stdout, _, _ = self.main(self.argv(), run=run)
        self.assertEqual((code, json.loads(stdout)["removed"], run.calls), (0, False, []))


class NoPullTests(Base):
    """Local mode never fetches an image (Codex HAMCP-309 Part 2 review, M1)."""

    def test_every_local_docker_run_uses_pull_never(self):
        for code in (host.STAGE_CODE, host.REMOVE_CODE):
            for source in (None, self.source):
                with self.subTest(code=code[:20], source=source):
                    argv = host.docker_argv(self.owned, code, source=source)
                    self.assertEqual(argv[:5], ["docker", "run", "--rm", "--pull", "never"])
                    self.assertEqual(argv.count("--pull"), 1)
                    self.assertNotIn("missing", argv)
                    self.assertNotIn("always", argv)

    def test_absent_image_refuses_naming_it_with_zero_pull_attempts(self):
        message = (f"core_evidence_host: refused: pinned fixture image {host.FIXTURE_IMAGE} is not "
                   "present locally; local mode never pulls, so load that exact digest first\n")
        self.destination.write_bytes(b"left alone")
        for argv in (self.stage_argv(), ["remove-owned-dir", "--directory", str(self.owned)]):
            with self.subTest(command=argv[0]):
                run = FakeDocker(self.owned, self.source if argv[0] == "stage" else None, present=False)
                code, stdout, stderr, execs = self.main(argv, run=run)
                self.assertEqual((code, stdout, execs), (1, "", []))
                self.assertEqual(stderr, f"core_evidence_host: mode local ({argv[0]})\n" + message)
                # Only the local lookup ran: no docker run, no pull, nothing written or removed.
                self.assertEqual((run.commands, run.calls), ([INSPECT], []))
                self.assertEqual(self.destination.read_bytes(), b"left alone")
                self.assertTrue((self.owned / ".storage" / "core.config").is_file())

    def test_present_image_is_looked_up_by_its_pinned_digest_without_pulling(self):
        self.assertIn("@sha256:", INSPECT[-1])
        for argv in (self.stage_argv(), ["remove-owned-dir", "--directory", str(self.owned)]):
            with self.subTest(command=argv[0]):
                run = FakeDocker(self.owned, self.source if argv[0] == "stage" else None)
                code, _, stderr, _ = self.main(argv, run=run)
                self.assertEqual(code, 0, stderr)
                self.assertEqual(run.commands[0], INSPECT)
                self.assertEqual(len(run.commands), 2)
                self.assertTrue(all("pull" not in command[:2] for command in run.commands))


class WorkflowTests(unittest.TestCase):
    """The minimal, Josh-authorized core-release-evidence.yml edit (2026-11-05)."""

    def setUp(self):
        self.text = WORKFLOW.read_text()
        self.lanes = yaml.safe_load(self.text)["jobs"]["lanes"]

    def step(self, name):
        return next(step for step in self.lanes["steps"] if step.get("name") == name)

    def test_no_sudo_remains_and_the_helper_carries_the_former_commands(self):
        self.assertNotIn("sudo", self.text)
        stage = self.step("Stage exact Alarmo and independent interval observer for the Core slot")["run"]
        self.assertIn('python3 -I -S scripts/core_evidence_host.py stage --mode 600 \\\n'
                      '  --source "$overlay/.storage/alarmo.storage" '
                      '--destination "$REAL_HA_CONTRACT_DIR/.storage/alarmo.storage"\n', stage)
        remove = self.step("Sanitize and remove disposable Home Assistant")
        self.assertEqual(remove["if"], "always()")
        lines = remove["run"].splitlines()
        self.assertEqual(lines[4], 'python3 -I -S scripts/core_evidence_host.py remove-owned-dir '
                                   '--directory "$REAL_HA_CONTRACT_DIR"')
        self.assertEqual(lines[5], 'docker network rm "$HA_CONTRACT_NETWORK" >/dev/null 2>&1 || true')
        # The helper's Actions argv for these exact arguments is the former command.
        args = host.argparse.Namespace(command="stage", mode="600", source="S", destination="D")
        self.assertEqual(host.actions_argv(args), ["sudo", "install", "-m", "600", "S", "D"])
        args = host.argparse.Namespace(command="remove-owned-dir", directory="D")
        self.assertEqual(host.actions_argv(args), ["sudo", "rm", "-rf", "D"])
        # Neither step sets the local mode on Actions.
        self.assertNotIn("CORE_EVIDENCE_HOST_MODE", self.text)

    def test_pinned_fixture_image_matches_the_workflow(self):
        self.assertEqual(self.lanes["env"]["HA_FIXTURE_WRITER_IMAGE"], host.FIXTURE_IMAGE)

    def test_alarmo_download_is_hash_pinned_to_the_profile(self):
        pin = json.loads(ALARMO_PROFILE.read_text())["archive_sha256"]
        stage = self.step("Stage exact Alarmo and independent interval observer for the Core slot")["run"]
        self.assertIn("--url 'https://codeload.github.com/nielsfaber/alarmo/tar.gz/"
                      "169e134f4b70d87aae36ba54a72a398ecc960afd' --output \"$archive\" \\\n"
                      f"  --sha256 {pin}\n", stage)
        self.assertRegex(pin, r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
