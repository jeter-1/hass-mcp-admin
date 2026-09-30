"""CI-only synthetic recorder fixture inside the immutable Core container.

No service/device calls, options flows, external endpoints or production data.
The only test injection is EventBus.async_fire(time_fired=...), an exact Core
API also used by its own logbook tests. The HTTP view and recorder stay real.
"""
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import signal


async def main():
    import homeassistant
    from homeassistant import loader
    from homeassistant.bootstrap import async_from_config_dict
    from homeassistant.core import HomeAssistant
    from homeassistant.const import __version__
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.tasks import CommitTask

    os.umask(0o077)
    logging.disable(logging.CRITICAL)
    pins = json.loads(Path('/fixture/pins.json').read_text())
    assert __version__ == pins['core_version']
    source_root = Path(homeassistant.__file__).parent.parent
    for name, digest in pins['core_files'].items():
        assert hashlib.sha256((source_root / name).read_bytes()).hexdigest() == digest
    hass = HomeAssistant('/config')
    loader.async_setup(hass)
    hass.config.skip_pip = True
    config = {
        'homeassistant': {'name': 'Synthetic logbook acceptance', 'latitude': 0,
                          'longitude': 0, 'elevation': 0, 'time_zone': 'UTC',
                          'unit_system': 'metric'},
        'http': {'server_host': ['0.0.0.0'], 'server_port': 8123},
        'recorder': {'auto_purge': False, 'auto_repack': False, 'commit_interval': 1},
        'logbook': {}, 'api': {},
    }
    stop = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stop.set)
    try:
        async with asyncio.timeout(180):
            assert await async_from_config_dict(config, hass) is hass
            await hass.async_start()
            await hass.async_block_till_done()
            recorder = get_instance(hass)
            await recorder.async_block_till_done()
            assert {'http', 'recorder', 'logbook', 'api'} <= hass.config.components
            now = datetime.now(timezone.utc).replace(microsecond=0)
            events = []
            for age in (192, 120, 48, 18, 6, -1):
                for entity in ('switch.synthetic_alpha', 'switch.synthetic_beta'):
                    message = f'synthetic_{entity.rsplit("_", 1)[1]}_{age}'
                    stamp = now - timedelta(hours=age)
                    hass.bus.async_fire('logbook_entry', {
                        'name': 'Synthetic interval fixture', 'message': message,
                        'domain': 'switch', 'entity_id': entity,
                    }, time_fired=stamp.timestamp())
                    events.append({'entity_id': entity, 'message': message, 'age_hours': age,
                                   'time': stamp.isoformat()})
            await hass.async_block_till_done()
            recorder.queue_task(CommitTask())
            await recorder.async_block_till_done()
            user = await hass.auth.async_create_user('Synthetic interval reader')
            refresh = await hass.auth.async_create_refresh_token(user, client_id='http://127.0.0.1/')
            token = hass.auth.async_create_access_token(refresh)
            Path('/config/ephemeral-token').write_text(token)
            Path('/config/ready.tmp').write_text(json.dumps({
                'core_version': __version__, 'source_commit': pins['core_source'],
                'verified_source_files': pins['core_files'], 'now': now.isoformat(),
                'events': events, 'seed_method': 'EventBus.async_fire -> recorder -> commit',
                'services_called': 0,
            }))
            Path('/config/ready.tmp').replace('/config/ready.json')
        await asyncio.wait_for(stop.wait(), timeout=600)
    finally:
        await asyncio.wait_for(hass.async_stop(), timeout=30)


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except BaseException as error:
        # Never export token, database or arbitrary exception text.
        print(json.dumps({'status': 'FAIL', 'category': type(error).__name__}), flush=True)
        raise SystemExit(1) from None
