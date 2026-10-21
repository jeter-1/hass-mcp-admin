"""HAMCP-217: Engineering TLS trust pinned on certifi 2026.10.19.

certifi 2026.10.19 (GHSA-6q48-6c7w-9gw2) removes exactly one root from the
bundled CA store, ``emSign ECC Root CA - G3``, whose intermediates have
compromised keys; core.py is unchanged. Engineering's httpx-based MCP clients
(the SDK default client factory used by clients/mcp.py and
clients/upstream_read.py, and the dashboard-analysis delegate transport) build
their verifying SSL context from ``certifi.where()``. aiohttp REST/WebSocket
clients use the interpreter default store and do not read certifi.

Loopback TLS with a synthetic, test-generated CA only; no external service,
credential or live Home Assistant.
"""

import asyncio
import datetime
import hashlib
from importlib import metadata
import inspect
import json
import os
from pathlib import Path
import re
import ssl
import sys
import tempfile
import unittest
from unittest import mock

import aiohttp.client_reqrep
import aiohttp.connector
import certifi
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
import httpx
from mcp.shared._httpx_utils import create_mcp_http_client

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
from ha_mcp_engineering.clients.dashboard_analysis import _AnalysisMcpHttp


VERSION = "2026.10.19"
WHEEL_SHA256 = "42d35340cc5b9ba78e818ddbff0c5b1c2b779de448a340fd1b989f5fa8670e1b"
# SHA-256 of the DER root removed by 2026.10.18 and kept out by 2026.10.19.
REMOVED_EMSIGN_ECC_G3 = "86a1ecba089c4a8d3bbe2734c612ba341d813e043cf9e8a862cd5c57a36bbe6b"
# Unaffected emSign roots 2026.10.19 restored after 2026.10.18 removed them.
RESTORED_EMSIGN = {
    "40f6af0346a99aa1cd1d555a4e9cce62c7f9634603ee406615833dc8c8d00367",  # Root CA - G1
    "125609aa301da0a249b97a8239cb6a34216f44dcac9f3954b14292f2e8c8608f",  # Root CA - C1
    "bc4d809b15189d78db3e1d8cf4f9726a795da1643ca5f1358e1ddb0edc0d7eb3",  # ECC Root CA - C3
}
BUNDLE_COUNT = 120
CERTIFI_IMPORT = re.compile(r"^\s*(?:import|from)\s+certifi\b", re.M)
PEM = re.compile(r"-----BEGIN CERTIFICATE-----\r?\n.+?\r?\n-----END CERTIFICATE-----", re.S)


def _fingerprints(der_certificates):
    return {hashlib.sha256(der).hexdigest() for der in der_certificates}


def _bundle():
    return _fingerprints(ssl.PEM_cert_to_DER_cert(block)
                         for block in PEM.findall(certifi.contents()))


def _trusted(context):
    return _fingerprints(context.get_ca_certs(binary_form=True))


def _default_env():
    # Engineering sets neither override; httpx would prefer them over certifi.
    environ = {k: v for k, v in os.environ.items() if k not in ("SSL_CERT_FILE", "SSL_CERT_DIR")}
    return mock.patch.dict(os.environ, environ, clear=True)


def _sdk_client():
    return create_mcp_http_client()


def _analysis_transport():
    return _AnalysisMcpHttp("https://127.0.0.1:1/mcp", {}, None, None, None, 0.0)


def _context(client_or_transport):
    if isinstance(client_or_transport, httpx.AsyncClient):
        return client_or_transport._transport._pool._ssl_context
    return client_or_transport.delegate._pool._ssl_context


class InstalledVersionTests(unittest.TestCase):
    def test_locked_version_is_installed(self):
        # Version expectation only; the trust tests below are the proof.
        self.assertEqual(metadata.version("certifi"), VERSION)
        self.assertEqual(certifi.__version__, VERSION)

    def test_both_locks_and_inventory_agree(self):
        entry = f"certifi=={VERSION} \\\n    --hash=sha256:{WHEEL_SHA256}\n"
        for lock in ("hass_mcp_engineering_beta/requirements.lock", "tests/requirements.lock"):
            text = (ROOT / lock).read_text()
            self.assertIn(entry, text, lock)
            self.assertEqual(text.count("\ncertifi=="), 1, lock)
        inputs = json.loads((ROOT / "hass_mcp_engineering_beta/build-inputs.json").read_text())
        self.assertEqual(inputs["runtime"]["certifi"], VERSION)


class BundleContentsTests(unittest.TestCase):
    def test_compromised_emsign_ecc_g3_root_is_absent(self):
        bundle = _bundle()
        self.assertNotIn(REMOVED_EMSIGN_ECC_G3, bundle)
        self.assertEqual(len(bundle), BUNDLE_COUNT)

    def test_unaffected_emsign_roots_remain(self):
        # Distinguishes 2026.10.19 from 2026.10.18, which removed all emSign roots.
        self.assertTrue(RESTORED_EMSIGN <= _bundle())


class EngineeringHttpxTrustTests(unittest.TestCase):
    """The verifying context of each Engineering httpx path is exactly the bundle."""

    def test_sdk_default_client_trusts_exactly_the_bundle(self):
        with _default_env():
            client = _sdk_client()
        trusted = _trusted(_context(client))
        self.assertEqual(trusted, _bundle())
        self.assertNotIn(REMOVED_EMSIGN_ECC_G3, trusted)

    def test_dashboard_analysis_delegate_trusts_exactly_the_bundle(self):
        with _default_env():
            transport = _analysis_transport()
        trusted = _trusted(_context(transport))
        self.assertEqual(trusted, _bundle())
        self.assertNotIn(REMOVED_EMSIGN_ECC_G3, trusted)

    def test_verification_and_hostname_checks_stay_required(self):
        with _default_env():
            contexts = (_context(_sdk_client()), _context(_analysis_transport()))
        for context in contexts:
            self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
            self.assertTrue(context.check_hostname)


class AiohttpTrustBoundaryTests(unittest.TestCase):
    def test_aiohttp_clients_do_not_read_certifi(self):
        # REST/WebSocket clients keep the interpreter default store; this bump
        # does not change them.
        for module in (aiohttp.connector, aiohttp.client_reqrep):
            self.assertNotIn("certifi", vars(module))
            self.assertIsNone(CERTIFI_IMPORT.search(inspect.getsource(module)), module.__name__)


def _synthetic_ca_and_leaf(directory):
    now = datetime.datetime.now(datetime.timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "HAMCP-217 synthetic test CA")])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
          .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now - datetime.timedelta(minutes=5))
          .not_valid_after(now + datetime.timedelta(hours=1))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False),
                         critical=True)
          .sign(ca_key, hashes.SHA256()))
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    leaf = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
            .issuer_name(ca_name).public_key(leaf_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(hours=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
            .add_extension(x509.ExtendedKeyUsage([x509.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(ca_key, hashes.SHA256()))
    ca_path, chain_path, key_path = (Path(directory) / name for name in ("ca.pem", "leaf.pem", "leaf.key"))
    ca_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    chain_path.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(leaf_key.private_bytes(serialization.Encoding.PEM,
                                                serialization.PrivateFormat.PKCS8,
                                                serialization.NoEncryption()))
    return ca_path, chain_path, key_path


class LoopbackTrustTests(unittest.IsolatedAsyncioTestCase):
    """A root outside the bundle is refused on both Engineering httpx paths."""

    async def asyncSetUp(self):
        loop = asyncio.get_running_loop()

        def quiet_refused_handshake(loop, context):
            # The client aborting an untrusted handshake is the expected outcome.
            if not isinstance(context.get("exception"), (ssl.SSLError, ConnectionError)):
                loop.default_exception_handler(context)

        loop.set_exception_handler(quiet_refused_handshake)
        self.directory = tempfile.TemporaryDirectory()
        self.ca, chain, key = _synthetic_ca_and_leaf(self.directory.name)
        server_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        server_context.load_cert_chain(chain, key)
        self.requests = 0

        async def handle(reader, writer):
            try:
                await reader.readuntil(b"\r\n\r\n")
                self.requests += 1
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
                await writer.drain()
            except (asyncio.IncompleteReadError, ConnectionError, ssl.SSLError):
                pass
            finally:
                writer.close()

        self.server = await asyncio.start_server(handle, "127.0.0.1", 0, ssl=server_context)
        self.url = f"https://localhost:{self.server.sockets[0].getsockname()[1]}/mcp"

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()
        self.directory.cleanup()

    async def _refused(self, client):
        async with client:
            with self.assertRaises(httpx.ConnectError) as caught:
                await client.get(self.url)
        self.assertIn("CERTIFICATE_VERIFY_FAILED", str(caught.exception))

    async def test_sdk_default_client_refuses_root_outside_bundle(self):
        with _default_env():
            client = _sdk_client()
        await self._refused(client)
        self.assertEqual(self.requests, 0)

    async def test_dashboard_analysis_delegate_refuses_root_outside_bundle(self):
        with _default_env():
            transport = _analysis_transport().delegate
        await self._refused(httpx.AsyncClient(transport=transport, trust_env=False))
        self.assertEqual(self.requests, 0)

    async def test_positive_control_same_peer_verifies_with_its_root(self):
        # Proves the refusals above are trust decisions, not a broken peer.
        context = ssl.create_default_context(cafile=str(self.ca))
        async with httpx.AsyncClient(verify=context, trust_env=False) as client:
            response = await client.get(self.url)
        self.assertEqual((response.status_code, response.text), (200, "ok"))
        self.assertEqual(self.requests, 1)


if __name__ == "__main__":
    unittest.main()
