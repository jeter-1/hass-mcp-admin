"""Offline hash locks, native smoke controls and final-digest input contracts."""

import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

import yaml


ROOT = Path(__file__).resolve().parents[1]
BETA = ROOT / "hass_mcp_engineering_beta"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


BUILD = load("build_inputs_test_subject", BETA / "build_inputs.py")
PREPARE = load("prepare_build_inputs_test_subject", ROOT / "scripts/prepare_build_inputs.py")
from tests import test_publication_source_verifier as publication
VERIFY = publication.MODULE


class BuildInputsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name in ("build-inputs.json", "requirements.lock", "requirements.txt", "Dockerfile"):
            shutil.copyfile(BETA / name, self.root / name)
        self.contract = json.loads((self.root / "build-inputs.json").read_text())

    def test_source_and_test_locks_preserve_the_entire_runtime(self):
        result = BUILD.verify_source(self.root)
        self.assertEqual(result["status"], "PASS")
        runtime = BUILD.parse_lock((BETA / "requirements.lock").read_bytes())
        tests = BUILD.parse_lock((ROOT / "tests/requirements.lock").read_bytes())
        for name, value in runtime.items():
            self.assertEqual(tests[name], value)
        for line in (ROOT / "tests/requirements.txt").read_text().splitlines():
            if line and not line.startswith("#"):
                name, version = line.split("==")
                self.assertEqual(tests[name][0], version)
        self.assertEqual(len(runtime), 39)
        self.assertNotIn("pip", runtime)

    def test_source_refuses_changed_bytes_and_bases(self):
        for name in ("requirements.lock", "requirements.txt", "Dockerfile"):
            with self.subTest(name=name):
                path = self.root / name
                original = path.read_bytes()
                changed = original + b"# changed\n" if name != "Dockerfile" else original.replace(b"@sha256:", b"@bad:")
                path.write_bytes(changed)
                with self.assertRaises(ValueError):
                    BUILD.verify_source(self.root)
                path.write_bytes(original)

    def test_contract_refuses_schema_platform_inventory_and_duplicate_drift(self):
        path = self.root / "build-inputs.json"
        for change in (
            lambda c: c.update(schema_version=True),
            lambda c: c.update(extra="unexpected"),
            lambda c: c["base_platforms"].pop("linux/arm64"),
            lambda c: c["base_platforms"]["linux/amd64"].update(configuration_digest="bad"),
            lambda c: c["runtime"].update(mcp="0.0.0"),
            lambda c: c.update(installer_version="latest"),
            lambda c: c.update(python_version="3.13.1"),
        ):
            value = copy.deepcopy(self.contract)
            change(value)
            path.write_text(json.dumps(value))
            with self.subTest(value=value), self.assertRaises(ValueError):
                BUILD.load_inputs(self.root)
        path.write_text('{"schema_version":1,"schema_version":1}')
        with self.assertRaisesRegex(ValueError, "DUPLICATE"):
            BUILD.load_inputs(self.root)

    def test_lock_refuses_unpinned_inputs_options_sources_and_duplicate_hashes(self):
        valid = "synthetic==1.0 --hash=sha256:" + "a" * 64
        self.assertEqual(BUILD.parse_lock(valid.encode())["synthetic"][0], "1.0")
        for bad in ("synthetic>=1", "-r other.txt", "synthetic @ https://example.invalid/a.whl",
                    valid + " ; python_version>'3'", valid + " --index-url=https://example.invalid",
                    valid + " --hash=sha256:" + "a" * 64, valid + "\n" + valid,
                    "synthetic==1.0 --hash=md5:" + "a" * 32, ""):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                BUILD.parse_lock(bad.encode())

    def test_installed_inventory_checks_exact_set_versions_python_and_platform(self):
        expected = {**self.contract["runtime"], "pip": self.contract["installer_version"]}
        with mock.patch.object(BUILD.platform, "python_version", return_value="3.12.14"), \
                mock.patch.object(BUILD.platform, "machine", return_value="x86_64"), \
                mock.patch.object(BUILD, "installed_inventory", return_value=expected) as installed:
            self.assertEqual(BUILD.verify_installed(self.root, "linux/amd64")["distributions"], expected)
            with self.assertRaisesRegex(ValueError, "PLATFORM"):
                BUILD.verify_installed(self.root, "linux/arm64")
            for bad in ({**expected, "unreviewed": "1"}, {**expected, "mcp": "0"},
                        {k: v for k, v in expected.items() if k != "mcp"}):
                installed.return_value = bad
                with self.assertRaisesRegex(ValueError, "INVENTORY"):
                    BUILD.verify_installed(self.root)
        with mock.patch.object(BUILD.platform, "python_version", return_value="3.12.99"):
            with self.assertRaisesRegex(ValueError, "PYTHON"):
                BUILD.verify_installed(self.root)

    def test_duplicate_installed_distribution_refuses(self):
        item = mock.Mock(metadata={"Name": "synthetic"}, version="1")
        with mock.patch.object(BUILD.importlib.metadata, "distributions", return_value=[item, item]):
            with self.assertRaisesRegex(ValueError, "DUPLICATE"):
                BUILD.installed_inventory()

    def test_cli_failure_is_bounded_and_does_not_echo_exception(self):
        output = io.StringIO()
        with mock.patch.object(BUILD, "load_inputs", side_effect=OSError("synthetic-sensitive-detail")), \
                contextlib.redirect_stdout(output):
            self.assertEqual(BUILD.main(["source", "--root", str(self.root)]), 1)
        self.assertNotIn("synthetic-sensitive-detail", output.getvalue())
        self.assertLess(len(output.getvalue()), 150)
        self.assertEqual(json.loads(output.getvalue())["status"], "FAIL")

    def test_file_bound_and_missing_file_refuse(self):
        path = self.root / "oversized"
        path.write_bytes(b"x" * 101)
        with self.assertRaises(ValueError):
            BUILD.read_bytes(path, 100)
        with self.assertRaises(OSError):
            BUILD.read_bytes(self.root / "missing")

    def test_build_uses_wheels_only_offline_install_and_networkless_smoke(self):
        dockerfile = (BETA / "Dockerfile").read_text()
        for required in ("--require-hashes", "--only-binary=:all:", "--no-index",
                         "--find-links=/wheels", "python -m pip check",
                         "RUN --network=none python /app/build_inputs.py smoke"):
            self.assertIn(required, dockerfile)
        for forbidden in ("apt-get", "pip wheel", "pip install --upgrade", "curl "):
            self.assertNotIn(forbidden, dockerfile)
        self.assertLess(dockerfile.index("COPY ha_mcp_engineering"), dockerfile.index("RUN --network=none"))

    def test_real_pip_accepts_exact_local_wheel_and_refuses_tampering_before_install(self):
        # Real pip, no network/index/build backend. Synthetic package only.
        wheel = self.root / "synthetic_input-1.0-py3-none-any.whl"
        with zipfile.ZipFile(wheel, "w") as archive:
            archive.writestr("synthetic_input.py", "VALUE = 7\n")
            archive.writestr("synthetic_input-1.0.dist-info/METADATA",
                             "Metadata-Version: 2.1\nName: synthetic-input\nVersion: 1.0\n")
            archive.writestr("synthetic_input-1.0.dist-info/WHEEL",
                             "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
            archive.writestr("synthetic_input-1.0.dist-info/RECORD", "")
        lock = self.root / "synthetic.lock"
        lock.write_text("synthetic-input==1.0 --hash=sha256:" + hashlib.sha256(wheel.read_bytes()).hexdigest())
        args = [sys.executable, "-m", "pip", "--isolated", "install", "--disable-pip-version-check",
                "--no-index", "--find-links", str(self.root), "--require-hashes", "--only-binary=:all:",
                "--no-deps", "-r", str(lock)]
        target = self.root / "accepted"
        good = subprocess.run([*args, "--target", str(target)], capture_output=True, timeout=30)
        self.assertEqual(good.returncode, 0, good.stderr.decode())
        self.assertEqual((target / "synthetic_input.py").read_text(), "VALUE = 7\n")
        with zipfile.ZipFile(wheel, "a") as archive:
            archive.writestr("unreviewed.txt", "changed")
        target = self.root / "refused"
        bad = subprocess.run([*args, "--target", str(target)], capture_output=True, timeout=30)
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn(b"DO NOT MATCH THE HASHES", bad.stderr)
        self.assertFalse((target / "synthetic_input.py").exists())


class LockPreparationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.report = {"version": "1", "pip_version": "25.0.1", "install": [{
            "metadata": {"name": "synthetic", "version": "1.0"}, "is_yanked": False, "is_direct": False,
            "download_info": {"url": "https://files.pythonhosted.org/packages/synthetic.whl",
                              "archive_info": {"hashes": {"sha256": "a" * 64}}}}]}

    def write(self, report, name="report.json"):
        path = self.root / name
        path.write_text(json.dumps(report))
        return path

    def test_same_versions_merge_architecture_hashes_and_render_deterministically(self):
        first = self.write(self.report)
        second = copy.deepcopy(self.report)
        second["install"][0]["download_info"]["archive_info"]["hashes"]["sha256"] = "b" * 64
        second = self.write(second, "arm64.json")
        raw = PREPARE.prepare([first, second])
        self.assertEqual(raw, PREPARE.prepare([second, first]))
        self.assertEqual(BUILD.parse_lock(raw)["synthetic"], ("1.0", ("a" * 64, "b" * 64)))

    def test_refuses_yanked_source_archive_untrusted_url_hash_and_version_drift(self):
        first = self.write(self.report)
        for change in (
            lambda x: x.update(is_yanked=True), lambda x: x.update(is_direct=True),
            lambda x: x["download_info"].update(url="https://files.pythonhosted.org/source.tar.gz"),
            lambda x: x["download_info"].update(url="https://unreviewed.invalid/file.whl"),
            lambda x: x["download_info"]["archive_info"]["hashes"].update(sha256="bad"),
            lambda x: x["metadata"].update(version="2.0"),
        ):
            second = copy.deepcopy(self.report)
            change(second["install"][0])
            with self.subTest(second=second), self.assertRaises(ValueError):
                PREPARE.prepare([first, self.write(second, "changed.json")])

    def test_test_lock_requires_all_runtime_pins_and_admitted_hashes(self):
        report = self.write(self.report)
        runtime = self.root / "runtime.lock"
        runtime.write_text("synthetic==1.0 --hash=sha256:" + "a" * 64 + " --hash=sha256:" + "b" * 64)
        self.assertEqual(len(BUILD.parse_lock(PREPARE.prepare([report], runtime))["synthetic"][1]), 2)
        for bad in ("synthetic==2.0", "missing==1.0"):
            runtime.write_text(bad + " --hash=sha256:" + "a" * 64)
            with self.assertRaisesRegex(ValueError, "RUNTIME_DRIFT"):
                PREPARE.prepare([report], runtime)

    def test_existing_output_is_not_overwritten(self):
        path = self.write(self.report)
        output = self.root / "existing.lock"
        output.write_bytes(b"preserve")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(PREPARE.main(["--report", str(path), "--output", str(output)]), 1)
        self.assertEqual(output.read_bytes(), b"preserve")


class PackagingWorkflowExecutionTests(unittest.TestCase):
    def test_both_architectures_execute_and_failures_stop_with_cleanup(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
        script = next(s["run"] for s in workflow["jobs"]["validate_packaging"]["steps"]
                      if s.get("name") == "Validate beta image on every declared architecture without pushing")
        for fail in (False, True):
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                docker = root / "docker"
                docker.write_text(f"#!{sys.executable}\n" + """
import json, os, sys
from pathlib import Path
with (Path(os.environ['RUNNER_TEMP']) / 'docker.jsonl').open('a') as stream:
    stream.write(json.dumps(sys.argv[1:]) + '\\n')
if sys.argv[1] == 'run':
    print('{"status":"PASS","fixture":true}')
    if os.environ['SYNTHETIC_SMOKE_FAIL'] == 'true':
        raise SystemExit(19)
""")
                docker.chmod(0o700)
                environment = {**os.environ, "PATH": str(root) + os.pathsep + os.environ["PATH"],
                               "RUNNER_TEMP": str(root), "SYNTHETIC_SMOKE_FAIL": str(fail).lower()}
                result = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", script], cwd=ROOT,
                                        env=environment, capture_output=True, timeout=30)
                calls = [json.loads(line) for line in (root / "docker.jsonl").read_text().splitlines()]
                runs = [c for c in calls if c[0] == "run"]
                cleanup = [c for c in calls if c[0] == "rm"]
                self.assertEqual(len(runs), 1 if fail else 2)
                for i, run in enumerate(runs):
                    self.assertIn("linux/" + ("amd64", "arm64")[i], run)
                    self.assertEqual(run[run.index("--network") + 1], "none")
                    self.assertIn("--read-only", run)
                    self.assertIn("/app/build_inputs.py", run)
                    self.assertNotIn("push", run)
                if fail:
                    self.assertEqual(result.returncode, 19)
                    self.assertEqual(cleanup, [["rm", "-f", "engineering-build-smoke-amd64"]])
                    self.assertFalse((root / "build-input-evidence/source-sha.txt").exists())
                else:
                    self.assertEqual(result.returncode, 0, result.stderr.decode())
                    self.assertEqual(cleanup, [])
                    self.assertTrue((root / "build-input-evidence/arm64.json").exists())
                    self.assertEqual(len((root / "build-input-evidence/source-sha.txt").read_text().strip()), 40)

    def test_publication_validation_lock_is_bound_to_workflow_not_recovery_source(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/publish-rc-image.yml").read_text())
        step = next(s for s in workflow["jobs"]["promote"]["steps"]
                    if s.get("name") == "Install release validation dependencies")
        self.assertEqual(step["env"], {"WORKFLOW_AUTHORITY_SHA": "${{ github.sha }}"})
        self.assertIn('git show "${WORKFLOW_AUTHORITY_SHA}:tests/requirements.lock"', step["run"])
        self.assertIn('--require-hashes --only-binary=:all:', step["run"])
        self.assertNotIn('requirements.txt', step["run"])


class PublicationBuildInputsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        beta = self.root / VERIFY.SOURCE_DIRECTORY
        beta.mkdir()
        for name in ("build-inputs.json", "requirements.lock", "requirements.txt", "Dockerfile"):
            shutil.copyfile(BETA / name, beta / name)
        self.sha = publication.ReleaseArchitectureContractTests().source_repo(
            self.root, "arch: [amd64, aarch64]\n")
        self.contract = VERIFY.release_build_inputs(self.root, self.sha)

    def packages(self):
        expected = {**self.contract["runtime"], "pip": self.contract["installer_version"]}
        return [{"name": n, "versionInfo": v, "externalRefs": [{"referenceType": "purl",
                 "referenceLocator": f"pkg:pypi/{n}@{v}"}]} for n, v in expected.items()]

    def dependency(self, platform="linux/amd64"):
        return {"resolvedDependencies": [{"uri": "pkg:docker/python@3.12-slim?platform=" + platform,
            "digest": {"sha256": self.contract["base_image"].split("@sha256:")[1]}}]}

    def test_committed_input_contract_ignores_dirty_checkout(self):
        (self.root / VERIFY.SOURCE_DIRECTORY / "build-inputs.json").write_text("{}")
        self.assertEqual(VERIFY.release_build_inputs(self.root, self.sha), self.contract)
        with self.assertRaises(VERIFY.VerificationError):
            VERIFY.release_build_inputs(self.root, "a" * 40)

    def test_complete_current_publication_checks_inputs_before_emitting_success(self):
        with mock.patch.dict(publication.__dict__, {"RELEASE_SHA": self.sha}):
            selected = {"linux/amd64", "linux/arm64"}
            images = {p: v for p, v in publication.valid_images().items() if p in selected}
            provenance = {p: v for p, v in publication.valid_provenance().items() if p in selected}
            sbom = {p: v for p, v in publication.valid_sbom().items() if p in selected}
            for p in selected:
                provenance[p]["SLSA"]["buildDefinition"].update(self.dependency(p))
                sbom[p]["SPDX"]["packages"] = self.packages()
            for corrupt in (False, True):
                if corrupt:
                    sbom["linux/arm64"]["SPDX"]["packages"].pop()
                result, output, _ = publication.PublicationSourceVerifierTests().verify_source(
                    self.root, manifest=publication.ReleaseArchitectureContractTests().current_manifest(),
                    images=images, provenance=provenance, sbom=sbom)
                if corrupt:
                    self.assertEqual(result, 1)
                    self.assertFalse(output.exists())
                else:
                    self.assertEqual(result, 0)
                    self.assertIn("build_inputs_status=verified", output.read_text())
                    output.unlink()

    def test_final_sbom_refuses_missing_extra_duplicate_and_changed_python_packages(self):
        VERIFY.verify_sbom_inventory(self.packages(), self.contract)
        for change in (
            lambda p: p.pop(), lambda p: p.append(copy.deepcopy(p[0])),
            lambda p: p[0].update(versionInfo="0"),
            lambda p: p.append({"name": "unknown", "versionInfo": "1", "externalRefs": [
                {"referenceType": "purl", "referenceLocator": "pkg:pypi/unknown@1"}]}),
        ):
            packages = self.packages()
            change(packages)
            with self.subTest(change=change), self.assertRaises(VERIFY.VerificationError):
                VERIFY.verify_sbom_inventory(packages, self.contract)

    def test_provenance_requires_expected_platform_and_exact_base_digest(self):
        for platform in self.contract["base_platforms"]:
            value = self.dependency(platform)
            VERIFY.verify_base_dependency(value, self.contract, platform)
            value["resolvedDependencies"][0]["digest"]["sha256"] = self.contract["base_platforms"][platform]["manifest_digest"][7:]
            VERIFY.verify_base_dependency(value, self.contract, platform)
        for value in ({}, {"resolvedDependencies": []}, {"resolvedDependencies": self.dependency()["resolvedDependencies"] * 2},
                      self.dependency("linux/arm/v7")):
            with self.subTest(value=value), self.assertRaises(VERIFY.VerificationError):
                VERIFY.verify_base_dependency(value, self.contract, "linux/amd64")
        value = self.dependency()
        value["resolvedDependencies"][0]["digest"]["sha256"] = "a" * 64
        with self.assertRaisesRegex(VERIFY.VerificationError, "BASE_INPUT_MISMATCH"):
            VERIFY.verify_base_dependency(value, self.contract, "linux/amd64")

    def test_committed_input_corruption_cannot_be_treated_as_legacy_absence(self):
        lock = self.root / VERIFY.SOURCE_DIRECTORY / "requirements.lock"
        lock.write_bytes(lock.read_bytes() + b"# tampered\n")
        subprocess.run(["git", "-C", str(self.root), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.root), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                        "-c", "commit.gpgsign=false", "commit", "-qm", "changed lock"], check=True, capture_output=True)
        sha = subprocess.check_output(["git", "-C", str(self.root), "rev-parse", "HEAD"], text=True).strip()
        with self.assertRaisesRegex(VERIFY.VerificationError, "HASH_MISMATCH"):
            VERIFY.release_build_inputs(self.root, sha)


if __name__ == "__main__":
    unittest.main()
