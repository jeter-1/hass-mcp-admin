# OWNER SIGNING RUNBOOK: HAMCP-182

This runbook does three things, in this order:

1. extend 2026.10.1 (sequence 10);
2. extend 2026.10.0 (sequence 11);
3. revoke 2026.9.4 (sequence 12).

**Status:** executed by Josh on 2026-10-15 from reviewed head `212261d`
(sequences 10, 11 and 12 signed and verified). The only edits since signing are
this status note and the `PY` line in Setup, which now names the interpreter the
signing actually used; see [record §11](core-registry-hamcp182.md#11-part-2-signed-chain-verification-and-publication-preparation).

**Precondition:** run this only after the Codex PRE-SIGNING review of this
pull request's exact head says **SAFE TO SIGN**. Run it from a checkout of that
reviewed head. Evidence: [`core-registry-hamcp182.md`](core-registry-hamcp182.md).

**Rules:**
- **Run this in a real local terminal**: a terminal window, or an SSH session
  in one, with every command typed or pasted at the shell prompt. Do **not**
  run it piped into a shell, from a script file, through `ssh host 'command'`,
  or in an IDE output, run or notebook pane. The Setup pre-flight checks this,
  and so does each signing step.
- Use only the commands below. Use no other signer, helper or edited command.
  - If any command refuses or prints anything other than what is expected:
    STOP, keep every file, and report. Do not retry with a different tool.
- Your Core signing PEM is passed **only** as the `--key-pem` path to
  `sign_step.py`.
  - An unencrypted PEM is used directly; there is no prompt. An encrypted PEM
    also works; this runbook does not require either form.
  - If the PEM is encrypted, `sign_step.py` asks for the passphrase only at its
    hidden prompt `PEM passphrase (hidden): `, and only after it has verified
    that the terminal will not echo it. Otherwise it refuses before reading
    anything. There is no passphrase argument, environment variable or file.
  - Never `cat`, `echo`, copy or move the PEM, never put it inside the
    repository, and never paste its contents or passphrase anywhere.
- Every output path must be new. Both tools refuse to overwrite.
- Do **not** edit or copy into `upstream-trust/ha-core-release-registry.json`.
  Do **not** mark the pull request Ready. Hand back the output paths;
  publication is a separate, later step.

## 0. Setup (no key)

```bash
cd <checkout of the reviewed PR head>
git rev-parse HEAD             # expect the reviewed head SHA named in the review
git status --short             # expect no output
sha256sum upstream-trust/ha-core-release-registry.json
#   expect ecaccd79ff79582f633e1406546a844ee4b38920703e4192e6463162dd4a4402 (sequence 9).
#   Anything else: STOP, candidate 1 is stale.
sha256sum scripts/prepare_core_release_registry.py docs/evidence/hamcp182/check_step.py \
  docs/evidence/hamcp182/sign_step.py docs/evidence/hamcp182/core-public-key.txt \
  docs/evidence/hamcp182/candidates/candidate-1-extend-2026.10.1.json
#   expect the hashes in the "Pinned files" table below. Any difference: STOP.
PY=<locked-env>/bin/python     # the repository's locked Python 3.12 environment (tests/requirements.lock)
D=docs/evidence/hamcp182
O=/home/josh/repos/hass-mcp-admin/.artifacts/hamcp182/owner   # outside the repository checkout
mkdir -p "$O"
KEY_PEM=<path to your Core signing PEM>
REASON="outside the CI support window; retired by owner direction (HAMCP-182)"
set +x
```

Terminal pre-flight. If you ever open a new terminal, redo Setup from the top.

```bash
[ -t 0 ] && [ -t 1 ] && [ -t 2 ] && echo "terminal OK" || echo "STOP: not a real terminal"
#   expect "terminal OK". Anything else: STOP and open a real local terminal.
```

Confirm the public-key fingerprint:

```bash
$PY -c "import base64,hashlib;print(hashlib.sha256(base64.b64decode(open('$D/core-public-key.txt').read().strip())).hexdigest())"
#   expect 7793e80f480b573fbef4e7ca762ff9d1ac783fee1e253db7c35ba8fe21f63dac
```

This base64 must equal the deployed add-on option
`ha_core_release_registry_public_key`, and it must be the public half of your
PEM. `sign_step.py` re-checks the second condition before it signs.

### Pinned files

| File | SHA256 |
|---|---|
| `scripts/prepare_core_release_registry.py` | `6e09d3f96e79253f730604ffb10caffe69d77c183fc734f5517a3733cdf4b368` |
| `docs/evidence/hamcp182/check_step.py` | `efd20c9ee5cbbb056c92d593148f51fbde0a3119117fe748ec6c1877144c7062` |
| `docs/evidence/hamcp182/sign_step.py` | `43491477e88f6bdd0fad5dd1e4862bf32dda9e003282a60ba00cb9e0c3681381` |
| `docs/evidence/hamcp182/core-public-key.txt` | `9b27fb7f81cae19b1de066e926178e8d33b185d2601b5c30723fbe447ccc9f42` |
| `docs/evidence/hamcp182/candidates/candidate-1-extend-2026.10.1.json` | `d9607779d6f7945083dc1ef8126857c880901653e33b32cae808eaece16488d4` |

`check_step.py` pins the six `inputs/` files itself.

## 1. Extend 2026.10.1 (sequence 10)

```bash
$PY $D/check_step.py candidate 1 --previous upstream-trust/ha-core-release-registry.json \
  --candidate $D/candidates/candidate-1-extend-2026.10.1.json
#   expect "references {'2026.10.0': 21, '2026.10.1': 22, '2026.9.4': 21}",
#   "Candidate SHA256: d9607779d6f7945083dc1ef8126857c880901653e33b32cae808eaece16488d4" and "OK"
[ -t 0 ] && [ -t 1 ] && [ -t 2 ] && echo "terminal OK" || echo "STOP: not a real terminal"
$PY $D/sign_step.py --key-pem "$KEY_PEM" \
  --previous upstream-trust/ha-core-release-registry.json \
  --candidate $D/candidates/candidate-1-extend-2026.10.1.json \
  --expected-candidate-sha256 d9607779d6f7945083dc1ef8126857c880901653e33b32cae808eaece16488d4 \
  --output $O/journal-1.json
#   expect "terminal OK"; for an encrypted PEM, the hidden prompt "PEM passphrase (hidden): "
#   (nothing appears as you type); then "Public key fingerprint: 7793e80f…3dac" and
#   "Signed journal SHA256: <J1>"
$PY $D/check_step.py signed 1 --previous upstream-trust/ha-core-release-registry.json \
  --candidate $D/candidates/candidate-1-extend-2026.10.1.json --signed $O/journal-1.json
#   expect "Signed journal verified with the public key: sequence 10 ..." and "OK"
```

## 2. Extend 2026.10.0 (sequence 11): prepare from journal-1, then sign

```bash
$PY scripts/prepare_core_release_registry.py extend-capabilities \
  --entry $D/inputs/entry-extend-2026.10.0.json --evidence $D/inputs/evidence-extend-2026.10.0.json \
  --public-key $D/core-public-key.txt --previous $O/journal-1.json \
  --output $O/candidate-2-extend-2026.10.0.json
#   note "Prepared output SHA256: <S2>"
$PY $D/check_step.py candidate 2 --previous $O/journal-1.json \
  --candidate $O/candidate-2-extend-2026.10.0.json
#   expect "references {'2026.10.0': 22, '2026.10.1': 22, '2026.9.4': 21}",
#   "Candidate SHA256: <S2>" (equal to the prepared SHA) and "OK"
[ -t 0 ] && [ -t 1 ] && [ -t 2 ] && echo "terminal OK" || echo "STOP: not a real terminal"
$PY $D/sign_step.py --key-pem "$KEY_PEM" --previous $O/journal-1.json \
  --candidate $O/candidate-2-extend-2026.10.0.json --expected-candidate-sha256 <S2> \
  --output $O/journal-2.json
$PY $D/check_step.py signed 2 --previous $O/journal-1.json \
  --candidate $O/candidate-2-extend-2026.10.0.json --signed $O/journal-2.json
#   expect "... sequence 11 ..." and "OK"
```

## 3. Revoke 2026.9.4 (sequence 12): prepare from journal-2, then sign

```bash
$PY scripts/prepare_core_release_registry.py revoke \
  --entry $D/inputs/entry-revoke-2026.9.4.json --evidence $D/inputs/evidence-revoke-2026.9.4.json \
  --public-key $D/core-public-key.txt --previous $O/journal-2.json --reason "$REASON" \
  --output $O/candidate-3-revoke-2026.9.4.json
#   note "Prepared output SHA256: <S3>"
$PY $D/check_step.py candidate 3 --previous $O/journal-2.json \
  --candidate $O/candidate-3-revoke-2026.9.4.json
#   expect "references {'2026.10.0': 22, '2026.10.1': 22}",
#   "revoked ['2026.9.2', '2026.9.3', '2026.9.4']", "Candidate SHA256: <S3>" and "OK"
[ -t 0 ] && [ -t 1 ] && [ -t 2 ] && echo "terminal OK" || echo "STOP: not a real terminal"
$PY $D/sign_step.py --key-pem "$KEY_PEM" --previous $O/journal-2.json \
  --candidate $O/candidate-3-revoke-2026.9.4.json --expected-candidate-sha256 <S3> \
  --output $O/journal-3.json
$PY $D/check_step.py signed 3 --previous $O/journal-2.json \
  --candidate $O/candidate-3-revoke-2026.9.4.json --signed $O/journal-3.json
#   expect "... sequence 12 ..." and "OK"
unset KEY_PEM
```

## 4. Hand back

Report:
- the reviewed head SHA you ran from;
- the paths and SHA256 of `journal-1.json`, `journal-2.json`, `journal-3.json`
  and `candidate-2`/`candidate-3`;
- any deviation.

Part 2 then runs, in a later session:
- re-verify the chain with the public key;
- re-run the Part 0 runtime check on the real signed journals at beta.9 and
  beta.8;
- add `journal-3.json` byte-identically to `upstream-trust/` in a draft PR.

That draft PR is published only by its merge, after review and Josh's Ready.

## If a step refuses

| Message | Meaning |
|---|---|
| `STOP: not a real terminal` | The pre-flight failed. Do not run the signer here. Open a real local terminal and redo Setup. |
| `Signing refused: the encrypted PEM needs its passphrase typed in a real local terminal with hidden input (not piped, not an IDE output pane). Nothing was read.` | Hidden input could not be guaranteed, so no passphrase was read and nothing was signed. Open a real local terminal and redo Setup. Never pipe or paste the passphrase into a command. |
| `Signing refused: the PEM is not the reviewed Core key (public key fingerprint …)` | Wrong PEM. Nothing was signed. |
| `Core registry signing refused; review inputs and bounds.` | Wrong passphrase, wrong expected SHA, stale predecessor, existing output, or invalid transition. Nothing was written. |
| `FAILED: …` from `check_step.py` | The candidate or signed journal is not the reviewed operation. Do not continue. |
