"""Owned disposable-directory host operations for Core evidence lanes (HAMCP-309).

The migration fixture writer runs as root, so the lane's owned disposable
configuration directory holds root-owned files the runner user cannot write
or remove.  Two operations need that authority:

* ``stage --mode 600 --source S --destination D`` installs one owned
  synthetic file;
* ``remove-owned-dir --directory D`` removes the owned directory.

The mode is explicit, logged on standard error, and never inferred from sudo
availability:

* ``actions`` (``GITHUB_ACTIONS=true``): replaces this process with exactly
  ``sudo install -m MODE S D`` or ``sudo rm -rf D``, the commands the workflow
  ran before this helper existed, so Actions behaviour is unchanged.
* ``local`` (off Actions, only with ``CORE_EVIDENCE_HOST_MODE=local``): no
  sudo.  The pinned fixture writer image runs with no network and mounts only
  the owned directory (and, to stage, the one source file read-only).  The
  destination's SHA-256 and mode are verified inside the container and the
  host checks that receipt; removal refuses if any residue remains.

Anything else refuses.  Standard library only.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys


FIXTURE_IMAGE = ("ghcr.io/home-assistant/home-assistant:2026.7.2"
                 "@sha256:1476924357b46e80735c13e94232ba5c853cac052e9df4bb28d50fa56348097b")
LANE = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
MODE = re.compile(r"[0-7]{3}")
DOCKER_TIMEOUT = 600
RECEIPT_BOUND = 4096

STAGE_CODE = r"""import hashlib,json,os,pathlib,stat,sys
expected,relative,mode=sys.argv[1],sys.argv[2],int(sys.argv[3],8)
owned=pathlib.Path('/owned'); parts=pathlib.PurePosixPath(relative).parts
current=owned
for part in parts[:-1]:
    current=current/part
    if current.is_symlink() or not current.is_dir(): raise SystemExit('unsafe destination parent')
destination=current/parts[-1]
data=pathlib.Path('/source').read_bytes()
if hashlib.sha256(data).hexdigest()!=expected: raise SystemExit('source changed')
fd=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,mode)
with os.fdopen(fd,'wb') as out:
    out.write(data); out.flush(); os.fchmod(out.fileno(),mode)
info=os.lstat(destination)
digest=hashlib.sha256(destination.read_bytes()).hexdigest()
print(json.dumps({'destination':relative,'mode':format(stat.S_IMODE(info.st_mode),'03o'),
                  'regular':stat.S_ISREG(info.st_mode),'sha256':digest}))
"""

REMOVE_CODE = r"""import pathlib,shutil
for child in pathlib.Path('/owned').iterdir():
    if child.is_symlink() or not child.is_dir(): child.unlink()
    else: shutil.rmtree(child)
"""


class Refusal(Exception):
    pass


def log(message, stderr):
    print(f"core_evidence_host: {message}", file=stderr, flush=True)


def select_mode(environ):
    requested = environ.get("CORE_EVIDENCE_HOST_MODE", "")
    if environ.get("GITHUB_ACTIONS") == "true":
        if requested not in ("", "actions"):
            raise Refusal("CORE_EVIDENCE_HOST_MODE conflicts with GitHub Actions")
        return "actions"
    if requested == "local":
        return "local"
    raise Refusal("off GitHub Actions the mode must be explicit: CORE_EVIDENCE_HOST_MODE=local")


def actions_argv(args):
    if args.command == "stage":
        return ["sudo", "install", "-m", args.mode, args.source, args.destination]
    return ["sudo", "rm", "-rf", args.directory]


def owned_directory(environ):
    """The lane's one owned disposable directory, exactly as the workflow names it."""
    temp, lane = environ.get("RUNNER_TEMP", ""), environ.get("HA_CONTRACT_LANE", "")
    if not LANE.fullmatch(lane) or not temp or not os.path.isabs(temp):
        raise Refusal("RUNNER_TEMP and HA_CONTRACT_LANE must identify the owned directory")
    if environ.get("HA_FIXTURE_WRITER_IMAGE") != FIXTURE_IMAGE:
        raise Refusal("HA_FIXTURE_WRITER_IMAGE is not the pinned fixture image")
    root = Path(temp)
    if root.is_symlink() or root.resolve() != root:
        raise Refusal("RUNNER_TEMP is not a canonical directory")
    return root, root / ("beta25-real-ha-" + lane)


def require_canonical_directory(path):
    if path.is_symlink() or not path.is_dir() or path.resolve() != path:
        raise Refusal("owned directory is missing, a symlink or not canonical")


def docker_argv(owned, code, *extra, source=None):
    argv = ["docker", "run", "--rm", "--pull", "missing", "--network", "none",
            "--user", "0:0", "--security-opt", "no-new-privileges", "--read-only",
            "--entrypoint", "python",
            "--mount", f"type=bind,source={owned},target=/owned"]
    if source is not None:
        argv += ["--mount", f"type=bind,source={source},target=/source,readonly"]
    return argv + [FIXTURE_IMAGE, "-c", code, *extra]


def local_stage(args, environ, run):
    root, owned = owned_directory(environ)
    if not MODE.fullmatch(args.mode):
        raise Refusal("mode must be three octal digits")
    require_canonical_directory(owned)
    source = Path(args.source)
    if (not source.is_absolute() or source.is_symlink() or not source.is_file()
            or source.resolve() != source or root not in source.parents):
        raise Refusal("source must be a canonical regular file inside RUNNER_TEMP")
    destination = Path(args.destination)
    if not destination.is_absolute() or ".." in destination.parts or owned not in destination.parents:
        raise Refusal("destination must be inside the owned directory")
    relative = PurePosixPath(destination.relative_to(owned).as_posix())
    expected = hashlib.sha256(source.read_bytes()).hexdigest()
    result = run(docker_argv(owned, STAGE_CODE, expected, str(relative), args.mode, source=source),
                 check=False, capture_output=True, timeout=DOCKER_TIMEOUT)
    if result.returncode != 0 or len(result.stdout) > RECEIPT_BOUND:
        raise Refusal("pinned fixture image staging failed")
    try:
        receipt = json.loads(result.stdout)
    except ValueError:
        raise Refusal("staging receipt is malformed") from None
    want = {"destination": str(relative), "mode": args.mode, "regular": True, "sha256": expected}
    if receipt != want:
        raise Refusal("staged destination failed SHA-256, mode or path verification")
    return receipt


def local_remove(args, environ, run):
    if args.directory == "":
        # Mirrors ``rm -rf ""``: an unset directory has nothing to remove.
        return {"removed": False, "reason": "no owned directory was set"}
    _, owned = owned_directory(environ)
    if Path(args.directory) != owned:
        raise Refusal("directory is not the lane's owned disposable directory")
    if not os.path.lexists(owned):
        return {"removed": False, "reason": "owned directory already absent"}
    require_canonical_directory(owned)
    result = run(docker_argv(owned, REMOVE_CODE), check=False, capture_output=True,
                 timeout=DOCKER_TIMEOUT)
    if result.returncode != 0:
        raise Refusal("pinned fixture image cleanup failed")
    try:
        owned.rmdir()
    except OSError:
        pass
    if os.path.lexists(owned):
        raise Refusal("owned disposable residue remains")
    return {"removed": True, "directory": str(owned)}


def main(argv=None, environ=os.environ, run=subprocess.run, execvp=os.execvp,
         stdout=None, stderr=None):
    stdout, stderr = stdout or sys.stdout, stderr or sys.stderr
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    stage = commands.add_parser("stage")
    stage.add_argument("--mode", required=True)
    stage.add_argument("--source", required=True)
    stage.add_argument("--destination", required=True)
    remove = commands.add_parser("remove-owned-dir")
    remove.add_argument("--directory", required=True)
    args = parser.parse_args(argv)
    try:
        mode = select_mode(environ)
        log(f"mode {mode} ({args.command})", stderr)
        if mode == "actions":
            command = actions_argv(args)
            execvp(command[0], command)
            raise Refusal("exec returned")
        receipt = (local_stage if args.command == "stage" else local_remove)(args, environ, run)
    except Refusal as exc:
        log(f"refused: {exc}", stderr)
        return 1
    print(json.dumps(receipt, sort_keys=True), file=stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
