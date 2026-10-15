"""HAMCP-182 harness helper: run sign_step.py on a pseudo-terminal or a pipe. Throwaway keys only.

`signer_command` runs the signer through a small wrapper that can first apply a
simulated terminal fault (SCENARIOS) and, for mutation testing, exact source
replacements to sign_step.py. The mutated source is compiled under the real
file name, never written to disk.

`on_pty` answers the passphrase prompt only after it appears, and records
whether the answer was echoed. `on_pipe` pre-loads the answer into a pipe and
records whether the signer consumed it. Neither keeps the answer: it is
redacted from the returned output.
"""

from __future__ import annotations

import os
from pathlib import Path
import pty
import select
import subprocess
import sys
import time

PROMPT = b"PEM passphrase (hidden):"
SCENARIOS = {
    "none": "",
    # Review R1 reproduction: every terminal echo-control call fails.
    "echo control fails": (
        "def fail(*a, **k):\n raise termios.error('simulated echo control failure')\n"
        "termios.tcsetattr = fail\n"),
    # Echo control appears to succeed but leaves ECHO on (getpass alone would echo silently).
    "echo flag not cleared": "termios.tcsetattr = lambda *a, **k: None\n",
    # Echo control works for the signer's pre-flight, then fails inside getpass.
    "echo control fails after pre-flight": (
        "real, calls = termios.tcsetattr, [0]\n"
        "def flaky(*a, **k):\n calls[0] += 1\n"
        " if calls[0] > 2: raise termios.error('simulated echo control failure')\n"
        " return real(*a, **k)\n"
        "termios.tcsetattr = flaky\n"),
    # getpass takes its echoed-input fallback path.
    "getpass fallback forced": "getpass.getpass = getpass.fallback_getpass\n",
}
WRAPPER = """import getpass, sys, termios
{scenario}path = sys.argv[1]
sys.argv = sys.argv[1:]
source = open(path, encoding="utf-8").read()
for old, new in {mutation!r}:
    assert source.count(old) == 1, "mutation does not apply"
    source = source.replace(old, new)
exec(compile(source, path, "exec"), {{"__name__": "__main__", "__file__": path}})
"""


def signer_command(signer: Path, argv: list, *, scenario: str = "none", mutation=()) -> list:
    wrapper = WRAPPER.format(scenario=SCENARIOS[scenario], mutation=list(mutation))
    return [sys.executable, "-c", wrapper, str(signer), *map(str, argv)]


def _result(code: int, raw: bytes, answer: bytes | None) -> dict:
    text = raw.replace(answer, b"[REDACTED]") if answer else raw
    return {"exit": code, "traceback": b"Traceback" in raw,
            "output": text.decode(errors="replace").replace("\r", "").strip().splitlines()}


def on_pty(command: list, *, answer: bytes | None, cwd: Path, timeout: float = 60) -> dict:
    """stdin/stdout/stderr on a new PTY (no controlling terminal, so getpass uses stdin)."""
    master, slave = pty.openpty()
    child = subprocess.Popen(command, stdin=slave, stdout=slave, stderr=slave, cwd=cwd,
                             start_new_session=True)
    os.close(slave)
    captured, answered, deadline = b"", False, time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            if select.select([master], [], [], 0.1)[0]:
                try:
                    chunk = os.read(master, 65536)
                except OSError:  # EIO once the child has exited and output is drained
                    chunk = b""
                if not chunk:
                    break
                captured += chunk
                if answer is not None and not answered and PROMPT in captured:
                    os.write(master, answer + b"\n")
                    answered = True
            elif child.poll() is not None:
                break
    finally:
        if child.poll() is None:
            child.kill()
        code = child.wait()
        os.close(master)
    return {**_result(code, captured, answer), "stdin": "pty", "prompt_shown": PROMPT in captured,
            "answered": answered, "echoed": answer is not None and answer in captured}


def on_pipe(command: list, *, answer: bytes | None, cwd: Path, timeout: float = 60) -> dict:
    """stdin is a pipe pre-loaded with the answer (or /dev/null when answer is None)."""
    if answer is None:
        done = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, cwd=cwd,
                              timeout=timeout, start_new_session=True)
        return {**_result(done.returncode, done.stdout + done.stderr, None), "stdin": "/dev/null"}
    read_end, write_end = os.pipe()
    os.write(write_end, answer + b"\n")
    os.close(write_end)
    try:
        done = subprocess.run(command, stdin=read_end, capture_output=True, cwd=cwd,
                              timeout=timeout, start_new_session=True)
        os.set_blocking(read_end, False)
        try:
            unread = os.read(read_end, 65536)
        except BlockingIOError:
            unread = b""
    finally:
        os.close(read_end)
    raw = done.stdout + done.stderr
    return {**_result(done.returncode, raw, answer), "stdin": "pipe",
            "prompt_shown": PROMPT in raw, "answer_left_unread": unread == answer + b"\n",
            "echoed": answer in raw}
