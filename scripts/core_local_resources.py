"""Read-only admission for local Core lanes; Josh's 2026-11-05 host rule.

No environment overrides. Unknown resource ownership/observation refuses.
Only aggregate metrics and job identities enter the receipt, never process argv
or container environment. Docker containers sharing an ownership label are one
lane. Evidence workers in one systemd service are one job.
"""
import json
import math
import os
from pathlib import Path
import subprocess
import time
from datetime import datetime, timezone

MIN_AVAILABLE_BYTES = 3_500_000_000
MAX_LOAD1 = 8
INTERVAL_SECONDS = 600
WAIT_SECONDS = 3600
LANE_LABELS = ('org.hamcp.helper-evidence-run', 'io.hass-mcp.assessment',
               'org.hass-mcp.dashboard-acceptance', 'org.hamcp.lane')


class Refusal(Exception):
    pass


def utc():
    return datetime.now(timezone.utc).isoformat()


def read_command(*argv):
    try:
        return subprocess.check_output(argv, text=True, stderr=subprocess.DEVNULL, timeout=30)
    except (OSError, subprocess.SubprocessError):
        raise Refusal('host_observation_failed') from None


def service(cgroup):
    return next((p for p in reversed(cgroup.strip().split('/')) if p.endswith('.service')), None)


def evidence_jobs():
    own = service(Path('/proc/self/cgroup').read_text())
    jobs = set()
    # Identify actual Evidence execution, not a unit merely named "evidence"
    # (which could supervise a container lane). Workers share the service ID.
    for path in Path('/proc').iterdir():
        if not path.name.isdigit():
            continue
        try:
            if path.joinpath('comm').read_text().strip().lower() not in ('pwsh', 'powershell'):
                continue
            argv = path.joinpath('cmdline').read_bytes().decode().split('\0')
            if not any(arg.endswith('check.ps1') for arg in argv):
                continue
            tier = next((argv[i + 1] for i, arg in enumerate(argv[:-1]) if arg.lower() == '-tier'), '')
            if tier.lower() != 'evidence':
                continue
            jobs.add(service(path.joinpath('cgroup').read_text()) or 'pid:' + path.name)
        except FileNotFoundError:
            continue  # exited during the observation
    return sorted(jobs - {own})


def lane_jobs(containers):
    jobs, unknown = set(), []
    for container in containers:
        labels = container['labels'] or {}
        owners = [(key, labels[key]) for key in LANE_LABELS if labels.get(key)]
        if len(owners) != 1:
            unknown.append(container['id'])
        else:
            key, value = owners[0]
            jobs.add(key + '=' + value)
    return sorted(jobs), sorted(unknown)


def sample():
    ids = read_command('docker', 'ps', '--quiet', '--no-trunc').split()
    containers = []
    if ids:
        # Explicit projection avoids reading or emitting container credentials.
        fmt = '{"id":{{json .Id}},"labels":{{json .Config.Labels}}}'
        containers = [json.loads(line) for line in read_command(
            'docker', 'inspect', '--format', fmt, *ids).splitlines()]
        if {row['id'] for row in containers} != set(ids):
            raise Refusal('container_observation_incomplete')
    lanes, unknown = lane_jobs(containers)
    evidence = evidence_jobs()
    mem = next(line.split()[1:] for line in Path('/proc/meminfo').read_text().splitlines()
               if line.startswith('MemAvailable:'))
    if len(mem) != 2 or mem[1] != 'kB':
        raise Refusal('memory_observation_invalid')
    return {'utc': utc(), 'other_heavy_jobs': len(evidence) + len(lanes),
            'evidence_jobs': evidence, 'lanes': lanes, 'container_count': len(containers),
            'unclassified_containers': unknown, 'mem_available_bytes': int(mem[0]) * 1024,
            'load1': os.getloadavg()[0]}


def refusals(observation):
    reasons = []
    if observation['other_heavy_jobs'] > 1:
        reasons.append('other_heavy_jobs_exceed_one')
    if observation['mem_available_bytes'] < MIN_AVAILABLE_BYTES:
        reasons.append('available_memory_below_3_5_GB')
    if not math.isfinite(observation['load1']) or observation['load1'] >= MAX_LOAD1:
        reasons.append('load1_not_below_8')
    if observation['unclassified_containers']:
        reasons.append('container_lane_ownership_unknown')
    return reasons


def wait_for_admission(receipt_path, *, observe=sample, sleep=time.sleep, monotonic=time.monotonic):
    """Persist every check, including refusal; exactly 10-minute check spacing.

    Waiting precedes container creation and the 30-minute acceptance body.
    An observation error refuses immediately, without inventing idle metrics.
    """
    receipt = {'model': 'core-local-resource-admission-v1', 'started_utc': utc(),
               'status': 'WAITING', 'checks': [], 'interval_seconds': INTERVAL_SECONDS,
               'wait_seconds': WAIT_SECONDS, 'min_available_bytes': MIN_AVAILABLE_BYTES,
               'max_other_heavy_jobs': 1, 'load1_exclusive_max': MAX_LOAD1}
    start = monotonic()
    def save():
        receipt_path.write_text(json.dumps(receipt, indent=2) + '\n')
    save()
    while True:
        try:
            observation = observe()
            reasons = refusals(observation)
        except (Refusal, OSError, ValueError, KeyError, StopIteration) as exc:
            receipt.update(status='REFUSED', reason='host_observation_failed',
                           failure_type=type(exc).__name__, ended_utc=utc())
            save()
            raise Refusal('host_observation_failed') from None
        receipt['checks'].append({**observation, 'reasons': reasons})
        elapsed = monotonic() - start
        if not reasons:
            receipt.update(status='ADMITTED', ended_utc=utc())
            save()
            return observation
        if elapsed >= WAIT_SECONDS:
            receipt.update(status='REFUSED', reason='host_admission_timeout', ended_utc=utc())
            save()
            raise Refusal('host_admission_timeout')
        save()
        sleep(min(INTERVAL_SECONDS, WAIT_SECONDS - elapsed))
