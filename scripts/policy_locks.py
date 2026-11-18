"""Policy-lock checks for pull requests (CODEX-022), run only from the base.

``lint`` compares the base and pull-request workflow trees and fails when a
validation workflow gains a write grant, a publication step, an unpinned
action, a floating runner or a reserved required-check name. ``scan`` looks
for high-confidence credential patterns (check.ps1 Test-SecretPatterns) in
the pull-request tree.

Both treat the pull-request tree as data: nothing in it is executed,
imported or installed, symlinks are never followed, and findings name a file
and line or job, never a matched value.
"""

import argparse
import os
from pathlib import Path
import re
import stat
import sys

WORKFLOWS = Path(".github/workflows")
# Only these may publish, tag, merge or hold write grants.
EXEMPT = frozenset({"publish-rc-image.yml", "enable-auto-merge.yml"})
WRITE_SCOPES = ("contents", "packages", "id-token", "attestations")
# Required checks and the only workflow allowed to produce each.
RESERVED_CHECKS = {
    "validate": "ci.yml",
    "policy-workflow-lint": "policy-locks.yml",
    "policy-secret-scan": "policy-locks.yml",
}
MAX_WORKFLOW_BYTES = 512 * 1024
MAX_SCAN_BYTES = 2 * 1024 * 1024
MAX_STRINGS = 10000
PINNED = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}")

# check.ps1 Test-SecretPatterns, matched case-insensitively as PowerShell
# -match does; the sk- group scopes its own case sensitivity.
SECRET_PATTERNS = (
    ("GitHub fine-grained token", r"github_pat_[A-Za-z0-9_]{20,}"),
    ("GitHub token", r"gh[pousr]_[A-Za-z0-9]{36,}"),
    ("API secret key",
     r"(?-i:(?<![A-Za-z0-9])sk-(?=[A-Za-z0-9_-]*[0-9A-Z])[A-Za-z0-9_-]{20,})"),
    ("private key", "-----BEGIN " + r"(?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
)
SECRETS = [(label, re.compile(pattern, re.IGNORECASE)) for label, pattern in SECRET_PATTERNS]

# Publication or merge commands in a validation workflow's run scripts.
PUBLISH_COMMANDS = (
    ("git push", r"\bgit\s+push\b"),
    ("git tag", r"\bgit\s+tag\s+(?!(?:-l|--list|-n\d*|--points-at|--contains|--merged"
                r"|--no-merged)\b)\S"),
    ("gh release", r"\bgh\s+release\s+(?:create|upload|edit|delete)\b"),
    ("gh pr merge", r"\bgh\s+pr\s+merge\b"),
    ("gh workflow run", r"\bgh\s+workflow\s+run\b"),
    ("gh api write method",
     r"\bgh\s+api\b[^\n]*(?:-X|--method)[\s=]*['\"]?(?:POST|PUT|PATCH|DELETE)\b"),
    ("docker push", r"\bdocker\s+(?:image\s+)?push\b"),
    ("buildx --push", r"\bdocker\s+buildx\s+(?:build|bake)\b[^\n]*--push\b"),
    ("registry copy", r"\b(?:oras|crane)\s+(?:push|copy|cp|tag)\b|\bskopeo\s+copy\b"),
    ("cosign", r"\bcosign\s+(?:sign|attest)\b"),
    ("package publish", r"\btwine\s+upload\b|\b(?:npm|pnpm|yarn)\s+publish\b"),
)
PUBLISH = [(label, re.compile(pattern, re.IGNORECASE)) for label, pattern in PUBLISH_COMMANDS]
PUBLISH_ACTION = re.compile(r"release|publish|deploy|merge|(?:^|[-_/])tags?(?:$|[-_/])"
                            r"|^actions/attest|^sigstore/", re.IGNORECASE)

MOVE = ("Move publish or merge steps to publish-rc-image.yml or enable-auto-merge.yml; "
        "keep validation workflows read-only.")
FIX = {
    "write_permission": "Grant only read scopes here. " + MOVE,
    "implicit_permission": "Declare an explicit read-only permissions block for the "
                           "workflow or this job.",
    "publish_step": MOVE,
    "unpinned_action": "Pin the action to a full 40-character commit SHA and keep the "
                       "version in a trailing comment.",
    "floating_runner": "Use an explicit runner image such as ubuntu-24.04, not *-latest.",
    "dynamic_runner": "Use a literal runner label, or a matrix of literal labels, so "
                      "the runner image can be checked.",
    "reserved_check": "Rename this job; that check name belongs to its required "
                      "producer workflow.",
    "unreadable_workflow": "Commit the workflow as a regular, valid YAML file under "
                           "512 KiB.",
}


class Finding:
    """A violation keyed without line numbers so that unchanged ones match."""

    def __init__(self, path, kind, where, detail=""):
        self.path, self.kind, self.where, self.detail = path, kind, where, detail

    @property
    def key(self):
        return (self.path, self.kind, self.where, self.detail)

    def message(self):
        detail = f" ({self.detail})" if self.detail else ""
        return f"{self.where}: {self.kind}{detail}. {FIX[self.kind]}"


def escape_data(value):
    return str(value).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def escape_property(value):
    return escape_data(value).replace(":", "%3A").replace(",", "%2C")


def annotate(path, message, line=None, stream=None):
    """Emit one GitHub error annotation; repository text cannot start a command."""

    location = f"file={escape_property(path)}"
    if line is not None:
        location += f",line={int(line)}"
    print(f"::error {location}::{escape_data(message)}", file=stream or sys.stdout)


def regular_file(path):
    """Return ``path``'s bytes only for a regular file, never through a link."""

    try:
        info = os.lstat(path)
    except OSError:
        return None
    if not stat.S_ISREG(info.st_mode):
        return None
    with open(path, "rb") as handle:
        return handle.read()


def load_workflow(raw):
    import yaml  # Base-side pinned dependency; the scan never needs it.

    return yaml.safe_load(raw)


def strings(value, limit=MAX_STRINGS):
    """Yield up to ``limit`` strings from a parsed value (aliases may repeat)."""

    pending = [value]
    while pending and limit > 0:
        item = pending.pop()
        if isinstance(item, str):
            limit -= 1
            yield item
        elif isinstance(item, dict):
            pending.extend(reversed(list(item.values())))
        elif isinstance(item, list):
            pending.extend(reversed(item))


def permission_findings(path, where, permissions):
    if permissions == "write-all":
        return [Finding(path, "write_permission", where, "write-all")]
    if permissions in ("read-all", None) or permissions == {}:
        return []
    if not isinstance(permissions, dict):
        return [Finding(path, "write_permission", where, "non-literal permissions")]
    return [Finding(path, "write_permission", where, f"{scope}: write")
            for scope in WRITE_SCOPES if permissions.get(scope) == "write"]


def uses_findings(path, where, uses, settings):
    found = []
    if not isinstance(uses, str):
        return [Finding(path, "unpinned_action", where, "non-literal uses")]
    if uses.startswith("./"):
        return found
    if uses.startswith("docker://"):
        if not DIGEST.search(uses):
            found.append(Finding(path, "unpinned_action", where, uses))
        return found
    name, _, ref = uses.partition("@")
    if not PINNED.fullmatch(ref):
        found.append(Finding(path, "unpinned_action", where, uses))
    settings = settings if isinstance(settings, dict) else {}
    pushes = name == "docker/build-push-action" and (
        str(settings.get("push", "false")).strip().lower() != "false"
        or re.search(r"registry|push=true", str(settings.get("outputs", "")), re.I))
    if pushes or (name != "docker/build-push-action" and PUBLISH_ACTION.search(name)):
        found.append(Finding(path, "publish_step", where, name))
    return found


def runner_findings(path, where, job):
    runs_on = job.get("runs-on")
    if isinstance(runs_on, dict):
        runs_on = [runs_on.get("group"), runs_on.get("labels")]
    labels = list(strings(runs_on))
    found = []
    for label in labels:
        if "${{" not in label:
            continue
        if re.fullmatch(r"\$\{\{\s*matrix\.[A-Za-z0-9_-]+\s*\}\}", label):
            # Every literal matrix value is checked; a computed matrix is not.
            matrix = (job.get("strategy") or {}).get("matrix")
            if isinstance(matrix, str) or any("${{" in value for value in strings(matrix)):
                found.append(Finding(path, "dynamic_runner", where, label))
            else:
                labels = labels + [value for value in strings(matrix)
                                   if value not in labels]
        else:
            found.append(Finding(path, "dynamic_runner", where, label))
    found += [Finding(path, "floating_runner", where, label.strip())
              for label in dict.fromkeys(labels)
              if "${{" not in label and label.strip().lower().endswith("-latest")]
    return found


def publish_findings(path, where, script):
    script = re.sub(r"\\\r?\n", " ", script)  # Join continued lines.
    found = [Finding(path, "publish_step", where, label)
             for label, pattern in PUBLISH if pattern.search(script)]
    for line in script.splitlines():
        # gh api sends POST once it has fields, unless the method is GET.
        if (re.search(r"\bgh\s+api\b", line)
                and re.search(r"\s(?:-[fF]|--field|--raw-field|--input)[\s=]", line)
                and not re.search(r"(?:-X|--method)[\s=]*['\"]?GET\b", line, re.I)):
            found.append(Finding(path, "publish_step", where, "gh api with fields"))
            break
    return found


def reserved_findings(path, name, job_id, job):
    found = []
    check = job.get("name", job_id)
    for reserved, producer in RESERVED_CHECKS.items():
        if name == producer:
            continue
        literal = isinstance(check, str) and "${{" not in check
        if (check == reserved) if literal else reserved in str(check):
            found.append(Finding(path, "reserved_check", f"job {job_id}", reserved))
    return found


def workflow_findings(path, name, raw):
    """Return the findings for one workflow file's bytes (``None`` if absent)."""

    if raw is None or len(raw) > MAX_WORKFLOW_BYTES:
        return [Finding(path, "unreadable_workflow", "workflow")]
    try:
        document = load_workflow(raw)
    except Exception:  # Any parser failure is a refusal, never a pass.
        return [Finding(path, "unreadable_workflow", "workflow")]
    if not isinstance(document, dict) or not isinstance(document.get("jobs"), dict):
        return [Finding(path, "unreadable_workflow", "workflow")]
    validation = name not in EXEMPT
    found = []
    top = document.get("permissions")
    if validation:
        found += permission_findings(path, "workflow", top)
    for job_id, job in document["jobs"].items():
        where = f"job {job_id}"
        if not isinstance(job, dict):
            found.append(Finding(path, "unreadable_workflow", where))
            continue
        found += reserved_findings(path, name, job_id, job)
        if not validation:
            continue
        if "permissions" in job:
            found += permission_findings(path, where, job["permissions"])
        elif top is None:
            found.append(Finding(path, "implicit_permission", where))
        if "uses" in job:  # A reusable workflow call.
            found += uses_findings(path, where, job["uses"], {})
        found += runner_findings(path, where, job)
        steps = job.get("steps") or []
        if not isinstance(steps, list):
            found.append(Finding(path, "unreadable_workflow", where))
            continue
        for index, step in enumerate(steps, 1):
            step_where = f"{where} step {index}"
            if not isinstance(step, dict):
                found.append(Finding(path, "unreadable_workflow", step_where))
                continue
            if "uses" in step:
                found += uses_findings(path, step_where, step["uses"], step.get("with"))
            if isinstance(step.get("run"), str):
                found += publish_findings(path, step_where, step["run"])
    return found


def workflow_tree(root):
    """Map workflow file name to bytes (``None`` when unreadable) for a tree."""

    directory = Path(root) / WORKFLOWS
    try:
        names = sorted(os.listdir(directory))
    except FileNotFoundError:
        return {}
    return {name: regular_file(directory / name) for name in names
            if name.endswith((".yml", ".yaml"))}


def lint(base, head):
    """Return findings present in ``head`` but not in ``base``.

    A finding already on the base is not new. An unreadable head workflow is
    always reported, because it would hide every other finding in that file.
    """

    def findings(root):
        result = {}
        for name, raw in workflow_tree(root).items():
            for finding in workflow_findings(str(WORKFLOWS / name), name, raw):
                result.setdefault(finding.key, finding)
        return result

    known = findings(base)
    return [finding for key, finding in sorted(findings(head).items())
            if key not in known or finding.kind == "unreadable_workflow"]


def scan(root):
    """Return ``(path, line, label)`` for each credential pattern in ``root``."""

    root = Path(root)
    found = []
    for directory, subdirectories, files in os.walk(root, followlinks=False):
        subdirectories[:] = sorted(name for name in subdirectories if name != ".git")
        for name in sorted(files):
            path = Path(directory) / name
            try:
                if os.lstat(path).st_size > MAX_SCAN_BYTES:
                    continue
            except OSError:
                continue
            raw = regular_file(path)
            if raw is None:
                continue
            text = raw.decode("latin-1")  # Lossless; the patterns are ASCII.
            relative = path.relative_to(root).as_posix()
            for label, pattern in SECRETS:
                for match in pattern.finditer(text):
                    found.append((relative, text.count("\n", 0, match.start()) + 1, label))
    return sorted(set(found))


def main(argv=None, stdout=None):
    stdout = stdout or sys.stdout
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    lint_parser = commands.add_parser("lint")
    lint_parser.add_argument("--base", required=True, type=Path)
    lint_parser.add_argument("--head", required=True, type=Path)
    scan_parser = commands.add_parser("scan")
    scan_parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.command == "lint":
        findings = lint(args.base, args.head)
        for finding in findings:
            annotate(finding.path, finding.message(), stream=stdout)
        print(f"policy-workflow-lint: {len(findings)} new workflow policy violation(s).",
              file=stdout)
        return 1 if findings else 0
    findings = scan(args.root)
    for path, line, label in findings:
        annotate(path, f"Potential {label} pattern. Remove it from the change, rotate the "
                       "credential, and keep secrets in GitHub secrets or a credential "
                       "manager; never commit them.", line=line, stream=stdout)
    print(f"policy-secret-scan: {len(findings)} credential pattern finding(s).", file=stdout)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
