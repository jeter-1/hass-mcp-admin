"""CODEX-022 policy locks: offline lint and scan fixtures in disposable trees."""

import importlib.util
import io
import os
from pathlib import Path
import re
import subprocess
import tempfile
import textwrap
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("policy_locks", ROOT / "scripts/policy_locks.py")
locks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(locks)
provenance_spec = importlib.util.spec_from_file_location(
    "ci_provenance_for_locks", ROOT / "scripts/validate_ci_provenance.py")
provenance = importlib.util.module_from_spec(provenance_spec)
provenance_spec.loader.exec_module(provenance)
WORKFLOW = ROOT / ".github/workflows/policy-locks.yml"
SHA = "a" * 40

CLEAN = f"""
name: Clean
on: pull_request
permissions:
  contents: read
jobs:
  test:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@{SHA} # v5
      - run: python -m unittest
"""


def tree(root, workflows):
    directory = Path(root) / ".github/workflows"
    directory.mkdir(parents=True, exist_ok=True)
    for name, text in workflows.items():
        (directory / name).write_text(textwrap.dedent(text), encoding="utf-8")
    return Path(root)


class LintTests(unittest.TestCase):
    def lint(self, head, base=None):
        with tempfile.TemporaryDirectory() as b, tempfile.TemporaryDirectory() as h:
            tree(b, base or {})
            tree(h, head)
            return [(f.path, f.kind, f.where, f.detail) for f in locks.lint(b, h)]

    def kinds(self, text, name="ci-extra.yml"):
        return {kind for _path, kind, _where, _detail in self.lint({name: text})}

    def test_clean_validation_workflow_passes(self):
        self.assertEqual(self.lint({"ci-extra.yml": CLEAN}), [])

    def test_write_grants_fail_at_workflow_and_job_level(self):
        for scope in locks.WRITE_SCOPES:
            with self.subTest(scope=scope):
                text = CLEAN.replace("contents: read", f"{scope}: write")
                self.assertIn("write_permission", self.kinds(text))
                job = CLEAN.replace("    runs-on:", f"    permissions:\n      {scope}: write\n    runs-on:")
                self.assertIn(("job test", f"{scope}: write"),
                              {(w, d) for _p, k, w, d in self.lint({"x.yml": job})})
        self.assertIn("write_permission", self.kinds(CLEAN.replace(
            "permissions:\n  contents: read", "permissions: write-all")))
        # Read grants and unrelated write scopes are not this lock.
        self.assertEqual(self.kinds(CLEAN.replace("contents: read", "checks: write")), set())

    def test_missing_permissions_fail_closed(self):
        text = CLEAN.replace("permissions:\n  contents: read\n", "")
        self.assertEqual(self.kinds(text), {"implicit_permission"})

    def test_publication_steps_fail(self):
        commands = (
            "git push origin HEAD", "git tag v1.0.0", "gh release create v1",
            "gh pr merge 1 --merge", "gh workflow run publish.yml",
            "gh api -X POST repos/o/r/issues", "gh api --method=DELETE repos/o/r/git/refs/x",
            "gh api repos/o/r/issues/1/comments -f body=x", "docker push ghcr.io/o/i:1",
            "docker buildx build \\\n  --push .", "skopeo copy a b", "cosign sign x",
            "twine upload dist/*", "npm publish",
        )
        for command in commands:
            with self.subTest(command=command):
                text = CLEAN.replace("python -m unittest", "|\n          " + command.replace(
                    "\n", "\n          "))
                self.assertEqual(self.kinds(text), {"publish_step"})

    def test_read_only_commands_pass(self):
        for command in ("git tag --list", "git tag -l 'v*'", "gh api repos/o/r/pulls/1",
                        "gh api -X GET repos/o/r/issues -f state=open", "git fetch origin",
                        "docker buildx build --load .", "gh release view v1"):
            with self.subTest(command=command):
                text = CLEAN.replace("python -m unittest", command)
                self.assertEqual(self.kinds(text), set())

    def test_release_and_publish_actions_fail(self):
        for action in ("softprops/action-gh-release", "actions/attest-build-provenance",
                       "sigstore/cosign-installer", "peaceiris/actions-gh-pages-deploy",
                       "pascalgn/automerge-action", "mathieudutour/github-tag-action"):
            with self.subTest(action=action):
                text = CLEAN.replace("python -m unittest", "true").replace(
                    "actions/checkout", action)
                self.assertIn("publish_step", self.kinds(text))
        push = CLEAN.replace("- run: python -m unittest", f"""- uses: docker/build-push-action@{SHA}
        with:
          push: true""")
        self.assertEqual(self.kinds(push), {"publish_step"})
        load = push.replace("push: true", "push: false\n          load: true")
        self.assertEqual(self.kinds(load), set())

    def test_unpinned_actions_and_images_fail(self):
        for ref in ("actions/checkout@v5", "actions/checkout@main",
                    f"actions/checkout@{SHA[:39]}", "actions/checkout",
                    "docker://alpine:3", "o/r/.github/workflows/w.yml@main"):
            with self.subTest(ref=ref):
                text = CLEAN.replace(f"actions/checkout@{SHA}", ref)
                self.assertEqual(self.kinds(text), {"unpinned_action"})
        digest = CLEAN.replace(f"actions/checkout@{SHA}", "docker://alpine@sha256:" + "b" * 64)
        self.assertEqual(self.kinds(digest), set())
        reusable = f"""
        on: pull_request
        permissions: {{contents: read}}
        jobs:
          call:
            uses: o/r/.github/workflows/w.yml@v1
        """
        self.assertEqual(self.kinds(reusable), {"unpinned_action"})

    def test_floating_and_dynamic_runners_fail(self):
        for runs_on in ("ubuntu-latest", "[self-hosted, windows-latest]",
                        "{group: big, labels: [macos-latest]}"):
            with self.subTest(runs_on=runs_on):
                text = CLEAN.replace("ubuntu-24.04", runs_on)
                self.assertEqual(self.kinds(text), {"floating_runner"})
        matrix = CLEAN.replace("runs-on: ubuntu-24.04", """strategy:
      matrix:
        os: [ubuntu-24.04, ubuntu-latest]
    runs-on: ${{ matrix.os }}""")
        self.assertEqual(self.kinds(matrix), {"floating_runner"})
        self.assertEqual(self.kinds(matrix.replace(", ubuntu-latest", "")), set())
        for runs_on in ("${{ inputs.runner }}", "${{ matrix.runner || 'ubuntu-latest' }}"):
            with self.subTest(runs_on=runs_on):
                text = CLEAN.replace("ubuntu-24.04", f'"{runs_on}"')
                self.assertEqual(self.kinds(text), {"dynamic_runner"})
        computed = matrix.replace("os: [ubuntu-24.04, ubuntu-latest]",
                                  "os: ${{ fromJSON(needs.m.outputs.os) }}")
        self.assertEqual(self.kinds(computed), {"dynamic_runner"})

    def test_reserved_check_names_belong_to_their_producers(self):
        for job_name in ("validate", "policy-workflow-lint", "policy-secret-scan"):
            with self.subTest(job_name=job_name):
                text = CLEAN.replace("  test:", f"  {job_name}:")
                self.assertEqual(self.kinds(text), {"reserved_check"})
                named = CLEAN.replace("    runs-on:", f"    name: {job_name}\n    runs-on:")
                # Even exempt workflows cannot produce a required check.
                self.assertEqual(self.kinds(named, "enable-auto-merge.yml"), {"reserved_check"})
        expression = CLEAN.replace("    runs-on:", "    name: ${{ 'validate' }}\n    runs-on:")
        self.assertEqual(self.kinds(expression), {"reserved_check"})
        self.assertEqual(self.kinds(CLEAN.replace("  test:", "  validate:"), "ci.yml"), set())
        self.assertEqual(self.kinds(CLEAN.replace("  test:", "  policy-secret-scan:"),
                                    "policy-locks.yml"), set())
        self.assertEqual(self.kinds(CLEAN.replace("  test:", "  validate-docs:")), set())

    def test_reserved_checks_match_the_merge_path_producers(self):
        self.assertEqual(locks.RESERVED_CHECKS,
                         {name: workflow for name, (workflow, _event)
                          in provenance.PRODUCERS.items()})

    def test_exempt_workflows_may_publish(self):
        text = CLEAN.replace("contents: read", "contents: write").replace(
            "python -m unittest", "gh pr merge 1 --merge")
        for name in sorted(locks.EXEMPT):
            with self.subTest(name=name):
                self.assertEqual(self.kinds(text, name), set())
        self.assertEqual(locks.EXEMPT, {"publish-rc-image.yml", "enable-auto-merge.yml"})

    def test_existing_violations_are_grandfathered_but_new_ones_fail(self):
        bad = CLEAN.replace("contents: read", "contents: write")
        self.assertEqual(self.lint({"x.yml": bad}, base={"x.yml": bad}), [])
        self.assertEqual(self.lint({"x.yml": "\n\n" + bad}, base={"x.yml": bad}), [])
        moved = self.lint({"y.yml": bad}, base={"x.yml": bad})
        self.assertEqual([(p, k) for p, k, _w, _d in moved],
                         [(".github/workflows/y.yml", "write_permission")])
        more = bad.replace("contents: write", "contents: write\n  packages: write")
        self.assertEqual([d for _p, _k, _w, d in self.lint({"x.yml": more}, base={"x.yml": bad})],
                         ["packages: write"])

    def test_unreadable_head_workflows_are_always_reported(self):
        for text in ("jobs: [", "just a string", "on: push\n", "jobs:\n  a: 1\n"):
            with self.subTest(text=text):
                found = self.lint({"x.yml": text}, base={"x.yml": text})
                self.assertIn("unreadable_workflow", {k for _p, k, _w, _d in found})
        big = CLEAN + "#" * (locks.MAX_WORKFLOW_BYTES + 1)
        self.assertEqual(self.kinds(big), {"unreadable_workflow"})

    def test_symlinked_workflow_is_not_followed(self):
        with tempfile.TemporaryDirectory() as b, tempfile.TemporaryDirectory() as h:
            tree(b, {})
            head = tree(h, {})
            outside = Path(h) / "outside.yml"
            outside.write_text(CLEAN, encoding="utf-8")
            os.symlink(outside, head / ".github/workflows/linked.yml")
            found = locks.lint(b, h)
        self.assertEqual([(f.path, f.kind) for f in found],
                         [(".github/workflows/linked.yml", "unreadable_workflow")])

    def test_aliases_cannot_expand_without_bound(self):
        bomb = "a: &a [x, x, x, x, x, x, x, x, x, x]\n" + "".join(
            f"{chr(98 + i)}: &{chr(98 + i)} [*{chr(97 + i)}, *{chr(97 + i)}, *{chr(97 + i)},"
            f" *{chr(97 + i)}, *{chr(97 + i)}, *{chr(97 + i)}, *{chr(97 + i)},"
            f" *{chr(97 + i)}, *{chr(97 + i)}, *{chr(97 + i)}]\n" for i in range(8))
        document = yaml.safe_load(bomb)
        self.assertEqual(sum(1 for _ in locks.strings(document)), locks.MAX_STRINGS)

    def test_main_reports_annotations_and_exit_status(self):
        with tempfile.TemporaryDirectory() as b, tempfile.TemporaryDirectory() as h:
            tree(b, {})
            tree(h, {"x.yml": CLEAN.replace("contents: read", "contents: write")})
            out = io.StringIO()
            self.assertEqual(locks.main(["lint", "--base", b, "--head", h], stdout=out), 1)
            self.assertIn("::error file=.github/workflows/x.yml::workflow: write_permission",
                          out.getvalue())
            self.assertIn("keep validation workflows read-only", out.getvalue())
            self.assertIn("1 new workflow policy violation(s).", out.getvalue())
            out = io.StringIO()
            self.assertEqual(locks.main(["lint", "--base", h, "--head", h], stdout=out), 0)

    def test_annotation_text_cannot_inject_workflow_commands(self):
        out = io.StringIO()
        locks.annotate("a,b:c%.yml", "x\n::add-mask::y\r%", line=3, stream=out)
        self.assertEqual(out.getvalue(),
                         "::error file=a%2Cb%3Ac%25.yml,line=3::x%0A::add-mask::y%0D%25\n")
        self.assertEqual(out.getvalue().count("\n"), 1)

    def test_current_main_workflows_have_no_new_violations(self):
        found = locks.lint(ROOT, ROOT)
        self.assertEqual(found, [])
        with tempfile.TemporaryDirectory() as empty:
            names = {f.path for f in locks.lint(empty, ROOT)}
        self.assertNotIn(".github/workflows/policy-locks.yml", names)


class ScanTests(unittest.TestCase):
    # Synthetic values are assembled at runtime so no committed file matches.
    SAMPLES = {
        "GitHub fine-grained token": "github" + "_pat_" + "Z" * 30,
        "GitHub token": "gh" + "p_" + "Z" * 36,
        "API secret key": "s" + "k-" + "SYNTHETIC0" * 3,
        "private key": "-----BEGIN " + "OPENSSH PRIVATE KEY-----",
    }

    def test_patterns_match_check_ps1(self):
        text = (ROOT / "scripts/check.ps1").read_text(encoding="utf-8")
        body = text[text.index("function Test-SecretPatterns"):]
        body = body[:body.index("$matchedFiles")]
        declared = [re.sub(r"'\s*\+\s*'", "", value)
                    for value in re.findall(r"^\s*\(?('[^\n]*')\)?,?\s*$", body, re.M)]
        declared = [value.strip("'") for value in declared]
        self.assertEqual(declared, [pattern for _label, pattern in locks.SECRET_PATTERNS])

    def test_each_pattern_is_reported_by_file_and_line_only(self):
        with tempfile.TemporaryDirectory() as root:
            for index, (label, value) in enumerate(sorted(self.SAMPLES.items())):
                (Path(root) / f"f{index}.txt").write_text(f"one\ntwo {value}\n", encoding="utf-8")
            found = locks.scan(root)
            self.assertEqual(found, [(f"f{index}.txt", 2, label)
                                     for index, label in enumerate(sorted(self.SAMPLES))])
            out = io.StringIO()
            self.assertEqual(locks.main(["scan", "--root", root], stdout=out), 1)
        for value in self.SAMPLES.values():
            self.assertNotIn(value, out.getvalue())
        self.assertIn("::error file=f0.txt,line=2::Potential", out.getvalue())
        self.assertIn("rotate the credential", out.getvalue())

    def test_lookalikes_git_symlinks_and_large_files_are_skipped(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as other:
            base = Path(root)
            (base / "doc.md").write_text("RISK-AND-ELEVATED-APPROVAL and sk-lowercase-only-anchor-name\n",
                                         encoding="utf-8")
            (base / ".git").mkdir()
            (base / ".git/config").write_text(self.SAMPLES["GitHub token"], encoding="utf-8")
            secret = Path(other) / "s.txt"
            secret.write_text(self.SAMPLES["GitHub token"], encoding="utf-8")
            os.symlink(secret, base / "link.txt")
            os.symlink(other, base / "linkdir")
            (base / "big.bin").write_bytes(b"x" * locks.MAX_SCAN_BYTES
                                           + self.SAMPLES["GitHub token"].encode())
            out = io.StringIO()
            self.assertEqual(locks.scan(root), [])
            self.assertEqual(locks.main(["scan", "--root", root], stdout=out), 0)

    def test_binary_and_non_utf8_files_are_scanned_without_error(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / "b.bin").write_bytes(b"\xff\xfe\x00" + self.SAMPLES["GitHub token"].encode())
            self.assertEqual([label for _p, _l, label in locks.scan(root)], ["GitHub token"])

    def test_repository_tree_is_clean(self):
        tracked = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], check=True,
                                 capture_output=True, text=True).stdout.split("\0")
        with tempfile.TemporaryDirectory() as root:
            for relative in filter(None, tracked):
                source = ROOT / relative
                if source.is_file() and not source.is_symlink():
                    target = Path(root) / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(source.read_bytes())
            self.assertEqual(locks.scan(root), [])


class WorkflowStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.workflow = yaml.safe_load(cls.text)

    def test_trigger_is_unfiltered_pull_request_target(self):
        trigger = self.workflow[True]  # YAML 1.1 reads the key "on" as True.
        self.assertEqual(list(trigger), ["pull_request_target"])
        self.assertEqual(trigger["pull_request_target"],
                         {"types": ["opened", "reopened", "synchronize", "edited"]})

    def test_read_only_token_and_no_secrets(self):
        self.assertEqual(self.workflow["permissions"], {"contents": "read", "pull-requests": "read"})
        self.assertNotIn("secrets.", self.text)
        self.assertNotIn("GITHUB_TOKEN", self.text)
        for job in self.workflow["jobs"].values():
            self.assertNotIn("permissions", job)

    def test_job_names_are_the_required_checks_and_never_validate(self):
        jobs = self.workflow["jobs"]
        self.assertEqual({key: job["name"] for key, job in jobs.items()},
                         {"policy-workflow-lint": "policy-workflow-lint",
                          "policy-secret-scan": "policy-secret-scan"})
        self.assertNotIn("validate", {key.casefold() for key in jobs})

    def test_only_the_base_is_executed(self):
        for name, job in self.workflow["jobs"].items():
            with self.subTest(job=name):
                self.assertEqual(job["runs-on"], "ubuntu-24.04")
                self.assertLessEqual(job["timeout-minutes"], 10)
                checkouts = [step for step in job["steps"]
                             if str(step.get("uses", "")).startswith("actions/checkout@")]
                self.assertEqual([step["with"]["path"] for step in checkouts], ["base", "pr-head"])
                self.assertEqual(checkouts[0]["with"]["ref"], "${{ github.sha }}")
                self.assertEqual(checkouts[1]["with"]["ref"],
                                 "${{ github.event.pull_request.head.sha }}")
                for step in checkouts:
                    self.assertIs(step["with"]["persist-credentials"], False)
                for step in job["steps"]:
                    if "uses" in step:
                        self.assertRegex(step["uses"], r"^[^@]+@[0-9a-f]{40}$")
                    run = str(step.get("run", ""))
                    self.assertNotIn("pr-head/", run.replace("--head pr-head", "")
                                     .replace("--root pr-head", ""))
                    joined = re.sub(r"\\\n\s*", " ", run)
                    for line in filter(None, (l.strip() for l in joined.splitlines())):
                        if line.startswith(("python", "base/")):
                            self.assertRegex(line, r"(?:-r |-I(?: -S)? )base/scripts/")
        lint = self.workflow["jobs"]["policy-workflow-lint"]["steps"][1]["with"]
        self.assertEqual(lint["sparse-checkout"], ".github/workflows")

    def test_parser_requirement_matches_the_hashed_lock(self):
        requirement = (ROOT / "scripts/policy_locks.requirements.txt").read_text(encoding="utf-8")
        lock = (ROOT / "tests/requirements.lock").read_text(encoding="utf-8")
        entry = re.search(r"^pyyaml==[^\n]*(?:\n    --hash=[^\n]*)+", lock, re.M | re.I).group(0)
        body = "\n".join(line for line in requirement.splitlines() if not line.startswith("#"))
        self.assertEqual(body.strip(), entry.strip())
        self.assertIn("--require-hashes", self.text)
        self.assertIn("--only-binary=:all:", self.text)


if __name__ == "__main__":
    unittest.main()
