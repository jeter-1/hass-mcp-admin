"""Supply-chain guard for jobs holding signing secrets or write tokens (HAMCP-202).

Every job that holds a signing environment, a non-default secret, or any write
permission must check out without persisted credentials and install only
committed hash locks on an exact Python.  Signing jobs additionally hold no
write token, run no general test or upstream-observation stack, install only
the runtime lock, and receive their secret in exactly one step.  Write jobs in
signing workflows run no Python at all.  Image-publishing write jobs (HAMCP-204)
install nothing and run Python only as allowlisted stdlib-only workflow-authority
scripts.  Mutation tests prove each rule fails.
"""

from __future__ import annotations

import ast
import copy
import itertools
import json
from pathlib import Path
import re
import shlex
import sys
import tempfile
import unittest
from unittest import mock

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
RELEASE_REGISTRY = "prepare-ha-mcp-release-registry-update.yml"
PUBLISH = "publish-rc-image.yml"
COMPATIBILITY = "prepare-upstream-compatibility-attestation.yml"
SIGNING_WORKFLOWS = {
    RELEASE_REGISTRY: ("ha-mcp-release-registry-signing", ("HA_MCP_RELEASE_REGISTRY_SIGNING_KEY",)),
    COMPATIBILITY: (
        "upstream-attestation-signing",
        (
            "UPSTREAM_TRUST_REGISTRY_SIGNING_KEY",
            "UPSTREAM_TRUST_REGISTRY_PUBLIC_KEY",
            "UPSTREAM_TRUST_REGISTRY_KEY_ID",
        ),
    ),
}
SIGNING_LOCK = "hass_mcp_engineering_beta/requirements.lock"
EXACT_PYTHON = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
SECRET_REFERENCE = re.compile(r"secrets\.([A-Za-z_][A-Za-z0-9_]*)")
# Locates install steps for the tests; the guard itself parses every command.
PIP_INSTALL = re.compile(r"\bpip3?\b[^\n]*?\binstall\b[^\n]*")
# Shell control operators and command substitutions that start a new command.
# Quotes are deliberately ignored here: a separator inside a quoted argument
# only splits more aggressively, and an unbalanced remainder fails closed.
SHELL_SEPARATOR = re.compile(r"&&|\|\||[;&|\n()`{}]|\$\(")
# Every package-installer name in a run script must belong to a parsed
# invocation; one that cannot be attributed (for example inside a Python
# string) fails closed.
# A dotted suffix (``pip.__main__``) is part of the name, and an attached
# Python ``-m`` cluster (``-mpip``, ``-Impip``) names its module too.
INSTALLER_NAME = re.compile(
    r"(?<![\w.-])(?:-[A-Za-z?]*m)?(?:pip(?:3(?:\.[0-9]+)?)?|pipx|uvx?)"
    r"(?:\.[A-Za-z_]\w*)*(?![\w.-])"
)
PIP_PROGRAM = re.compile(r"pip(?:3(?:\.[0-9]+)?)?")
PYTHON_PROGRAM = re.compile(r"python(?:[0-9]+(?:\.[0-9]+)?)?")
# CPython's short options (Modules/main.c, Python/getopt.c): flags that take no
# argument, and options whose argument is the cluster remainder or else the
# next word.  ``-c`` and ``-m`` end option processing.  Any other letter or
# long option is unknown to the guard and fails closed.
PYTHON_FLAGS = frozenset("bBdEhiIOPqRsSuvVx?")
PYTHON_VALUE_OPTIONS = frozenset("cmWX")
PYTHON_LONG_FLAGS = {"--help", "--help-all", "--help-env", "--help-xoptions", "--version"}
PYTHON_LONG_VALUE_OPTIONS = {"--check-hash-based-pycs"}
# Installer modules ``python -m`` can run, and the tool each one is.
INSTALLER_MODULES = {"pip": "pip", "pipx": "pipx", "uv": "uv"}
# Operator tokens that end an interpreter's words in a quote-aware split.
SHELL_OPERATORS = {"&&", "||", ";", ";;", "|", "&", "(", ")", "<", ">", "<<", ">>", "|&"}
# Interpreter code (``-c`` or standard input) that mentions an installer
# cannot be attributed and fails closed.
INSTALLER_CODE = re.compile(r"pip|install|\buvx?\b", re.IGNORECASE)
NESTED_SHELL_TEXT = re.compile(r"\s")
# The only options a hash-locked pip install may carry.
INSTALL_FLAGS = {"--isolated", "--require-hashes"}
INSTALL_VALUE_OPTIONS = {"--index-url", "-i", "-r", "--requirement", "--only-binary"}
GENERAL_TEST_STACK = ("unittest", "pytest", "check.ps1", "tests/requirements")
OBSERVATION_STACK = (
    "docker ",
    "docker/",
    "imagetools",
    "fake_ha_",
    "review_upstream_read_release",
    "inspect_upstream_dashboard_runtime",
)
SIGNING_JOB_ACTIONS = {
    "actions/checkout",
    "actions/setup-python",
    "actions/download-artifact",
    "actions/upload-artifact",
}

# Write jobs of a workflow that holds `packages: write` publish images (HAMCP-204).
# They install nothing, not even a hash lock, and run no release-validation
# stack.  Owner decision (HAMCP-204 M1): they run Python only as the plain
# ``python`` word executing an allowlisted, stdlib-only repository script that
# the job materialized from the workflow-authority commit, by path, with no
# interpreter option, no ``-c``/``-m`` and no code or data on standard input.
PUBLISHING_TEST_STACK = GENERAL_TEST_STACK + (
    "compileall",
    "discover",
    "requirements.lock",
    "unit_test_shards",
)
PUBLISHING_SCRIPTS = frozenset({
    "publication_claim.py",
    "publication_digest_binding.py",
    "publication_github_release.py",
    "publication_handoff.py",
    "publish_registry_tags_guarded.py",
    "validate_ci_provenance.py",
    "validate_ready_authorization.py",
})
AUTHORITY_ENV = "${{ github.sha }}"
POLICY_DIRECTORY = "$RUNNER_TEMP/publication-policy"
POLICY_SCRIPT_PATH = re.compile(r"\$RUNNER_TEMP/publication-policy/([A-Za-z0-9_]+\.py)")
# The reviewed materialization: workflow-authority scripts into the policy directory.
POLICY_MATERIALIZATION = (
    'mkdir -p "$RUNNER_TEMP/publication-policy"',
    'git show "${WORKFLOW_AUTHORITY_SHA}:scripts/${policy}" '
    '> "$RUNNER_TEMP/publication-policy/${policy}"',
)
POLICY_LOOP = re.compile(r"for policy in ([A-Za-z0-9_. ]+); do")
POLICY_INVOCATION = re.compile(r'python[ \t]+"' + POLICY_SCRIPT_PATH.pattern + '"')
POLICY_INVOCATION_SCRIPT = re.compile(r'(?<![\w./$-])python[ \t]+"' + POLICY_SCRIPT_PATH.pattern + '"')
# Every interpreter-like name (any case, any path or suffix, ``PYTHONPATH``
# included) must belong to an approved invocation; any other fails closed.
INTERPRETER_NAME = re.compile(r"python", re.IGNORECASE)
# Words after which a simple command starts.  Anything else in front of the
# interpreter (a pipe, ``exec``, ``env``, ``xargs``, an assignment) fails.
COMMAND_START = {None, "&&", "||", ";", "!", "(", "{", "if", "elif", "then", "else", "do"}
COMMAND_END = {"&&", "||", ";", ";;", "|", "|&", "&", ")"}
PYTHON_CONTROL = ("site-packages", "sitecustomize", "usercustomize")
# Package managers a publishing job may not run, by name or by path.
PACKAGE_MANAGER = re.compile(
    r"(?<![\w.-])(?:apt(?:-get)?|aptitude|dpkg|snap|yum|dnf|microdnf|rpm|apk|zypper|pacman"
    r"|brew|conda|mamba|micromamba|npm|npx|yarn|pnpm|gem|cargo|pipx|uvx?|poetry|pipenv|pdm"
    r"|hatch|easy_install|ensurepip|pip(?:[0-9]+(?:\.[0-9]+)?)?)(?![\w-])"
)
PUBLISHING_JOB_ACTIONS = {
    "actions/checkout",
    "actions/setup-python",
    "docker/setup-buildx-action",
    "docker/setup-qemu-action",
    "docker/login-action",
    "docker/build-push-action",
}

# Reviewed exceptions.  HAMCP-204 split the publication `promote` job, so no
# privileged job is exempt.  The set must match observed violations exactly so
# it cannot go stale or hide a new violation.
REVIEWED_EXCEPTIONS: dict[tuple[str, str], set[str]] = {}


def _load(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _run_text(step: dict) -> str:
    # Join shell continuations so a multi-line install is checked as one command.
    return re.sub(r"\\\n\s*", " ", str(step.get("run", "")))


def _action(step: dict) -> str:
    return str(step.get("uses", "")).split("@", 1)[0]


def _permissions(workflow: dict, job: dict) -> object:
    return job.get("permissions", workflow.get("permissions"))


def _has_write(permissions: object) -> bool:
    if isinstance(permissions, str):
        return permissions == "write-all"
    return isinstance(permissions, dict) and "write" in permissions.values()


def _signing_secrets(job: dict) -> set[str]:
    return set(SECRET_REFERENCE.findall(json.dumps(job))) - {"GITHUB_TOKEN"}


def _is_signing_job(job: dict) -> bool:
    return "environment" in job or bool(_signing_secrets(job))


class Install:
    """One parsed package-installer invocation from a run script."""

    def __init__(self, tool: str, arguments: list[str], names: int | None = None) -> None:
        self.tool = tool
        self.arguments = arguments
        # Installer names this invocation accounts for (``uv pip`` has two).
        self.names = len(tool.split()) if names is None else names

    def __repr__(self) -> str:
        return f"Install({self.tool!r}, {self.arguments!r})"

    @property
    def is_install(self) -> bool:
        if self.tool in {"pipx", "uvx", "uv"}:
            # pipx, uvx and uv project/tool commands resolve and install
            # packages without the committed hash lock.
            return True
        return "install" in self.arguments or "sync" in self.arguments

    def requirement_files(self) -> list[str] | None:
        """Return the -r files of a hash-locked pip install, or None if unlocked."""

        if self.tool not in {"pip", "uv pip"}:
            return None
        arguments = list(self.arguments)
        subcommand = "install"
        while arguments and arguments[0] == "--isolated":
            arguments.pop(0)
        if not arguments or arguments.pop(0) != subcommand:
            return None
        flags: set[str] = set()
        values: dict[str, list[str]] = {}
        while arguments:
            argument = arguments.pop(0)
            option, equals, inline = argument.partition("=")
            if argument in INSTALL_FLAGS:
                flags.add(argument)
            elif option in INSTALL_VALUE_OPTIONS:
                if equals:
                    value = inline
                elif arguments:
                    value = arguments.pop(0)
                else:
                    return None
                values.setdefault(option, []).append(value)
            else:
                # Package specifiers, editable installs, extra indexes,
                # constraints, redirections and every unknown option fail.
                return None
        files = values.get("-r", []) + values.get("--requirement", [])
        if (
            "--require-hashes" not in flags
            or values.get("--only-binary") != [":all:"]
            or len(values.get("--index-url", []) + values.get("-i", [])) > 1
            or not files
            or any(not value.endswith(".lock") for value in files)
        ):
            return None
        return files


def _python_command(arguments: list[str]) -> tuple[str, str | None, int]:
    """Walk CPython's options before the script or module.

    Returns ``(kind, value, consumed)``: kind ``"m"`` (module), ``"c"`` (code),
    ``"script"`` or ``"none"``; value is the module, code or script; consumed
    counts the words that belong to the interpreter's own options, including
    the ``-m``/``-c`` argument.  Raises ``ValueError`` for an option shape the
    guard cannot parse with certainty.
    """

    index = 0
    while index < len(arguments):
        word = arguments[index]
        if word == "--":
            script = arguments[index + 1 : index + 2]
            return "script", (script or [None])[0], index + 1
        if word == "-" or not word.startswith("-"):
            return "script", word, index
        index += 1
        if word.startswith("--"):
            if word in PYTHON_LONG_FLAGS:
                continue
            if word in PYTHON_LONG_VALUE_OPTIONS and index < len(arguments):
                index += 1
                continue
            raise ValueError(f"unknown Python option {word!r}")
        cluster = word[1:]
        for position, letter in enumerate(cluster):
            if letter in PYTHON_FLAGS:
                continue
            if letter not in PYTHON_VALUE_OPTIONS:
                raise ValueError(f"unknown Python option -{letter}")
            value = cluster[position + 1 :]
            if not value:
                if index >= len(arguments):
                    raise ValueError(f"Python option -{letter} has no argument")
                value = arguments[index]
                index += 1
            if letter in "cm":
                return letter, value, index
            break  # -W and -X consumed the rest of the cluster
    return "none", None, index


def _module_tool(module: str) -> str | None:
    """Return the installer a ``python -m`` module runs, or None if it is none.

    Raises ``ValueError`` for an installer submodule other than ``__main__``.
    """

    package, _dot, submodule = module.partition(".")
    tool = "pip" if PIP_PROGRAM.fullmatch(package) else INSTALLER_MODULES.get(package)
    if tool is None:
        return None
    if submodule not in {"", "__main__"}:
        raise ValueError(f"unattributable installer module {module!r}")
    return tool


def _shell_words(text: str) -> list[str]:
    lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    return list(lexer)


def _interpreter_code_is_unattributable(text: str) -> bool:
    """Whether a Python interpreter runs code the guard cannot attribute.

    The separator split ignores quotes, so interpreter code is checked first,
    one command line at a time (joined while a quote is open).  Code given by
    ``-c``, a heredoc on standard input or the rest of the line that mentions
    an installer fails closed, as does an unparseable option or quote.
    """

    lines = text.split("\n")
    index = 0
    while index < len(lines):
        chunk = lines[index]
        index += 1
        if "python" not in chunk or chunk.lstrip().startswith("#"):
            continue
        while True:
            try:
                words = _shell_words(chunk)
                break
            except ValueError:
                if index >= len(lines):
                    return bool(INSTALLER_CODE.search(chunk))
                chunk += "\n" + lines[index]
                index += 1
        for position, word in enumerate(words):
            if not PYTHON_PROGRAM.fullmatch(word.lstrip("`").rsplit("/", 1)[-1]):
                continue
            command = list(
                itertools.takewhile(lambda item: item not in SHELL_OPERATORS, words[position + 1 :])
            )
            try:
                kind, value, _consumed = _python_command(command)
            except ValueError:
                return True
            if kind == "c" and INSTALLER_CODE.search(value):
                return True
            if kind == "none" or value == "-":
                # Standard input: a heredoc body, or text piped on this line.
                stdin = chunk
                after = words[position + 1 + len(command) :]
                if after[:1] == ["<<"] and len(after) > 1:
                    tag = after[1].lstrip("-")
                    body = list(
                        itertools.takewhile(lambda line: line.strip() != tag, lines[index:])
                    )
                    stdin = "\n".join(body)
                if INSTALLER_CODE.search(stdin):
                    return True
    return False


def _segment_installs(argv: list[str]) -> list[Install] | None:
    """Find every installer invocation in one simple command's words.

    Returns None when an interpreter invocation cannot be parsed with certainty.
    """

    starts: list[tuple[int, int, str]] = []
    index = 0
    while index < len(argv):
        name = argv[index].rsplit("/", 1)[-1]
        tool = None
        arguments_at = index + 1
        if PIP_PROGRAM.fullmatch(name):
            tool = "pip"
        elif name in {"pipx", "uvx", "uv"}:
            tool = name
        elif PYTHON_PROGRAM.fullmatch(name):
            try:
                kind, value, consumed = _python_command(argv[index + 1 :])
                tool = _module_tool(value) if kind == "m" else None
            except ValueError:
                return None
            if kind == "c" and INSTALLER_CODE.search(value):
                return None
            arguments_at = index + 1 + consumed
        if tool == "uv" and argv[arguments_at : arguments_at + 1] == ["pip"]:
            tool, arguments_at = "uv pip", arguments_at + 1
        if tool is not None:
            starts.append((index, arguments_at, tool))
        # The interpreter's or installer's own prefix words are not rescanned.
        index = arguments_at
    installs: list[Install] = []
    for position, (start, arguments_at, tool) in enumerate(starts):
        end = starts[position + 1][0] if position + 1 < len(starts) else len(argv)
        names = sum(len(INSTALLER_NAME.findall(word)) for word in argv[start:arguments_at])
        installs.append(Install(tool, argv[arguments_at:end], names))
    return installs


def install_invocations(text: str) -> list[Install] | None:
    """Parse every installer invocation in a run script, or None if unparseable.

    Commands are split on ``&&``, ``||``, ``;``, ``|``, ``&``, newlines,
    subshells, braces and command substitutions; quoted words that contain
    shell text (``bash -c "..."``) are parsed recursively.
    """

    text = re.sub(r"\\\n\s*", " ", text)
    if _interpreter_code_is_unattributable(text):
        return None
    installs: list[Install] = []
    for segment in SHELL_SEPARATOR.split(text):
        if not segment.strip():
            continue
        try:
            argv = shlex.split(segment)
        except ValueError:
            if INSTALLER_NAME.search(segment):
                return None
            continue
        segment_installs = _segment_installs(argv)
        if segment_installs is None:
            return None
        installs.extend(segment_installs)
        for word in argv:
            if NESTED_SHELL_TEXT.search(word) and INSTALLER_NAME.search(word):
                nested = install_invocations(word)
                if nested is None:
                    return None
                installs.extend(nested)
    # Every installer name must be attributed to a parsed invocation; an
    # unattributed name means an install the parser cannot see.
    if len(INSTALLER_NAME.findall(text)) != sum(install.names for install in installs):
        return None
    return installs


def _install_violations(text: str) -> bool:
    installs = install_invocations(text)
    if installs is None:
        return True
    return any(
        install.is_install and install.requirement_files() is None
        for install in installs
    )


def _publishes_packages(workflow: dict, job: dict) -> bool:
    permissions = _permissions(workflow, job)
    if isinstance(permissions, str):
        return permissions == "write-all"
    return isinstance(permissions, dict) and permissions.get("packages") == "write"


def _authority_materialization(step: dict) -> str | None:
    """Return the step text without its reviewed policy materialization, if any.

    The loop may name only allowlisted scripts, from the workflow authority.
    """
    run = _run_text(step)
    env = step.get("env", {}) or {}
    loops = POLICY_LOOP.findall(run)
    if (
        env.get("WORKFLOW_AUTHORITY_SHA") != AUTHORITY_ENV
        or not all(line in run for line in POLICY_MATERIALIZATION)
        or len(loops) != 1
        or not set(loops[0].split()) <= PUBLISHING_SCRIPTS
    ):
        return None
    for line in POLICY_MATERIALIZATION:
        run = run.replace(line, "")
    return POLICY_LOOP.sub("", run)


def _is_operator(word: str | None) -> bool:
    return bool(word) and set(word) <= set("();<>|&")


def _interpreter_word(word: str) -> bool:
    """Whether a shell word names a Python interpreter, by name or by path."""
    return bool(PYTHON_PROGRAM.fullmatch(word.lstrip("`").rsplit("/", 1)[-1]))


def _approved_python(words: list[str], position: int) -> bool:
    """Whether ``words[position]`` starts the one approved interpreter form.

    ``python "$RUNNER_TEMP/publication-policy/<allowlisted>.py" args...`` at a
    command start, with no interpreter option and no standard-input redirect,
    heredoc, here-string or pipe into it.
    """

    if words[position] != "python":
        return False  # python3, an absolute or relative path, `python, ...
    if (words[position - 1] if position else None) not in COMMAND_START:
        return False  # piped into, or run through exec/env/xargs/an assignment
    after = words[position + 1 :]
    command = list(itertools.takewhile(lambda word: not _is_operator(word), after))
    try:
        kind, script, consumed = _python_command(command)
    except ValueError:
        return False
    match = POLICY_SCRIPT_PATH.fullmatch(script or "")
    if kind != "script" or consumed or not match or match.group(1) not in PUBLISHING_SCRIPTS:
        return False  # -c, -m, -, options, stdin, or a non-allowlisted path
    simple = itertools.takewhile(lambda word: word not in COMMAND_END, after)
    return not any(_is_operator(word) and word.startswith("<") for word in simple)


def _runs_unreviewed_python(text: str) -> bool:
    """Whether a publishing-job run script runs Python outside the approved form.

    Interpreters are recognized by name or path (``python``, ``python3.12``,
    ``/usr/bin/python3``, ``./venv/bin/python``) anywhere in a command line,
    including nested shell text.  Inline ``-c`` code, ``-m``, standard input
    (``-``, no script, ``<``, ``<<``, ``<<-``, ``<<<`` or a pipe) and options
    fail; so does any interpreter name the parser cannot attribute.
    """

    text = re.sub(r"\\\n\s*", " ", text)
    names = len(INTERPRETER_NAME.findall(text))
    if not names:
        return False
    approved = 0
    lines = text.split("\n")
    index = 0
    while index < len(lines):
        chunk = lines[index]
        index += 1
        if not INTERPRETER_NAME.search(chunk):
            continue
        while True:
            try:
                words = _shell_words(chunk)
                break
            except ValueError:
                if index >= len(lines):
                    return True  # an unclosed quote cannot be attributed
                chunk += "\n" + lines[index]
                index += 1
        for position, word in enumerate(words):
            if not _interpreter_word(word):
                continue
            if not _approved_python(words, position):
                return True
            approved += 1
    # A name inside a quoted word (``bash -c '...'``), an assignment, a
    # comment or a non-interpreter word is never attributed to an approved run.
    return names != approved


def _shell_is_bash(value: object) -> bool:
    return value is None or value == "bash"


def _publishing_violations(workflow: dict, job: dict) -> set[str]:
    violations: set[str] = set()
    steps = job.get("steps", [])
    serialized = json.dumps(job)
    if any(marker in serialized for marker in PUBLISHING_TEST_STACK):
        violations.add("PUBLISHING_JOB_RUNS_TEST_STACK")
    if any(marker in serialized for marker in PYTHON_CONTROL) or any(
        INTERPRETER_NAME.search(str(name))
        for scope in (workflow, job, *steps)
        for name in (scope.get("env", {}) or {})
    ):
        violations.add("PUBLISHING_JOB_RUNS_UNREVIEWED_PYTHON")
    for scope in (workflow, job):
        defaults = (scope.get("defaults", {}) or {}).get("run", {}) or {}
        if not _shell_is_bash(defaults.get("shell")):
            violations.add("PUBLISHING_JOB_RUNS_UNREVIEWED_PYTHON")
    for step in steps:
        action = _action(step)
        if action and action not in PUBLISHING_JOB_ACTIONS:
            violations.add("PUBLISHING_JOB_EXTRA_ACTION")
        if "run" in step and not _shell_is_bash(step.get("shell")):
            violations.add("PUBLISHING_JOB_RUNS_UNREVIEWED_PYTHON")
        run = _run_text(step)
        installs = install_invocations(run)
        if (
            installs is None
            or any(install.is_install for install in installs)
            or PACKAGE_MANAGER.search(run)
        ):
            violations.add("PUBLISHING_JOB_INSTALLS_PACKAGES")
        if _runs_unreviewed_python(run):
            violations.add("PUBLISHING_JOB_RUNS_UNREVIEWED_PYTHON")
        # Only the reviewed materialization may name the policy directory
        # other than to run an allowlisted script from it.
        remainder = _authority_materialization(step)
        text = run if remainder is None else remainder
        if POLICY_DIRECTORY in POLICY_INVOCATION.sub("", text):
            violations.add("PUBLISHING_JOB_RUNS_UNREVIEWED_PYTHON")
    return violations


def job_violations(
    workflow: dict,
    job: dict,
    *,
    signing_workflow: bool,
    publishing_workflow: bool = False,
) -> set[str]:
    signing = _is_signing_job(job)
    write = _has_write(_permissions(workflow, job))
    if not (signing or write):
        return set()
    violations: set[str] = set()
    steps = job.get("steps", [])
    serialized = json.dumps(job)
    for step in steps:
        action = _action(step)
        options = step.get("with", {}) or {}
        if action == "actions/checkout" and options.get("persist-credentials") is not False:
            violations.add("CHECKOUT_PERSISTS_CREDENTIALS")
        if action == "actions/setup-python":
            if not EXACT_PYTHON.fullmatch(str(options.get("python-version", ""))):
                violations.add("FLOATING_PYTHON")
            if "cache" in options:
                violations.add("PIP_CACHE")
        if _install_violations(_run_text(step)):
            violations.add("UNHASHED_INSTALL")
    if any(marker in serialized for marker in GENERAL_TEST_STACK):
        violations.add("GENERAL_TEST_STACK")

    if signing:
        if write:
            violations.add("SIGNING_JOB_HAS_WRITE")
        if any(marker in serialized for marker in OBSERVATION_STACK):
            violations.add("SIGNING_JOB_RUNS_OBSERVATION_STACK")
        for step in steps:
            action = _action(step)
            if action and action not in SIGNING_JOB_ACTIONS:
                violations.add("SIGNING_JOB_EXTRA_ACTION")
            for install in install_invocations(_run_text(step)) or []:
                if install.is_install and install.requirement_files() != [SIGNING_LOCK]:
                    violations.add("SIGNING_JOB_EXTRA_INSTALL")
        if SECRET_REFERENCE.search(json.dumps(job.get("env", {}))):
            violations.add("SIGNING_SECRET_SCOPE")
        holders = [
            step for step in steps if SECRET_REFERENCE.search(json.dumps(step))
        ]
        if len(holders) > 1 or any(
            SECRET_REFERENCE.search(json.dumps(step.get("with", {}))) for step in holders
        ):
            violations.add("SIGNING_SECRET_SCOPE")
    elif signing_workflow:
        # Write jobs beside a signer run no Python or package installation.
        if any(_action(step) == "actions/setup-python" for step in steps) or any(
            re.search(r"\b(python[0-9.]*|pip[0-9.]*)\b", _run_text(step)) for step in steps
        ):
            violations.add("WRITE_JOB_RUNS_PYTHON")
    if write and publishing_workflow:
        violations |= _publishing_violations(workflow, job)
    return violations


def workflow_violations(name: str, workflow: dict) -> dict[str, set[str]]:
    jobs = workflow.get("jobs", {})
    signing_workflow = any(_is_signing_job(job) for job in jobs.values())
    publishing_workflow = any(_publishes_packages(workflow, job) for job in jobs.values())
    if signing_workflow:
        # Secrets may never be referenced by a non-signing job of a signer.
        for job_name, job in jobs.items():
            if not _is_signing_job(job) and SECRET_REFERENCE.search(
                json.dumps(job).replace("secrets.GITHUB_TOKEN", "")
            ):
                raise AssertionError(f"{name}:{job_name} references a signing secret")
    result = {}
    for job_name, job in jobs.items():
        found = job_violations(
            workflow,
            job,
            signing_workflow=signing_workflow,
            publishing_workflow=publishing_workflow,
        )
        if found:
            result[job_name] = found
    return result


def all_violations() -> dict[tuple[str, str], set[str]]:
    result: dict[tuple[str, str], set[str]] = {}
    for path in sorted(WORKFLOWS.glob("*.y*ml")):
        for job_name, found in workflow_violations(path.name, _load(path.name)).items():
            result[(path.name, job_name)] = found
    return result


class PrivilegedJobSupplyChainGuardTests(unittest.TestCase):
    def test_privileged_jobs_match_reviewed_exceptions_exactly(self) -> None:
        self.assertEqual(all_violations(), REVIEWED_EXCEPTIONS)

    def test_guard_covers_every_privileged_job(self) -> None:
        privileged = set()
        for path in sorted(WORKFLOWS.glob("*.y*ml")):
            workflow = _load(path.name)
            for job_name, job in workflow.get("jobs", {}).items():
                if _is_signing_job(job) or _has_write(_permissions(workflow, job)):
                    privileged.add((path.name, job_name))
        for name in SIGNING_WORKFLOWS:
            self.assertIn((name, "sign"), privileged)
            self.assertIn((name, "publish"), privileged)
            self.assertNotIn((name, "evidence"), privileged)
        for job_name in ("claim", "build", "promote"):
            self.assertIn((PUBLISH, job_name), privileged)
        for job_name in ("verify-release", "verify-image", "summary"):
            self.assertNotIn((PUBLISH, job_name), privileged)

    def test_publishing_rules_cover_exactly_the_image_publication_workflow(self) -> None:
        publishing = {
            path.name
            for path in sorted(WORKFLOWS.glob("*.y*ml"))
            if any(
                _publishes_packages(_load(path.name), job)
                for job in _load(path.name).get("jobs", {}).values()
            )
        }
        self.assertEqual(publishing, {PUBLISH})
        self.assertEqual(REVIEWED_EXCEPTIONS, {})

    def test_signing_workflows_split_observation_signing_and_publication(self) -> None:
        for name, (environment, secrets) in SIGNING_WORKFLOWS.items():
            with self.subTest(workflow=name):
                workflow = _load(name)
                jobs = workflow["jobs"]
                self.assertEqual(set(jobs), {"evidence", "sign", "publish"})
                self.assertEqual(workflow["permissions"], {"contents": "read"})
                self.assertEqual(jobs["evidence"]["permissions"], {"contents": "read"})
                self.assertEqual(jobs["sign"]["permissions"], {"contents": "read"})
                self.assertEqual(
                    jobs["publish"]["permissions"],
                    {"contents": "write", "pull-requests": "write"},
                )
                self.assertEqual(jobs["sign"]["environment"], environment)
                self.assertNotIn("environment", jobs["evidence"])
                self.assertNotIn("environment", jobs["publish"])
                self.assertEqual(jobs["sign"]["needs"], "evidence")
                self.assertEqual(jobs["publish"]["needs"], "sign")
                for job_name in ("evidence", "sign", "publish"):
                    job = jobs[job_name]
                    self.assertEqual(job["if"], "github.ref == 'refs/heads/main'")
                    self.assertEqual(job["runs-on"], "ubuntu-24.04")
                    self.assertIsInstance(job["timeout-minutes"], int)
                sign_text = json.dumps(jobs["sign"])
                for secret in secrets:
                    self.assertIn(f"secrets.{secret}", sign_text)
                    self.assertNotIn(secret, json.dumps(jobs["evidence"]))
                    self.assertNotIn(secret, json.dumps(jobs["publish"]))
                self.assertNotIn("github.token", sign_text)
                self.assertNotIn("github.token", json.dumps(jobs["evidence"]))
                self.assertEqual(
                    {
                        _action(step)
                        for step in jobs["publish"]["steps"]
                        if "uses" in step
                    },
                    {"actions/checkout", "actions/download-artifact"},
                )

    def test_evidence_job_installs_only_hash_locked_runtime_on_exact_python(self) -> None:
        for name in SIGNING_WORKFLOWS:
            with self.subTest(workflow=name):
                job = _load(name)["jobs"]["evidence"]
                self.assertEqual(
                    job_violations(
                        {"permissions": {"contents": "write"}},
                        job,
                        signing_workflow=False,
                    )
                    - {"SIGNING_JOB_HAS_WRITE"},
                    set(),
                )
                installs = [
                    step for step in job["steps"] if PIP_INSTALL.search(_run_text(step))
                ]
                self.assertEqual(len(installs), 1)
                self.assertIn(f"-r {SIGNING_LOCK}", installs[0]["run"])

    def test_artifacts_are_bound_by_id_and_hash_between_jobs(self) -> None:
        for name in SIGNING_WORKFLOWS:
            with self.subTest(workflow=name):
                jobs = _load(name)["jobs"]
                for job_name, upstream in (("sign", "evidence"), ("publish", "sign")):
                    downloads = [
                        step
                        for step in jobs[job_name]["steps"]
                        if _action(step) == "actions/download-artifact"
                    ]
                    self.assertEqual(len(downloads), 1)
                    self.assertEqual(
                        downloads[0]["with"]["artifact-ids"],
                        f"${{{{ needs.{upstream}.outputs.artifact_id }}}}",
                    )
                    self.assertNotIn("name", downloads[0]["with"])
                    self.assertNotIn("pattern", downloads[0]["with"])
                for job_name in ("evidence", "sign"):
                    uploads = [
                        step
                        for step in jobs[job_name]["steps"]
                        if _action(step) == "actions/upload-artifact"
                    ]
                    self.assertEqual(len(uploads), 1)
                    self.assertEqual(uploads[0]["with"]["retention-days"], 1)
                    self.assertEqual(uploads[0]["with"]["if-no-files-found"], "error")
                    self.assertIn("${{ github.run_attempt }}", uploads[0]["with"]["name"])
                publish = json.dumps(jobs["publish"])
                self.assertIn("needs.sign.outputs.manifest_sha256", publish)
                self.assertIn("sha256sum --strict --check", publish)
                self.assertIn("credential.helper=!gh auth git-credential", publish)

    def test_publication_push_uses_step_scoped_token_only(self) -> None:
        for name in SIGNING_WORKFLOWS:
            with self.subTest(workflow=name):
                steps = _load(name)["jobs"]["publish"]["steps"]
                pushing = [step for step in steps if "push origin" in step.get("run", "")]
                self.assertEqual(len(pushing), 1)
                self.assertEqual(pushing[0]["env"]["GH_TOKEN"], "${{ github.token }}")
                self.assertIn("-c credential.helper= ", pushing[0]["run"])
                self.assertNotIn("extraheader", json.dumps(steps))
                self.assertNotIn("GH_TOKEN", json.dumps(_load(name)["jobs"]["publish"].get("env", {})))


class GuardMutationTests(unittest.TestCase):
    def _mutated(self, name: str, mutate) -> dict[str, set[str]]:
        workflow = copy.deepcopy(_load(name))
        mutate(workflow)
        return workflow_violations(name, workflow)

    @staticmethod
    def _step(job: dict, predicate) -> dict:
        return next(step for step in job["steps"] if predicate(step))

    def _install(self, job: dict) -> dict:
        return self._step(job, lambda step: PIP_INSTALL.search(_run_text(step)))

    def _checkout(self, job: dict) -> dict:
        return self._step(job, lambda step: _action(step) == "actions/checkout")

    def test_baselines_are_clean(self) -> None:
        for name in SIGNING_WORKFLOWS:
            self.assertEqual(self._mutated(name, lambda workflow: None), {})

    def test_dropping_require_hashes_fails(self) -> None:
        for name in SIGNING_WORKFLOWS:
            for job_name in ("sign",):
                def mutate(workflow, job_name=job_name):
                    step = self._install(workflow["jobs"][job_name])
                    step["run"] = step["run"].replace("--require-hashes ", "")
                with self.subTest(workflow=name, job=job_name):
                    self.assertIn("UNHASHED_INSTALL", self._mutated(name, mutate)[job_name])

    def test_dropping_only_binary_or_using_unlocked_requirements_fails(self) -> None:
        replacements = (
            ("--only-binary=:all: ", ""),
            (SIGNING_LOCK, "hass_mcp_engineering_beta/requirements.txt"),
        )
        for name in SIGNING_WORKFLOWS:
            for old, new in replacements:
                def mutate(workflow, old=old, new=new):
                    step = self._install(workflow["jobs"]["sign"])
                    step["run"] = step["run"].replace(old, new)
                with self.subTest(workflow=name, replaced=old):
                    self.assertIn("UNHASHED_INSTALL", self._mutated(name, mutate)["sign"])

    def test_persisting_credentials_fails(self) -> None:
        for name in SIGNING_WORKFLOWS:
            for job_name in ("sign", "publish"):
                for value in (True, None):
                    def mutate(workflow, job_name=job_name, value=value):
                        options = self._checkout(workflow["jobs"][job_name])["with"]
                        if value is None:
                            options.pop("persist-credentials")
                        else:
                            options["persist-credentials"] = value
                    with self.subTest(workflow=name, job=job_name, value=value):
                        self.assertIn(
                            "CHECKOUT_PERSISTS_CREDENTIALS",
                            self._mutated(name, mutate)[job_name],
                        )

    def test_floating_python_or_pip_cache_in_signing_job_fails(self) -> None:
        for name in SIGNING_WORKFLOWS:
            def floating(workflow):
                self._step(
                    workflow["jobs"]["sign"],
                    lambda step: _action(step) == "actions/setup-python",
                )["with"]["python-version"] = "3.12"

            def cached(workflow):
                self._step(
                    workflow["jobs"]["sign"],
                    lambda step: _action(step) == "actions/setup-python",
                )["with"]["cache"] = "pip"

            with self.subTest(workflow=name):
                self.assertIn("FLOATING_PYTHON", self._mutated(name, floating)["sign"])
                self.assertIn("PIP_CACHE", self._mutated(name, cached)["sign"])

    def test_test_stack_in_signing_job_fails(self) -> None:
        mutations = {
            "tests lock": lambda run: run + " -r tests/requirements.lock",
            "unittest": lambda run: run + "\npython -m unittest discover -s tests",
        }
        for name in SIGNING_WORKFLOWS:
            for label, change in mutations.items():
                def mutate(workflow, change=change):
                    step = self._install(workflow["jobs"]["sign"])
                    step["run"] = change(step["run"])
                with self.subTest(workflow=name, mutation=label):
                    found = self._mutated(name, mutate)["sign"]
                    self.assertIn("GENERAL_TEST_STACK", found)

    def test_extra_install_or_observation_in_signing_job_fails(self) -> None:
        for name in SIGNING_WORKFLOWS:
            def extra_lock(workflow):
                step = self._install(workflow["jobs"]["sign"])
                step["run"] = step["run"] + " -r scripts/other.lock"

            def observe(workflow):
                workflow["jobs"]["sign"]["steps"].append(
                    {"name": "observe", "run": "docker pull ghcr.io/example/image"}
                )

            def extra_action(workflow):
                workflow["jobs"]["sign"]["steps"].append(
                    {"uses": "docker/setup-buildx-action@" + "0" * 40}
                )

            with self.subTest(workflow=name):
                self.assertIn("SIGNING_JOB_EXTRA_INSTALL", self._mutated(name, extra_lock)["sign"])
                self.assertIn(
                    "SIGNING_JOB_RUNS_OBSERVATION_STACK",
                    self._mutated(name, observe)["sign"],
                )
                self.assertIn("SIGNING_JOB_EXTRA_ACTION", self._mutated(name, extra_action)["sign"])

    def test_write_permission_on_signing_job_fails(self) -> None:
        for name in SIGNING_WORKFLOWS:
            def mutate(workflow):
                workflow["jobs"]["sign"]["permissions"]["contents"] = "write"
            with self.subTest(workflow=name):
                self.assertIn("SIGNING_JOB_HAS_WRITE", self._mutated(name, mutate)["sign"])

    def test_moving_secret_into_evidence_or_publish_job_fails(self) -> None:
        for name, (_environment, secrets) in SIGNING_WORKFLOWS.items():
            for job_name in ("evidence", "publish"):
                def mutate(workflow, job_name=job_name, secret=secrets[0]):
                    workflow["jobs"][job_name]["steps"][0].setdefault("env", {})[
                        "LEAK"
                    ] = f"${{{{ secrets.{secret} }}}}"
                with self.subTest(workflow=name, job=job_name):
                    found = self._mutated(name, mutate)[job_name]
                    # A secret makes the job a signer; it then fails on its
                    # observation stack (evidence) or its write token (publish).
                    self.assertTrue(
                        found
                        & {"SIGNING_JOB_HAS_WRITE", "SIGNING_JOB_RUNS_OBSERVATION_STACK"}
                    )

    def test_secret_in_job_env_or_second_step_fails(self) -> None:
        for name, (_environment, secrets) in SIGNING_WORKFLOWS.items():
            def job_env(workflow, secret=secrets[0]):
                workflow["jobs"]["sign"]["env"]["LEAK"] = f"${{{{ secrets.{secret} }}}}"

            def second_step(workflow, secret=secrets[0]):
                self._install(workflow["jobs"]["sign"]).setdefault("env", {})[
                    "LEAK"
                ] = f"${{{{ secrets.{secret} }}}}"

            with self.subTest(workflow=name):
                self.assertIn("SIGNING_SECRET_SCOPE", self._mutated(name, job_env)["sign"])
                self.assertIn("SIGNING_SECRET_SCOPE", self._mutated(name, second_step)["sign"])

    def test_python_in_publication_job_fails(self) -> None:
        for name in SIGNING_WORKFLOWS:
            def mutate(workflow):
                workflow["jobs"]["publish"]["steps"].append(
                    {"name": "parse", "run": "python -c 'print(1)'"}
                )
            with self.subTest(workflow=name):
                self.assertIn("WRITE_JOB_RUNS_PYTHON", self._mutated(name, mutate)["publish"])

    def test_multiline_unhashed_install_is_detected(self) -> None:
        self.assertTrue(
            _install_violations("python -m pip install \\\n  -r tests/requirements.txt")
        )
        self.assertTrue(_install_violations("pip install requests"))
        self.assertFalse(
            _install_violations(
                "python -m pip --isolated install --require-hashes --only-binary=:all: "
                "--index-url https://pypi.org/simple -r hass_mcp_engineering_beta/requirements.lock"
            )
        )


class PublishingJobGuardTests(unittest.TestCase):
    """HAMCP-204: image-publishing write jobs hold no installer or test stack."""

    PUBLISHING_JOBS = ("claim", "build", "promote")

    def _mutated(self, mutate) -> dict[str, set[str]]:
        workflow = copy.deepcopy(_load(PUBLISH))
        mutate(workflow["jobs"])
        return workflow_violations(PUBLISH, workflow)

    @staticmethod
    def _append(job: dict, run: str, **extra) -> None:
        job["steps"].append({"name": "mutation", "shell": "bash", "run": run, **extra})

    @staticmethod
    def _checkout(job: dict) -> dict:
        return next(step for step in job["steps"] if _action(step) == "actions/checkout")

    @staticmethod
    def _setup_python(job: dict) -> dict:
        return next(step for step in job["steps"] if _action(step) == "actions/setup-python")

    def test_baseline_is_clean_and_read_only_jobs_hold_no_write(self) -> None:
        self.assertEqual(self._mutated(lambda jobs: None), {})
        jobs = _load(PUBLISH)["jobs"]
        self.assertEqual(jobs["verify-release"]["permissions"], {"contents": "read"})
        self.assertEqual(jobs["verify-image"]["permissions"], {"contents": "read"})
        self.assertEqual(jobs["summary"]["permissions"], {})
        # The validation stack still runs, in a job that cannot publish.
        self.assertIn("unittest", json.dumps(jobs["verify-release"]))

    def test_pre_split_promote_pattern_fails(self) -> None:
        # The old single promote job: persisted checkout credentials, a pip
        # cache, the hash-locked tests stack and release-checkout unittest.
        def old_pattern(jobs):
            job = jobs["promote"]
            self._checkout(job)["with"]["persist-credentials"] = True
            self._setup_python(job)["with"]["cache"] = "pip"
            self._append(job, LOCKED_INSTALL.replace(SIGNING_LOCK, "tests/requirements.lock"))
            self._append(job, "python -m unittest discover -s tests -v")

        self.assertEqual(
            self._mutated(old_pattern),
            {
                "promote": {
                    "CHECKOUT_PERSISTS_CREDENTIALS",
                    "PIP_CACHE",
                    "GENERAL_TEST_STACK",
                    "PUBLISHING_JOB_INSTALLS_PACKAGES",
                    "PUBLISHING_JOB_RUNS_TEST_STACK",
                    "PUBLISHING_JOB_RUNS_UNREVIEWED_PYTHON",
                }
            },
        )

    def test_unhashed_install_fails_in_every_publishing_job(self) -> None:
        for job_name in self.PUBLISHING_JOBS:
            for run in ("pip install requests", "python -m pip install -r scripts/requirements.txt"):
                with self.subTest(job=job_name, run=run):
                    found = self._mutated(
                        lambda jobs, job_name=job_name, run=run: self._append(jobs[job_name], run)
                    )[job_name]
                    self.assertIn("UNHASHED_INSTALL", found)
                    self.assertIn("PUBLISHING_JOB_INSTALLS_PACKAGES", found)

    def test_hash_locked_install_still_fails_in_publishing_jobs(self) -> None:
        for job_name in self.PUBLISHING_JOBS:
            with self.subTest(job=job_name):
                found = self._mutated(
                    lambda jobs, job_name=job_name: self._append(jobs[job_name], LOCKED_INSTALL)
                )[job_name]
                self.assertIn("PUBLISHING_JOB_INSTALLS_PACKAGES", found)
                self.assertNotIn("UNHASHED_INSTALL", found)

    def test_persisted_credentials_fail_in_every_publishing_job(self) -> None:
        for job_name in self.PUBLISHING_JOBS:
            for value in (True, None):
                def mutate(jobs, job_name=job_name, value=value):
                    options = self._checkout(jobs[job_name])["with"]
                    if value is None:
                        options.pop("persist-credentials")
                    else:
                        options["persist-credentials"] = value
                with self.subTest(job=job_name, value=value):
                    self.assertIn("CHECKOUT_PERSISTS_CREDENTIALS", self._mutated(mutate)[job_name])

    def test_floating_python_or_pip_cache_fails_in_publishing_jobs(self) -> None:
        for job_name in self.PUBLISHING_JOBS:
            def floating(jobs, job_name=job_name):
                self._setup_python(jobs[job_name])["with"]["python-version"] = "3.12"

            def cached(jobs, job_name=job_name):
                self._setup_python(jobs[job_name])["with"]["cache"] = "pip"

            with self.subTest(job=job_name):
                self.assertIn("FLOATING_PYTHON", self._mutated(floating)[job_name])
                self.assertIn("PIP_CACHE", self._mutated(cached)[job_name])

    def test_test_stack_fails_in_every_publishing_job(self) -> None:
        stacks = {
            "unittest": "python -m unittest discover -s tests -v",
            "compileall": "python -m compileall -q hass_mcp_engineering_beta",
            "check.ps1": "pwsh -File scripts/check.ps1 -Tier Full",
            "tests lock": 'git show "${WORKFLOW_AUTHORITY_SHA}:tests/requirements.lock" > "$RUNNER_TEMP/lock"',
            "shards": "echo scripts/unit_test_shards.py",
        }
        for job_name in self.PUBLISHING_JOBS:
            for label, run in stacks.items():
                with self.subTest(job=job_name, stack=label):
                    found = self._mutated(
                        lambda jobs, job_name=job_name, run=run: self._append(jobs[job_name], run)
                    )[job_name]
                    self.assertIn("PUBLISHING_JOB_RUNS_TEST_STACK", found)

    def test_release_checkout_python_fails_in_publishing_jobs(self) -> None:
        runs = (
            "python scripts/publication_digest_binding.py --github-output x",
            "python -m json.tool x.json",
            "python3 -c 'print(1)'",
            'verifier="scripts/verify_publication_source_image.py"\npython "$verifier" verify-source',
            'cp scripts/publication_claim.py "$RUNNER_TEMP/publication-policy/publication_claim.py"',
            'python "$RUNNER_TEMP/elsewhere/publication_claim.py" create',
        )
        for job_name in self.PUBLISHING_JOBS:
            for run in runs:
                with self.subTest(job=job_name, run=run):
                    found = self._mutated(
                        lambda jobs, job_name=job_name, run=run: self._append(
                            jobs[job_name], run, env={"WORKFLOW_AUTHORITY_SHA": AUTHORITY_ENV}
                        )
                    )[job_name]
                    self.assertIn("PUBLISHING_JOB_RUNS_UNREVIEWED_PYTHON", found)

    def test_policy_materialized_from_the_release_checkout_fails(self) -> None:
        for job_name in self.PUBLISHING_JOBS:
            def release_authority(jobs, job_name=job_name):
                step = next(
                    step
                    for step in jobs[job_name]["steps"]
                    if _authority_materialization(step) is not None
                )
                step["env"]["WORKFLOW_AUTHORITY_SHA"] = (
                    "${{ needs.detect-release.outputs.release_sha }}"
                )
            with self.subTest(job=job_name):
                self.assertIn(
                    "PUBLISHING_JOB_RUNS_UNREVIEWED_PYTHON",
                    self._mutated(release_authority)[job_name],
                )

    def test_allowlisted_workflow_authority_scripts_still_pass(self) -> None:
        for script in sorted(PUBLISHING_SCRIPTS):
            run = f'python "$RUNNER_TEMP/publication-policy/{script}" --flag "$VALUE"'
            with self.subTest(script=script):
                self.assertFalse(_runs_unreviewed_python(run))
        for job_name in self.PUBLISHING_JOBS:
            with self.subTest(job=job_name):
                self.assertEqual(
                    self._mutated(
                        lambda jobs, job_name=job_name: self._append(
                            jobs[job_name],
                            'if ! python "$RUNNER_TEMP/publication-policy/publication_handoff.py" '
                            'guard --release "$R" --authority "$A" > "$RUNNER_TEMP/out"; then\n'
                            "  exit 1\nfi",
                        )
                    ),
                    {},
                )

    def test_former_inline_and_variable_python_allowances_now_fail(self) -> None:
        # Pre-correction allowances: inline standard input and a quoted
        # variable written by git show.  Neither is an allowlisted path.
        for run in (
            "python - \"$x\" <<'PY'\nprint(1)\nPY",
            'publisher="$RUNNER_TEMP/p.py"\n'
            'git show "${WORKFLOW_AUTHORITY_SHA}:scripts/publish_registry_tags_guarded.py" '
            '> "$publisher"\npython "$publisher" --help',
        ):
            with self.subTest(run=run):
                self.assertTrue(_runs_unreviewed_python(run))

    def test_non_publishing_write_workflow_is_not_subject_to_publishing_rules(self) -> None:
        # A contents-write job elsewhere may run Python; adding packages: write
        # brings it under the publishing rules.
        workflow = copy.deepcopy(_load(PUBLISH))
        job = workflow["jobs"]["claim"]
        self._append(job, "python -m json.tool x.json")
        for name in ("build", "promote"):
            workflow["jobs"].pop(name)
        self.assertEqual(workflow_violations(PUBLISH, workflow), {})
        job["permissions"]["packages"] = "write"
        self.assertEqual(
            workflow_violations(PUBLISH, workflow),
            {"claim": {"PUBLISHING_JOB_RUNS_UNREVIEWED_PYTHON"}},
        )

    def test_reviewed_exception_mutation_fails_the_exact_match(self) -> None:
        # Re-adding the pre-HAMCP-204 exception no longer matches the observed set.
        stale = {
            (PUBLISH, "promote"): {
                "CHECKOUT_PERSISTS_CREDENTIALS",
                "GENERAL_TEST_STACK",
                "PIP_CACHE",
            },
        }
        self.assertNotEqual(all_violations(), stale)
        self.assertEqual(all_violations(), REVIEWED_EXCEPTIONS)


LOCKED_INSTALL = (
    "python -m pip --isolated install --require-hashes --only-binary=:all: "
    f"--index-url https://pypi.org/simple -r {SIGNING_LOCK}"
)
UNHASHED_PACKAGE = "synthetic-unhashed-package"


def _first_install_only(text: str) -> list[Install] | None:
    """Mutant parser: sees only the first installer invocation in a step."""

    installs = _REAL_INSTALL_INVOCATIONS(text)
    return installs if installs is None else installs[:1]


def _line_regex_install_violations(text: str) -> bool:
    """Mutant guard: the pre-correction (7974e33) physical-line regex check."""

    for match in PIP_INSTALL.finditer(text):
        command = match.group(0)
        if "--require-hashes" not in command or "--only-binary=:all:" not in command:
            return True
        files = re.findall(r"(?:^|\s)(?:-r|--requirement)(?:\s+|=)(\S+)", command)
        files = [value.strip("'\"") for value in files]
        if not files or any(not value.endswith(".lock") for value in files):
            return True
    return False


_REAL_INSTALL_INVOCATIONS = install_invocations


class ChainedInstallGuardTests(unittest.TestCase):
    """Every install in a privileged step is parsed and locked (Codex M2)."""

    CHAINS = {
        "and": f"{LOCKED_INSTALL} && python -m pip install {UNHASHED_PACKAGE}",
        "or": f"{LOCKED_INSTALL} || python -m pip install {UNHASHED_PACKAGE}",
        "semicolon": f"{LOCKED_INSTALL}; python -m pip install {UNHASHED_PACKAGE}",
        "newline": f"{LOCKED_INSTALL}\npython -m pip install {UNHASHED_PACKAGE}",
    }

    def _sign_with(self, name: str, run: str) -> set[str]:
        workflow = copy.deepcopy(_load(name))
        step = GuardMutationTests._step(
            workflow["jobs"]["sign"], lambda step: PIP_INSTALL.search(_run_text(step))
        )
        self.assertEqual(step["run"], LOCKED_INSTALL)
        step["run"] = run
        return workflow_violations(name, workflow).get("sign", set())

    def test_codex_chained_probe_fails_in_both_signing_jobs(self) -> None:
        for name in SIGNING_WORKFLOWS:
            for label, run in self.CHAINS.items():
                with self.subTest(workflow=name, chain=label):
                    found = self._sign_with(name, run)
                    self.assertIn("UNHASHED_INSTALL", found)
                    self.assertIn("SIGNING_JOB_EXTRA_INSTALL", found)

    def test_every_shell_separator_exposes_the_second_install(self) -> None:
        second = f"pip install {UNHASHED_PACKAGE}"
        scripts = {
            "and": f"{LOCKED_INSTALL} && {second}",
            "or": f"{LOCKED_INSTALL} || {second}",
            "semicolon": f"{LOCKED_INSTALL};{second}",
            "pipe": f"echo y | {second}",
            "background": f"{LOCKED_INSTALL} & {second}",
            "newline": f"{LOCKED_INSTALL}\n{second}",
            "continuation": f"{LOCKED_INSTALL} \\\n  && {second}",
            "continued arguments": f"{LOCKED_INSTALL} \\\n  {UNHASHED_PACKAGE}",
            "subshell": f"{LOCKED_INSTALL} && ({second})",
            "command substitution": f"echo $({second})",
            "backticks": f"echo `{second}`",
            "braces": f"{{ {LOCKED_INSTALL}; {second}; }}",
            "bash -c": f"bash -c '{LOCKED_INSTALL} && {second}'",
            "sh -c double quotes": f'sh -c "{second}"',
            "if": f"if true; then {second}; fi",
            "trailing package": f"{LOCKED_INSTALL} {UNHASHED_PACKAGE}",
            "unbalanced quote": f"{LOCKED_INSTALL} && {second} '",
            "python string": f"python -c \"import os; os.system('{second}')\"",
        }
        for label, script in scripts.items():
            with self.subTest(separator=label):
                self.assertTrue(_install_violations(script))

    def test_every_installer_invocation_is_checked(self) -> None:
        variants = {
            "pip": f"pip install {UNHASHED_PACKAGE}",
            "pip3": f"pip3 install {UNHASHED_PACKAGE}",
            "pip3.12": f"pip3.12 install {UNHASHED_PACKAGE}",
            "absolute pip": f"/usr/bin/pip install {UNHASHED_PACKAGE}",
            "python -m pip": f"python -m pip install {UNHASHED_PACKAGE}",
            "python3 -m pip": f"python3 -m pip install {UNHASHED_PACKAGE}",
            "python3.12 -m pip3": f"python3.12 -m pip3 install {UNHASHED_PACKAGE}",
            "python -I -m pip": f"python -I -m pip install {UNHASHED_PACKAGE}",
            "sudo pip": f"sudo pip install {UNHASHED_PACKAGE}",
            "env pip": f"env PIP_NO_INPUT=1 pip install {UNHASHED_PACKAGE}",
            "uv pip": f"uv pip install {UNHASHED_PACKAGE}",
            "uv pip sync": "uv pip sync requirements.txt",
            "uv tool": f"uv tool install {UNHASHED_PACKAGE}",
            "uv add": f"uv add {UNHASHED_PACKAGE}",
            "uvx": f"uvx {UNHASHED_PACKAGE}",
            "pipx": f"pipx install {UNHASHED_PACKAGE}",
            "pipx run": f"pipx run {UNHASHED_PACKAGE}",
            "extra package": f"{LOCKED_INSTALL} {UNHASHED_PACKAGE}",
            "extra index": f"{LOCKED_INSTALL} --extra-index-url https://example.invalid/simple",
            "second index": f"{LOCKED_INSTALL} --index-url https://example.invalid/simple",
            "unlocked file": LOCKED_INSTALL.replace(".lock", ".txt"),
            "constraint": f"{LOCKED_INSTALL} -c constraints.txt",
            "editable": f"{LOCKED_INSTALL} -e .",
            "no hashes": LOCKED_INSTALL.replace("--require-hashes ", ""),
            "source builds": LOCKED_INSTALL.replace("--only-binary=:all: ", ""),
            "partial binary": LOCKED_INSTALL.replace(":all:", "cryptography"),
        }
        for label, script in variants.items():
            with self.subTest(invocation=label):
                self.assertTrue(_install_violations(script))

    def test_signing_job_rejects_a_second_locked_file(self) -> None:
        for name in SIGNING_WORKFLOWS:
            run = (
                f"{LOCKED_INSTALL} && python -m pip install --require-hashes "
                "--only-binary=:all: -r scripts/other.lock"
            )
            with self.subTest(workflow=name):
                found = self._sign_with(name, run)
                self.assertNotIn("UNHASHED_INSTALL", found)
                self.assertIn("SIGNING_JOB_EXTRA_INSTALL", found)

    def test_legitimate_locked_installs_still_pass(self) -> None:
        legitimate = {
            "single locked": LOCKED_INSTALL,
            "two locked": f"{LOCKED_INSTALL} && {LOCKED_INSTALL}",
            "folded multiline": (
                "python -m pip --isolated install --require-hashes --only-binary=:all: \\\n"
                '  --index-url https://pypi.org/simple -r "$RUNNER_TEMP/release-validation.lock"'
            ),
            "requirement option": LOCKED_INSTALL.replace(" -r ", " --requirement="),
            "uv pip locked": (
                f"uv pip install --require-hashes --only-binary=:all: -r {SIGNING_LOCK}"
            ),
            "coreutils install": "install -D -m 0644 -- source target",
            "pip version": "python -m pip --version",
            "no installer": "python scripts/sign.py --output out.json",
        }
        for label, script in legitimate.items():
            with self.subTest(script=label):
                self.assertFalse(_install_violations(script))
        for name in SIGNING_WORKFLOWS:
            with self.subTest(workflow=name):
                self.assertEqual(self._sign_with(name, LOCKED_INSTALL), set())
        self.assertEqual(all_violations(), REVIEWED_EXCEPTIONS)

    def test_first_install_only_guard_misses_the_chain(self) -> None:
        module = sys.modules[__name__]
        for name in SIGNING_WORKFLOWS:
            run = self.CHAINS["and"]
            with self.subTest(workflow=name, guard="real"):
                self.assertIn("UNHASHED_INSTALL", self._sign_with(name, run))
            with self.subTest(workflow=name, guard="first install only"):
                with mock.patch.object(module, "install_invocations", _first_install_only):
                    self.assertEqual(self._sign_with(name, run), set())
            with self.subTest(workflow=name, guard="pre-correction line regex"):
                with mock.patch.object(
                    module, "_install_violations", _line_regex_install_violations
                ):
                    self.assertNotIn("UNHASHED_INSTALL", self._sign_with(name, run))

def _space_separated_module_only(arguments: list[str]) -> tuple[str, str | None, int]:
    """Mutant interpreter parser: the pre-correction (feb65a4) ``-m pip`` match."""

    if "-m" in arguments:
        module_at = arguments.index("-m")
        module = arguments[module_at + 1 : module_at + 2]
        return "m", (module or [""])[0], module_at + 2
    return "none", None, 0


_PRE_CORRECTION_INSTALLER_NAME = re.compile(
    r"(?<![\w.-])(?:pip(?:3(?:\.[0-9]+)?)?|pipx|uvx?)(?![\w.-])"
)


class PythonInterpreterGuardTests(unittest.TestCase):
    """Python invocations are parsed the way CPython parses them (Codex M2 residual)."""

    # Interpreter prefixes that run pip, each followed by its pip arguments.
    PIP_PREFIXES = {
        "codex -mpip": "python -mpip",
        "codex -Impip": "python3 -Impip",
        "-I -m pip": "python -I -m pip",
        "-Im pip": "python -Im pip",
        "-BIm pip": "python -BIm pip",
        "-W ignore -m pip": "python -W ignore -m pip",
        "-Wignore -mpip": "python -Wignore -mpip",
        "-X dev -m pip": "python -X dev -m pip",
        "-Xdev -Im pip": "python -Xdev -Im pip",
        "-W consumes -m": "python -W -m -m pip",
        "-OO -m pip": "python -OO -m pip",
        "--version flag": "python --version -m pip",
        "--check-hash-based-pycs": "python --check-hash-based-pycs never -m pip",
        "python3.12 -mpip": "python3.12 -mpip",
        "python4 -m pip": "python4 -m pip",
        "/usr/bin/python3 -m pip": "/usr/bin/python3 -m pip",
        "relative venv -Impip": "./.venv/bin/python -Impip",
        "-m pip.__main__": "python -m pip.__main__",
        "-mpip3": "python -mpip3",
        "sudo -Impip": "sudo python3 -Impip",
    }

    def _sign_with(self, name: str, run: str) -> set[str]:
        return ChainedInstallGuardTests._sign_with(self, name, run)

    def test_codex_attached_option_probes_fail_in_both_signing_jobs(self) -> None:
        probes = {
            "-mpip chained": f"{LOCKED_INSTALL} && python -mpip install {UNHASHED_PACKAGE}",
            "-Impip chained": f"{LOCKED_INSTALL} && python3 -Impip install {UNHASHED_PACKAGE}",
            "-mpip standalone": f"python -mpip install {UNHASHED_PACKAGE}",
            "-Impip standalone": f"python3 -Impip install {UNHASHED_PACKAGE}",
        }
        for name in SIGNING_WORKFLOWS:
            for label, run in probes.items():
                with self.subTest(workflow=name, probe=label):
                    found = self._sign_with(name, run)
                    self.assertIn("UNHASHED_INSTALL", found)
                    self.assertIn("SIGNING_JOB_EXTRA_INSTALL", found)

    def test_option_variants_identify_the_pip_module(self) -> None:
        for label, prefix in self.PIP_PREFIXES.items():
            script = f"{prefix} install {UNHASHED_PACKAGE}"
            with self.subTest(prefix=label):
                installs = install_invocations(script)
                self.assertIsNotNone(installs)
                self.assertEqual(
                    [(install.tool, install.arguments) for install in installs],
                    [("pip", ["install", UNHASHED_PACKAGE])],
                )
                self.assertTrue(_install_violations(script))
            for name in SIGNING_WORKFLOWS:
                with self.subTest(prefix=label, workflow=name):
                    found = self._sign_with(name, f"{LOCKED_INSTALL} && {script}")
                    self.assertIn("UNHASHED_INSTALL", found)
                    self.assertIn("SIGNING_JOB_EXTRA_INSTALL", found)

    def test_locked_attached_option_installs_still_pass(self) -> None:
        locked_arguments = LOCKED_INSTALL.removeprefix("python -m pip ")
        for label in ("codex -Impip", "-BIm pip", "-W ignore -m pip", "python3.12 -mpip"):
            run = f"{self.PIP_PREFIXES[label]} {locked_arguments}"
            with self.subTest(prefix=label):
                self.assertEqual(
                    [install.requirement_files() for install in install_invocations(run)],
                    [[SIGNING_LOCK]],
                )
                self.assertFalse(_install_violations(run))
            for name in SIGNING_WORKFLOWS:
                with self.subTest(prefix=label, workflow=name):
                    self.assertEqual(self._sign_with(name, run), set())

    def test_unparseable_interpreter_invocations_fail_closed(self) -> None:
        unparseable = {
            "-c pip": "python -c \"import pip; pip.main(['install', 'x'])\"",
            "-c runpy": "python -c 'import runpy; runpy.run_module(\"pip\")'",
            "-Ic install": "python3 -Ic 'import subprocess; subprocess.run([\"install\"])'",
            "-c uv": "python -c 'import uv'",
            "unknown short option": f"python -t -m pip install {UNHASHED_PACKAGE}",
            "unknown cluster letter": f"python -IJm pip install {UNHASHED_PACKAGE}",
            "unknown long option": f"python --isolated -m pip install {UNHASHED_PACKAGE}",
            "long option with equals": (
                f"python --check-hash-based-pycs=never -m pip install {UNHASHED_PACKAGE}"
            ),
            "missing -W argument": "python -W",
            "missing -m argument": "python -I -m",
            "pip submodule": f"python -m pip._internal.cli.main install {UNHASHED_PACKAGE}",
            "computed interpreter": f'"$PYTHON" -mpip install {UNHASHED_PACKAGE}',
            "multi-line -c": "python -c '\nimport sys\nimport pip\n' && true",
            "unclosed -c": "python -c 'import pip",
            "heredoc stdin": "python - <<'PY'\nimport runpy\nrunpy.run_module('pip')\nPY",
            "stripped heredoc": "python3 -I <<-PY\n\timport pip\n\tPY",
            "piped stdin": "echo 'import pip' | python -",
        }
        for label, script in unparseable.items():
            with self.subTest(script=label):
                self.assertIsNone(install_invocations(script))
                self.assertTrue(_install_violations(script))
        # Script arguments are not interpreter options: the bare installer
        # word is then parsed as its own (unlocked) invocation.
        for label, script in {
            "script arguments": f"python scripts/run.py -m pip install {UNHASHED_PACKAGE}",
            "after --": f"python -- -m pip install {UNHASHED_PACKAGE}",
        }.items():
            with self.subTest(script=label):
                self.assertTrue(_install_violations(script))
        for name in SIGNING_WORKFLOWS:
            for label in ("-c pip", "-c runpy", "unknown short option"):
                with self.subTest(workflow=name, script=label):
                    found = self._sign_with(name, f"{LOCKED_INSTALL} && {unparseable[label]}")
                    self.assertIn("UNHASHED_INSTALL", found)

    def test_other_installer_modules_are_checked(self) -> None:
        for script in (
            f"python -m uv pip install {UNHASHED_PACKAGE}",
            f"python -muv add {UNHASHED_PACKAGE}",
            f"python3 -Impipx install {UNHASHED_PACKAGE}",
        ):
            with self.subTest(script=script):
                self.assertTrue(_install_violations(script))

    def test_non_installer_python_still_passes(self) -> None:
        for script in (
            "python -c 'print(1)'",
            "python -c 'import json, sys; print(json.load(open(sys.argv[1]))[\"revision\"])' x.json",
            "python -I -B scripts/sign.py --output out.json",
            "python -W ignore -X dev scripts/sign.py",
            "python -- scripts/sign.py",
            "python3 -Im compileall -q scripts",
            "python -m json.tool in.json",
            "python",
            "python - out.md <<'PY'\nimport sys\nprint(sys.argv)\nPY\nset -euo pipefail",
            "# the signer's own lock\npython -c 'print(1)'",
        ):
            with self.subTest(script=script):
                self.assertEqual(install_invocations(script), [])
                self.assertFalse(_install_violations(script))

    def test_space_separated_module_parser_is_killed(self) -> None:
        module = sys.modules[__name__]
        probe = f"{LOCKED_INSTALL} && python3 -Impip install {UNHASHED_PACKAGE}"
        locked = f"python3 -Impip {LOCKED_INSTALL.removeprefix('python -m pip ')}"
        with mock.patch.object(module, "_python_command", _space_separated_module_only):
            # The mutant cannot identify an attached module: the backstop
            # refuses the locked control, so the variant tests fail.
            self.assertIsNone(install_invocations(f"python -Impip install {UNHASHED_PACKAGE}"))
            self.assertTrue(_install_violations(locked))
            with mock.patch.object(
                module, "INSTALLER_NAME", _PRE_CORRECTION_INSTALLER_NAME
            ), mock.patch.object(module, "_interpreter_code_is_unattributable", lambda text: False):
                # With the old name pattern and no code pass, it is the guard
                # Codex bypassed.
                for name in SIGNING_WORKFLOWS:
                    with self.subTest(workflow=name, guard="pre-correction"):
                        self.assertEqual(self._sign_with(name, probe), set())
        self.assertFalse(_install_violations(locked))
        for name in SIGNING_WORKFLOWS:
            with self.subTest(workflow=name, guard="real"):
                self.assertIn("UNHASHED_INSTALL", self._sign_with(name, probe))


POLICY_SCRIPT = '"$RUNNER_TEMP/publication-policy/publication_claim.py"'
# The surviving mutation from the Codex review of fc69da1 (R1), verbatim.
CODEX_R1_PROBE = "/usr/bin/python3 scripts/validate_promotion_candidate.py --help"
INTERPRETER_PATH_PROBES = (
    CODEX_R1_PROBE,
    f"/usr/bin/python3 {POLICY_SCRIPT} create",
    f"/usr/bin/python {POLICY_SCRIPT} create",
    f"./venv/bin/python {POLICY_SCRIPT} create",
    f"venv/bin/python3.12 {POLICY_SCRIPT} create",
    f"python3 {POLICY_SCRIPT} create",
    f"python3.12 {POLICY_SCRIPT} create",
    f"$(which python3) {POLICY_SCRIPT} create",
    f'"$(command -v python)" {POLICY_SCRIPT} create',
    f"`which python` {POLICY_SCRIPT} create",
    'interpreter=/usr/bin/python3\n"$interpreter" -c "print(1)"',
    "bash -c '/usr/bin/python3 scripts/validate_promotion_candidate.py --help'",
    'sh -c "python -c 1"',
)
STDIN_PROBES = (
    "python - < scripts/validate_promotion_candidate.py",
    "python -",
    "python",
    "python -IS -",
    "python <<'PY'\nprint(1)\nPY",
    "python <<-PY\n\tprint(1)\n\tPY",
    "python <<< 'print(1)'",
    "python3 - <<'PY'\nprint(1)\nPY",
    "/usr/bin/python3 - <<'PY'\nprint(1)\nPY",
    "cat scripts/validate_promotion_candidate.py | python",
    "cat scripts/validate_promotion_candidate.py | /usr/bin/python3 -",
    "echo 'print(1)' |& python -",
    f"python {POLICY_SCRIPT} create < scripts/payload",
    f"python {POLICY_SCRIPT} create 0< scripts/payload",
    f"python {POLICY_SCRIPT} create <<'PY'\nprint(1)\nPY",
    f"python {POLICY_SCRIPT} create <<< 'print(1)'",
    f"cat scripts/payload | python {POLICY_SCRIPT} create",
)
INLINE_PROBES = (
    "python -c 'print(1)'",
    "python -Ic 'print(1)'",
    "python -I -c 'print(1)'",
    "/usr/bin/python3 -c 'print(1)'",
    "python -m json.tool x.json",
    "python -Im json.tool x.json",
    f"python -I {POLICY_SCRIPT} create",
    f"python -- {POLICY_SCRIPT} create",
    f"exec python {POLICY_SCRIPT} create",
    f"env python {POLICY_SCRIPT} create",
    f"PYTHONPATH=. python {POLICY_SCRIPT} create",
    f"X=1 python {POLICY_SCRIPT} create",
    "python scripts/publication_claim.py create",
    'python "$RUNNER_TEMP/publication-policy/verify_publication_source_image.py"',
    'python "$RUNNER_TEMP/publication-policy/../publication_claim.py"',
    'python "$RUNNER_TEMP/elsewhere/publication_claim.py" create',
    "# python is mentioned here",
    'echo "run python later"',
)
PACKAGE_MANAGER_PROBES = (
    "apt-get install -y jq",
    "sudo apt install -y jq",
    "/usr/bin/apt-get install -y jq",
    "dpkg -i x.deb",
    "npm ci",
    "npx some-tool",
    "pipx run some-tool",
    "uv run some-tool",
    "uvx some-tool",
    "conda install x",
    "brew install x",
    "gem install x",
    "cargo install x",
    "poetry install",
    "pip3 install x",
)


def _stdin_blind_approved_python(words: list[str], position: int) -> bool:
    """Mutant: approves an allowlisted script whatever feeds its standard input."""

    if words[position] != "python":
        return False
    command = list(itertools.takewhile(lambda word: not _is_operator(word), words[position + 1 :]))
    try:
        kind, script, consumed = _python_command(command)
    except ValueError:
        return False
    match = POLICY_SCRIPT_PATH.fullmatch(script or "")
    return kind == "script" and not consumed and bool(match) and match.group(1) in PUBLISHING_SCRIPTS



def _stdlib_violations(path: Path) -> list[str]:
    """Imports outside the standard library, or dynamic code loading, in a script."""

    siblings = {Path(name).stem for name in PUBLISHING_SCRIPTS}
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = []
    for node in ast.walk(tree):
        modules = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                found.append(f"relative import at line {node.lineno}")
            modules = [node.module or ""]
        for module in modules:
            top = module.split(".", 1)[0]
            if top in {"importlib", "runpy", "pkgutil", "zipimport", "site"}:
                found.append(f"dynamic import machinery {module} at line {node.lineno}")
            elif top not in sys.stdlib_module_names and top not in siblings:
                found.append(f"non-stdlib import {module} at line {node.lineno}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {
            "__import__", "exec", "eval", "compile",
        }:
            found.append(f"dynamic code {node.func.id} at line {node.lineno}")
        if isinstance(node, ast.Attribute) and node.attr in {"executable", "path"} and (
            isinstance(node.value, ast.Name) and node.value.id == "sys"
        ):
            found.append(f"sys.{node.attr} at line {node.lineno}")
    return found


def _sibling_imports(path: Path) -> set[str]:
    siblings = {Path(name).stem for name in PUBLISHING_SCRIPTS} | {
        item.stem for item in (ROOT / "scripts").glob("*.py")
    }
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {alias.name.split(".", 1)[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module.split(".", 1)[0])
    return {module + ".py" for module in modules if module in siblings}


class PublishingPythonInterpreterTests(unittest.TestCase):
    """HAMCP-204 M1/M2: the approved Python form in image-publishing write jobs."""

    PUBLISHING_JOBS = ("claim", "build", "promote")

    def _with(self, job_name: str, run: str, **extra) -> set[str]:
        workflow = copy.deepcopy(_load(PUBLISH))
        workflow["jobs"][job_name]["steps"].append(
            {"name": "mutation", "shell": "bash", "run": run, **extra}
        )
        return workflow_violations(PUBLISH, workflow).get(job_name, set())

    def test_codex_r1_mutation_fails_in_promote(self) -> None:
        # R1 verbatim: appended to the step before the digest binding step.
        workflow = copy.deepcopy(_load(PUBLISH))
        steps = workflow["jobs"]["promote"]["steps"]
        bind = next(
            index for index, step in enumerate(steps)
            if step.get("name", "").startswith("Bind the promoted digest")
        )
        steps[bind - 1]["run"] = steps[bind - 1]["run"] + "\n" + CODEX_R1_PROBE + "\n\n"
        self.assertEqual(
            workflow_violations(PUBLISH, workflow),
            {"promote": {"PUBLISHING_JOB_RUNS_UNREVIEWED_PYTHON"}},
        )

    def test_absolute_relative_and_substituted_interpreters_fail(self) -> None:
        for job_name in self.PUBLISHING_JOBS:
            for run in INTERPRETER_PATH_PROBES:
                with self.subTest(job=job_name, run=run):
                    self.assertIn(
                        "PUBLISHING_JOB_RUNS_UNREVIEWED_PYTHON", self._with(job_name, run)
                    )

    def test_stdin_fed_python_fails(self) -> None:
        for job_name in self.PUBLISHING_JOBS:
            for run in STDIN_PROBES:
                with self.subTest(job=job_name, run=run):
                    self.assertIn(
                        "PUBLISHING_JOB_RUNS_UNREVIEWED_PYTHON", self._with(job_name, run)
                    )

    def test_inline_module_option_and_unlisted_python_fails(self) -> None:
        for job_name in self.PUBLISHING_JOBS:
            for run in INLINE_PROBES:
                with self.subTest(job=job_name, run=run):
                    self.assertIn(
                        "PUBLISHING_JOB_RUNS_UNREVIEWED_PYTHON", self._with(job_name, run)
                    )

    def test_allowlisted_script_by_path_still_passes(self) -> None:
        for job_name in self.PUBLISHING_JOBS:
            for run in (
                f"python {POLICY_SCRIPT} create --release \"$R\"",
                f"python {POLICY_SCRIPT} create | tee \"$RUNNER_TEMP/out\"",
                f"result=1\npython {POLICY_SCRIPT} create > \"$RUNNER_TEMP/out\" 2>&1",
            ):
                with self.subTest(job=job_name, run=run):
                    self.assertEqual(self._with(job_name, run), set())

    def test_python_shell_defaults_and_environment_fail(self) -> None:
        for job_name in self.PUBLISHING_JOBS:
            with self.subTest(job=job_name, form="shell: python"):
                self.assertIn(
                    "PUBLISHING_JOB_RUNS_UNREVIEWED_PYTHON",
                    self._with(job_name, "print(1)", shell="python"),
                )
            with self.subTest(job=job_name, form="shell: python {0}"):
                self.assertIn(
                    "PUBLISHING_JOB_RUNS_UNREVIEWED_PYTHON",
                    self._with(job_name, "print(1)", shell="python {0}"),
                )
            with self.subTest(job=job_name, form="step PYTHONPATH"):
                self.assertIn(
                    "PUBLISHING_JOB_RUNS_UNREVIEWED_PYTHON",
                    self._with(job_name, f"python {POLICY_SCRIPT}", env={"PYTHONPATH": "."}),
                )
            for scope in ("workflow", "job"):
                workflow = copy.deepcopy(_load(PUBLISH))
                target = workflow if scope == "workflow" else workflow["jobs"][job_name]
                target["defaults"] = {"run": {"shell": "python"}}
                with self.subTest(job=job_name, form=f"{scope} default shell"):
                    self.assertIn(
                        "PUBLISHING_JOB_RUNS_UNREVIEWED_PYTHON",
                        workflow_violations(PUBLISH, workflow)[job_name],
                    )
                workflow = copy.deepcopy(_load(PUBLISH))
                target = workflow if scope == "workflow" else workflow["jobs"][job_name]
                target.setdefault("env", {})["PYTHONSTARTUP"] = "scripts/x.py"
                with self.subTest(job=job_name, form=f"{scope} PYTHONSTARTUP"):
                    self.assertIn(
                        "PUBLISHING_JOB_RUNS_UNREVIEWED_PYTHON",
                        workflow_violations(PUBLISH, workflow)[job_name],
                    )
            with self.subTest(job=job_name, form="site customization"):
                self.assertIn(
                    "PUBLISHING_JOB_RUNS_UNREVIEWED_PYTHON",
                    self._with(job_name, 'cp scripts/x "$TOOLCACHE/site-packages/x.pth"'),
                )

    def test_package_managers_and_extra_actions_fail(self) -> None:
        for job_name in self.PUBLISHING_JOBS:
            for run in PACKAGE_MANAGER_PROBES:
                with self.subTest(job=job_name, run=run):
                    self.assertIn("PUBLISHING_JOB_INSTALLS_PACKAGES", self._with(job_name, run))
            workflow = copy.deepcopy(_load(PUBLISH))
            workflow["jobs"][job_name]["steps"].append(
                {"uses": "astral-sh/setup-uv@" + "0" * 40}
            )
            with self.subTest(job=job_name, action="setup-uv"):
                self.assertIn(
                    "PUBLISHING_JOB_EXTRA_ACTION", workflow_violations(PUBLISH, workflow)[job_name]
                )
        # set -euo pipefail and other baseline words are not package managers.
        self.assertIsNone(PACKAGE_MANAGER.search("set -euo pipefail; git show x > y"))

    def test_materialization_names_only_allowlisted_scripts(self) -> None:
        for job_name in self.PUBLISHING_JOBS:
            for extra in ("verify_publication_source_image.py", "validate_promotion_candidate.py"):
                workflow = copy.deepcopy(_load(PUBLISH))
                step = next(
                    step for step in workflow["jobs"][job_name]["steps"]
                    if _authority_materialization(step) is not None
                )
                step["run"] = step["run"].replace(
                    "for policy in publication_handoff.py",
                    f"for policy in {extra} publication_handoff.py",
                )
                with self.subTest(job=job_name, extra=extra):
                    self.assertIn(
                        "PUBLISHING_JOB_RUNS_UNREVIEWED_PYTHON",
                        workflow_violations(PUBLISH, workflow)[job_name],
                    )

    def test_every_job_materializes_the_imports_of_the_scripts_it_runs(self) -> None:
        jobs = _load(PUBLISH)["jobs"]
        for job_name in self.PUBLISHING_JOBS:
            steps = jobs[job_name]["steps"]
            materialized = set()
            run = set()
            for step in steps:
                loops = POLICY_LOOP.findall(_run_text(step))
                if _authority_materialization(step) is not None:
                    materialized |= set(loops[0].split())
                run |= set(POLICY_INVOCATION_SCRIPT.findall(_run_text(step)))
            with self.subTest(job=job_name):
                self.assertTrue(run)
                closure = set(run)
                pending = list(run)
                while pending:
                    imports = _sibling_imports(ROOT / "scripts" / pending.pop())
                    pending.extend(imports - closure)
                    closure |= imports
                self.assertLessEqual(closure, materialized)
                self.assertLessEqual(materialized, PUBLISHING_SCRIPTS)

    def test_allowlisted_scripts_import_only_the_standard_library(self) -> None:
        for name in sorted(PUBLISHING_SCRIPTS):
            with self.subTest(script=name):
                self.assertEqual(_stdlib_violations(ROOT / "scripts" / name), [])

    def test_stdlib_import_check_rejects_third_party_and_dynamic_imports(self) -> None:
        for source in (
            "import yaml\n",
            "def f():\n    import yaml\n",
            "from requests import get\n",
            "import importlib\n",
            "from importlib import import_module\n",
            "import runpy\n",
            "__import__('yaml')\n",
            "exec('import yaml')\n",
            "import sys\nsys.path.insert(0, '.')\n",
            "import sys\nsys.executable\n",
            "from . import sibling\n",
            "import verify_publication_source_image\n",
        ):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "candidate.py"
                path.write_text(source, encoding="utf-8")
                self.assertTrue(_stdlib_violations(path))
        # The verifier that parses YAML stays outside the allowlist.
        self.assertTrue(_stdlib_violations(ROOT / "scripts/publication_handoff_verify.py"))
        self.assertNotIn("publication_handoff_verify.py", PUBLISHING_SCRIPTS)

    def test_path_blind_parser_is_killed(self) -> None:
        module = sys.modules[__name__]
        blind = re.compile(r"(?<![\w./$-])python(?:[0-9]+(?:\.[0-9]+)?)?(?![\w.-])")
        bare = lambda word: bool(PYTHON_PROGRAM.fullmatch(word))  # noqa: E731
        with mock.patch.object(module, "INTERPRETER_NAME", blind), mock.patch.object(
            module, "_interpreter_word", bare
        ):
            # Recognizing only bare words, counting with the fc69da1 lookbehind:
            # the absolute-path probes and R1 pass unseen, bare python3 does not.
            self.assertTrue(_runs_unreviewed_python(f"python3 {POLICY_SCRIPT} create"))
            for run in (CODEX_R1_PROBE, f"/usr/bin/python3 {POLICY_SCRIPT} create",
                        f"./venv/bin/python {POLICY_SCRIPT} create"):
                with self.subTest(guard="path-blind", run=run):
                    self.assertFalse(_runs_unreviewed_python(run))
        for run in (CODEX_R1_PROBE, f"/usr/bin/python3 {POLICY_SCRIPT} create",
                    f"./venv/bin/python {POLICY_SCRIPT} create"):
            with self.subTest(guard="real", run=run):
                self.assertTrue(_runs_unreviewed_python(run))

    def test_stdin_blind_parser_is_killed(self) -> None:
        module = sys.modules[__name__]
        probes = (
            f"python {POLICY_SCRIPT} create < scripts/payload",
            f"python {POLICY_SCRIPT} create <<'PY'\nprint(1)\nPY",
            f"python {POLICY_SCRIPT} create <<< 'print(1)'",
            f"cat scripts/payload | python {POLICY_SCRIPT} create",
        )
        with mock.patch.object(module, "_approved_python", _stdin_blind_approved_python):
            for run in probes:
                with self.subTest(guard="stdin-blind", run=run):
                    self.assertFalse(_runs_unreviewed_python(run))
        for run in probes:
            with self.subTest(guard="real", run=run):
                self.assertTrue(_runs_unreviewed_python(run))


# How each workflow binds the recorded revision to the verified attestation.
ATTESTED_REVISION = {
    RELEASE_REGISTRY: (
        'print(provenance["workflow_sha"])',
        'revision="$(cat "$RUNNER_TEMP/attested-revision.txt")"',
    ),
    COMPATIBILITY: (
        '"revision": provenance["workflow_sha"]',
        'revision="$(python -c \'import json, sys; print(json.load(open(sys.argv[1]))["revision"])\' '
        '"$RUNNER_TEMP/attested.json")"',
    ),
}


def provenance_violations(name: str, source: str) -> set[str]:
    """Return why a workflow's image step would accept label-only evidence."""
    violations: set[str] = set()
    image = next(
        step
        for step in yaml.safe_load(source)["jobs"]["evidence"]["steps"]
        if step.get("id") == "image"
    )["run"]
    for required in (
        "python scripts/verify_upstream_image_provenance.py",
        '--index-digest "$index_digest"',
        '--source-commit "${{ steps.source.outputs.source_commit }}"',
        '--version "$VERSION"',
    ):
        if required not in image:
            violations.add("ATTESTATION_NOT_VERIFIED")
    extracted, assigned = ATTESTED_REVISION[name]
    assignments = [
        line.strip()
        for line in image.splitlines()
        if re.match(r"\s*revision=", line)
    ]
    if extracted not in image or assignments != [assigned]:
        violations.add("REVISION_NOT_ATTESTED")
    if 'test "$label_revision" = "$revision"' not in image:
        violations.add("LABEL_AGREEMENT_UNCHECKED")
    # The revision is never taken from an image label.
    if re.search(r'(?<![A-Za-z_])revision="\$\(docker image inspect', source):
        violations.add("REVISION_FROM_LABEL")
    # Annotation strings are only locators inside the verifier, never evidence.
    for prohibited in (
        '"slsa" in',
        "spdx\" in",
        "in-toto.io/predicate-type",
        "vnd.docker.reference.type",
    ):
        if prohibited in source:
            violations.add("ANNOTATION_AS_EVIDENCE")
    if len(re.findall(r'imagetools inspect(?: --raw)? "\$tagged"', source)) != 1:
        violations.add("TAG_RESOLVED_MORE_THAN_ONCE")
    if (
        'docker pull --platform linux/amd64 "$image"' in source
        or 'docker image inspect "$image"' in source
    ):
        violations.add("MUTABLE_IMAGE_REFERENCE")
    return violations


class ProvenanceWorkflowTests(unittest.TestCase):
    def _source(self, name: str) -> str:
        return (WORKFLOWS / name).read_text(encoding="utf-8")

    def _assert_mutation(self, name: str, old: str, new: str, expected: str) -> None:
        source = self._source(name)
        self.assertIn(old, source)
        self.assertIn(expected, provenance_violations(name, source.replace(old, new)))

    def test_both_workflows_verify_attestation_payloads(self) -> None:
        for name in SIGNING_WORKFLOWS:
            with self.subTest(workflow=name):
                self.assertEqual(provenance_violations(name, self._source(name)), set())

    def test_label_only_revision_fails(self) -> None:
        for name in SIGNING_WORKFLOWS:
            with self.subTest(workflow=name):
                # Take the revision from the image label instead of the attestation.
                self._assert_mutation(
                    name,
                    'label_revision="$(docker image inspect',
                    'revision="$(docker image inspect',
                    "REVISION_FROM_LABEL",
                )
                _, assigned = ATTESTED_REVISION[name]
                self._assert_mutation(name, assigned, 'revision="unverified"', "REVISION_NOT_ATTESTED")
                # A later reassignment cannot replace the attested revision.
                self._assert_mutation(
                    name,
                    assigned,
                    assigned + '\n          revision="$label_revision"',
                    "REVISION_NOT_ATTESTED",
                )
                self._assert_mutation(
                    name, 'provenance["workflow_sha"]', 'provenance["version"]', "REVISION_NOT_ATTESTED"
                )

    def test_dropping_verifier_or_label_agreement_fails(self) -> None:
        for name in SIGNING_WORKFLOWS:
            with self.subTest(workflow=name):
                self._assert_mutation(
                    name,
                    "python scripts/verify_upstream_image_provenance.py",
                    "true scripts/verify_upstream_image_provenance.py",
                    "ATTESTATION_NOT_VERIFIED",
                )
                self._assert_mutation(
                    name,
                    '--source-commit "${{ steps.source.outputs.source_commit }}"',
                    "",
                    "ATTESTATION_NOT_VERIFIED",
                )
                self._assert_mutation(
                    name,
                    'test "$label_revision" = "$revision"',
                    "true",
                    "LABEL_AGREEMENT_UNCHECKED",
                )

    def test_annotation_strings_or_mutable_tags_as_evidence_fail(self) -> None:
        for name in SIGNING_WORKFLOWS:
            with self.subTest(workflow=name):
                self._assert_mutation(
                    name,
                    'immutable="${UPSTREAM_IMAGE_REPOSITORY}@${index_digest}"',
                    'immutable="${UPSTREAM_IMAGE_REPOSITORY}@${index_digest}"\n'
                    '          grep -q "in-toto.io/predicate-type" "$inspect"',
                    "ANNOTATION_AS_EVIDENCE",
                )
                self._assert_mutation(
                    name,
                    'docker pull --platform linux/amd64 "$immutable"',
                    'docker pull --platform linux/amd64 "$image"',
                    "MUTABLE_IMAGE_REFERENCE",
                )

if __name__ == "__main__":
    unittest.main()
