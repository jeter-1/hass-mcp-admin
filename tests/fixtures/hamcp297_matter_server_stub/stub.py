"""HAMCP-297 test-only Matter Server stub (server side of the WS subset).

Protocol subset pinned to matter-js/matterjs-server v1.5.5 (commit
a7922e85f4b94a5db94a1dbee1a02c685c2ec71d) and matter-python-client 1.5.5,
the client Core 2026.11.0 pins. Test-only; never a product path.

Residual (disclosed): this stub applies write_attribute to its own attribute
cache and reports it back. Upstream matterjs-server's TestNodeCommandHandler
(TestNodeCommandHandler.ts:187-190) only logs the write and returns success,
with no actual write. A lane on this stub therefore proves the Core-side
command and the read-back path, not a real device write.

get_network_topology (added for HAMCP-231's A2 lane) returns the static,
synthetic TOPOLOGY below. Core forwards whatever the Matter Server builds, so a
lane on it proves pass-through and projection, not topology construction.
"""

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time

from aiohttp import web, WSMsgType

PINNED_DUMPS = {
    "eve_energy_20ecn4101.json": "48a7a21131eee56e47a0db856d21134fd6bc983295f633ea9e3aef6d122de58d",
    "eve_energy_plug.json": "1ed129e955c909f119e40303b234bdc2b83dfadd8347f26570e59e0ef71fa14d",
    "aqara_door_window_p2.json": "5fdde93c56c824795be29ccbb2dd9b919f4ff5a4b17df0f9220d82c675e0836f",
}
# matterjs-server v1.5.5 WebSocketControllerHandler.ts SCHEMA_VERSION / MIN_SUPPORTED_SCHEMA_VERSION
SCHEMA_VERSION = 14
MIN_SUPPORTED_SCHEMA_VERSION = 11
# ServerErrorCode (types/WebSocketMessageTypes.ts)
INVALID_ARGUMENTS = 8
INVALID_COMMAND = 9
NODE_NOT_EXISTS = 5
SDK_STACK_ERROR = 7

# Static synthetic topology. Field names and types follow matter-python-client
# 1.5.5 matter_server/common/models.py:307-380 (TopologyDirectionInfo,
# NetworkTopologyNode, NetworkTopologyConnection, NetworkTopology); the client
# parses it with dataclass_from_dict (client/client.py:417-435 for the command,
# :1522-1525 for the network_topology_updated event). Id conventions follow
# matterjs-server a7922e85: String(node_id), br_<ext address>, thread_<ext mac>
# (ws-client topology-utils.ts:428,471), ap_<bssid without colons>
# (NetworkTopologyService.ts:402); roles per NetworkTopologyService.ts:435-450;
# Wi-Fi stations per :349-362. The ext_address, ext_pan_id, network_name,
# host_name, ssid, bssid and vendor_name values are disclosure canaries; the
# vendor_name carries a 32-hex network-key-shaped string.
TOPOLOGY_CANARY_KEY = "7f3a9c21d4e85b60a1f2c3d4e5f60718"
TOPOLOGY_GHOST_ID = "thread_FFFF00000000DEAD"
TOPOLOGY = {
    "collected_at": 1794268800000,
    "nodes": [
        {"id": "61", "kind": "matter", "network_type": "thread", "node_id": 61, "role": "router",
         "available": True, "is_bridge": False, "ext_address": "6E61A1B2C3D4E561", "rloc16": 17409,
         "vendor_name": "Eve Systems", "model_name": "Eve Energy", "last_seen": 1794268790000},
        {"id": "199", "kind": "matter", "network_type": "thread", "node_id": 199, "role": "reed",
         "available": True, "is_bridge": False, "ext_address": "6E199A1B2C3D4E19", "rloc16": 18432,
         "vendor_name": "Eve Systems", "model_name": "Eve Energy 20ECN4101", "last_seen": 1794268791000},
        {"id": "91", "kind": "matter", "network_type": "wifi", "node_id": 91, "role": "station",
         "available": True, "is_bridge": False, "ssid": "HAMCP231-Canary-SSID", "bssid": "02:A0:C9:31:23:10",
         "vendor_name": "Aqara", "model_name": "Door and Window Sensor P2", "last_seen": 1794268792000},
        {"id": "br_D0C4A5E1F00D7C01", "kind": "border_router", "network_type": "thread", "role": "leader",
         "ext_address": "D0C4A5E1F00D7C01", "rloc16": 17408, "ext_pan_id": "DEADBEEFCAFE2970",
         "network_name": "HAMCP231-Canary-Mesh", "host_name": "hamcp231-canary-br",
         "vendor_name": "canary " + TOPOLOGY_CANARY_KEY, "model_name": "Canary Border Router"},
        {"id": "thread_A1B2C3D4E5F60231", "kind": "thread_unknown", "network_type": "thread",
         "role": "end_device", "ext_address": "A1B2C3D4E5F60231"},
        {"id": "ap_02a0c9312310", "kind": "wifi_ap", "network_type": "wifi",
         "ssid": "HAMCP231-Canary-SSID", "bssid": "02:A0:C9:31:23:10"},
    ],
    "connections": [
        {"source": "61", "target": "br_D0C4A5E1F00D7C01", "network": "thread", "strength": "strong",
         "source_to_target": {"strength": "strong", "lqi": 3, "rssi": -48},
         "target_to_source": {"strength": "strong", "lqi": 3, "rssi": -50},
         "via_route_table": True, "path_cost": 1},
        {"source": "199", "target": "61", "network": "thread", "strength": "medium",
         "source_to_target": {"strength": "medium", "lqi": 2, "rssi": -71},
         "target_to_source": {"strength": "weak", "lqi": 1, "rssi": -84},
         "via_route_table": False, "path_cost": 2},
        {"source": "199", "target": "thread_A1B2C3D4E5F60231", "network": "thread", "strength": "weak",
         "source_to_target": {"strength": "weak", "lqi": 1, "rssi": -88}, "via_route_table": False},
        {"source": "61", "target": TOPOLOGY_GHOST_ID, "network": "thread", "strength": "none",
         "source_to_target": {"strength": "none"}},
        {"source": "91", "target": "ap_02a0c9312310", "network": "wifi", "strength": "strong",
         "source_to_target": {"strength": "strong", "rssi": -42}},
    ],
}


class Stub:
    def __init__(self, nodes_dir, out_path):
        self.nodes = {}
        self.dump_hashes = {}
        for name, expected in PINNED_DUMPS.items():
            raw = open(os.path.join(nodes_dir, name), "rb").read()
            digest = hashlib.sha256(raw).hexdigest()
            if digest != expected:
                raise SystemExit(f"dump hash mismatch {name}: {digest}")
            self.dump_hashes[name] = digest
            node = json.loads(raw)
            self.nodes[int(node["node_id"])] = node
        self.out_path = out_path
        self.listeners = set()
        # Connections that issued get_network_topology (the upstream opt-in to
        # network_topology_updated pushes, WebSocketControllerHandler.ts:146-148).
        self.topology_listeners = set()
        self.topology_requests = []
        self.writes = []
        self.commands = []
        # fault: {"mode": "none|delay|error|stall|mismatch|topology_error", ...}
        # plus optional "server_info_schema_version" (e.g. 12). Core caches
        # schema_version at connect, so that value takes effect for Core only
        # after a reconnect (a Matter config-entry reload, or set it before Core
        # connects).
        self.fault = {"mode": "none"}

    def record(self, kind, payload):
        entry = {"t": time.time(), "kind": kind, **payload}
        with open(self.out_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
        return entry

    def server_info(self):
        return {
            "fabric_id": 1,
            "compressed_fabric_id": 1311768467463790320,
            "fabric_index": 1,
            "schema_version": int(self.fault.get("server_info_schema_version", SCHEMA_VERSION)),
            "min_supported_schema_version": MIN_SUPPORTED_SCHEMA_VERSION,
            "sdk_version": "matter-server/1.5.5 (hamcp-297-test-stub)",
            "wifi_credentials_set": False,
            "thread_credentials_set": False,
            "bluetooth_enabled": False,
            "ble_proxy_enabled": False,
            "controller_node_id": 112233,
        }

    async def push_attribute(self, node_id, path, value):
        message = json.dumps({"event": "attribute_updated", "data": [node_id, path, value]})
        for ws in list(self.listeners):
            try:
                await ws.send_str(message)
            except Exception:  # noqa: BLE001
                self.listeners.discard(ws)

    async def push_topology(self):
        message = json.dumps({"event": "network_topology_updated", "data": TOPOLOGY})
        sent = 0
        for ws in list(self.topology_listeners):
            try:
                await ws.send_str(message)
                sent += 1
            except Exception:  # noqa: BLE001
                self.topology_listeners.discard(ws)
        self.record("network_topology_updated", {"sent": sent})
        return sent

    async def write_attribute(self, args):
        node_id = args.get("node_id")
        path = args.get("attribute_path")
        value = args.get("value")
        node = self.nodes.get(node_id)
        if node is None:
            return None, (NODE_NOT_EXISTS, f"Node {node_id} does not exist")
        parts = str(path).split("/")
        if len(parts) != 3 or not all(p.isdigit() for p in parts):
            return None, (INVALID_ARGUMENTS, "write_attribute needs attribute_path as endpoint/cluster/attribute")
        if path not in node["attributes"]:
            return None, (INVALID_ARGUMENTS, f"unknown attribute {path}")
        before = node["attributes"][path]
        self.writes.append({"node_id": node_id, "attribute_path": path, "value": value, "before": before})
        self.record("write", {"node_id": node_id, "attribute_path": path, "value": value, "before": before})
        mode = self.fault.get("mode", "none")
        if mode == "error":
            return None, (SDK_STACK_ERROR, "test-stub injected write failure")
        if mode == "stall":
            await asyncio.sleep(float(self.fault.get("seconds", 60)))
        stored = self.fault.get("stored", value) if mode == "mismatch" else value
        ep, cl, at = (int(p) for p in parts)
        result = [{"Path": {"EndpointId": ep, "ClusterId": cl, "AttributeId": at}, "Status": 0}]

        async def report():
            if mode == "delay":
                await asyncio.sleep(float(self.fault.get("seconds", 3)))
            node["attributes"][path] = stored
            self.record("attribute_updated", {"node_id": node_id, "attribute_path": path, "value": stored})
            await self.push_attribute(node_id, path, stored)

        return (result, report), None

    async def handle_ws(self, request):
        ws = web.WebSocketResponse(max_msg_size=0)
        await ws.prepare(request)
        await ws.send_str(json.dumps(self.server_info()))
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                cmd = json.loads(msg.data)
                message_id = cmd.get("message_id", "")
                command = cmd.get("command")
                args = cmd.get("args") or {}
                self.commands.append(command)
                self.record("command", {"command": command, "args": args if command != "write_attribute" else None})
                after = None
                error = None
                if command in ("start_listening", "get_nodes"):
                    result = list(self.nodes.values())
                    if command == "start_listening":
                        self.listeners.add(ws)
                elif command == "get_node":
                    node = self.nodes.get(args.get("node_id"))
                    result, error = (node, None) if node else (None, (NODE_NOT_EXISTS, "no node"))
                elif command == "server_info":
                    result = self.server_info()
                elif command == "set_default_fabric_label":
                    result = None
                elif command == "check_node_update":
                    result = None
                elif command == "get_network_topology":
                    self.topology_requests.append({"refresh": args.get("refresh")})
                    self.topology_listeners.add(ws)
                    if self.fault.get("mode") == "topology_error":
                        error = (SDK_STACK_ERROR, "test-stub injected topology failure")
                    else:
                        result = TOPOLOGY
                elif command == "write_attribute":
                    ok, error = await self.write_attribute(args)
                    result, after = ok if ok else (None, None)
                else:
                    error = (INVALID_COMMAND, f"Invalid command: {command}")
                if error is not None:
                    reply = {"message_id": message_id, "error_code": error[0], "details": error[1]}
                else:
                    reply = {"message_id": message_id, "result": result}
                await ws.send_str(json.dumps(reply))
                if after is not None:
                    asyncio.get_running_loop().create_task(after())
        finally:
            self.listeners.discard(ws)
            self.topology_listeners.discard(ws)
        return ws

    async def handle_control(self, request):
        if request.method == "POST":
            body = await request.json()
            if body.get("action") == "push_network_topology":
                return web.json_response({"pushed": await self.push_topology()})
            self.fault = body
            return web.json_response({"fault": self.fault})
        return web.json_response({
            "writes": self.writes,
            "commands": self.commands,
            "topology_requests": self.topology_requests,
            "dump_hashes": self.dump_hashes,
            "stub_sha256": hashlib.sha256(open(__file__, "rb").read()).hexdigest(),
            "nodes": {
                str(k): {p: v["attributes"].get(p) for p in ("1/6/16387", "2/6/16387", "1/319486977/319422481",
                                                            "2/319486977/319422481", "1/128/0", "1/128/1")}
                for k, v in self.nodes.items()
            },
        })


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--nodes", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--port", type=int, default=5580)
    args = parser.parse_args()
    stub = Stub(args.nodes, args.out)
    app = web.Application()
    app.router.add_get("/ws", stub.handle_ws)
    app.router.add_route("*", "/_stub/control", stub.handle_control)
    print("stub ready", json.dumps(stub.dump_hashes), flush=True)
    web.run_app(app, host="0.0.0.0", port=args.port, print=None)


if __name__ == "__main__":
    sys.exit(main())
