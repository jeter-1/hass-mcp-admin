"""Offline regression for GHSA-42vr-xj54-vc7v and retained JWT verification."""

import base64
import json
import io
import unittest
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa


def token_with_payload(payload):
    header = base64.urlsafe_b64encode(b'{"alg":"HS256","kid":"synthetic"}').rstrip(b'=')
    body = base64.urlsafe_b64encode(payload).rstrip(b'=')
    return (header + b'.' + body + b'.Zm9yZ2Vk').decode('ascii')


class PyJWTDependencySecurityTests(unittest.TestCase):
    def nested_token(self):
        # The C JSON parser on Python 3.12 has a distinct recursion budget.
        depth = 20_000
        return token_with_payload(b'[' * depth + b']' * depth)

    def test_deep_payload_is_a_typed_invalid_token(self):
        with self.assertRaises(jwt.DecodeError) as failure:
            jwt.decode(self.nested_token(), options={"verify_signature": False})
        self.assertIsInstance(failure.exception.__cause__, RecursionError)

    def test_jwks_preverification_rejects_before_key_lookup_or_network(self):
        client = jwt.PyJWKClient('https://jwks.invalid/synthetic')
        with patch.object(client, 'get_signing_key') as lookup, patch(
            'urllib.request.build_opener', side_effect=AssertionError('unexpected network')
        ):
            with self.assertRaises(jwt.DecodeError):
                client.get_signing_key_from_jwt(self.nested_token())
            lookup.assert_not_called()

    def test_invalid_json_and_nonobject_payload_remain_rejected(self):
        for payload in (b'{', b'[]', b'null'):
            with self.subTest(payload=payload), self.assertRaises(jwt.DecodeError):
                jwt.decode(token_with_payload(payload), options={"verify_signature": False})

    def test_valid_signature_and_claims_round_trip(self):
        key = b'offline-synthetic-test-key-never-a-credential'
        claims = {'sub': 'synthetic', 'aud': 'engineering-test', 'iss': 'offline'}
        token = jwt.encode(claims, key, algorithm='HS256')
        self.assertEqual(jwt.decode(token, key, algorithms=['HS256'],
                                    audience='engineering-test', issuer='offline'), claims)

    def test_wrong_signature_algorithm_and_expiry_still_fail(self):
        key = b'offline-synthetic-test-key-never-a-credential'
        token = jwt.encode({'sub': 'synthetic'}, key, algorithm='HS256')
        with self.assertRaises(jwt.InvalidSignatureError):
            jwt.decode(token, b'a-different-synthetic-key-of-sufficient-length', algorithms=['HS256'])
        with self.assertRaises(jwt.InvalidAlgorithmError):
            jwt.decode(token, key, algorithms=['HS512'])
        expired = jwt.encode({'exp': 0}, key, algorithm='HS256')
        with self.assertRaises(jwt.ExpiredSignatureError):
            jwt.decode(expired, key, algorithms=['HS256'])

    def test_jwks_cached_key_preserves_verified_rsa_decode(self):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
        jwk.update(kid='synthetic', use='sig', alg='RS256')
        client = jwt.PyJWKClient('https://jwks.invalid/synthetic')
        token = jwt.encode({'sub': 'synthetic'}, key, algorithm='RS256', headers={'kid': 'synthetic'})
        with patch('urllib.request.build_opener') as opener:
            opener.return_value.open.side_effect = lambda *a, **k: io.BytesIO(
                json.dumps({'keys': [jwk]}).encode('utf-8'))
            for _ in range(2):
                selected = client.get_signing_key_from_jwt(token)
                self.assertEqual(jwt.decode(token, selected.key, algorithms=['RS256']),
                                 {'sub': 'synthetic'})
            self.assertEqual(opener.return_value.open.call_count, 1)
