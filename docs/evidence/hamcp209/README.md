# HAMCP-209 offline measurements

Synthetic model only, not production. `measure.py` builds the dependency index
against `tests/hamcp209_fixtures.py` with 113 generated automations. Core's
stored-config REST route is modelled as one lock plus a whole-file re-parse
per read or write, at 2.65 ms per automation (about 0.30 s per read at
N=113, as observed live). Loaded configuration is served by a pipelined
WebSocket batch at 0.5 ms per command (5 ms in the slow run).

During each build:
- an automation POST is issued 50 ms in;
- a second index reader arrives at the same time (a write that needs the
  index while the startup prewarm is building).

The write is also timed on an idle model.

```sh
# Python 3.12 venv built from the hashed locks
python docs/evidence/hamcp209/measure.py --tree . --label head --output head.json
python docs/evidence/hamcp209/measure.py --tree <base tree> --label base-a1236c4 --output base-a1236c4.json
python docs/evidence/hamcp209/measure.py --tree . --label head-slow-websocket \
    --websocket-seconds-per-command 0.005 --output head-slow-websocket.json
```

The base tree was extracted from `a1236c4` (2.4.1-beta.19) with
`git archive a1236c4 hass_mcp_engineering_beta`. All three runs used the same
host, back to back. `measurement.json` summarizes them and gives the
projected live effect.
