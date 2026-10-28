"""Record file access made from a measured body's heartbeat (HAMCP-219).

Not collected by discovery. Usage: ``loop_io_probe.py NAME``. NAME selects a
measured body from ``loop_stall_probe.BODIES``; it runs unchanged under an
audit hook that records every file open, directory listing or directory scan
made while a frame named ``heartbeat`` is on the calling thread's stack. The
heartbeat runs on the event-loop thread, so any such access delays its next
turn; PR #321 review 1 (M1) measured up to 153 ms for the per-heartbeat
``/proc/self/task/*/schedstat`` reads this probe was added to exclude.

Before the body, a control ``heartbeat`` lists a directory and opens a file to
prove the hook sees both. The probe prints one verdict line and exits 2 when
the control was not seen, 1 when the body's heartbeat touched a file, else 0.
Body timing is reported but not judged here: the outer test reads the body's
JSON record to confirm the heartbeat ran.
"""
import asyncio
import importlib
import json
import os
import sys
import unittest

from support.loop_stall_probe import BODIES

EVENTS = frozenset({"open", "os.listdir", "os.scandir"})
seen = []


def hook(event, args):
    if event not in EVENTS:
        return
    frame = sys._getframe(1)
    while frame is not None:
        if frame.f_code.co_name == "heartbeat":
            seen.append({"event": event, "path": str(args[0]) if args else None})
            return
        frame = frame.f_back


async def heartbeat():
    os.listdir(os.path.dirname(os.path.abspath(__file__)))
    with open(__file__, "rb"):
        pass


def main(name):
    module_name, case_name, method, *_ = BODIES[name]
    module = importlib.import_module(module_name)
    case = getattr(module, case_name)(method)
    sys.addaudithook(hook)
    asyncio.run(heartbeat())
    control = list(seen)
    seen.clear()
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([case]))
    print("LOOP_IO_PROBE", name, "CONTROL", len(control), "HEARTBEAT_IO", len(seen), json.dumps(seen[:20]),
          "BODY_PASSED", result.wasSuccessful(), flush=True)
    if {entry["event"] for entry in control} != {"open", "os.listdir"}:
        raise SystemExit(2)
    raise SystemExit(1 if seen else 0)


if __name__ == "__main__":
    main(sys.argv[1])
