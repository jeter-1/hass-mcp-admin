"""HAMCP-371 S12: build the P9 socket record from the committed S6 receipts.

Read-only. Reads the committed S6 run receipts and per-call files, and, when
given, the uncommitted S6 peer logs, which must hash to the values the
committed run SHA256SUMS records. Writes one JSON document to stdout.

    python -I p9_record.py <s6-dir> [--peer-logs <dir-with-run-subdirs>]

From a peer log only lines whose message starts with one of LOG_MESSAGES are
kept. Each kept line is reduced to its timestamp, logger and message.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

RUNS = {
    "A": ("run-a-1", ("9.1.2", "9.2.0")),
    "B-9.1.2": ("run-b-9.1.2-1", ("9.1.2",)),
    "B-9.2.0": ("run-b-9.2.0-2", ("9.2.0",)),
}

# S2 table (a), rows reachable over the socket at v9.2.0: 9 websocket-only and
# 15 websocket and REST. Rows 2 and 8 are REST only, row 18 has no Core call.
SOCKET_ONLY = {"r03", "r04", "r07", "r10", "r13", "r14", "r15", "r23", "r27"}
SOCKET_AND_REST = {"r01", "r05", "r06", "r09", "r11", "r12", "r16", "r17", "r19",
                   "r20", "r21", "r22", "r24", "r25", "r26"}

LOG_MESSAGES = (
    "Connecting to Home Assistant WebSocket: ",
    "WebSocket connected and authenticated successfully",
    "WebSocket disconnected",
    "WebSocket reconnected successfully",
    "WebSocket reconnection failed",
    "WebSocket message failed (",
)
LINE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) (ha_mcp\.client\.[a-z_]+) ([A-Z]+): (.*)$")


def body_success(path: Path):
    """The ``success`` field of the read's JSON text item, or None."""

    record = json.loads(path.read_text(encoding="utf-8"))
    result = (record["exchange"].get("response") or {}).get("result") or {}
    for item in result.get("content") or ():
        if item.get("type", "text") == "text":
            try:
                return json.loads(item["text"]).get("success")
            except (ValueError, AttributeError):
                return None
    return None


def response_date(path: Path):
    """The HTTP ``Date`` header of the reply (one-second resolution, GMT)."""

    record = json.loads(path.read_text(encoding="utf-8"))
    for name, value in record["exchange"].get("response_headers") or ():
        if name.lower() == "date":
            return value
    return None


def calls(folder: Path):
    index = json.loads((folder / "_index.json").read_text(encoding="utf-8"))
    for call in index["calls"]:
        yield call, body_success(folder / call["file"]), response_date(folder / call["file"])


def socket_class(call_id: str) -> str | None:
    prefix = call_id[:3]
    if prefix in SOCKET_ONLY:
        return "websocket only"
    if prefix in SOCKET_AND_REST:
        return "websocket and REST"
    return None


def version_record(s6: Path, run: str, version: str) -> dict:
    out = s6 / run / "out" / version
    reads, transport, after = [], [], []
    for call, success, date in calls(out / "calls"):
        row = {
            "date": date,
            "id": call["id"],
            "tool": call["upstream_tool"],
            "is_error": call["is_error"],
            "success": success,
            "exchange_failure": call["exchange_failure"],
            "body_bytes": call["body_bytes"],
            "seconds": call["seconds"],
        }
        cls = socket_class(call["id"]) if call["group"] == "reads" else None
        if cls:
            reads.append({**row, "socket_class": cls})
        if call["group"] == "transport":
            transport.append({**row, "idle_seconds_before": call["idle_seconds_before"],
                              "new_session": call["new_session"]})
    folder = out / "calls-after-core-restart"
    if folder.is_dir():
        for call, success, date in calls(folder):
            after.append({"id": call["id"], "tool": call["upstream_tool"], "date": date,
                          "is_error": call["is_error"], "success": success,
                          "exchange_failure": call["exchange_failure"],
                          "seconds": call["seconds"]})
    receipt = json.loads((s6 / run / "receipt.json").read_text(encoding="utf-8"))
    capture = receipt["captures"][version]
    return {
        "socket_reads": reads,
        "socket_reads_summary": {
            "reads": len(reads),
            "is_error": sum(r["is_error"] for r in reads),
            "success_false": sum(r["success"] is False for r in reads),
            "exchange_failures": sum(r["exchange_failure"] is not None for r in reads),
        },
        "largest_socket_read": max(reads, key=lambda r: r["body_bytes"])["id"] if reads else None,
        "transport": transport,
        "after_core_restart": after,
        "network_after_core_restart_checked": (capture.get("network_after_core_restart") or {}).get("checked"),
    }


def image_record(s6: Path) -> dict:
    """Python from each image's default environment; vendored files from the
    profile B package copy (taken from the image export)."""

    out = {}
    for run, versions in RUNS.values():
        receipt = json.loads((s6 / run / "receipt.json").read_text(encoding="utf-8"))
        for version in versions:
            env = receipt["upstream_images"][version]["image_defaults"]["environment"]
            items = env if isinstance(env, list) else [f"{k}={v}" for k, v in env.items()]
            row = out.setdefault(version, {})
            row["python_version"] = next(e.split("=", 1)[1] for e in items
                                         if e.startswith("PYTHON_VERSION="))
            copy = (receipt.get("profile_b") or {}).get("package_copy")
            if copy:
                row["dist_info"] = copy["source"]["dist_info"]
                for record in copy["records"]:
                    if record["path"] in ("ha_mcp/_vendor/requirements.txt",
                                          "ha_mcp/_vendor/websockets/VENDORED"):
                        row[record["path"]] = record["sha256"]
    return out


def log_record(s6: Path, logs: Path, run: str, version: str) -> dict:
    name = f"peer-{version}.log"
    sums = {}
    for line in (s6 / run / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, _, path = line.partition("  ")
        sums[path] = digest
    path = logs / run / name
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if sums.get(name) != digest:
        raise SystemExit(f"{run}/{name}: sha256 {digest} is not the committed {sums.get(name)}")
    kept = []
    for raw in data.decode("utf-8").splitlines():
        match = LINE.match(raw)
        if match and match.group(4).startswith(LOG_MESSAGES):
            kept.append(" ".join(match.groups()[:2]) + " " + match.group(3) + ": " + match.group(4))
    return {"file": f"{run}/{name}", "sha256": digest, "lines": len(data.splitlines()),
            "socket_lines": kept}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("s6", type=Path)
    parser.add_argument("--peer-logs", type=Path)
    args = parser.parse_args(argv)
    record = {"images": image_record(args.s6), "runs": {}}
    for profile, (run, versions) in RUNS.items():
        for version in versions:
            row = version_record(args.s6, run, version)
            if args.peer_logs:
                row["peer_log"] = log_record(args.s6, args.peer_logs, run, version)
            record["runs"][f"{profile} {version}"] = row
    json.dump(record, sys.stdout, indent=1, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
