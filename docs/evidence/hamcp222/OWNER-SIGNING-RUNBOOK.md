# OWNER SIGNING RUNBOOK: HAMCP-222

This runbook does two things, in this order:

1. extend 2026.10.1 with four capabilities, 22 → 26 references (sequence 13);
2. add 2026.10.3 with the same 26 references (sequence 14).

What each step means in plain English, with every fingerprint, run ID and
receipt hash: [record, "What you are signing"](core-registry-hamcp222.md#what-you-are-signing-in-plain-english).

**Status:** executed on 2026-10-25. Josh signed both steps from reviewed head
`ffafdabe`. Do not run it again: both sequences are signed, and the signer and
the runtime refuse a stale predecessor. Signing and publication record:
[record §10](core-registry-hamcp222.md#10-signing-and-publication-2026-10-25).

**Precondition:** run this only after the independent PRE-SIGNING review of
this pull request's exact head says **SAFE TO SIGN**. Run it from a checkout of
that reviewed head. Sign step 1 before **2027-01-13T13:40:21Z**, when the
production sequence-12 tip expires (record §6).

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
  `sign_step.py` (the reviewed HAMCP-182 signer, unchanged).
  - An unencrypted PEM is used directly; there is no prompt. An encrypted PEM
    also works.
  - If the PEM is encrypted, `sign_step.py` asks for the passphrase only at its
    hidden prompt `PEM passphrase (hidden): `, and only after it has verified
    that the terminal will not echo it. Otherwise it refuses before reading
    anything. There is no passphrase argument, environment variable or file.
  - Never `cat`, `echo`, copy or move the PEM, never put it inside the
    repository, and never paste its contents or passphrase anywhere.
- Every output path must be new. The tools refuse to overwrite.
- Do **not** edit or copy into `upstream-trust/ha-core-release-registry.json`.
  Do **not** mark the pull request Ready. Hand back the output paths;
  publication is a separate, later pull request (record §7).

## 0. Setup (no key)

```bash
cd <checkout of the reviewed PR head>
git rev-parse HEAD             # expect the reviewed head SHA named in the review
git status --short             # expect no output
sha256sum upstream-trust/ha-core-release-registry.json
#   expect 089247cafa29b3ab0c1687a63419d999ae9c894fa0a94248f73008eabf6785e6 (sequence 12).
#   Anything else: STOP, candidate 1 is stale.
sha256sum scripts/prepare_core_release_registry.py docs/evidence/hamcp222/check_step.py \
  docs/evidence/hamcp182/sign_step.py docs/evidence/hamcp222/core-public-key.txt \
  docs/evidence/hamcp222/candidates/candidate-1-extend-2026.10.1.json
#   expect the hashes in the "Pinned files" table below. Any difference: STOP.
PY=<locked-env>/bin/python     # the repository's locked Python 3.12 environment (tests/requirements.lock),
                               # the same kind you used for HAMCP-182 (it must import jinja2)
D=docs/evidence/hamcp222
S=docs/evidence/hamcp182/sign_step.py
O=/home/josh/repos/hass-mcp-admin/.artifacts/hamcp222/owner   # outside the repository checkout
mkdir -p "$O"
KEY_PEM=<path to your Core signing PEM>
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
| `docs/evidence/hamcp222/check_step.py` | `54f38c6afab7eca663870c0fb991f66c99363a8356ff30caafa4fa10d2dc007a` |
| `docs/evidence/hamcp182/sign_step.py` | `43491477e88f6bdd0fad5dd1e4862bf32dda9e003282a60ba00cb9e0c3681381` |
| `docs/evidence/hamcp222/core-public-key.txt` | `9b27fb7f81cae19b1de066e926178e8d33b185d2601b5c30723fbe447ccc9f42` |
| `docs/evidence/hamcp222/candidates/candidate-1-extend-2026.10.1.json` | `923bd135b02618046552786261036d53dced703be05da93f27c1f80510072458` |

`check_step.py` pins the four `inputs/` files itself.

## 1. Extend 2026.10.1 (sequence 13)

```bash
$PY $D/check_step.py candidate 1 --previous upstream-trust/ha-core-release-registry.json \
  --candidate $D/candidates/candidate-1-extend-2026.10.1.json
#   expect "Step 1 extend-capabilities 2026.10.1: sequence 13, references {'2026.10.0': 22, '2026.10.1': 26},
#   revoked ['2026.9.2', '2026.9.3', '2026.9.4']",
#   "Candidate SHA256: 923bd135b02618046552786261036d53dced703be05da93f27c1f80510072458" and "OK"
[ -t 0 ] && [ -t 1 ] && [ -t 2 ] && echo "terminal OK" || echo "STOP: not a real terminal"
$PY $S --key-pem "$KEY_PEM" \
  --previous upstream-trust/ha-core-release-registry.json \
  --candidate $D/candidates/candidate-1-extend-2026.10.1.json \
  --expected-candidate-sha256 923bd135b02618046552786261036d53dced703be05da93f27c1f80510072458 \
  --output $O/journal-1.json
#   expect "terminal OK"; for an encrypted PEM, the hidden prompt "PEM passphrase (hidden): "
#   (nothing appears as you type); then "Public key fingerprint: 7793e80f…3dac" and
#   "Signed journal SHA256: <J1>"
$PY $D/check_step.py signed 1 --previous upstream-trust/ha-core-release-registry.json \
  --candidate $D/candidates/candidate-1-extend-2026.10.1.json --signed $O/journal-1.json
#   expect the same "Step 1 …" and "Candidate SHA256: 923bd135…" lines,
#   "Signed journal verified with the public key: sequence 13, digest sha256:<…>, SHA256 <J1>" and "OK"
```

## 2. Add 2026.10.3 (sequence 14): prepare from journal-1, then sign

```bash
$PY scripts/prepare_core_release_registry.py add \
  --entry $D/inputs/entry-add-2026.10.3.json --evidence $D/inputs/evidence-add-2026.10.3.json \
  --public-key $D/core-public-key.txt --previous $O/journal-1.json \
  --output $O/candidate-2-add-2026.10.3.json
#   note "Prepared output SHA256: <S2>"
$PY $D/check_step.py candidate 2 --previous $O/journal-1.json \
  --candidate $O/candidate-2-add-2026.10.3.json
#   expect "Step 2 add 2026.10.3: sequence 14, references {'2026.10.0': 22, '2026.10.1': 26, '2026.10.3': 26},
#   revoked ['2026.9.2', '2026.9.3', '2026.9.4']", "Candidate SHA256: <S2>" (equal to the prepared SHA) and "OK"
[ -t 0 ] && [ -t 1 ] && [ -t 2 ] && echo "terminal OK" || echo "STOP: not a real terminal"
$PY $S --key-pem "$KEY_PEM" --previous $O/journal-1.json \
  --candidate $O/candidate-2-add-2026.10.3.json --expected-candidate-sha256 <S2> \
  --output $O/journal-2.json
#   expect "terminal OK", the prompt for an encrypted PEM, "Public key fingerprint: 7793e80f…3dac"
#   and "Signed journal SHA256: <J2>"
$PY $D/check_step.py signed 2 --previous $O/journal-1.json \
  --candidate $O/candidate-2-add-2026.10.3.json --signed $O/journal-2.json
#   expect "Signed journal verified with the public key: sequence 14, digest sha256:<…>, SHA256 <J2>" and "OK"
unset KEY_PEM
```

## 3. Hand back

Report:
- the reviewed head SHA you ran from and the interpreter (`$PY -V`);
- the paths and SHA256 of `journal-1.json`, `candidate-2-add-2026.10.3.json`
  and `journal-2.json`;
- any deviation.

The publication step then runs in a later session, as a new draft pull
request after PR #305 is merged:
- re-verify the real chain with the public key (the reviewed checker and an
  independent verifier);
- re-run the runtime check (record §5) on the real signed journals at beta.36
  and beta.35;
- add `journal-2.json` byte-identically as
  `upstream-trust/ha-core-release-registry.json` and update the tests that pin
  sequence 12 (record §7).

Merging that pull request is the publication. The running add-on then fetches
it from `main` by itself: no Engineering release, image or add-on update is
needed, and a restart is optional (record §7, §5.1).

## Rollback

Do not revert the registry file: the add-on refuses older sequences, so a
revert changes nothing it has already accepted. To withdraw what was signed,
sign a **new** sequence through the reviewed preparation path:

- withdraw 2026.10.3 entirely: `prepare_core_release_registry.py revoke` for
  2026.10.3 with a reason;
- withdraw some of the four capabilities from a release: a reviewed re-issue of
  that entry with fewer references (it needs its own review and runbook).

Each is a separate, reviewed signing task. Rolling Engineering back to beta.34
keeps the journal usable without delete (record §5.2).

## If a step refuses

| Message | Meaning |
|---|---|
| `STOP: not a real terminal` | The pre-flight failed. Do not run the signer here. Open a real local terminal and redo Setup. |
| `Signing refused: the encrypted PEM needs its passphrase typed in a real local terminal with hidden input (not piped, not an IDE output pane). Nothing was read.` | Hidden input could not be guaranteed, so no passphrase was read and nothing was signed. Open a real local terminal and redo Setup. Never pipe or paste the passphrase into a command. |
| `Signing refused: the PEM is not the reviewed Core key (public key fingerprint …)` | Wrong PEM. Nothing was signed. |
| `Core registry signing refused; review inputs and bounds.` | Wrong passphrase, wrong expected SHA, stale or expired predecessor, existing output, or invalid transition. Nothing was written. |
| `Core registry preparation refused; review inputs and bounds.` | Step 2 preparation refused (wrong previous journal, existing output, or invalid input). Nothing was written. |
| `FAILED: …` from `check_step.py` | The candidate or signed journal is not the reviewed operation. Do not continue. |
| A Python traceback such as `FileNotFoundError` from `check_step.py` | A path is wrong (often a new terminal without Setup). Nothing was signed. Redo Setup. |
