"""Regenerate the HAMCP-130 sample report from synthetic fixtures (offline).

The fixtures are the ones ``tests/test_hamcp130_backup_inventory.py`` models
on the pinned Core 2026.10.1 backup shapes: a Supervisor installation with a
local + Home Assistant Cloud copy of the newest automatic backup, an older
manual partial backup on network storage, an encryption key configured, and
the most recent automatic attempt not completed. Durations are zeroed so the
output is deterministic.

Usage:
    PYTHONPATH=tests:hass_mcp_engineering_beta python docs/evidence/hamcp130/generate_sample.py \
        > docs/evidence/hamcp130/sample-output.json
"""

import asyncio
import json

from test_hamcp130_backup_inventory import KEY, at, backup, config, end_request, info, setup


def fixtures():
    event = {"manager_state": "create_backup", "reason": "upload_failed", "stage": None, "state": "failed"}
    return {
        "backup/config/info": config(password=KEY, agent_ids=("hassio.local", "cloud.cloud"),
                                     attempted=at(0.2), completed=at(1.2),
                                     agents={"cloud.cloud": {"protected": True, "retention": {"copies": 10, "days": None}}}),
        "backup/info": info([
            backup("5f1a0c2e", 1.2, name="Automatic backup 2026.10.1",
                   agents={"hassio.local": {"protected": True, "size": 734_003_200},
                           "cloud.cloud": {"protected": True, "size": 734_003_200}},
                   addons=(("Mosquitto broker", "core_mosquitto"), ("Synthetic add-on", "local_synthetic"))),
            backup("9b3d77a1", 4.5, name="Before kitchen changes", folders=("share",), database=False,
                   automatic=False, agents={"hassio.synthetic_nas": {"protected": False, "size": 52_428_800}}),
        ], attempted=at(0.2), completed=at(1.2), event=event),
    }


async def main():
    service, _, _, _, token = setup(fixtures())
    try:
        report = await service.inventory(25)
    finally:
        end_request(token)
    for source in report["sources"]:
        source["duration_ms"] = 0
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    asyncio.run(main())
