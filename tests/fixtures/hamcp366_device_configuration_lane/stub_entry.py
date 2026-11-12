"""HAMCP-366 lane entry for the unmodified HAMCP-297 Matter Server stub.

Test-only; never a product path. It loads the committed stub by path (the
lane checks that file and its three node dumps against the committed
provenance before any container starts), serves the stub's own WebSocket
handler, and replaces the stub's open control route with one closed lane
route. A POST body that is not one of the listed fault or drift shapes is
refused and changes nothing.

Drift (design section 9.2: "report the attribute update without the write"):
the stub has no such mode, and its source hash is pinned by another lane, so
the drift lives here. It sets one existing attribute of one loaded node and
pushes the stub's own ``attribute_updated`` event; no write is recorded.
"""
import argparse
import hashlib
import importlib.util
import json
import sys

from aiohttp import web

REFUSED = "lane_control_request_not_listed"
SCALARS = (type(None), bool, int)
STUB = web.AppKey("stub", object)


def load(path):
    spec = importlib.util.spec_from_file_location("hamcp297_matter_server_stub", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _seconds(value, maximum):
    return type(value) is int and 1 <= value <= maximum


def fault_listed(body):
    """Exactly the write faults of design section 9.2; no other stub mode."""
    if type(body) is not dict or type(body.get("mode")) is not str:
        return False
    mode, keys = body["mode"], set(body)
    if mode in ("none", "error"):
        return keys == {"mode"}
    if mode == "delay":
        return keys == {"mode", "seconds"} and _seconds(body["seconds"], 10)
    if mode == "stall":
        return keys == {"mode", "seconds"} and _seconds(body["seconds"], 60)
    if mode == "mismatch":
        return keys == {"mode", "stored"} and type(body["stored"]) in SCALARS
    return False


def drift_listed(body):
    return (type(body) is dict and set(body) == {"action", "node_id", "attribute_path", "value"}
            and body["action"] == "drift" and type(body["node_id"]) is int
            and type(body["attribute_path"]) is str and type(body["value"]) in SCALARS)


def refused():
    return web.json_response({"error": REFUSED}, status=400)


async def control(request):
    stub = request.app[STUB]
    if request.method == "GET":
        observed = json.loads((await stub.handle_control(request)).text)
        with open(__file__, "rb") as handle:
            observed["entry_sha256"] = hashlib.sha256(handle.read()).hexdigest()
        return web.json_response(observed)
    if request.method != "POST":
        return refused()
    try:
        body = await request.json()
    except ValueError:
        return refused()
    if fault_listed(body):
        stub.fault = dict(body)
        stub.record("lane_fault", {"fault": body})
        return web.json_response({"fault": body})
    if drift_listed(body):
        node = stub.nodes.get(body["node_id"])
        path = body["attribute_path"]
        if node is None or path not in node["attributes"]:
            return refused()
        before = node["attributes"][path]
        node["attributes"][path] = body["value"]
        stub.record("lane_drift", {"node_id": body["node_id"], "attribute_path": path,
                                   "value": body["value"], "before": before})
        await stub.push_attribute(body["node_id"], path, body["value"])
        return web.json_response({"drift": {"before": before, "value": body["value"]}})
    return refused()


def application(stub):
    app = web.Application()
    app[STUB] = stub
    app.router.add_get("/ws", stub.handle_ws)
    app.router.add_route("*", "/_lane/control", control)
    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stub", required=True)
    parser.add_argument("--nodes", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--port", type=int, default=5580)
    args = parser.parse_args()
    stub = load(args.stub).Stub(args.nodes, args.out)
    print("lane stub ready", json.dumps(stub.dump_hashes), flush=True)
    web.run_app(application(stub), host="0.0.0.0", port=args.port, print=None)


if __name__ == "__main__":
    sys.exit(main())
