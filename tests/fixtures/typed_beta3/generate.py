"""Generate synthetic declarations using the exact shipped beta.3 writer."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

SOURCE = '349e0e49607e44a31eececd9ec457a5e124dbf98'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source = args.source.resolve()
    head = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    dirty = subprocess.check_output(['git', '-C', str(source), 'status', '--porcelain'], text=True)
    if head != SOURCE or dirty or args.output.exists():
        raise SystemExit('Exact clean shipped source and absent output directory required.')
    sys.path.insert(0, str(source / 'hass_mcp_engineering_beta'))
    from ha_mcp_engineering.fan.service import FanService
    from ha_mcp_engineering.fan.contracts import FanRequest, FAN_CONTRACTS
    from ha_mcp_engineering.power.service import PowerService
    from ha_mcp_engineering.power.contracts import PowerRequest, POWER_CONTRACTS
    import ha_mcp_engineering.fan.service as writer
    assert Path(writer.__file__).resolve().is_relative_to(source)
    args.output.mkdir(parents=True)
    records = []
    stamp = 1789833600
    for family, service_type, request_type, contracts in (
        ('fan', FanService, FanRequest, FAN_CONTRACTS),
        ('power', PowerService, PowerRequest, POWER_CONTRACTS),
    ):
        for i, contract in enumerate(contracts):
            entity = 'fan.synthetic_historical' if family == 'fan' else 'switch.synthetic_historical'
            request = request_type(entity_id=entity, action='turn_on',
                                   operation_id=f'{stamp}-' + str(i+1)*32,
                                   **({'percentage':50} if family == 'fan' else {}))
            baseline = {'entity_id':entity, 'state':'off', 'last_updated':'synthetic-beta3-revision'}
            if family == 'fan':
                baseline.update(percentage=0, supported_features=49)
            with tempfile.TemporaryDirectory() as directory:
                service = service_type(directory, object(), object(),
                                       now=lambda: datetime.fromtimestamp(stamp, timezone.utc))
                prepared = service.prepare_record(request, baseline, contract)
                service.save(prepared)
                raw = service._path(request.task_id).read_bytes()
            name = f'{family}-{i}.json'
            (args.output/name).write_bytes(raw)
            records.append({'file':name, 'family':family, 'contract':contract,
                            'task_id':request.task_id, 'sha256':hashlib.sha256(raw).hexdigest()})
    provenance = {'source_commit':SOURCE, 'synthetic':True,
                  'writer':'exact shipped FanService/PowerService.prepare_record and save',
                  'generation':'Run this script with --source pointing to the exact clean shipped checkout.',
                  'generator_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  'records':records}
    (args.output/'provenance.json').write_text(json.dumps(provenance, indent=2)+'\n')
    print(f'Generated {len(records)} declarations using exact shipped source {SOURCE}.')


if __name__ == '__main__':
    main()
