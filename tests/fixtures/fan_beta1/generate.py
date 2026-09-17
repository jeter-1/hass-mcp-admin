"""Regenerate synthetic receipt bytes with the exact shipped beta.1 writer."""
import argparse
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile

SOURCE = '1e503411095fcab3b29c95e7f88bdfcb83ae1c72'
EXPECTED = '09b7ed86104f07c47dbbaeb2afbc16e4c1dd6b2d40dd592a2e421a1f00104ef0'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source = args.source.resolve()
    head = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    status = subprocess.check_output(['git', '-C', str(source), 'status', '--porcelain'], text=True)
    if head != SOURCE or status or args.output.exists():
        raise SystemExit('Exact clean shipped checkout and absent output are required.')
    sys.path.insert(0, str(source / 'hass_mcp_engineering_beta'))
    import ha_mcp_engineering.fan.service as writer
    from ha_mcp_engineering.fan.adapter import prepare_record
    from ha_mcp_engineering.fan.contracts import FanRequest
    assert Path(writer.__file__).resolve().is_relative_to(source)
    request = FanRequest(
        entity_id='fan.synthetic_historical', action='turn_on', percentage=66,
        operation_id='1789646400-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
    ).checked()
    baseline = {
        'entity_id': request.entity_id, 'state': 'off', 'percentage': 0,
        'supported_features': 49, 'last_updated': 'synthetic-historical-revision',
    }
    with tempfile.TemporaryDirectory() as directory:
        service = writer.FanService(
            directory, object(), object(),
            now=lambda: datetime.fromtimestamp(1789646400, timezone.utc),
        )
        service.save(prepare_record(request, baseline))
        raw = service._path(request.task_id).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == EXPECTED
    with args.output.open('xb') as output:
        output.write(raw)
    print('PASS: exact shipped writer reproduced the historical synthetic receipt.')


if __name__ == '__main__':
    main()
