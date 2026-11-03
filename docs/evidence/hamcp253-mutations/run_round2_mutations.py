"""HAMCP-253 round-2 correction mutants (R2-M1, R2-M2, R2-M3).

Reuses the copy, preflight, kill rule and reporting of ``run_mutations.py``
with a mutant set aimed only at the code the round-2 corrections added or
changed, and runs every HAMCP-253 test module. Only an assertion failure
(``FAIL:``) is a kill.

Classes:

- ``R2S*``: projection-failure rows screen configured values (R2-M1);
- ``R2D*``: an unusable audit destination is refused before the ledger
  (R2-M2);
- ``R2U*``: a write that fails after the ledger replacement is reported as
  pending, never refused (R2-M3).

Usage (idle host, from the worktree root):

    systemd-run --user --unit=hamcp253-r2-mutations -p MemoryMax=3G \\
        -p MemorySwapMax=0 -p OOMPolicy=continue \\
        --working-directory=<worktree> /bin/bash -c \\
        '.artifacts/venv/bin/python docs/evidence/hamcp253-mutations/run_round2_mutations.py \\
         --work .artifacts/hamcp253-r2-mut \\
         --output docs/evidence/hamcp253-mutations/results-round2-corrections.json \\
         > .artifacts/hamcp253-r2-mut.log 2>&1'
"""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_mutations as harness  # noqa: E402
from run_mutations import RA, SV, E, m  # noqa: E402


PD = f"{E}/governance/projection_diagnostics.py"
S = "projection-failure row shows an unscreened identifier (R2-M1)"
D = "unusable audit destination reaches the ledger (R2-M2)"
U = "uncertain ledger write reported as refused or settled (R2-M3)"

MUTATIONS = [
    # R2-M1: health and list_change_plans projection-failure rows.
    m("R2S1-health-rows-without-configured-values", S,
      (SV, "                projection_failures, sensitive_values=self.sensitive_values\n",
           "                projection_failures, sensitive_values=()\n")),
    m("R2S2-health-rows-screen-ignores-configured-values", S,
      (PD, "                screened_record_id(plan.plan_id, sensitive_values)\n",
           "                screened_record_id(plan.plan_id, ())\n")),
    m("R2S3-health-rows-raw-identifier", S,
      (PD, "                screened_record_id(plan.plan_id, sensitive_values)\n"
           "                or UNRECOGNIZED\n",
           "                plan.plan_id\n")),
    m("R2S4-list-plans-without-configured-values", S,
      (SV, "                        plan_id, self.sensitive_values\n",
           "                        plan_id, ()\n")),
    m("R2S5-list-plans-raw-identifier", S,
      (SV, "                    record_inspection.screened_record_id(\n"
           "                        plan_id, self.sensitive_values\n"
           "                    )\n"
           "                    or record_inspection.UNRECOGNIZED\n",
           "                    plan_id\n")),
    m("R2S6-list-plans-marker-dropped", S,
      (SV, "                    or record_inspection.UNRECOGNIZED\n"
           "                ),\n"
           '                "error_code": error_code.value,\n',
           "                    or None\n"
           "                ),\n"
           '                "error_code": error_code.value,\n')),
    # R2-M2: the destination check before the ledger.
    m("R2D1-enabled-only-check", D,
      (SV, "        if refusal is None and not self._acknowledgement_audit_usable():\n",
           "        if refusal is None and not self._acknowledgement_audit_enabled():\n")),
    m("R2D2-non-regular-target-accepted", D,
      (SV, "                    if not stat.S_ISREG(os.stat(target).st_mode):\n",
           "                    if False:\n")),
    m("R2D3-lock-target-not-checked", D,
      (SV, '        for target, mode in ((path, "ab"), (path + ".lock", "a+b")):\n',
           '        for target, mode in ((path, "ab"),):\n')),
    m("R2D4-log-target-not-checked", D,
      (SV, '        for target, mode in ((path, "ab"), (path + ".lock", "a+b")):\n',
           '        for target, mode in ((path + ".lock", "a+b"),):\n')),
    m("R2D5-open-failure-accepted", D,
      (SV, "            except (OSError, ValueError):\n"
           "                return False\n"
           "        return True\n",
           "            except (OSError, ValueError):\n"
           "                pass\n"
           "        return True\n")),
    m("R2D6-missing-target-refused", D,
      (SV, "                except FileNotFoundError:\n"
           "                    pass\n"
           "                with open(target, mode):\n",
           "                except FileNotFoundError:\n"
           "                    return False\n"
           "                with open(target, mode):\n")),
    m("R2D7-probe-truncates-the-log", D,
      (SV, '        for target, mode in ((path, "ab"), (path + ".lock", "a+b")):\n',
           '        for target, mode in ((path, "wb"), (path + ".lock", "a+b")):\n')),
    m("R2D8-disabled-logger-accepted", D,
      (SV, "        if not self._acknowledgement_audit_enabled():\n"
           "            return False\n"
           "        path = str(self.audit.path)\n",
           "        path = str(getattr(self.audit, 'path', ''))\n")),
    # R2-M3: persistence uncertainty after the replacement.
    m("R2U1-replacement-never-marked", U,
      (RA, "            replaced = True\n", "            replaced = False\n")),
    m("R2U2-replacement-marked-before-replace", U,
      (RA, '            self._inject("before_replace")\n'
           "            os.replace(temporary, self.path)\n",
           "            replaced = True\n"
           '            self._inject("before_replace")\n'
           "            os.replace(temporary, self.path)\n")),
    m("R2U3-uncertain-never-raised", U,
      (RA, "            if replaced:\n", "            if False:\n")),
    m("R2U4-candidate-not-attached", U,
      (RA, "            exc.candidate = deepcopy(event)\n", "            pass\n")),
    m("R2U5-service-drops-candidate", U,
      (SV, "                        decision = exc.candidate\n",
           "                        pass\n")),
    m("R2U6-uncertain-decision-reported-refused", U,
      (SV, "                        decision = exc.candidate\n"
           "                        raise\n",
           "                        raise AcknowledgementStorageError(\n"
           "                            str(exc)\n"
           "                        ) from None\n")),
]

EQUIVALENT: dict[str, str] = {}


def main() -> int:
    harness.MODULES = (
        "test_hamcp253_record_acknowledgement.py",
        "test_hamcp253_record_acknowledgement_web.py",
        "test_hamcp253_round1_corrections.py",
        "test_hamcp253_round2_corrections.py",
    )
    harness.MUTATIONS = MUTATIONS
    harness.EQUIVALENT = EQUIVALENT
    return harness.main()


if __name__ == "__main__":
    raise SystemExit(main())
