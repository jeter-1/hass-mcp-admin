"""Generate the HAMCP-224 real-format host journal fixtures (D10).

Step 1 (pure Python, deterministic): write the synthetic journal export
streams ``persistent.export`` and ``runtime.export`` (systemd's Journal
Export Format, binary-safe fields for multi-line messages).

Step 2 (``--journals``, needs Docker and network): in a disposable container
from the pinned Engineering base image, install Debian trixie's
``systemd-journal-remote`` at the exact version of the image's
``libsystemd0`` and convert each stream into a journal file twice:
``--compress=no`` (the inferred HAOS shape: keyed hash, compact, no
compression, unsealed) and ``--compress=yes`` (zstd). Each file is stored
gzip-compressed (mtime 0) because journal files are mostly preallocated
zeros, and ``provenance.json`` records the image digest, package versions,
commands and the SHA-256 of every input and output.

The export streams are byte-reproducible; the journal files are not
(journald assigns random file, sequence-number and keyed-hash IDs), so a
regeneration changes the output digests but not the entries.

Every value is synthetic. Planted secrets carry ``PLANTED`` so that a test
can prove none is ever returned.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile


HERE = Path(__file__).resolve().parent
BASE_IMAGE = "python@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea"
JOURNAL_REMOTE_VERSION = "257.13-1~deb13u1"

MACHINE_ID = "5e7e7e7e000000000000000000000224"
BOOT_A = "b0070000000000000000000000000a01"
BOOT_B = "b0070000000000000000000000000b02"
# 2026-10-20T10:00:00Z in microseconds.
T0 = 1_792_490_400_000_000
REPRODUCIBILITY = ("Export streams are byte-reproducible. Journal files are not: journald assigns random "
                   "file, sequence-number and keyed-hash IDs, so regeneration changes output digests only.")
SECOND = 1_000_000

# Fields a reader must never read or return; planted on every entry.
NEVER_READ = {
    "_HOSTNAME": "homeassistant",
    "_PID": "4242",
    "_UID": "0",
    "_CMDLINE": "/usr/bin/planted --token PLANTED-CMDLINE-0000",
    "_SYSTEMD_UNIT": "planted-unit.service",
    "MESSAGE_ID": "fc2e22bc6ee647b6b90729ab34a250b1",
    "CODE_FILE": "planted.c",
}
DOCKER_NEVER_READ = {
    "CONTAINER_ID": "0123456789ab",
    "CONTAINER_ID_FULL": "0123456789ab" * 5 + "cdef",
    "IMAGE_NAME": "ghcr.io/planted/image:PLANTED-IMAGE-0000",
    # Docker's own RFC 3339 text time: deliberately wrong, never read.
    "SYSLOG_TIMESTAMP": "2001-01-01T00:00:00.000000000Z",
    "CONTAINER_LOG_EPOCH": "planted",
    "CONTAINER_LOG_ORDINAL": "1",
}


def docker(name: str, message: str | bytes, priority: int | None = 6, **extra: str) -> dict:
    fields = {
        "_TRANSPORT": "journal", "SYSLOG_IDENTIFIER": name, "CONTAINER_NAME": name,
        "CONTAINER_TAG": name, "MESSAGE": message, **DOCKER_NEVER_READ,
    }
    if priority is not None:
        fields["PRIORITY"] = str(priority)
    fields.update(extra)
    return fields


def native(identifier: str, message: str | bytes, priority: int | None = 6, transport: str = "journal",
           **extra: str) -> dict:
    fields = {"_TRANSPORT": transport, "SYSLOG_IDENTIFIER": identifier, "MESSAGE": message}
    if priority is not None:
        fields["PRIORITY"] = str(priority)
    fields.update(extra)
    return fields


def kernel(message: str, subsystem: str | None = None, device: str | None = None, priority: int = 6) -> dict:
    fields = {"_TRANSPORT": "kernel", "SYSLOG_IDENTIFIER": "kernel", "SYSLOG_FACILITY": "0",
              "PRIORITY": str(priority), "MESSAGE": message}
    if subsystem is not None:
        fields["_KERNEL_SUBSYSTEM"] = subsystem
        fields["_KERNEL_DEVICE"] = f"+{subsystem}:{device}"
        fields["_UDEV_SYSNAME"] = device
        fields["_UDEV_DEVNODE"] = f"/dev/planted/{device}"
    return fields


LONG_MESSAGE = ("esphome long line " + "x" * 64 + " ") * 240  # about 20 KiB

PERSISTENT = [
    # (offset from T0 in microseconds, boot, fields)
    (0, BOOT_A, docker("hassio_supervisor",
                       "\x1b[32m2026-10-20 10:00:00.120 INFO (MainThread) [supervisor.core] "
                       "Supervisor is up and running\x1b[0m")),
    (1 * SECOND + 123_456, BOOT_A, docker("app_core_mosquitto",
                                          "1792490401: New connection from 172.30.32.1:54012 on port 1883.")),
    (2 * SECOND, BOOT_A, docker("addon_core_mosquitto", "1792490402: Client ha-mcp-legacy connected")),
    (3 * SECOND, BOOT_A, docker("app_core_mosquitto", "Error: Connection refused", priority=3)),
    # Near miss: another add-on whose slug extends the requested one.
    (4 * SECOND, BOOT_A, docker("app_core_mosquitto_extra", "near-miss add-on line")),
    # Spoofs: the identifier without the Docker container fields.
    (5 * SECOND, BOOT_A, native("app_core_mosquitto", "spoofed stdout line", transport="stdout")),
    (6 * SECOND, BOOT_A, docker("app_core_mosquitto", "spoofed container name line",
                                CONTAINER_NAME="app_other")),
    (7 * SECOND, BOOT_A, kernel("usb 1-1.2: USB disconnect, device number 5", "usb", "1-1.2")),
    (8 * SECOND, BOOT_A, kernel("usb 1-1.2: new full-speed USB device number 6 using xhci_hcd", "usb", "1-1.2")),
    (9 * SECOND, BOOT_A, kernel("sda: detected capacity change from 0 to 62521344", "block", "sda", 5)),
    (10 * SECOND, BOOT_A, native("NetworkManager",
                                 "<info>  [1792490410.1234] device (wlan0): state change: activated -> disconnected")),
    (11 * SECOND, BOOT_A, native("wpa_supplicant", "wlan0: psk=PLANTED-WIFI-PSK-0000", priority=7)),
    (12 * SECOND, BOOT_A, docker("app_core_mosquitto",
                                 "mosquitto_sub -h core-mosquitto -u mqtt -P PLANTED-MQTT-PASS-0001")),
    (13 * SECOND, BOOT_A, docker("app_45df7312_zigbee2mqtt",
                                 "MQTT server mqtt://user:PLANTED-URI-PASS-0002@core-mosquitto:1883")),
    (14 * SECOND, BOOT_A, docker("app_45df7312_zigbee2mqtt", "network_key: PLANTED-ZIGBEE-KEY-0003")),
    (15 * SECOND, BOOT_A, docker("app_core_matter_server",
                                 b"Traceback (most recent call last):\n  File \"matter.py\", line 1\n"
                                 b"RuntimeError: Thread border router lost", priority=3)),
    (16 * SECOND, BOOT_A, native("rauc", "Booted into slot B")),
    (17 * SECOND, BOOT_A, native("dockerd", "level=info msg=\"Loading containers: done.\"")),
    (18 * SECOND, BOOT_A, native("systemd-udevd", "sdb: Process '/usr/bin/planted' failed with exit code 1.", 4)),
    (19 * SECOND, BOOT_A, docker("app_5c53de3b_esphome", LONG_MESSAGE)),
    (20 * SECOND, BOOT_A, docker("app_5c53de3b_esphome", "partial docker line", CONTAINER_PARTIAL_MESSAGE="true")),
    (21 * SECOND, BOOT_A, docker("app_5c53de3b_esphome", "line without priority", priority=None)),
    (22 * SECOND, BOOT_A, docker("hassio_supervisor",
                                 "2026-10-20 10:00:22.000 WARNING (MainThread) [supervisor.apps.validate] "
                                 "Invalid option password=PLANTED-OPTION-PASS-0004")),
    (23 * SECOND, BOOT_A, docker("homeassistant", "2026-10-20 10:00:23.000 INFO (MainThread) core line")),
    (24 * SECOND, BOOT_A, native("sshd", "not in the Supervisor host set")),
    (25 * SECOND, BOOT_A, docker("app_core_mosquitto", "-----BEGIN OPENSSH PRIVATE KEY----- PLANTED-PEM-0005")),
    (26 * SECOND, BOOT_A, native("haos-data-disk-detach", "Data disk detached")),
    (27 * SECOND, BOOT_A, kernel("ata1: SATA link down", None, None, 3)),
    # Three entries in one microsecond: the continuation digest picks one.
    (28 * SECOND, BOOT_A, docker("app_5c53de3b_esphome", "same microsecond one")),
    (28 * SECOND, BOOT_A, docker("app_5c53de3b_esphome", "same microsecond two")),
    (28 * SECOND, BOOT_A, docker("app_5c53de3b_esphome", "same microsecond three")),
    (29 * SECOND, BOOT_A, native("systemd-timesyncd", "Contacted time server 192.0.2.1:123 (planted).")),
    (30 * SECOND, BOOT_A, docker("app_core_mosquitto", "1792490430: Saving in-memory database to disk.", 0)),
]

RUNTIME = [
    (3600 * SECOND, BOOT_B, docker("hassio_supervisor",
                                   "2026-10-20 11:00:00.000 INFO (MainThread) [supervisor.core] Supervisor started")),
    (3601 * SECOND, BOOT_B, kernel("usb 1-1.2: USB disconnect, device number 6", "usb", "1-1.2")),
    (3602 * SECOND, BOOT_B, docker("app_core_mosquitto", "1792494002: after reboot", 4)),
    # The clock steps back three seconds within boot B (an NTP correction):
    # monotonic order continues, realtime decreases.
    (3599 * SECOND, BOOT_B, docker("app_core_mosquitto", "1792493999: after clock step back")),
    (3603 * SECOND, BOOT_B, docker("app_core_mosquitto", "1792494003: clock recovered")),
    (3604 * SECOND, BOOT_B, docker("hassio_supervisor", "2026-10-20 11:00:04.000 ERROR (MainThread) "
                                   "[supervisor.apps] App core_mosquitto failed", 3)),
]


def _field(name: str, value: str | bytes) -> bytes:
    data = value.encode("utf-8") if isinstance(value, str) else value
    if b"\n" in data or isinstance(value, bytes):
        return name.encode("ascii") + b"\n" + struct.pack("<Q", len(data)) + data + b"\n"
    return name.encode("ascii") + b"=" + data + b"\n"


def export_stream(entries: list[tuple[int, str, dict]]) -> bytes:
    out = bytearray()
    monotonic = 5 * SECOND
    for offset, boot, fields in entries:
        monotonic += 250_000
        out += _field("__REALTIME_TIMESTAMP", str(T0 + offset))
        out += _field("__MONOTONIC_TIMESTAMP", str(monotonic))
        out += _field("_BOOT_ID", boot)
        out += _field("_MACHINE_ID", MACHINE_ID)
        for name, value in {**NEVER_READ, **fields}.items():
            out += _field(name, value)
        out += b"\n"
    return bytes(out)


STREAMS = {"persistent": PERSISTENT, "runtime": RUNTIME}
VARIANTS = {"uncompressed": "no", "zstd": "yes"}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_exports() -> dict[str, str]:
    digests = {}
    for name, entries in STREAMS.items():
        data = export_stream(entries)
        (HERE / f"{name}.export").write_bytes(data)
        digests[f"{name}.export"] = sha256(data)
    return digests


CONTAINER_SCRIPT = r"""
set -eu
apt-get update -qq >/dev/null
apt-get install -y -qq --no-install-recommends systemd-journal-remote={version} >/dev/null
dpkg-query -W -f='${{Package}} ${{Version}}\n' systemd-journal-remote libsystemd0 libsystemd-shared libzstd1 > /out/versions.txt
for stream in persistent runtime; do
  for variant in uncompressed:no zstd:yes; do
    name=${{variant%%:*}}; flag=${{variant#*:}}
    /usr/lib/systemd/systemd-journal-remote --compress=$flag --seal=no \
      --output=/out/$stream-$name.journal /in/$stream.export
    journalctl --header --file=/out/$stream-$name.journal > /out/$stream-$name.header.txt
  done
done
chown -R {owner} /out
"""


def write_journals(digests: dict[str, str]) -> None:
    # Outside the repository: the container writes here as root.
    work = Path(tempfile.mkdtemp(prefix="hamcp224-journal-"))
    command = [
        "docker", "run", "--rm", "-e", "SYSTEMD_JOURNAL_KEYED_HASH=1", "-e", "SYSTEMD_JOURNAL_COMPACT=1",
        "-v", f"{HERE}:/in:ro", "-v", f"{work}:/out", BASE_IMAGE,
        "sh", "-c", CONTAINER_SCRIPT.format(version=JOURNAL_REMOTE_VERSION, owner=f"{os.getuid()}:{os.getgid()}"),
    ]
    subprocess.run(command, check=True)
    outputs = {}
    headers = {}
    for stream in STREAMS:
        for variant in VARIANTS:
            raw = (work / f"{stream}-{variant}.journal").read_bytes()
            packed = gzip.compress(raw, compresslevel=9, mtime=0)
            (HERE / f"{stream}-{variant}.journal.gz").write_bytes(packed)
            outputs[f"{stream}-{variant}.journal"] = {
                "sha256": sha256(raw), "bytes": len(raw),
                "stored_as": f"{stream}-{variant}.journal.gz", "stored_sha256": sha256(packed),
            }
            header = (work / f"{stream}-{variant}.header.txt").read_text()
            headers[f"{stream}-{variant}"] = [
                line for line in header.splitlines()
                if line.startswith(("Incompatible flags:", "Compatible flags:", "Header size:", "Entry objects:"))
            ]
    provenance = {
        "task": "HAMCP-224",
        "purpose": "Real-format host journal fixtures for the get_incident_logs journal sources (D10).",
        "synthetic": True,
        "generator": "tests/fixtures/hamcp224_journal/generate.py --journals",
        "base_image": BASE_IMAGE,
        "packages": (work / "versions.txt").read_text().splitlines(),
        "environment": {"SYSTEMD_JOURNAL_KEYED_HASH": "1", "SYSTEMD_JOURNAL_COMPACT": "1"},
        "command": "systemd-journal-remote --compress=<no|yes> --seal=no --output=<stream>-<variant>.journal "
                   "<stream>.export",
        "machine_id_directory": MACHINE_ID,
        "inputs": digests,
        "outputs": outputs,
        "headers": headers,
        "reproducibility": REPRODUCIBILITY,
    }
    (HERE / "provenance.json").write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n")
    print(f"container output kept in {work}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--journals", action="store_true")
    args = parser.parse_args()
    digests = write_exports()
    if args.journals:
        write_journals(digests)


if __name__ == "__main__":
    main()
