import asyncio
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest


SERVER_DIR = Path(__file__).resolve().parents[1] / "hass_mcp_admin"
sys.path.insert(0, str(SERVER_DIR))
spec = importlib.util.spec_from_file_location("server", SERVER_DIR / "server.py")
server = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(server)


class RecordingApp:
    def __init__(self):
        self.scopes = []

    async def __call__(self, scope, receive, send):
        self.scopes.append(scope)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


class GatewayTests(unittest.TestCase):
    secret = "a" * 24

    def request(self, path, body=b"", app=None):
        app = app or RecordingApp()
        gateway = server.Gateway(app, self.secret)
        messages = []
        received = False

        async def receive():
            nonlocal received
            if received:
                return {"type": "http.disconnect"}
            received = True
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message):
            messages.append(message)

        scope = {
            "type": "http",
            "method": "POST",
            "path": path,
            "raw_path": path.encode(),
            "headers": [],
            "client": ("127.0.0.1", 1234),
        }
        asyncio.run(gateway(scope, receive, send))
        status = next(m["status"] for m in messages if m["type"] == "http.response.start")
        return status, messages, app

    def test_authenticated_path_without_trailing_slash(self):
        status, _, app = self.request(f"/{self.secret}/mcp")
        self.assertEqual(status, 200)
        self.assertEqual(app.scopes[0]["path"], "/mcp/")
        self.assertEqual(app.scopes[0]["raw_path"], b"/mcp/")

    def test_authenticated_path_with_trailing_slash(self):
        status, _, app = self.request(f"/{self.secret}/mcp/")
        self.assertEqual(status, 200)
        self.assertEqual(app.scopes[0]["path"], "/mcp/")

    def test_unauthenticated_root_mcp_paths_are_rejected(self):
        for path in ("/mcp", "/mcp/"):
            with self.subTest(path=path):
                status, _, app = self.request(path)
                self.assertEqual(status, 404)
                self.assertEqual(app.scopes, [])

    def test_initialize_request_succeeds_without_redirect(self):
        body = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                       "clientInfo": {"name": "test", "version": "1"}},
        }).encode()
        status, messages, app = self.request(f"/{self.secret}/mcp", body)
        self.assertEqual(status, 200)
        self.assertNotIn(307, [m.get("status") for m in messages])
        self.assertEqual(app.scopes[0]["path"], "/mcp/")

    def test_audit_path_redacts_complete_secret(self):
        old_path = server.AUDIT_PATH
        try:
            with tempfile.TemporaryDirectory() as tmp:
                server.AUDIT_PATH = os.path.join(tmp, "audit.jsonl")
                self.request(f"/{self.secret}x/mcp")
                audit = Path(server.AUDIT_PATH).read_text()
                self.assertNotIn(self.secret, audit)
                self.assertIn("<access_secret>", audit)
        finally:
            server.AUDIT_PATH = old_path


if __name__ == "__main__":
    unittest.main()
