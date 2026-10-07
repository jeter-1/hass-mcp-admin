"""Generate synthetic persisted records using exact shipped beta.16 source.

Run with the controlled Python, SOURCE checkout/archive root and a new OUTPUT.
The caller must verify SOURCE comes from WRITER_COMMIT. No serialized record is
sanitized or altered after this writer computes hashes.
"""
import asyncio
import hashlib
import json
from pathlib import Path
import shutil
import sys

WRITER_COMMIT = 'fce97c66848796d68d989f12efcf53fa43ddf32d'
source, output = map(lambda p: Path(p).resolve(), sys.argv[1:])
sys.path.insert(0, str(source))
from tests.test_hamcp135_configuration_execution import ExecutionTests
from tests.test_hamcp135_configuration_eligibility import fixture

async def generate():
    output.mkdir(parents=True, exist_ok=False)
    case = ExecutionTests()
    await case.asyncSetUp()
    try:
        created = await case.create_retry_plan()
        await case.approve(created)
        result = await case.service.apply(created['plan_id'], created['plan_hash'])
        assert result['task_state'] == 'succeeded_verified'
        inverse = await case.service.rollback_change(created['plan_id'], created['plan_hash'])
        assert inverse['status'] == 'rollback_unavailable'
        shutil.copytree(case.root / 'plans', output / 'store')
        # Bodies are already the committed synthetic fixtures before all hashes.
        configs = [{'resource_type': m['resource_type'], 'target_id': m['target_id'],
                    'config': case.gateway.configs[(m['resource_type'],m['target_id'])]}
                   for m in fixture().values()]
        (output/'gateway.json').write_text(json.dumps(configs,indent=2)+'\n')
        metadata = {'writer_commit':WRITER_COMMIT,'source_plan_id':created['plan_id'],
                    'source_plan_hash':created['plan_hash'],'task_id':result['task_id'],
                    'cached_prohibited_inverse_id':inverse['rollback_plan_id'],
                    'generator_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    'input_fixture_sha256':hashlib.sha256((source/'tests/fixtures/hamcp135_configuration/caller_owned_retry.json').read_bytes()).hexdigest(),
                    'sanitization':'Committed synthetic forward bodies used before hashing. No record edits.'}
        (output/'PROVENANCE.json').write_text(json.dumps(metadata,indent=2)+'\n')
        files=sorted(p for p in output.rglob('*') if p.is_file())
        (output/'SHA256SUMS').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.relative_to(output).as_posix()+'\n' for p in files))
        print(json.dumps(metadata,indent=2))
    finally:
        await case.asyncTearDown()

asyncio.run(generate())
