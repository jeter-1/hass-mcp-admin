"""HAMCP-182 reviewed owner signer: the shipped `sign` operation with a PEM key form.

HAMCP-161 §6.4: the shipped `prepare_core_release_registry.py sign` reads only a
raw base64 key from HA_CORE_RELEASE_REGISTRY_SIGNING_KEY, and the owner's key is
a PEM file. This file is the reviewed replacement for the unreviewed per-step
PEM helpers used there. It changes only how the key is loaded; signing is the
shipped `sign_candidate` (hash fence, journal fence, transition validation,
output re-verification), and the output is written with the shipped `write_new`
(never overwrites, mode 0600).

Key handling:
  - The PEM path is an argument; the file is read once, bounded to 64 KiB.
  - An unencrypted PEM loads directly, with no prompt and no terminal needed.
  - An encrypted PEM asks for the passphrase only through a hidden prompt.
    Before prompting, stdin must be a terminal whose echo is verifiably switched
    off, or the signer refuses without reading anything. getpass's fallback to
    echoed input (GetPassWarning) is a refusal, never a warning. There is no
    passphrase argument, environment variable or file.
  - Nothing is read from the environment, written, logged or printed except
    public fingerprints and output hashes.
  - The loaded key must be Ed25519 and its PUBLIC key must have the reviewed
    fingerprint before anything is signed.
  - Every failure exits with a fixed message and no traceback or input value.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
from pathlib import Path
import sys
import warnings

try:
    import termios
except ImportError:  # no POSIX terminal control: an encrypted PEM is refused
    termios = None

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "hass_mcp_engineering_beta")]
import prepare_core_release_registry as prepare  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

PUBLIC_KEY_SHA256 = "7793e80f480b573fbef4e7ca762ff9d1ac783fee1e253db7c35ba8fe21f63dac"
MAX_PEM_BYTES = 64 * 1024
NO_HIDDEN_INPUT = ("Signing refused: the encrypted PEM needs its passphrase typed in a real local "
                   "terminal with hidden input (not piped, not an IDE output pane). Nothing was read.")


def require_hidden_input() -> None:
    """Refuse unless stdin is a terminal whose echo can be verifiably switched off."""
    hidden = False
    try:
        fd = sys.stdin.fileno()
        if termios is not None and sys.stdin.isatty():
            old = termios.tcgetattr(fd)
            quiet = list(old)
            quiet[3] &= ~termios.ECHO
            termios.tcsetattr(fd, termios.TCSANOW, quiet)
            try:
                hidden = not termios.tcgetattr(fd)[3] & termios.ECHO
            finally:
                termios.tcsetattr(fd, termios.TCSANOW, old)
    except (AttributeError, OSError, ValueError) + ((termios.error,) if termios else ()):
        hidden = False
    if not hidden:
        raise SystemExit(NO_HIDDEN_INPUT)


def read_passphrase() -> bytes:
    require_hidden_input()
    with warnings.catch_warnings():
        # getpass warns before falling back to echoed input; that fallback is a refusal.
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            return getpass.getpass("PEM passphrase (hidden): ").encode()
        except getpass.GetPassWarning:
            raise SystemExit(NO_HIDDEN_INPUT) from None


def load_key(path: Path) -> Ed25519PrivateKey:
    pem = prepare.read_bounded(path, MAX_PEM_BYTES)
    try:
        key = serialization.load_pem_private_key(pem, password=None)
    except TypeError:  # encrypted PEM: password required
        passphrase = read_passphrase()
        key = serialization.load_pem_private_key(pem, password=passphrase)
        del passphrase
    del pem
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("key type")
    return key


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--key-pem", type=Path, required=True)
    parser.add_argument("--previous", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--expected-candidate-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    # Only for the ephemeral-key rehearsal (harness/); never used when signing.
    parser.add_argument("--rehearsal-key-sha256", default=PUBLIC_KEY_SHA256)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("output exists")
    key = load_key(args.key_pem)
    fingerprint = hashlib.sha256(key.public_key().public_bytes_raw()).hexdigest()
    if fingerprint != args.rehearsal_key_sha256:
        del key
        raise SystemExit("Signing refused: the PEM is not the reviewed Core key "
                         f"(public key fingerprint {fingerprint}).")
    raw = prepare.sign_candidate(prepare.read_bounded(args.candidate),
                                 expected_sha256=args.expected_candidate_sha256,
                                 previous=prepare.read_bounded(args.previous), key=key)
    del key
    prepare.write_new(args.output, raw)
    print("Public key fingerprint: " + fingerprint)
    print("Signed journal SHA256: " + hashlib.sha256(raw).hexdigest())


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException:
        # Key, passphrase and input errors must never export supplied values or traceback.
        raise SystemExit("Core registry signing refused; review inputs and bounds.") from None
