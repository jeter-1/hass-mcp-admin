# Engineering 2.1 roadmap

Status: development ordering for `2.1.0`

The required milestone order is:

```text
2.1A Operational Administration
→ 2.1B Broader governed administration
→ 2.1C Upstream lifecycle automation
```

The signed compatibility registry may move ahead of 2.1B only if upstream
release churn becomes the higher operational risk.

## 2.1A staged delivery

1. Dev1 establishes versioned operational plans, reusable configuration-check
   evidence, externally approved full-backup creation, independent backup
   verification, indeterminate recovery, and operational health/audit data.
2. Dev2 may add controlled reload only with successful configuration
   validation during planning and again immediately before apply, followed by
   reload-specific recovery verification.
3. Dev3 may add exact governed add-on restart with protected-target policy and
   disconnect, reconnect, and health verification.
4. Dev4 may add governed Home Assistant restart with fresh configuration
   validation and full recovery verification of Home Assistant, governance,
   audit, provider admission, and dependency prewarm.

Each operation must remain operation-specific. This roadmap does not authorize
a generic administrator, arbitrary Supervisor command, service-call shortcut,
fallback, restore, or deletion.
