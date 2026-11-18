"""Policy-lock checks for pull requests (CODEX-022), run only from the base.

``lint`` compares the base and pull-request workflow trees and fails when a
validation workflow gains a write grant, a publication step, an unpinned or
local action, a floating runner or a reserved required-check name. ``scan``
looks for high-confidence credential patterns (check.ps1 Test-SecretPatterns)
in the pull-request tree.

Both treat the pull-request tree as data: nothing in it is executed,
imported or installed, and symlinks are never followed. Findings name a file,
a line, a job or step by position and a fixed violation category; they never
repeat a value from the pull request (action references, runner expressions,
job identifiers, step text or matched credentials).
"""

import argparse
import hashlib
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
# Byte-order marks, as .NET ReadAllText detects them (UTF-32 before UTF-16).
BOMS = (
    (b"\x00\x00\xfe\xff", "utf-32-be"),
    (b"\xff\xfe\x00\x00", "utf-32-le"),
    (b"\xef\xbb\xbf", "utf-8"),
    (b"\xfe\xff", "utf-16-be"),
    (b"\xff\xfe", "utf-16-le"),
)

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
    "local_action": "Inline these steps in the workflow, or use an external action "
                    "pinned to a full 40-character commit SHA; local actions and "
                    "reusable workflows are not checked by this lint.",
    "floating_runner": "Use an explicit runner image such as ubuntu-24.04, not *-latest.",
    "dynamic_runner": "Use a literal runner label, or a matrix of literal labels, so "
                      "the runner image can be checked.",
    "reserved_check": "Rename this job; that check name belongs to its required "
                      "producer workflow.",
    "unreadable_workflow": "Commit the workflow as a regular, valid YAML file under "
                           "512 KiB.",
}


def fingerprint(*values):
    """Hash parsed values (bounded) so a key can tell them apart unprinted."""

    digest = hashlib.sha256()
    for value in values:
        pending, limit = [value], MAX_STRINGS
        while pending and limit > 0:
            item = pending.pop()
            limit -= 1
            if isinstance(item, dict):
                digest.update(b"{")
                for key in sorted(item, key=repr):
                    pending.extend([item[key], key])
            elif isinstance(item, list):
                digest.update(b"[")
                pending.extend(reversed(item))
            else:
                digest.update(repr(item).encode("utf-8", "replace") + b"\x00")
        digest.update(b"\x01")
    return digest.hexdigest()


class Location:
    """Where a finding is: positions for display, identifiers for its key."""

    def __init__(self, job_index=None, job_id=None, step_index=None, line=None):
        self.job_index, self.job_id, self.step_index = job_index, job_id, step_index
        self.line = line

    def step(self, index, line=None):
        return Location(self.job_index, self.job_id, index, line or self.line)

    def text(self):
        if self.job_index is None:
            return "workflow"
        text = f"job {self.job_index}"
        return text + (f" step {self.step_index}" if self.step_index is not None else "")

    def identity(self):
        return fingerprint(self.job_id, self.step_index) if self.job_index else "workflow"


WORKFLOW_LEVEL = Location()


class Finding:
    """A violation keyed without line numbers so that unchanged ones match.

    ``detail`` is a fixed category from this module; ``subject`` holds the
    pull-request values that tell instances apart and only enters the key,
    as a hash.
    """

    def __init__(self, path, kind, location, detail="", subject=None):
        self.path, self.kind, self.location, self.detail = path, kind, location, detail
        self.subject = fingerprint(subject) if subject is not None else ""

    @property
    def where(self):
        return self.location.text()

    @property
    def line(self):
        return self.location.line

    @property
    def key(self):
        return (self.path, self.kind, self.location.identity(), self.detail, self.subject)

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


def workflow_lines(raw):
    """Map job key text to ``(line, {step index: line})`` from the YAML nodes."""

    import yaml

    try:
        root = yaml.compose(raw, Loader=yaml.SafeLoader)
    except Exception:
        return {}

    def entry(node, name):
        if isinstance(node, yaml.MappingNode):
            for key, value in reversed(node.value):  # The last duplicate wins.
                if isinstance(key, yaml.ScalarNode) and key.value == name:
                    return key, value
        return None, None

    lines = {}
    _key, jobs = entry(root, "jobs")
    if not isinstance(jobs, yaml.MappingNode):
        return lines
    for key, job in jobs.value:
        if not isinstance(key, yaml.ScalarNode) or key.value in lines:
            continue
        _steps_key, steps = entry(job, "steps")
        step_lines = {}
        if isinstance(steps, yaml.SequenceNode):
            step_lines = {index: step.start_mark.line + 1
                          for index, step in enumerate(steps.value, 1)}
        lines[key.value] = (key.start_mark.line + 1, step_lines)
    return lines


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
        return [Finding(path, "write_permission", where, "non-literal permissions",
                        permissions)]
    return [Finding(path, "write_permission", where, f"{scope}: write")
            for scope in WRITE_SCOPES if permissions.get(scope) == "write"]


def uses_findings(path, where, uses, settings):
    if not isinstance(uses, str):
        return [Finding(path, "unpinned_action", where, "non-literal uses", uses)]
    reference = uses.strip()
    if reference.startswith(("./", ".\\")) or reference in (".", ".."):
        # Not in the sparse checkout and never inspected: refuse new ones.
        return [Finding(path, "local_action", where, "local action or workflow", uses)]
    found = []
    if reference.startswith("docker://"):
        if not DIGEST.search(reference):
            found.append(Finding(path, "unpinned_action", where, "container image", uses))
        return found
    name, _, ref = reference.partition("@")
    if not PINNED.fullmatch(ref):
        found.append(Finding(path, "unpinned_action", where, "action reference", uses))
    settings = settings if isinstance(settings, dict) else {}
    if name == "docker/build-push-action":
        if (str(settings.get("push", "false")).strip().lower() != "false"
                or re.search(r"registry|push=true", str(settings.get("outputs", "")), re.I)):
            found.append(Finding(path, "publish_step", where, "image push action", uses))
    elif PUBLISH_ACTION.search(name):
        found.append(Finding(path, "publish_step", where, "publish action", uses))
    return found


MATRIX_FIELD = re.compile(r"\bmatrix\s*(?:\.\s*([A-Za-z0-9_-]+)|\[\s*'([^']*)'\s*\]"
                          r"|\[\s*\"([^\"]*)\"\s*\])")
BARE_MATRIX = re.compile(r"\$\{\{\s*matrix\.[A-Za-z0-9_-]+\s*\}\}")


def matrix_values(matrix, expression):
    """Literal matrix strings an expression can read, including include/exclude."""

    if not isinstance(matrix, dict):
        return []
    fields = [next(filter(None, groups)) for groups in MATRIX_FIELD.findall(expression)
              if any(groups)]
    if not fields or re.search(r"\bmatrix\b(?!\s*(?:\.\s*[A-Za-z0-9_-]|\[\s*['\"]))",
                               expression):
        return list(strings(matrix))  # The whole matrix is reachable.
    values = []
    for field in dict.fromkeys(fields):
        values += strings(matrix.get(field))
        for extra in ("include", "exclude"):
            entries = matrix.get(extra)
            for entry in entries if isinstance(entries, list) else [entries]:
                if isinstance(entry, dict):
                    values += strings(entry.get(field))
    return values


def floating(label):
    return "${{" not in label and label.strip().lower().endswith("-latest")


def runner_findings(path, where, job):
    runs_on = job.get("runs-on")
    if isinstance(runs_on, dict):
        runs_on = [runs_on.get("group"), runs_on.get("labels")]
    labels = list(strings(runs_on))
    matrix = (job.get("strategy") or {}).get("matrix") if isinstance(
        job.get("strategy"), dict) else None
    computed = isinstance(matrix, str) or any("${{" in value for value in strings(matrix))
    found = []
    for label in labels:
        if "${{" not in label:
            if floating(label):
                found.append(Finding(path, "floating_runner", where, "runner label", label))
            continue
        # Every literal the expression can reach is checked, even when the
        # expression itself is a reviewed or grandfathered dynamic runner.
        quoted = re.findall(r"'([^']*)'", label)
        for value in quoted:
            if floating(value):
                found.append(Finding(path, "floating_runner", where, "expression literal",
                                     value))
        for value in matrix_values(matrix, label):
            if floating(value):
                found.append(Finding(path, "floating_runner", where, "matrix value", value))
        if computed or not BARE_MATRIX.fullmatch(label):
            # Keyed by the expression and the whole matrix: any change is new.
            found.append(Finding(path, "dynamic_runner", where, "runner expression",
                                 (label, matrix)))
    return found


# Publication or merge commands in a validation workflow's run scripts. The
# script is split into simple commands twice and both are checked: once
# keeping quoted text as one word (so a quoted option value such as
# ``-c "user.name=A B"`` stays one word), and once dropping quotes and
# backslashes (so a command inside a quoted string, such as
# ``bash -c 'git push'``, is split and checked too). Both passes join
# continued lines, split at ; & | ( ) ` and newlines, and remove redirections
# and their targets (``push>/dev/null`` is ``push``; ``&>`` is a redirection,
# not a separator). Quoted words that hold more than one word, and here-string
# (``<<<``) inputs, are shell text that ``bash -c``, ``eval`` or a shell's
# input can run, so they are split and checked again as scripts, up to
# MAX_NESTING levels deep. Every word naming one of these tools is checked,
# wherever it sits, after skipping the tool's global options. Over-matching
# text such as ``echo git push`` is accepted.
SEPARATORS = re.compile(r"[\n;&|()`]")
PUBLISH_WORDS = frozenset({"push", "tag", "release", "merge", "publish", "upload"})
WRAPPERS = frozenset({"sudo", "env", "exec", "command", "builtin", "nohup", "time", "eval",
                      "xargs", "then", "do", "else", "!"})
GIT_VALUE_OPTIONS = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace",
                               "--super-prefix", "--config-env", "--attr-source"})
GIT_PUSH = frozenset({"push", "send-pack", "http-push"})
TAG_LISTING = re.compile(r"-l|--list|-n\d*|--points-at|--contains|--merged|--no-merged"
                         r"|--(?:points-at|contains|merged|no-merged)=.*")
GH_VALUE_OPTIONS = frozenset({"-R", "--repo", "--hostname", "-X", "--method", "-f", "-F",
                              "--field", "--raw-field", "--input", "-H", "--header",
                              "-q", "--jq", "-t", "--template"})
GH_FIELDS = frozenset({"-f", "-F", "--field", "--raw-field", "--input"})
DOCKER_VALUE_OPTIONS = frozenset({"-c", "--context", "-H", "--host", "--config", "-l",
                                  "--log-level", "--tlscacert", "--tlscert", "--tlskey"})
COMPOSE_VALUE_OPTIONS = frozenset({"-f", "--file", "-p", "--project-name", "--profile",
                                   "--env-file", "--project-directory", "--ansi",
                                   "--progress", "--parallel"})


REDIRECT = object()  # Marks a redirection operator while splitting.
HERE_STRING = object()  # Marks <<<, whose target is shell input.
MAX_NESTING = 4


def split_commands(script, keep_quotes):
    """Return ``(commands, nested)`` for ``script``.

    ``commands`` are its simple commands as lists of words; ``nested`` is the
    shell text inside them (multi-word quoted words and here-string inputs)
    to check again as scripts.
    """

    script = re.sub(r"\\\r?\n", " ", script)  # Join continued lines.
    commands, nested, words, word, quote, quoted, index = [], [], [], None, None, False, 0

    def end_word():
        nonlocal word, quoted
        if word is not None:
            words.append(word)
            if quoted and len(word.split()) > 1:
                nested.append(word)  # Shell text that bash -c or eval can run.
            word = None
        quoted = False

    def end_command():
        nonlocal words
        end_word()
        kept, skip = [], None
        for item in words:
            if item is REDIRECT or item is HERE_STRING:
                skip = item  # The next word is the redirection's target.
            elif skip is not None:
                if skip is HERE_STRING:
                    nested.append(item)  # A here-string is the shell's input.
                skip = None
            else:
                kept.append(item)
        if kept:
            commands.append(kept)
        words = []

    while index < len(script):
        char = script[index]
        index += 1
        if quote == "'":
            if char == "'":
                quote = None
            else:
                word += char
        elif quote == '"':
            if char == '"':
                quote = None
            elif char == "\\" and index < len(script):
                word += script[index]
                index += 1
            else:
                word += char
        elif char in "'\"" and keep_quotes:
            quote, quoted = char, True
            word = word or ""
        elif char == "\\" and keep_quotes:
            if index < len(script):
                word = (word or "") + script[index]
                index += 1
        elif char in "'\"\\":
            continue  # Dropped: the quoted text is split like the rest.
        elif char in "<>" or (char == "&" and script[index:index + 1] == ">"):
            if word is not None and word.isdigit():
                word = None  # A file descriptor number such as the 2 in 2>.
            end_word()
            start = index - 1
            while index < len(script) and script[index] in "<>&|":
                index += 1  # The rest of the operator: >> >& &> &>> <<< >| and so on.
            marker = HERE_STRING if script[start:index].startswith("<<<") else REDIRECT
            if words[-1:] not in ([REDIRECT], [HERE_STRING]):
                words.append(marker)
        elif SEPARATORS.match(char):
            end_command()
        elif char.isspace():
            end_word()
        else:
            word = (word or "") + char
    end_command()
    return commands, nested


def command_words(script):
    """Yield each simple command of a run script, and of the shell text inside
    it, as a list of words."""

    scripts, seen = [(script, 0)], set()
    while scripts:
        text, depth = scripts.pop()
        if text in seen:
            continue
        seen.add(text)
        for keep_quotes in (True, False):
            commands, nested = split_commands(text, keep_quotes)
            yield from commands
            if depth < MAX_NESTING:
                scripts += [(inner, depth + 1) for inner in nested]


def options(words, start, value_options, leading=True):
    """Split ``words[start:]`` into ``(positional words, [(option, value)], end)``.

    With ``leading``, stop at the first positional word (git-style global
    options); otherwise options may appear anywhere (gh-style flags).
    """

    positional, found, index = [], [], start
    while index < len(words):
        word = words[index]
        if not word.startswith("-") or word == "-":
            if leading:
                break
            positional.append(word)
            index += 1
            continue
        name, value = word, ""
        if word.startswith("--"):
            name, equals, value = word.partition("=")
            if not equals and name in value_options and index + 1 < len(words):
                index += 1
                value = words[index]
        elif word[:2] in value_options:
            name, value = word[:2], word[2:]
            if not value and index + 1 < len(words):
                index += 1
                value = words[index]
        found.append((name, value))
        index += 1
    return positional, found, index


def git_publish(words, start):
    _positional, found, index = options(words, start, GIT_VALUE_OPTIONS)
    if any(name in ("-c", "--config-env") and value.lower().startswith("alias.")
           for name, value in found):
        return "git alias"  # An alias can name any subcommand.
    command = words[index].lower() if index < len(words) else ""
    if command in GIT_PUSH:
        return "git push"
    if command == "tag":
        rest = words[index + 1:]
        if rest and not TAG_LISTING.fullmatch(rest[0]):
            return "git tag"
    return None


def gh_publish(words, start):
    positional, found, _index = options(words, start, GH_VALUE_OPTIONS, leading=False)
    command = [word.lower() for word in positional[:2]]
    if command[:1] == ["api"]:
        methods = {value.upper() for name, value in found if name in ("-X", "--method")}
        if methods & {"POST", "PUT", "PATCH", "DELETE"}:
            return "gh api write method"
        # gh api sends POST once it has fields, unless the method is GET.
        if any(name in GH_FIELDS for name, _value in found) and "GET" not in methods:
            return "gh api with fields"
        return None
    if command[:1] == ["release"] and command[1:] and command[1] in (
            "create", "upload", "edit", "delete", "delete-asset"):
        return "gh release"
    if command == ["pr", "merge"]:
        return "gh pr merge"
    if command == ["workflow", "run"]:
        return "gh workflow run"
    return None


def docker_publish(words, start):
    _positional, _found, index = options(words, start, DOCKER_VALUE_OPTIONS)
    command = words[index].lower() if index < len(words) else ""
    rest = [word.lower() for word in words[index + 1:]]
    if command == "push":
        return "docker push"
    if command in ("image", "manifest", "trust"):
        positional, _found, _end = options(words, index + 1, frozenset(), leading=False)
        if positional[:1] and positional[0].lower() in ("push", "sign"):
            return "docker push"
    if command == "compose":
        positional, _found, _end = options(words, index + 1, COMPOSE_VALUE_OPTIONS,
                                           leading=False)
        if positional[:1] and positional[0].lower() == "push":
            return "docker push"
    if command == "buildx":
        _positional, _found, inner = options(words, index + 1, frozenset({"--builder"}))
        command = words[inner].lower() if inner < len(words) else ""
        rest = [word.lower() for word in words[inner + 1:]]
        if command == "imagetools" and rest[:1] == ["create"]:
            return "docker push"
    if command in ("build", "b", "bake", "builder") and any(
            word in ("--push", "--push=true") or "push=true" in word
            or "type=registry" in word for word in rest):
        return "buildx --push"
    return None


def positional_publish(label, verbs, depth=2):
    def check(words, start):
        positional, _found, _end = options(words, start, frozenset(), leading=False)
        if any(word.lower().startswith(verbs) for word in positional[:depth]):
            return label
        return None
    return check


TOOLS = {
    "git": git_publish,
    "gh": gh_publish,
    "docker": docker_publish,
    "podman": docker_publish,
    "buildah": docker_publish,
    "oras": positional_publish("registry copy", ("push", "copy", "cp", "tag")),
    "crane": positional_publish("registry copy", ("push", "copy", "cp", "tag")),
    "skopeo": positional_publish("registry copy", ("copy", "sync")),
    "cosign": positional_publish("cosign", ("sign", "attest")),
    "twine": positional_publish("package publish", ("upload",)),
    "npm": positional_publish("package publish", ("publish",)),
    "pnpm": positional_publish("package publish", ("publish",)),
    "yarn": positional_publish("package publish", ("publish",), depth=3),
}


def tool_name(word):
    name = word.rsplit("/", 1)[-1].lower()
    return name[:-4] if name.endswith(".exe") else name


def command_publish(words):
    """Return the publication categories one simple command can reach."""

    labels = []
    for index, word in enumerate(words):
        check = TOOLS.get(tool_name(word))
        label = check(words, index + 1) if check else None
        if label:
            labels.append(label)
    commands = list(words)
    while commands and (commands[0] in WRAPPERS
                        or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=\S*", commands[0])):
        commands.pop(0)  # Assignments and wrappers before the program name.
    if (commands and commands[0].startswith("$")
            and any(word.lower() in PUBLISH_WORDS for word in commands[1:])):
        labels.append("unparsed command")  # A variable names the program.
    return labels


def publish_findings(path, where, script):
    labels = []
    for words in command_words(script):
        labels += command_publish(words)
    return [Finding(path, "publish_step", where, label)
            for label in dict.fromkeys(labels)]


def reserved_findings(path, name, where, job_id, job):
    found = []
    check = job.get("name", job_id)
    for reserved, producer in RESERVED_CHECKS.items():
        if name == producer:
            continue
        literal = isinstance(check, str) and "${{" not in check
        if (check == reserved) if literal else reserved in str(check):
            found.append(Finding(path, "reserved_check", where, reserved))
    return found


def workflow_findings(path, name, raw):
    """Return the findings for one workflow file's bytes (``None`` if absent)."""

    if raw is None or len(raw) > MAX_WORKFLOW_BYTES:
        return [Finding(path, "unreadable_workflow", WORKFLOW_LEVEL)]
    try:
        document = load_workflow(raw)
    except Exception:  # Any parser failure is a refusal, never a pass.
        return [Finding(path, "unreadable_workflow", WORKFLOW_LEVEL)]
    if not isinstance(document, dict) or not isinstance(document.get("jobs"), dict):
        return [Finding(path, "unreadable_workflow", WORKFLOW_LEVEL)]
    lines = workflow_lines(raw)
    validation = name not in EXEMPT
    found = []
    top = document.get("permissions")
    if validation:
        found += permission_findings(path, WORKFLOW_LEVEL, top)
    for job_index, (job_id, job) in enumerate(document["jobs"].items(), 1):
        job_line, step_lines = lines.get(str(job_id), (None, {}))
        where = Location(job_index, job_id, line=job_line)
        if not isinstance(job, dict):
            found.append(Finding(path, "unreadable_workflow", where))
            continue
        found += reserved_findings(path, name, where, job_id, job)
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
            step_where = where.step(index, step_lines.get(index))
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
    new = [finding for key, finding in findings(head).items()
           if key not in known or finding.kind == "unreadable_workflow"]
    return sorted(new, key=lambda f: (f.path, f.location.job_index or 0,
                                      f.location.step_index or 0, f.kind, f.detail))


def decoded_texts(raw):
    """Latin-1 (lossless; the patterns are ASCII) plus any BOM-declared text."""

    texts = [raw.decode("latin-1")]
    for bom, encoding in BOMS:
        if raw.startswith(bom):
            texts.append(raw[len(bom):].decode(encoding, errors="replace"))
            break
    return texts


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
            relative = path.relative_to(root).as_posix()
            for text in decoded_texts(raw):
                for label, pattern in SECRETS:
                    for match in pattern.finditer(text):
                        found.append((relative, text.count("\n", 0, match.start()) + 1,
                                      label))
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
            annotate(finding.path, finding.message(), line=finding.line, stream=stdout)
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
