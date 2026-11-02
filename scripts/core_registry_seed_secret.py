"""Owner-run only (HAMCP-278): Core signing PEM -> environment-secret value.

Josh runs this on his own machine with its standard output piped directly into
``gh secret set``. It writes base64 of the raw 32-byte Ed25519 seed, the form
``prepare_core_release_registry.py sign`` reads, and nothing else. It refuses
to write to a terminal, refuses any key that is not the committed owner Core
key, never writes a file, and prints no key material on any refusal. Agents
never run it against the real key.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
from pathlib import Path
import sys
import warnings

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

OWNER_CORE_KEY_SHA256 = "7793e80f480b573fbef4e7ca762ff9d1ac783fee1e253db7c35ba8fe21f63dac"


class Refusal(Exception):
    """A fixed refusal reason; never carries key material."""


def load_owner_key(pem: bytes, password_reader=None) -> Ed25519PrivateKey:
    if not 0 < len(pem) <= 65536:
        raise Refusal("the PEM is empty or too large")
    try:
        key = serialization.load_pem_private_key(pem, password=None)
    except TypeError:
        reader = password_reader or getpass.getpass
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            try:
                password = reader("PEM passphrase (hidden): ").encode("utf-8")
            except getpass.GetPassWarning:
                raise Refusal("hidden passphrase input is unavailable") from None
        try:
            key = serialization.load_pem_private_key(pem, password=password)
        except Exception:
            raise Refusal("the PEM could not be decrypted") from None
    except Exception:
        raise Refusal("the file is not a readable private-key PEM") from None
    if not isinstance(key, Ed25519PrivateKey):
        raise Refusal("the PEM is not an Ed25519 key")
    if hashlib.sha256(key.public_key().public_bytes_raw()).hexdigest() != OWNER_CORE_KEY_SHA256:
        raise Refusal("the PEM is not the committed owner Core key")
    return key


def secret_value(key: Ed25519PrivateKey) -> bytes:
    # No trailing newline: the signer decodes with base64 validate=True.
    return base64.b64encode(key.private_bytes_raw())


def main(argv=None, *, stdout=None, password_reader=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pem", type=Path, required=True)
    args = parser.parse_args(argv)
    out = stdout or sys.stdout.buffer
    if out.isatty():
        raise Refusal("standard output is a terminal; pipe it into gh secret set")
    with args.pem.open("rb") as stream:
        pem = stream.read(65537)
    out.write(secret_value(load_owner_key(pem, password_reader)))
    out.flush()


if __name__ == "__main__":
    try:
        main()
    except Refusal as exc:
        raise SystemExit(f"Seed conversion refused: {exc}. Nothing was written.") from None
    except Exception:
        raise SystemExit("Seed conversion refused; nothing was written.") from None
