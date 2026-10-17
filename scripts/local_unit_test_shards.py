"""Run CI's unit-test shards as local parallel workers and prove completeness.

This is the opt-in ``check.ps1 -Parallel <N>`` engine. It owns no shard
assignment or completeness logic of its own: every worker calls
``unit_test_shards.run_shard`` (the function behind CI's
``unit_test_shards.py run``) and the final proof calls
``unit_test_shards.verify`` (behind CI's ``aggregate``) over the manifests the
workers wrote. The plan, the real-time isolation and the refusal categories are
therefore CI's, byte for byte.

Scheduling: CI's general shards (``0..general_shards-1``) run with at most
``--workers`` processes at a time; the final real-time shard, which holds every
``@real_time_measurement`` test, then runs alone with no other shard competing.

A per-invocation nonce fills the manifests' run identity, which CI takes from
``GITHUB_RUN_ID``/``GITHUB_RUN_ATTEMPT``. The nonce is passed to the shard and
verifier functions directly; it is never placed in the environment the tests
see, so tests run with the same environment as ``unittest discover``.

The run passes only if every shard process exited 0, wrote its manifest and
ended with unittest's own ``OK`` summary, and ``verify`` accepted the manifests
(each discovered test executed exactly once across the shards and succeeded).
Anything else, including a crashed, killed or timed-out worker, fails the run
and prints that shard's output. Partial results are never reported as passing.
When only the final proof refuses, no shard is reported as passed: the shards
whose manifests name missing or repeated tests are marked failed and their
output printed (every shard's output when none can be named), and the refusal
is reported on its own line with those test IDs.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
import sys
import time

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import unit_test_shards  # noqa: E402  (the shared CI module; never copied)

MAX_WORKERS = 4
DEFAULT_SHARD_TIMEOUT = 3600
SUMMARY_SCHEMA_VERSION = 1
RUN_ATTEMPT = "1"
POLL_SECONDS = 0.2
MAX_LISTED_IDS = 20
RAN_LINE = re.compile(r"^Ran (\d+) tests? in [0-9.]+s$")
STATUS_LINE = re.compile(r"^(OK|FAILED|NO TESTS RAN)(?: \((.*)\))?$")


def new_run_id():
    """A fresh value accepted by CI's run-identity check (``[1-9][0-9]{0,19}``)."""

    return str(10**18 + secrets.randbelow(9 * 10**18))


def run_identity_env(run_id):
    return {"GITHUB_RUN_ID": run_id, "GITHUB_RUN_ATTEMPT": RUN_ATTEMPT}


def runner_totals(text):
    """Return (ran, skipped, ok) from the last unittest runner summary in a log.

    The shard runner writes its summary after every test, so the last
    ``Ran N tests`` line and the status line after it are the runner's own.
    """

    lines = text.splitlines()
    for index in range(len(lines) - 1, -1, -1):
        ran = RAN_LINE.match(lines[index].strip())
        if not ran:
            continue
        for follower in lines[index + 1:]:
            status = STATUS_LINE.match(follower.strip())
            if status:
                details = dict(
                    part.strip().split("=", 1) for part in (status.group(2) or "").split(",") if "=" in part)
                skipped = details.get("skipped", "0")
                if not skipped.isdigit():
                    return None
                if status.group(1) == "NO TESTS RAN":
                    return (0, 0, True) if ran.group(1) == "0" else None
                return int(ran.group(1)), int(skipped), status.group(1) == "OK"
        return None
    return None


def worker_command(python, root, shard, manifest, run_id):
    return [python, str(Path(__file__).resolve()), "worker", "--repo-root", str(root),
            "--shard", str(shard), "--output", str(manifest), "--run-id", run_id]


class Shard:
    def __init__(self, index, phase, root, layout, python, run_id):
        self.index = index
        self.phase = phase
        self.manifest = layout["manifests"] / f"shard-{index}.json"
        self.log = layout["logs"] / f"shard-{index}.log"
        self.command = worker_command(python, root, index, self.manifest, run_id)
        self.root = root
        self.process = None
        self.handle = None
        self.started = None
        self.exit_code = None
        self.duration = None
        self.finished = None
        self.timed_out = False

    def start(self):
        self.handle = open(self.log, "wb")
        self.started = time.monotonic()
        # A new session makes the worker a process-group leader, so a timeout
        # stops exactly this worker and the processes its tests started.
        self.process = subprocess.Popen(self.command, cwd=str(self.root), stdin=subprocess.DEVNULL,
                                        stdout=self.handle, stderr=subprocess.STDOUT,
                                        start_new_session=os.name != "nt")

    def poll(self, timeout):
        if self.process.poll() is None:
            if time.monotonic() - self.started <= timeout:
                return False
            self.timed_out = True
            self.stop()
        self.finish()
        return True

    def stop(self):
        if self.process is None or self.process.poll() is not None:
            return
        if os.name != "nt":
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            self.process.kill()
        self.process.wait()

    def finish(self):
        self.exit_code = self.process.wait()
        self.finished = time.monotonic()
        self.duration = round(self.finished - self.started, 3)
        self.handle.close()

    def text(self):
        try:
            return self.log.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def outcome(self, epoch):
        """Classify this shard; any doubt is a failure."""

        totals = runner_totals(self.text())
        problems = []
        if self.timed_out:
            problems.append("timed_out")
        if self.exit_code is None:
            problems.append("did_not_run")
        elif self.exit_code < 0:
            problems.append(f"killed_by_signal_{-self.exit_code}")
        elif self.exit_code != 0:
            problems.append(f"exit_code_{self.exit_code}")
        if not self.manifest.is_file():
            problems.append("manifest_missing")
        if totals is None:
            problems.append("runner_summary_missing")
        elif not totals[2]:
            problems.append("runner_reported_failure")
        return {
            "shard": self.index,
            "phase": self.phase,
            "status": "failed" if problems else "passed",
            "problems": problems,
            "exit_code": self.exit_code,
            "started_offset_seconds": None if self.started is None else round(self.started - epoch, 3),
            "finished_offset_seconds": None if self.finished is None else round(self.finished - epoch, 3),
            "duration_seconds": self.duration,
            "tests_run": totals[0] if totals else None,
            "skipped": totals[1] if totals else None,
            "log": str(self.log),
        }


def run_pool(shards, workers, timeout):
    """Run shards with at most ``workers`` alive; stop survivors on interruption."""

    pending = list(shards)
    running = []
    try:
        while pending or running:
            while pending and len(running) < workers:
                shard = pending.pop(0)
                shard.start()
                running.append(shard)
            for shard in list(running):
                if shard.poll(timeout):
                    running.remove(shard)
            if running:
                time.sleep(POLL_SECONDS)
    finally:
        for shard in running:
            shard.stop()
            if shard.handle and not shard.handle.closed:
                shard.handle.close()


def prepare_layout(output_dir):
    output_dir = Path(output_dir)
    if output_dir.exists() or output_dir.is_symlink():
        raise unit_test_shards.Refusal("output_directory_exists")
    layout = {"root": output_dir, "manifests": output_dir / "manifests", "logs": output_dir / "logs"}
    layout["manifests"].mkdir(parents=True)
    layout["logs"].mkdir()
    return layout


def finalize(root, manifests_dir, run_id, shard_results):
    """Apply CI's verifier; return (summary fields, problems)."""

    if any(row["tests_run"] is None for row in shard_results):
        return {}, ["runner_totals_unavailable"]
    try:
        proof = unit_test_shards.verify(root, unit_test_shards.load_manifests(manifests_dir),
                                        run_identity_env(run_id))
    except unit_test_shards.Refusal as exc:
        return {}, [f"completeness_refused:{exc}"]
    except Exception as exc:  # A verifier crash is a failure, never a pass.
        return {}, [f"completeness_error:{type(exc).__name__}"]
    return proof, []


def completeness_findings(manifests_dir):
    """Name the tests behind a refused proof, per shard; a diagnostic, never a verdict.

    ``unit_test_shards.verify`` alone decides completeness. This only rereads
    the manifests the workers wrote so a person can see which shard lost,
    repeated or added which test IDs. An unreadable manifest names nothing.
    """

    findings = []
    shards_by_test = {}
    for path in sorted(Path(manifests_dir).glob("*.json")):
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
            shard, assigned, executed = manifest["shard"], manifest["assigned"], manifest["executed"]
            ids = [row[0] for row in executed]
            counts = Counter(ids)
            missing, extra = sorted(set(assigned) - set(ids)), sorted(set(ids) - set(assigned))
        except (OSError, ValueError, TypeError, KeyError, IndexError):
            continue
        if type(shard) is not int:
            continue
        for test_id in counts:
            shards_by_test.setdefault(test_id, []).append(shard)
        for kind, tests in (("missing", missing), ("duplicated", sorted(t for t, n in counts.items() if n > 1)),
                            ("unassigned", extra)):
            if tests:
                findings.append({"kind": kind, "shards": [shard], "tests": tests})
    across = {}
    for test_id, owners in sorted(shards_by_test.items()):
        if len(owners) > 1:
            across.setdefault(tuple(sorted(owners)), []).append(test_id)
    findings.extend({"kind": "executed_in_multiple_shards", "shards": list(owners), "tests": tests}
                    for owners, tests in sorted(across.items()))
    return findings


def listed(tests):
    shown = ", ".join(tests[:MAX_LISTED_IDS])
    hidden = len(tests) - MAX_LISTED_IDS
    return shown + (f", ... and {hidden} more (see summary.json)" if hidden > 0 else "")


def run(root, workers, output_dir, *, python=sys.executable, timeout=DEFAULT_SHARD_TIMEOUT, stream=sys.stdout):
    if type(workers) is not int or not 1 <= workers <= MAX_WORKERS:
        raise unit_test_shards.Refusal("invalid_worker_count")
    root = Path(root).resolve()
    general = unit_test_shards.load_plan_file(root)[0]
    layout = prepare_layout(output_dir)
    run_id = new_run_id()
    started = time.monotonic()
    parallel = [Shard(i, "parallel", root, layout, python, run_id) for i in range(general)]
    serial = [Shard(general, "serial_real_time", root, layout, python, run_id)]
    print(f"Sharded unittest run {run_id}: shards 0..{general - 1} with at most {workers} worker(s), "
          f"then real-time shard {general} alone.", file=stream, flush=True)
    run_pool(parallel, workers, timeout)
    run_pool(serial, 1, timeout)
    results = [shard.outcome(started) for shard in parallel + serial]
    problems = [f"shard_{row['shard']}:{p}" for row in results for p in row["problems"]]
    proof = {}
    completeness = None
    if not problems:
        proof, final_problems = finalize(root, layout["manifests"], run_id, results)
        problems.extend(final_problems)
        if final_problems:
            # Every worker exited cleanly, but the proof refused: no shard has passed.
            findings = completeness_findings(layout["manifests"])
            implicated = {shard for finding in findings for shard in finding["shards"]}
            completeness = {"result": "failed", "problems": final_problems, "findings": findings,
                            "implicated_shards": sorted(implicated)}
            for row in results:
                if row["shard"] in implicated or not implicated:
                    row["status"] = "failed"
                    row["problems"] = sorted({f"completeness_{f['kind']}" for f in findings
                                              if row["shard"] in f["shards"]}) or ["completeness_refused"]
                    problems.extend(f"shard_{row['shard']}:{p}" for p in row["problems"])
                else:
                    row["status"] = "unproven"
    passed = not problems
    summary = {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "result": "passed" if passed else "failed",
        "mode": "sharded",
        "workers": workers,
        "max_workers": MAX_WORKERS,
        "general_shards": general,
        "shard_count": general + 1,
        "serial_shards": [general],
        "run_id": run_id,
        "problems": problems,
        "tests_run": sum(row["tests_run"] for row in results) if passed else None,
        "skipped": sum(row["skipped"] for row in results) if passed else None,
        "wall_seconds": round(time.monotonic() - started, 3),
        "shards": results,
        "completeness": completeness,
        **{key: proof.get(key) for key in (
            "source_sha", "discovered_count", "discovered_sha256", "plan_sha256")},
    }
    (layout["root"] / "summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n",
                                                 encoding="utf-8")
    for row, shard in zip(results, parallel + serial):
        status = row["status"]
        if status == "unproven":
            status = "unproven (worker exited cleanly; completeness refused)"
        print(f"Shard {row['shard']} ({row['phase']}): {status}, exit {row['exit_code']}, "
              f"ran {row['tests_run']}, skipped {row['skipped']}, {row['duration_seconds']}s, log {row['log']}",
              file=stream)
        if row["status"] == "failed":
            print(f"----- shard {row['shard']} output ({', '.join(row['problems'])}) -----", file=stream)
            print(shard.text().rstrip("\n"), file=stream)
            print(f"----- end shard {row['shard']} output -----", file=stream)
    if completeness:
        print("Completeness check FAILED (" + ", ".join(completeness["problems"]) + "): "
              + ("; ".join(f"shard(s) {', '.join(map(str, f['shards']))} {f['kind'].replace('_', ' ')}: "
                           f"{listed(f['tests'])}" for f in completeness["findings"])
                 or "no shard manifest names the offending tests; every shard's output is shown above"),
              file=stream)
    if passed:
        print(f"Completeness proven: {summary['discovered_count']} discovered tests executed exactly once "
              f"(discovery {summary['discovered_sha256']}, plan {summary['plan_sha256']}).", file=stream)
        print(f"Sharded totals: ran={summary['tests_run']} skipped={summary['skipped']} "
              f"in {summary['wall_seconds']}s.", file=stream)
    else:
        print("Sharded unittest run FAILED: " + "; ".join(problems), file=stream)
    print(f"Summary: {layout['root'] / 'summary.json'}", file=stream, flush=True)
    return summary


def worker(root, shard, output, run_id):
    manifest = unit_test_shards.run_shard(root, shard, run_identity_env(run_id), output)
    print(f"Shard {shard}: {len(manifest['executed'])} of {manifest['discovered_count']} tests executed.", flush=True)
    return 0 if manifest["was_successful"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="operation", required=True)
    run_parser = commands.add_parser("run", help="run every shard and prove completeness")
    run_parser.add_argument("--workers", type=int, required=True)
    run_parser.add_argument("--output-dir", type=Path, required=True)
    run_parser.add_argument("--shard-timeout", type=int, default=DEFAULT_SHARD_TIMEOUT)
    check = commands.add_parser("verify", help="re-apply CI's completeness proof to a retained run")
    check.add_argument("--manifests", type=Path, required=True)
    check.add_argument("--run-id", required=True)
    work = commands.add_parser("worker", help="internal: run one shard with a local run identity")
    work.add_argument("--shard", type=int, required=True)
    work.add_argument("--output", type=Path, required=True)
    work.add_argument("--run-id", required=True)
    for command in (run_parser, check, work):
        command.add_argument("--repo-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    try:
        if args.operation == "worker":
            sys.exit(worker(args.repo_root, args.shard, args.output, args.run_id))
        if args.operation == "verify":
            proof, problems = finalize(args.repo_root, args.manifests, args.run_id, [])
            print(json.dumps({**proof, "result": "failed" if problems else "passed", "problems": problems},
                             sort_keys=True))
            sys.exit(1 if problems else 0)
        if args.shard_timeout < 1:
            raise unit_test_shards.Refusal("invalid_shard_timeout")
        summary = run(args.repo_root, args.workers, args.output_dir, timeout=args.shard_timeout)
        sys.exit(0 if summary["result"] == "passed" else 1)
    except unit_test_shards.Refusal as exc:
        parser.exit(1, "Local sharded unittest run refused: " + str(exc) + "\n")


if __name__ == "__main__":
    main()
