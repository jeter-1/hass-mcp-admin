"""HAMCP-234 step 2: a live-shaped, benign journal corpus and its before/after report.

The matter.js lines follow the format of the beta.42 live calibration
(``addon:core_matter_server``, 2026-10-27): a colon-rich timestamp, a padded
level and facility, then session, peer, node and exchange addresses that
carry ``@`` and ``:`` in separate words. They are constructed from that
sample and matter.js diagnostic conventions; they are not captured live
data. The Supervisor and Python-logging lines follow the formats
``container_levels`` parses. No line holds a credential or a secret-shaped
value: every credential variant a test needs is assembled at run time.

``report()`` decides every line with the beta.42 journal screen (labelled,
``user_pass_host`` in both forms) and with the step 2 journal screen
(``host_journal.screen_text_rule``), and counts shown and withheld lines and
the deciding rules per corpus. ``docs/evidence/hamcp234-step2`` holds the
committed report; a test keeps it equal to this computation.
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.configuration_definitions import credentials  # noqa: E402
from ha_mcp_engineering.incident_logs import host_journal as hj  # noqa: E402

REPORT_PATH = ROOT / "docs" / "evidence" / "hamcp234-step2" / "corpus-report.json"

_T = "2026-10-27 17:11:46.221"

# matter.js lines (the format core_matter_server logged live): each is
# shown by the step 2 journal screen; all but the first and the websocket
# line met ``user_pass_host`` only in the squashed form on beta.42, as the
# live calibration found.
MATTER_JS = (
    f"{_T} INFO   Session              •unsecured#4c1e9a0b2d7f3e61 Session ended",
    f"{_T} INFO   PeerSet              Connected to peer@1:5a3c on session secure#2b71",
    f"{_T} INFO   NodeSession          secure#2b71@peer@1:5a3c Session established, resumption true",
    f"{_T} DEBUG  ExchangeManager      Received message «a9e3» on secure#2b71@peer@1:5a3c type 0x05",
    f"{_T} INFO   InteractionClient    peer@1:12 Subscription 0x4f21 established, interval 1-60 s",
    f"{_T} INFO   MatterNode           Node 12 (peer@1:c) is now available",
    f"{_T} WARN   MatterNode           Node 7 (peer@1:7) did not respond: timeout after 30000 ms",
    f"{_T} INFO   CaseClient           Pairing peer@1:9 with session parameters idle 500 ms, active 300 ms",
    f"{_T} DEBUG  MessageChannel       Sending message @1:5a3c exchange 18443 counter 92315",
    f"{_T} INFO   SubscriptionClient   Subscription 0x4f21 for peer@1:12 refreshed at 17:12:46",
    f"{_T} INFO   ControllerCommissioner Commissioning of node 14 on fabric 1 complete: peer@1:e",
    f"{_T} DEBUG  Exchange             exchange#18443@secure#2b71 closed (reason: done)",
    f"{_T} INFO   MdnsScanner          Discovered operational device 4A1B2C3D@fabric:1 at fe80::21e:5eff:fe12:3456%eth0 port 5540",
    f"{_T} ERROR  InteractionClient    peer@1:7 Read failed: StatusResponseError: UNSUPPORTED_ATTRIBUTE (134)",
    f"{_T} INFO   MatterServer         Websocket client 3 connected from 172.30.32.1:44120 at 17:11:46",
    f"{_T} DEBUG  Session              secure#2b71: resumption record saved for peer@1:5a3c",
    f"{_T} INFO   Session              •unsecured#9f02 Session created for peer@0:0 (PASE)",
    # ``@`` before ``://`` is no URI user-info: shown.
    f"{_T} INFO   PeerSet              peer@1:5a3c reachable at udp://[fd8f:2c1a::7]:5540",
)

# A line that meets another rule, unchanged by step 2: a URI scheme followed
# later by ``@`` is ``credential_url`` in the normal form, withheld.
MATTER_JS_OTHER_RULE = (
    f"{_T} DEBUG  MessageChannel       udp://[fe80::1%eth0]:5540 -> peer@1:9 message 0x3a",
)

# Supervisor (``INFO (MainThread) [logger]``), with ``:`` and ``@`` as they occur.
SUPERVISOR = (
    f"{_T[:19]}.221 INFO (MainThread) [supervisor.resolution.evaluate] Starting system evaluation with state running",
    f"{_T[:19]}.221 INFO (MainThread) [supervisor.api.middleware.security] /host/info access from a0d7b954_ssh",
    f"{_T[:19]}.221 INFO (MainThread) [supervisor.homeassistant.api] Updated Home Assistant API token",
    f"{_T[:19]}.221 INFO (MainThread) [supervisor.docker.addon] Starting Docker add-on core_matter_server at 172.30.33.4",
    f"{_T[:19]}.221 WARNING (MainThread) [supervisor.addons.options] Option 'log_level' does not exist in the schema for Matter Server (core_matter_server)",
    f"{_T[:19]}.221 INFO (SyncWorker_3) [supervisor.docker.manager] Cleaning addon_core_matter_server application",
    f"{_T[:19]}.221 INFO (MainThread) [supervisor.updater] Fetching update data from https://version.home-assistant.io/stable.json",
    # A contiguous ``name:tag@digest`` image reference reads as ``user_pass_host``
    # in the normal form and stays withheld (owner decision: one token).
    f"{_T[:19]}.221 INFO (MainThread) [supervisor.docker.interface] Pulling image ghcr.io/home-assistant/amd64-addon-matter-server:8.1.0@sha256:0f3e",
)

# Python logging (Home Assistant and add-ons).
PYTHON_LOGGING = (
    f"{_T[:19]}.221 INFO (MainThread) [homeassistant.components.matter] Matter Server is running at ws://core-matter-server:5580/ws",
    f"{_T[:19]}.221 WARNING (MainThread) [homeassistant.components.mqtt.client] Disconnected from MQTT server core-mosquitto:1883 (7)",
    f"{_T[:19]},221 INFO [zigpy.application] Device 0x1a2b (00:12:4b:00:25:1f:9e:01) joined the network",
    f"{_T[:19]},221 - zigbee2mqtt - INFO - MQTT publish: topic 'zigbee2mqtt/bridge/state', payload 'online'",
    f"{_T[:19]} ERROR (MainThread) [homeassistant.components.esphome] Error connecting to living_room@192.168.1.40: timed out",
    f"{_T[:19]} DEBUG (MainThread) [homeassistant.core] Bus:Handling <Event state_changed[L]: entity_id=sensor.door, old_state=on @ 17:11:46>",
)

CORPORA = {
    "matter_js": MATTER_JS + MATTER_JS_OTHER_RULE,
    "supervisor": SUPERVISOR,
    "python_logging": PYTHON_LOGGING,
}


def beta42_rule(line: str) -> str | None:
    """The beta.42 journal decision: the labelled screen, ``user_pass_host`` in both forms."""

    return credentials.screen_rule(line, (), labelled=True)[1]


def step2_rule(line: str) -> str | None:
    """The step 2 journal decision, through the reader's own screen."""

    return hj.screen_text_rule(line.encode(), ())[2]


def _counts(lines, decide) -> dict:
    rules: dict[str, int] = {}
    for line in lines:
        rule = decide(line)
        if rule is not None:
            rules[rule] = rules.get(rule, 0) + 1
    withheld = sum(rules.values())
    return {"lines": len(lines), "shown": len(lines) - withheld, "withheld": withheld,
            "withheld_by_rule": dict(sorted(rules.items()))}


def report() -> dict:
    return {
        "schema": "hamcp234-step2-corpus-report/1",
        "note": ("Benign, live-shaped lines (no credential). 'beta42' is the labelled journal screen with "
                 "user_pass_host in both forms; 'step2' is the journal screen with user_pass_host as one "
                 "contiguous token in the normal form only. Generated by "
                 "docs/evidence/hamcp234-step2/corpus_report.py."),
        "corpora": {name: {"beta42": _counts(lines, beta42_rule), "step2": _counts(lines, step2_rule)}
                    for name, lines in CORPORA.items()},
    }
